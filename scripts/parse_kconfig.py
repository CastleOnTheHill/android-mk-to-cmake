#!/usr/bin/env python3
from __future__ import annotations

import argparse
import pathlib
import re
from typing import Any

from common import Manifest, atomic_write_json, ensure_state, input_hash, rel, resolve_root, resolve_under, sha256_file


SET_RE = re.compile(r"^(CONFIG_[A-Za-z0-9_]+)=(.*)$")
UNSET_RE = re.compile(r"^#\s+(CONFIG_[A-Za-z0-9_]+)\s+is not set$")


def find_config_files(config_path: pathlib.Path) -> list[pathlib.Path]:
    if not config_path.exists():
        return []
    if config_path.is_file():
        return [config_path]
    return sorted(path for path in config_path.rglob(".config") if path.is_file())


def product_name(path: pathlib.Path, config_root: pathlib.Path) -> str:
    if path.name == ".config":
        try:
            parent = path.parent.relative_to(config_root)
            return parent.as_posix() or path.parent.name
        except ValueError:
            return path.parent.name
    return path.stem


def parse_one(path: pathlib.Path, config_root: pathlib.Path, root: pathlib.Path) -> dict[str, Any]:
    symbols: dict[str, Any] = {}
    for line_no, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        line = raw.strip()
        match = UNSET_RE.match(line)
        if match:
            key = match.group(1)
            symbols[key] = {"value": "n", "enabled": False, "line": line_no, "raw": raw}
            continue
        match = SET_RE.match(line)
        if not match:
            continue
        key, value = match.groups()
        value = value.strip()
        enabled = value in {"y", "m"}
        symbols[key] = {"value": value, "enabled": enabled, "line": line_no, "raw": raw}
    return {
        "product": product_name(path, config_root),
        "path": rel(path, root),
        "sha256": sha256_file(path),
        "symbols": symbols,
    }


def build_index(products: list[dict[str, Any]]) -> dict[str, Any]:
    index: dict[str, Any] = {}
    for product in products:
        name = product["product"]
        for symbol, item in product["symbols"].items():
            row = index.setdefault(symbol, {"values": {}, "products": {}})
            value = item["value"]
            row["values"].setdefault(value, []).append(name)
            row["products"][name] = value
    return index


def parse_kconfig_stage(
    root: str | pathlib.Path = ".",
    config_dir: str = "config",
    state_dir: str = "state",
    force: bool = False,
) -> dict[str, Any]:
    root = resolve_root(root)
    state = ensure_state(root, state_dir)
    config_root = resolve_under(root, config_dir)
    output = state / "products.json"
    manifest = Manifest(root, state)

    files = find_config_files(config_root)
    digest = input_hash([str(config_root), *[f"{rel(path, root)}:{sha256_file(path)}" for path in files]])
    if not force and manifest.done("parse_kconfig", "all", digest, [output]):
        return {"stage": "parse_kconfig", "status": "done", "reused": True, "products": len(files), "output": rel(output, root)}

    products = [parse_one(path, config_root, root) for path in files]
    result = {
        "schema_version": 1,
        "config_root": rel(config_root, root),
        "products": products,
        "symbols": build_index(products),
    }
    atomic_write_json(output, result)
    manifest.mark("parse_kconfig", "all", "done", digest, [output])
    return {"stage": "parse_kconfig", "status": "done", "reused": False, "products": len(products), "output": rel(output, root)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse Kconfig .config files into product configuration index.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--config-dir", default="config")
    parser.add_argument("--state-dir", default="state")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    result = parse_kconfig_stage(args.root, args.config_dir, args.state_dir, args.force)
    if result["reused"]:
        print(f"parse_kconfig: reused {result['output']}")
    else:
        print(f"parse_kconfig: parsed {result['products']} product config(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import pathlib
from typing import Any

from common import Manifest, atomic_write_json, ensure_state, input_hash, rel, resolve_root, resolve_under, sha256_file, stable_id


DEFAULT_PATTERNS = ["Android.mk", "package.mk", "*.mk", "Makefile"]
DEFAULT_IGNORES = [".git", "state", "build", "cmake-build", ".tools", "node_modules"]


def ignored(path: pathlib.Path, root: pathlib.Path, patterns: list[str]) -> bool:
    rel_path = rel(path, root)
    parts = path.relative_to(root).parts if path.is_relative_to(root) else path.parts
    return any(part in patterns for part in parts) or any(fnmatch.fnmatch(rel_path, pattern) for pattern in patterns)


def matches(path: pathlib.Path, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path.name, pattern) for pattern in patterns)


def kind_guess(path: pathlib.Path) -> str:
    if path.name == "Android.mk":
        return "android_mk"
    if path.name == "package.mk":
        return "package_mk"
    if path.name == "Makefile":
        return "makefile"
    return "mk"


def scan_mk_stage(
    root: str | pathlib.Path = ".",
    scan_dir: str = ".",
    state_dir: str = "state",
    patterns: list[str] | None = None,
    extra_ignores: list[str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    root = resolve_root(root)
    scan_path = resolve_under(root, scan_dir)
    state = ensure_state(root, state_dir)
    output = state / "mk_files.json"
    manifest = Manifest(root, state)
    patterns = patterns or DEFAULT_PATTERNS
    ignores = DEFAULT_IGNORES + (extra_ignores or []) + [rel(state, root)]

    candidates = sorted(path for path in scan_path.rglob("*") if path.is_file())
    files = []
    for path in candidates:
        if ignored(path, root, ignores) or not matches(path, patterns):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        source_rel = rel(path, root)
        files.append(
            {
                "id": stable_id(source_rel),
                "path": source_rel,
                "name": path.name,
                "kind_guess": kind_guess(path),
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
                "line_count": len(text.splitlines()),
            }
        )

    digest = input_hash([rel(scan_path, root), *[f"{item['path']}:{item['sha256']}" for item in files]])
    if not force and manifest.done("scan_mk", "all", digest, [output]):
        return {"stage": "scan_mk", "status": "done", "reused": True, "files": len(files), "output": rel(output, root)}

    atomic_write_json(output, {"schema_version": 1, "root": rel(root, root), "files": files})
    manifest.mark("scan_mk", "all", "done", digest, [output])
    return {"stage": "scan_mk", "status": "done", "reused": False, "files": len(files), "output": rel(output, root)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan project for Android.mk, package.mk, *.mk, and Makefile inputs.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--scan-dir", default=".")
    parser.add_argument("--state-dir", default="state")
    parser.add_argument("--pattern", action="append", default=[])
    parser.add_argument("--ignore", action="append", default=[])
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    result = scan_mk_stage(args.root, args.scan_dir, args.state_dir, args.pattern or None, args.ignore, args.force)
    if result["reused"]:
        print(f"scan_mk: reused {result['output']}")
    else:
        print(f"scan_mk: discovered {result['files']} mk/make file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

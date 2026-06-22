#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import pathlib

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


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan project for Android.mk, package.mk, *.mk, and Makefile inputs.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--scan-dir", default=".")
    parser.add_argument("--state-dir", default="state")
    parser.add_argument("--pattern", action="append", default=[])
    parser.add_argument("--ignore", action="append", default=[])
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    root = resolve_root(args.root)
    scan_dir = resolve_under(root, args.scan_dir)
    state = ensure_state(root, args.state_dir)
    output = state / "mk_files.json"
    manifest = Manifest(root, state)
    patterns = args.pattern or DEFAULT_PATTERNS
    ignores = DEFAULT_IGNORES + args.ignore + [rel(state, root)]

    candidates = sorted(path for path in scan_dir.rglob("*") if path.is_file())
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

    digest = input_hash([rel(scan_dir, root), *[f"{item['path']}:{item['sha256']}" for item in files]])
    if not args.force and manifest.done("scan_mk", "all", digest, [output]):
        print(f"scan_mk: reused {rel(output, root)}")
        return 0

    atomic_write_json(output, {"schema_version": 1, "root": rel(root, root), "files": files})
    manifest.mark("scan_mk", "all", "done", digest, [output])
    print(f"scan_mk: discovered {len(files)} mk/make file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

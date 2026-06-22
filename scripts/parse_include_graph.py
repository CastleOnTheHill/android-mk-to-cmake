#!/usr/bin/env python3
from __future__ import annotations

import argparse
import pathlib
import re
from typing import Any

from common import (
    Manifest,
    atomic_write_json,
    ensure_state,
    input_hash,
    load_json,
    logical_lines,
    read_text,
    rel,
    resolve_root,
    resolve_under,
    sha256_file,
    strip_comment,
)


INCLUDE_RE = re.compile(r"^(?P<kind>-?include|sinclude)\s+(?P<expr>.+)$")
SPECIAL_INCLUDES = {
    "$(CLEAR_VARS)",
    "$(BUILD_SHARED_LIBRARY)",
    "$(BUILD_STATIC_LIBRARY)",
    "$(BUILD_EXECUTABLE)",
    "$(BUILD_PREBUILT)",
}


def normalize_expr(expr: str) -> str:
    return expr.strip().strip('"').strip("'")


def local_path_for(source: pathlib.Path) -> str:
    return source.parent.as_posix()


def candidate_paths(root: pathlib.Path, source: pathlib.Path, expr: str) -> list[pathlib.Path]:
    value = normalize_expr(expr)
    value = value.replace("$(LOCAL_PATH)", local_path_for(source))
    value = value.replace("${LOCAL_PATH}", local_path_for(source))
    if "$" in value or "(" in value or ")" in value:
        return []
    path = pathlib.Path(value)
    if path.is_absolute():
        return [path]
    return [(root / path).resolve(), (root / source.parent / path).resolve()]


def parse_file(root: pathlib.Path, source_rel: str) -> list[dict[str, Any]]:
    source = root / source_rel
    edges: list[dict[str, Any]] = []
    for row in logical_lines(read_text(source)):
        text = strip_comment(row["text"]).strip()
        if not text:
            continue
        match = INCLUDE_RE.match(text)
        if not match:
            continue
        kind = match.group("kind")
        expr = normalize_expr(match.group("expr"))
        optional = kind in {"-include", "sinclude"}
        if expr in SPECIAL_INCLUDES or expr.startswith("$(BUILD_"):
            edges.append(
                {
                    "from": source_rel,
                    "to": "",
                    "line": row["start_line"],
                    "raw": row["raw"],
                    "expr": expr,
                    "kind": "special",
                    "optional": optional,
                    "resolved": False,
                    "reason": "android_build_macro",
                }
            )
            continue
        resolved = []
        if "*" in expr and "$" not in expr:
            base_expr = expr.replace("$(LOCAL_PATH)", source.parent.as_posix())
            pattern = pathlib.Path(base_expr)
            glob_base = root if not pattern.is_absolute() else pathlib.Path("/")
            resolved = sorted(path.resolve() for path in glob_base.glob(pattern.as_posix()) if path.is_file())
        else:
            for candidate in candidate_paths(root, pathlib.Path(source_rel), expr):
                if candidate.exists() and candidate.is_file():
                    resolved.append(candidate)
        if resolved:
            seen = set()
            for target in resolved:
                target_rel = rel(target, root)
                if target_rel in seen:
                    continue
                seen.add(target_rel)
                edges.append(
                    {
                        "from": source_rel,
                        "to": target_rel,
                        "line": row["start_line"],
                        "raw": row["raw"],
                        "expr": expr,
                        "kind": "include",
                        "optional": optional,
                        "resolved": True,
                        "reason": "",
                    }
                )
        else:
            edges.append(
                {
                    "from": source_rel,
                    "to": "",
                    "line": row["start_line"],
                    "raw": row["raw"],
                    "expr": expr,
                    "kind": "include",
                    "optional": optional,
                    "resolved": False,
                    "reason": "unresolved_expression" if "$" in expr else "file_not_found",
                }
            )
    return edges


def main() -> int:
    parser = argparse.ArgumentParser(description="Build mk include graph before conversion.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--state-dir", default="state")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    root = resolve_root(args.root)
    state = ensure_state(root, args.state_dir)
    mk_files_path = state / "mk_files.json"
    output = state / "include_graph.json"
    manifest = Manifest(root, state)
    mk_files = load_json(mk_files_path, {"files": []})["files"]
    digest = input_hash([f"{item['path']}:{item['sha256']}" for item in mk_files])
    if not args.force and manifest.done("parse_include_graph", "all", digest, [output]):
        print(f"parse_include_graph: reused {rel(output, root)}")
        return 0

    edges: list[dict[str, Any]] = []
    nodes = [{"id": item["id"], "path": item["path"], "kind_guess": item["kind_guess"]} for item in mk_files]
    for item in mk_files:
        edges.extend(parse_file(root, item["path"]))
    unresolved = [edge for edge in edges if edge["kind"] == "include" and not edge["resolved"] and not edge["optional"]]
    result = {
        "schema_version": 1,
        "nodes": nodes,
        "edges": edges,
        "unresolved": unresolved,
    }
    atomic_write_json(output, result)
    manifest.mark("parse_include_graph", "all", "done", digest, [output])
    print(f"parse_include_graph: {len(edges)} include edge(s), {len(unresolved)} unresolved required")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
    split_words,
    stable_id,
    strip_comment,
)


ASSIGN_RE = re.compile(r"^(?P<var>[A-Za-z0-9_.$()/{}+-]+)\s*(?P<op>:=|\+=|\?=|=)\s*(?P<value>.*)$")
INCLUDE_RE = re.compile(r"^(?P<kind>-?include|sinclude)\s+(?P<expr>.+)$")
COND_RE = re.compile(r"^(?P<kind>ifeq|ifneq|ifdef|ifndef)\s*(?P<expr>.*)$")
BUILD_RULES = {
    "$(BUILD_SHARED_LIBRARY)": "BUILD_SHARED_LIBRARY",
    "$(BUILD_STATIC_LIBRARY)": "BUILD_STATIC_LIBRARY",
    "$(BUILD_EXECUTABLE)": "BUILD_EXECUTABLE",
    "$(BUILD_PREBUILT)": "BUILD_PREBUILT",
}
LOCAL_VARS = {
    "LOCAL_MODULE",
    "LOCAL_LAYER",
    "LOCAL_SRC_FILES",
    "LOCAL_GENERATED_SOURCES",
    "LOCAL_C_INCLUDES",
    "LOCAL_EXPORT_C_INCLUDE_DIRS",
    "LOCAL_CFLAGS",
    "LOCAL_CPPFLAGS",
    "LOCAL_CONLYFLAGS",
    "LOCAL_SHARED_LIBRARIES",
    "LOCAL_STATIC_LIBRARIES",
    "LOCAL_WHOLE_STATIC_LIBRARIES",
    "LOCAL_LDFLAGS",
}


def cmake_condition(raw_kind: str, expr: str) -> str:
    text = expr.strip()
    if raw_kind == "ifdef":
        return text
    if raw_kind == "ifndef":
        return f"NOT {text}"
    match = re.match(r"^\(?\s*\$\(([^)]+)\)\s*,\s*([^)]+?)\s*\)?$", text)
    if match:
        var, value = match.groups()
        value = value.strip().strip('"').strip("'")
        cond = var if value == "y" else f'{var} STREQUAL "{value}"'
        return cond if raw_kind == "ifeq" else f"NOT ({cond})"
    return text if raw_kind == "ifeq" else f"NOT ({text})"


def sanitize_target(name: str) -> str:
    value = name.strip()
    value = re.sub(r"[^A-Za-z0-9_.+-]", "_", value)
    return value or "unknown_target"


def object_target_name(module: str) -> str:
    name = sanitize_target(module)
    return name[3:] if name.startswith("lib") and len(name) > 3 else name


def classify_file_type(events: list[dict[str, Any]], targets: list[dict[str, Any]]) -> str:
    if targets:
        return "target_definition"
    has_target_mutation = any(event.get("variable") in LOCAL_VARS for event in events)
    has_real_include = any(event["kind"] == "include" and event.get("include_kind") == "include" for event in events)
    if has_target_mutation:
        return "target_fragment"
    if has_real_include:
        return "connector"
    return "unknown"


def current_vars(block_events: list[dict[str, Any]]) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for event in block_events:
        variable = event.get("variable")
        if not variable:
            continue
        words = event.get("values", [])
        if event.get("operator") == "+=":
            values.setdefault(variable, []).extend(words)
        else:
            values[variable] = list(words)
    return values


def target_from_block(source_rel: str, block_events: list[dict[str, Any]], build_event: dict[str, Any]) -> dict[str, Any]:
    variables = current_vars(block_events)
    module = " ".join(variables.get("LOCAL_MODULE", []))
    layer = " ".join(variables.get("LOCAL_LAYER", []))
    build_rule = build_event["build_rule"]
    if build_rule == "BUILD_SHARED_LIBRARY":
        cmake_kind = "shared_library"
        cmake_target = sanitize_target(module)
    elif build_rule == "BUILD_EXECUTABLE":
        cmake_kind = "executable"
        cmake_target = sanitize_target(module)
    elif build_rule == "BUILD_STATIC_LIBRARY" and layer:
        cmake_kind = "object_library"
        cmake_target = object_target_name(module)
    elif build_rule == "BUILD_STATIC_LIBRARY":
        cmake_kind = "static_library"
        cmake_target = sanitize_target(module)
    else:
        cmake_kind = "unknown"
        cmake_target = sanitize_target(module)
    return {
        "id": stable_id(f"{source_rel}:{module}:{build_event['line']}"),
        "module": module,
        "cmake_target": cmake_target,
        "cmake_kind": cmake_kind,
        "build_rule": build_rule,
        "local_layer": layer,
        "source_range": {"start_line": block_events[0]["line"] if block_events else build_event["line"], "end_line": build_event["line"]},
        "events": block_events + [build_event],
    }


def parse_file(root: pathlib.Path, source_rel: str) -> dict[str, Any]:
    source = root / source_rel
    text = read_text(source)
    condition_stack: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    targets: list[dict[str, Any]] = []
    block_events: list[dict[str, Any]] = []
    in_block = False
    unknown: list[dict[str, Any]] = []

    for row in logical_lines(text):
        cleaned = strip_comment(row["text"]).strip()
        if not cleaned:
            continue
        base = {
            "line": row["start_line"],
            "end_line": row["end_line"],
            "raw": row["raw"],
            "condition_stack": [item["raw"] for item in condition_stack],
        }
        cond = COND_RE.match(cleaned)
        if cond:
            item = {
                **base,
                "kind": "if",
                "if_kind": cond.group("kind"),
                "expr": cond.group("expr").strip(),
                "cmake_condition": cmake_condition(cond.group("kind"), cond.group("expr")),
            }
            events.append(item)
            if in_block:
                block_events.append(item)
            condition_stack.append({"raw": cleaned, "cmake_condition": item["cmake_condition"]})
            continue
        if cleaned == "else":
            item = {**base, "kind": "else"}
            events.append(item)
            if in_block:
                block_events.append(item)
            continue
        if cleaned == "endif":
            item = {**base, "kind": "endif"}
            events.append(item)
            if in_block:
                block_events.append(item)
            if condition_stack:
                condition_stack.pop()
            else:
                unknown.append({**base, "reason": "endif_without_if"})
            continue
        include = INCLUDE_RE.match(cleaned)
        if include:
            expr = include.group("expr").strip()
            optional = include.group("kind") in {"-include", "sinclude"}
            if expr == "$(CLEAR_VARS)":
                in_block = True
                block_events = [{**base, "kind": "clear_vars"}]
                events.append(block_events[0])
                continue
            if expr in BUILD_RULES:
                item = {**base, "kind": "build_rule", "build_rule": BUILD_RULES[expr], "expr": expr}
                events.append(item)
                if in_block:
                    targets.append(target_from_block(source_rel, block_events, item))
                    block_events = []
                    in_block = False
                else:
                    unknown.append({**base, "reason": "build_rule_without_clear_vars", "expr": expr})
                continue
            item = {
                **base,
                "kind": "include",
                "include_kind": "include",
                "optional": optional,
                "expr": expr,
            }
            events.append(item)
            if in_block:
                block_events.append(item)
            continue
        assign = ASSIGN_RE.match(cleaned)
        if assign:
            variable = assign.group("var")
            value = assign.group("value").strip()
            item = {
                **base,
                "kind": "assign" if assign.group("op") != "+=" else "append",
                "variable": variable,
                "operator": assign.group("op"),
                "value": value,
                "values": split_words(value),
            }
            events.append(item)
            if in_block:
                block_events.append(item)
            if "$(" in value and "LOCAL_PATH" not in value:
                unknown.append({**base, "reason": "unresolved_make_expression", "variable": variable, "value": value})
            continue
        if "$(" in cleaned or cleaned.startswith("\t") or ":" in cleaned:
            unknown.append({**base, "reason": "unparsed_statement", "text": cleaned})
        events.append({**base, "kind": "raw", "text": cleaned})
        if in_block:
            block_events.append(events[-1])

    result = {
        "schema_version": 1,
        "source_file": source_rel,
        "source_sha256": sha256_file(source),
        "file_type": classify_file_type(events, targets),
        "events": events,
        "targets": targets,
        "unknown": unknown,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse each mk file into order-preserving IR.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--state-dir", default="state")
    parser.add_argument("--file", action="append", default=[])
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    root = resolve_root(args.root)
    state = ensure_state(root, args.state_dir)
    manifest = Manifest(root, state)
    mk_files = load_json(state / "mk_files.json", {"files": []})["files"]
    wanted = set(args.file)
    selected = [item for item in mk_files if not wanted or item["path"] in wanted]
    out_dir = state / "files"
    unknown_dir = state / "unknown"
    count = 0
    unknown_count = 0

    for item in selected:
        source = root / item["path"]
        output = out_dir / f"{item['id']}.ir.json"
        unknown_output = unknown_dir / f"{item['id']}.unknown.json"
        digest = input_hash([item["path"], sha256_file(source)])
        if not args.force and manifest.done("parse_mk", item["id"], digest, [output, unknown_output]):
            count += 1
            continue
        ir = parse_file(root, item["path"])
        atomic_write_json(output, ir)
        atomic_write_json(unknown_output, {"schema_version": 1, "source_file": item["path"], "items": ir["unknown"]})
        manifest.mark("parse_mk", item["id"], "done", digest, [output, unknown_output])
        count += 1
        unknown_count += len(ir["unknown"])
    print(f"parse_mk: parsed {count} file(s), {unknown_count} unknown item(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

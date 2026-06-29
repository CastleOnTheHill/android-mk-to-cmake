#!/usr/bin/env python3
from __future__ import annotations

import argparse
import pathlib
from typing import Any

from common import (
    Manifest,
    atomic_write_json,
    atomic_write_text,
    generated_rel_for_source,
    input_hash,
    load_json,
    rel,
    resolve_root,
    ensure_state,
    sha256_file,
)
from ai_unknown import accepted_fragments_by_source


SOURCE_VARS = {"LOCAL_SRC_FILES", "LOCAL_GENERATED_SOURCES"}
INCLUDE_VARS = {"LOCAL_C_INCLUDES", "LOCAL_EXPORT_C_INCLUDE_DIRS"}
FLAG_VARS = {"LOCAL_CFLAGS", "LOCAL_CPPFLAGS", "LOCAL_CONLYFLAGS"}
LINK_LIB_VARS = {"LOCAL_SHARED_LIBRARIES", "LOCAL_STATIC_LIBRARIES", "LOCAL_WHOLE_STATIC_LIBRARIES"}
LINK_OPTION_VARS = {"LOCAL_LDFLAGS"}
META_VARS = {"LOCAL_MODULE", "LOCAL_LAYER"}


def indent(level: int) -> str:
    return "    " * level


def cmake_quote(path: str, path_prefix: str) -> str:
    if path.startswith("${") or path.startswith("$<"):
        return path
    if "$(" in path:
        return f"# unresolved: {path}"
    return f"{path_prefix}/{path}"


def path_prefix_for(output_rel: pathlib.Path) -> str:
    return "${CMAKE_CURRENT_LIST_DIR}" if output_rel.name != "CMakeLists.txt" else "${CMAKE_CURRENT_SOURCE_DIR}"


def normalize_path_value(value: str) -> str:
    return value.replace("$(LOCAL_PATH)/", "").replace("${LOCAL_PATH}/", "")


def classify_flags(values: list[str]) -> tuple[list[str], list[str], list[str], list[str]]:
    definitions: list[str] = []
    compile_options: list[str] = []
    include_dirs: list[str] = []
    link_options: list[str] = []
    for value in values:
        if value.startswith("-D") and len(value) > 2:
            definitions.append(value[2:])
        elif value.startswith("-I") and len(value) > 2:
            include_dirs.append(value[2:])
        elif value.startswith("-Wl,") or value.startswith("-L"):
            link_options.append(value)
        elif value:
            compile_options.append(value)
    return definitions, compile_options, include_dirs, link_options


def block_command(command: str, target: str, scope: str, values: list[str], level: int) -> list[str]:
    if not values:
        return []
    lines = [f"{indent(level)}{command}({target}", f"{indent(level + 1)}{scope}"]
    for value in values:
        lines.append(f"{indent(level + 2)}{value}")
    lines.append(f"{indent(level)})")
    lines.append("")
    return lines


def source_values(values: list[str], prefix: str) -> list[str]:
    rows = []
    for value in values:
        normalized = normalize_path_value(value)
        rows.append(cmake_quote(normalized, prefix))
    return rows


def include_values(values: list[str], prefix: str) -> list[str]:
    return [cmake_quote(normalize_path_value(value), prefix) for value in values]


def lib_values(values: list[str], whole_archive: bool = False) -> list[str]:
    if not whole_archive:
        return values
    return [f"$<LINK_LIBRARY:WHOLE_ARCHIVE,{value}>" for value in values]


def add_source_comment(lines: list[str], source_file: str, event: dict[str, Any], level: int) -> None:
    raw = " ".join(str(event.get("raw", "")).split())
    if len(raw) > 120:
        raw = raw[:117] + "..."
    lines.append(f"{indent(level)}# from {source_file}:{event['line']} {raw}")


def emit_assignment(lines: list[str], source_file: str, target: str, event: dict[str, Any], prefix: str, level: int) -> None:
    variable = event.get("variable")
    values = event.get("values", [])
    if variable in META_VARS:
        return
    add_source_comment(lines, source_file, event, level)
    if variable in SOURCE_VARS:
        lines.extend(block_command("target_sources", target, "PRIVATE", source_values(values, prefix), level))
        return
    if variable == "LOCAL_C_INCLUDES":
        lines.extend(block_command("target_include_directories", target, "PRIVATE", include_values(values, prefix), level))
        return
    if variable == "LOCAL_EXPORT_C_INCLUDE_DIRS":
        lines.extend(block_command("target_include_directories", target, "PUBLIC", include_values(values, prefix), level))
        return
    if variable in FLAG_VARS:
        definitions, compile_options, include_dirs, link_options = classify_flags(values)
        lines.extend(block_command("target_compile_definitions", target, "PRIVATE", definitions, level))
        lines.extend(block_command("target_compile_options", target, "PRIVATE", compile_options, level))
        lines.extend(block_command("target_include_directories", target, "PRIVATE", include_values(include_dirs, prefix), level))
        lines.extend(block_command("target_link_options", target, "PRIVATE", link_options, level))
        return
    if variable in {"LOCAL_SHARED_LIBRARIES", "LOCAL_STATIC_LIBRARIES"}:
        lines.extend(block_command("target_link_libraries", target, "PRIVATE", lib_values(values), level))
        return
    if variable == "LOCAL_WHOLE_STATIC_LIBRARIES":
        lines.extend(block_command("target_link_libraries", target, "PRIVATE", lib_values(values, whole_archive=True), level))
        return
    if variable in LINK_OPTION_VARS:
        lines.extend(block_command("target_link_options", target, "PRIVATE", values, level))
        return
    lines.append(f"{indent(level)}# unresolved variable: {variable} {' '.join(values)}")
    lines.append("")


def emit_include(lines: list[str], source_file: str, event: dict[str, Any], output_rel: pathlib.Path, level: int) -> None:
    expr = event.get("expr", "")
    if not expr:
        return
    add_source_comment(lines, source_file, event, level)
    optional = " OPTIONAL" if event.get("optional") else ""
    cmake_expr = expr.replace(".mk", ".cmake")
    cmake_expr = cmake_expr.replace("$(LOCAL_PATH)/", "")
    if "$(" in cmake_expr:
        lines.append(f"{indent(level)}# unresolved include expression: {expr}")
    else:
        lines.append(f'{indent(level)}include("{cmake_expr}"{optional})')
    lines.append("")


def emit_target(ir: dict[str, Any], target: dict[str, Any], output_rel: pathlib.Path) -> str:
    lines: list[str] = []
    source_file = ir["source_file"]
    prefix = path_prefix_for(output_rel)
    name = target["cmake_target"]
    kind = target["cmake_kind"]
    build_rule_line = target["source_range"]["end_line"]
    lines.append(f"# target from {source_file}:{target['source_range']['start_line']}-{target['source_range']['end_line']}")
    if kind == "shared_library":
        lines.append(f"# from {source_file}:{build_rule_line} include $({target['build_rule']})")
        lines.append(f"add_library({name} SHARED)")
    elif kind == "static_library":
        lines.append(f"# from {source_file}:{build_rule_line} include $({target['build_rule']})")
        lines.append(f"add_library({name} STATIC)")
    elif kind == "executable":
        lines.append(f"# from {source_file}:{build_rule_line} include $({target['build_rule']})")
        lines.append(f"add_executable({name})")
    elif kind == "object_library":
        lines.append(f"# from {source_file}:{build_rule_line} include $({target['build_rule']}) with LOCAL_LAYER")
        lines.append(f"add_library({name} OBJECT)")
    else:
        lines.append(f"# unresolved target kind for {target.get('module', '')}")
    lines.append("")
    level = 0
    for event in target["events"]:
        event_kind = event.get("kind")
        if event_kind == "if":
            add_source_comment(lines, source_file, event, level)
            lines.append(f"{indent(level)}if({event.get('cmake_condition', event.get('expr', ''))})")
            level += 1
            continue
        if event_kind == "else":
            level = max(level - 1, 0)
            add_source_comment(lines, source_file, event, level)
            lines.append(f"{indent(level)}else()")
            level += 1
            continue
        if event_kind == "endif":
            level = max(level - 1, 0)
            add_source_comment(lines, source_file, event, level)
            lines.append(f"{indent(level)}endif()")
            lines.append("")
            continue
        if event_kind in {"assign", "append"}:
            emit_assignment(lines, source_file, name, event, prefix, level)
    if kind == "object_library" and target.get("local_layer"):
        lines.append(f"# from {source_file}:{build_rule_line} LOCAL_LAYER direct link")
        lines.extend(block_command("target_link_libraries", target["local_layer"], "PRIVATE", [name], 0))
    return "\n".join(lines).rstrip() + "\n"


def emit_connector(ir: dict[str, Any], output_rel: pathlib.Path) -> str:
    lines = [f"# connector from {ir['source_file']}", ""]
    level = 0
    for event in ir["events"]:
        kind = event.get("kind")
        if kind == "if":
            add_source_comment(lines, ir["source_file"], event, level)
            lines.append(f"{indent(level)}if({event.get('cmake_condition', event.get('expr', ''))})")
            level += 1
        elif kind == "else":
            level = max(level - 1, 0)
            add_source_comment(lines, ir["source_file"], event, level)
            lines.append(f"{indent(level)}else()")
            level += 1
        elif kind == "endif":
            level = max(level - 1, 0)
            add_source_comment(lines, ir["source_file"], event, level)
            lines.append(f"{indent(level)}endif()")
            lines.append("")
        elif kind == "include":
            emit_include(lines, ir["source_file"], event, output_rel, level)
    return "\n".join(lines).rstrip() + "\n"


def emit_ai_fragments(fragments: list[dict[str, Any]]) -> str:
    if not fragments:
        return ""
    lines = ["# AI fallback fragments for unresolved Make statements.", "# Review before promoting generated CMake.", ""]
    for fragment in fragments:
        lines.append(f"# ai task {fragment.get('task_id', '')} line {fragment.get('line', '')} confidence {fragment.get('confidence', '')}")
        for risk in fragment.get("risks", []):
            lines.append(f"# ai risk: {risk}")
        lines.append(fragment.get("cmake_fragment", "").rstrip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def convert_ir(ir: dict[str, Any], output_rel: pathlib.Path, ai_fragments: list[dict[str, Any]] | None = None) -> str:
    parts: list[str] = [
        "# Generated by android-mk-to-cmake.",
        "# Keep source comments when reviewing migration correctness.",
        "",
    ]
    if ir.get("targets"):
        for target in ir["targets"]:
            parts.append(emit_target(ir, target, output_rel))
    else:
        parts.append(emit_connector(ir, output_rel))
    if ai_fragments:
        parts.append(emit_ai_fragments(ai_fragments))
    return "\n".join(part.rstrip() for part in parts if part is not None).rstrip() + "\n"


def convert_ir_stage(
    root: str | pathlib.Path = ".",
    state_dir: str = "state",
    force: bool = False,
) -> dict[str, Any]:
    root = resolve_root(root)
    state = ensure_state(root, state_dir)
    manifest = Manifest(root, state)
    ir_dir = state / "files"
    output_root = state / "generated"
    converted: list[dict[str, Any]] = []
    ai_by_source = accepted_fragments_by_source(state)

    for ir_path in sorted(ir_dir.glob("*.ir.json")):
        ir = load_json(ir_path)
        source_rel = ir["source_file"]
        out_rel = generated_rel_for_source(source_rel)
        output = output_root / out_rel
        fragments = ai_by_source.get(source_rel, [])
        digest = input_hash(
            [
                source_rel,
                ir.get("source_sha256", ""),
                sha256_file(ir_path),
                str(len(fragments)),
                *[f"{item.get('task_id', '')}:{item.get('cmake_fragment', '')}" for item in fragments],
            ]
        )
        if not force and manifest.done("convert_ir", ir_path.stem, digest, [output]):
            converted.append({"source": source_rel, "output": rel(output, root), "reused": True})
            continue
        cmake = convert_ir(ir, out_rel, fragments)
        atomic_write_text(output, cmake)
        manifest.mark("convert_ir", ir_path.stem, "done", digest, [output])
        converted.append({"source": source_rel, "output": rel(output, root), "reused": False, "ai_fragments": len(fragments)})

    summary = state / "generated_manifest.json"
    atomic_write_json(summary, {"schema_version": 1, "files": converted})
    return {
        "stage": "convert_ir",
        "status": "done",
        "files": len(converted),
        "reused_files": sum(1 for item in converted if item.get("reused")),
        "ai_fragments": sum(int(item.get("ai_fragments", 0)) for item in converted),
        "output": rel(summary, root),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert parsed mk IR files to CMake files under state/generated.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--state-dir", default="state")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    result = convert_ir_stage(args.root, args.state_dir, args.force)
    print(f"convert_ir: generated {result['files']} CMake file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

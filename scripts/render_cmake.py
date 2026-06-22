#!/usr/bin/env python3
from __future__ import annotations

import argparse
from typing import Any

from common import atomic_write_text, ensure_state, load_json, rel, resolve_root


def table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    header = rows[0]
    body = rows[1:]
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)


def load_irs(state) -> list[dict[str, Any]]:
    return [load_json(path) for path in sorted((state / "files").glob("*.ir.json"))]


def render_report(state, root) -> str:
    products = load_json(state / "products.json", {"products": []})
    mk_files = load_json(state / "mk_files.json", {"files": []})
    graph = load_json(state / "include_graph.json", {"edges": [], "unresolved": []})
    generated = load_json(state / "generated_manifest.json", {"files": []})
    irs = load_irs(state)

    target_rows = [["Source", "Module", "CMake target", "Kind", "Layer"]]
    layer_rows = [["LOCAL_MODULE", "CMake object target", "LOCAL_LAYER", "处理方式"]]
    file_type_rows = [["File", "Type", "Targets", "Unknowns"]]
    for ir in irs:
        file_type_rows.append([ir["source_file"], ir.get("file_type", ""), str(len(ir.get("targets", []))), str(len(ir.get("unknown", [])))])
        for target in ir.get("targets", []):
            target_rows.append(
                [
                    ir["source_file"],
                    target.get("module", ""),
                    target.get("cmake_target", ""),
                    target.get("cmake_kind", ""),
                    target.get("local_layer", ""),
                ]
            )
            if target.get("cmake_kind") == "object_library":
                layer_rows.append(
                    [
                        target.get("module", ""),
                        target.get("cmake_target", ""),
                        target.get("local_layer", ""),
                        "OBJECT library + direct target_link_libraries",
                    ]
                )

    include_rows = [["From", "To / Expr", "Optional", "Resolved", "Line"]]
    for edge in graph.get("edges", []):
        if edge.get("kind") == "special":
            continue
        include_rows.append(
            [
                edge.get("from", ""),
                edge.get("to") or edge.get("expr", ""),
                "yes" if edge.get("optional") else "no",
                "yes" if edge.get("resolved") else "no",
                str(edge.get("line", "")),
            ]
        )

    generated_rows = [["Source", "Generated"]]
    for item in generated.get("files", []):
        generated_rows.append([item.get("source", ""), item.get("output", "")])

    unresolved_lines = []
    for ir in irs:
        for item in ir.get("unknown", []):
            unresolved_lines.append(f"- {ir['source_file']}:{item.get('line')} {item.get('reason')}")
    for edge in graph.get("unresolved", []):
        unresolved_lines.append(f"- {edge.get('from')}:{edge.get('line')} unresolved include `{edge.get('expr')}`")

    return "\n".join(
        [
            "# Android MK to CMake Migration Report",
            "",
            "## 输入文件",
            "",
            table([["Path", "Kind", "Lines"], *[[item["path"], item["kind_guess"], str(item["line_count"])] for item in mk_files.get("files", [])]]),
            "",
            "## 产品配置",
            "",
            table([["Product", "Path", "Symbols"], *[[p["product"], p["path"], str(len(p.get("symbols", {})))] for p in products.get("products", [])]]),
            "",
            "## 文件类型判断",
            "",
            table(file_type_rows),
            "",
            "## Include 图",
            "",
            table(include_rows),
            "",
            "## Target 列表",
            "",
            table(target_rows),
            "",
            "## LOCAL_LAYER 静态库",
            "",
            table(layer_rows) if len(layer_rows) > 1 else "_None_",
            "",
            "## 生成文件",
            "",
            table(generated_rows),
            "",
            "## 未解析项",
            "",
            "\n".join(unresolved_lines) if unresolved_lines else "_None_",
            "",
            "## CMake 语法检查结果",
            "",
            "_Run `check_cmake.py` to populate check results._",
            "",
            "## 需要人工确认的点",
            "",
            "- Review generated source-line comments before committing.",
            "- Review unresolved items above.",
        ]
    ) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Render migration report from generated state.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--state-dir", default="state")
    args = parser.parse_args()
    root = resolve_root(args.root)
    state = ensure_state(root, args.state_dir)
    output = state / "report.md"
    atomic_write_text(output, render_report(state, root))
    print(f"render_cmake: wrote {rel(output, root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

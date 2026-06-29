#!/usr/bin/env python3
from __future__ import annotations

import argparse
import pathlib
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


def load_model_results(state) -> list[dict[str, Any]]:
    return [load_json(path) for path in sorted((state / "model_results").glob("*.model.json"))]


def render_report(state, root) -> str:
    products = load_json(state / "products.json", {"products": []})
    mk_files = load_json(state / "mk_files.json", {"files": []})
    graph = load_json(state / "include_graph.json", {"edges": [], "unresolved": []})
    generated = load_json(state / "generated_manifest.json", {"files": []})
    graph_run = load_json(state / "graph_run.json", {"stage_results": []})
    irs = load_irs(state)
    model_results = load_model_results(state)

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

    graph_rows = [["Stage", "Status", "Reused", "Summary"]]
    for item in graph_run.get("stage_results", []):
        summary = ", ".join(
            f"{key}={value}"
            for key, value in item.items()
            if key not in {"stage", "status", "reused", "output"} and isinstance(value, (str, int, bool))
        )
        graph_rows.append([item.get("stage", ""), item.get("status", ""), str(item.get("reused", "")), summary])

    ai_rows = [["Source", "Tasks", "Converted", "Failed", "Skipped", "Cache hits"]]
    for item in model_results:
        tasks = item.get("tasks", [])
        ai_rows.append(
            [
                item.get("source_file", ""),
                str(len(tasks)),
                str(sum(1 for task in tasks if task.get("result", {}).get("status") == "converted")),
                str(sum(1 for task in tasks if task.get("result", {}).get("status") == "failed")),
                str(sum(1 for task in tasks if task.get("result", {}).get("status") == "skipped")),
                str(sum(1 for task in tasks if task.get("cache_hit"))),
            ]
        )

    unresolved_lines = []
    for ir in irs:
        for item in ir.get("unknown", []):
            unresolved_lines.append(f"- {ir['source_file']}:{item.get('line')} {item.get('reason')}")
    for edge in graph.get("unresolved", []):
        unresolved_lines.append(f"- {edge.get('from')}:{edge.get('line')} unresolved include `{edge.get('expr')}`")
    for result in model_results:
        for task in result.get("tasks", []):
            status = task.get("result", {}).get("status")
            if status == "converted":
                continue
            risks = "; ".join(task.get("result", {}).get("risks", []))
            unresolved_lines.append(f"- {result.get('source_file')}:{task.get('line')} ai {status}: {risks}")

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
            "## LangGraph 执行摘要",
            "",
            table(graph_rows) if len(graph_rows) > 1 else "_Not run through LangGraph._",
            "",
            "## AI fallback 摘要",
            "",
            table(ai_rows) if len(ai_rows) > 1 else "_No AI fallback result files._",
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


def render_cmake_stage(root: str | pathlib.Path = ".", state_dir: str = "state") -> dict[str, Any]:
    root = resolve_root(root)
    state = ensure_state(root, state_dir)
    output = state / "report.md"
    atomic_write_text(output, render_report(state, root))
    return {"stage": "render_cmake", "status": "done", "output": rel(output, root)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Render migration report from generated state.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--state-dir", default="state")
    args = parser.parse_args()
    result = render_cmake_stage(args.root, args.state_dir)
    print(f"render_cmake: wrote {result['output']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

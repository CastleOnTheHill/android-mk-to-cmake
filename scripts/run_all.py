#!/usr/bin/env python3
from __future__ import annotations

import argparse
import pathlib
import sys
from typing import Any

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from ai_unknown import DEFAULT_OPENCODE_AGENT, DEFAULT_OPENCODE_MODEL, ai_unknown_stage
from check_cmake import check_cmake_stage
from common import atomic_write_json, ensure_state, resolve_root, stable_id
from convert_ir import convert_ir_stage
from parse_include_graph import parse_include_graph_stage
from parse_kconfig import parse_kconfig_stage
from parse_mk import parse_mk_stage
from render_cmake import render_cmake_stage
from scan_mk import scan_mk_stage


def initial_state(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "root": args.root,
        "config_dir": args.config_dir,
        "scan_dir": args.scan_dir,
        "state_dir": args.state_dir,
        "force": args.force,
        "skip_check": args.skip_check,
        "ai_provider": args.ai_provider,
        "opencode_command": args.opencode_command,
        "opencode_model": args.opencode_model,
        "opencode_agent": args.opencode_agent,
        "stage_results": [],
        "errors": [],
    }


def write_graph_run(root_value: str, state_dir_value: str, results: list[dict[str, Any]], errors: list[str] | None = None) -> None:
    root = resolve_root(root_value)
    state = ensure_state(root, state_dir_value)
    atomic_write_json(
        state / "graph_run.json",
        {
            "schema_version": 1,
            "thread_id": stable_id(f"{root}:{state_dir_value}"),
            "stage_results": results,
            "errors": errors or [],
        },
    )


def run_with_langgraph(args: argparse.Namespace) -> dict[str, Any] | None:
    try:
        from graph_workflow import build_graph
    except Exception as exc:
        if args.require_langgraph:
            raise
        print(f"run_all: LangGraph unavailable, using stage-function fallback: {exc}", file=sys.stderr)
        return None
    graph = build_graph()
    state = initial_state(args)
    config = {"configurable": {"thread_id": stable_id(f"{resolve_root(args.root)}:{args.state_dir}")}}
    result = graph.invoke(state, config=config)
    for stage in result.get("stage_results", []):
        if stage.get("stage") == "check_cmake" and stage.get("status") == "fail":
            result["returncode"] = int(stage.get("returncode", 1))
            break
    return result


def run_without_langgraph(args: argparse.Namespace) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    stages = [
        lambda: parse_kconfig_stage(args.root, args.config_dir, args.state_dir, args.force),
        lambda: scan_mk_stage(args.root, args.scan_dir, args.state_dir, force=args.force),
        lambda: parse_include_graph_stage(args.root, args.state_dir, args.force),
        lambda: parse_mk_stage(args.root, args.state_dir, force=args.force),
        lambda: ai_unknown_stage(
            root=args.root,
            state_dir=args.state_dir,
            force=args.force,
            provider=args.ai_provider,
            opencode_command=args.opencode_command,
            model=args.opencode_model,
            agent=args.opencode_agent,
        ),
        lambda: convert_ir_stage(args.root, args.state_dir, args.force),
    ]
    for stage in stages:
        result = stage()
        results.append(result)
        print(f"run_all: {result.get('stage')} {result.get('status')}")
    write_graph_run(args.root, args.state_dir, results, errors)
    render_result = render_cmake_stage(args.root, args.state_dir)
    results.append(render_result)
    print(f"run_all: {render_result.get('stage')} {render_result.get('status')}")
    if not args.skip_check:
        check_result = check_cmake_stage(args.root, args.state_dir)
        results.append(check_result)
        print(f"run_all: {check_result.get('stage')} {check_result.get('status')}")
        if check_result.get("status") == "fail":
            write_graph_run(args.root, args.state_dir, results, errors)
            return {"stage_results": results, "errors": errors, "returncode": int(check_result.get("returncode", 1))}
    write_graph_run(args.root, args.state_dir, results, errors)
    return {"stage_results": results, "errors": errors, "returncode": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the LangGraph Android.mk/Makefile to CMake conversion workflow.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--config-dir", default="config")
    parser.add_argument("--scan-dir", default=".")
    parser.add_argument("--state-dir", default="state")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-check", action="store_true")
    parser.add_argument("--ai-provider", choices=["opencode", "skipped"], default="opencode")
    parser.add_argument("--opencode-command", default="opencode")
    parser.add_argument("--opencode-model", default=DEFAULT_OPENCODE_MODEL)
    parser.add_argument("--opencode-agent", default=DEFAULT_OPENCODE_AGENT)
    parser.add_argument("--require-langgraph", action="store_true", help="Fail instead of using the compatibility fallback when LangGraph is unavailable.")
    args = parser.parse_args()

    result = run_with_langgraph(args)
    if result is None:
        result = run_without_langgraph(args)
    code = int(result.get("returncode", 0))
    if code == 0:
        print("run_all: done")
    else:
        print(f"run_all: failed with exit code {code}", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())

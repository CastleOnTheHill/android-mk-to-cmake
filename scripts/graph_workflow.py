#!/usr/bin/env python3
from __future__ import annotations

import operator
import pathlib
import sys
from typing import Annotated, Any, TypedDict

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

try:
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph
except ImportError as exc:  # pragma: no cover - exercised only without optional dependency
    raise RuntimeError("LangGraph workflow requires Python 3.11+ and the langgraph dependencies from pyproject.toml") from exc


class Mk2CMakeState(TypedDict, total=False):
    root: str
    config_dir: str
    scan_dir: str
    state_dir: str
    force: bool
    skip_check: bool
    ai_provider: str
    opencode_command: str
    opencode_model: str
    opencode_agent: str
    stage_results: Annotated[list[dict[str, Any]], operator.add]
    errors: Annotated[list[str], operator.add]


def context(state: Mk2CMakeState) -> dict[str, Any]:
    return {
        "root": state.get("root", "."),
        "config_dir": state.get("config_dir", "config"),
        "scan_dir": state.get("scan_dir", "."),
        "state_dir": state.get("state_dir", "state"),
        "force": bool(state.get("force", False)),
        "skip_check": bool(state.get("skip_check", False)),
        "ai_provider": state.get("ai_provider", "opencode"),
        "opencode_command": state.get("opencode_command", "opencode"),
        "opencode_model": state.get("opencode_model", DEFAULT_OPENCODE_MODEL),
        "opencode_agent": state.get("opencode_agent", DEFAULT_OPENCODE_AGENT),
    }


def node_parse_kconfig(state: Mk2CMakeState) -> Mk2CMakeState:
    ctx = context(state)
    result = parse_kconfig_stage(ctx["root"], ctx["config_dir"], ctx["state_dir"], ctx["force"])
    return {"stage_results": [result]}


def node_scan_mk(state: Mk2CMakeState) -> Mk2CMakeState:
    ctx = context(state)
    result = scan_mk_stage(ctx["root"], ctx["scan_dir"], ctx["state_dir"], force=ctx["force"])
    return {"stage_results": [result]}


def node_parse_include_graph(state: Mk2CMakeState) -> Mk2CMakeState:
    ctx = context(state)
    result = parse_include_graph_stage(ctx["root"], ctx["state_dir"], ctx["force"])
    return {"stage_results": [result]}


def node_parse_mk(state: Mk2CMakeState) -> Mk2CMakeState:
    ctx = context(state)
    result = parse_mk_stage(ctx["root"], ctx["state_dir"], force=ctx["force"])
    return {"stage_results": [result]}


def node_ai_unknown(state: Mk2CMakeState) -> Mk2CMakeState:
    ctx = context(state)
    result = ai_unknown_stage(
        root=ctx["root"],
        state_dir=ctx["state_dir"],
        force=ctx["force"],
        provider=ctx["ai_provider"],
        opencode_command=ctx["opencode_command"],
        model=ctx["opencode_model"],
        agent=ctx["opencode_agent"],
    )
    return {"stage_results": [result]}


def node_convert_ir(state: Mk2CMakeState) -> Mk2CMakeState:
    ctx = context(state)
    result = convert_ir_stage(ctx["root"], ctx["state_dir"], ctx["force"])
    return {"stage_results": [result]}


def node_record_graph_run(state: Mk2CMakeState) -> Mk2CMakeState:
    ctx = context(state)
    root = resolve_root(ctx["root"])
    state_path = ensure_state(root, ctx["state_dir"])
    output = state_path / "graph_run.json"
    atomic_write_json(
        output,
        {
            "schema_version": 1,
            "thread_id": stable_id(f"{root}:{ctx['state_dir']}"),
            "stage_results": state.get("stage_results", []),
            "errors": state.get("errors", []),
        },
    )
    return {"stage_results": [{"stage": "record_graph_run", "status": "done", "output": str(output)}]}


def node_render_report(state: Mk2CMakeState) -> Mk2CMakeState:
    ctx = context(state)
    result = render_cmake_stage(ctx["root"], ctx["state_dir"])
    return {"stage_results": [result]}


def node_check_cmake(state: Mk2CMakeState) -> Mk2CMakeState:
    ctx = context(state)
    result = check_cmake_stage(ctx["root"], ctx["state_dir"])
    return {"stage_results": [result]}


def route_after_report(state: Mk2CMakeState) -> str:
    return "end" if state.get("skip_check") else "check_cmake"


def build_graph():
    graph = StateGraph(Mk2CMakeState)
    graph.add_node("parse_kconfig", node_parse_kconfig)
    graph.add_node("scan_mk", node_scan_mk)
    graph.add_node("parse_include_graph", node_parse_include_graph)
    graph.add_node("parse_mk", node_parse_mk)
    graph.add_node("ai_unknown", node_ai_unknown)
    graph.add_node("convert_ir", node_convert_ir)
    graph.add_node("record_graph_run", node_record_graph_run)
    graph.add_node("render_report", node_render_report)
    graph.add_node("check_cmake", node_check_cmake)
    graph.add_edge(START, "parse_kconfig")
    graph.add_edge("parse_kconfig", "scan_mk")
    graph.add_edge("scan_mk", "parse_include_graph")
    graph.add_edge("parse_include_graph", "parse_mk")
    graph.add_edge("parse_mk", "ai_unknown")
    graph.add_edge("ai_unknown", "convert_ir")
    graph.add_edge("convert_ir", "record_graph_run")
    graph.add_edge("record_graph_run", "render_report")
    graph.add_conditional_edges("render_report", route_after_report, {"check_cmake": "check_cmake", "end": END})
    graph.add_edge("check_cmake", END)
    return graph.compile(checkpointer=MemorySaver())


mk2cmake_graph = build_graph()

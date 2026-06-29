#!/usr/bin/env python3
from __future__ import annotations

import json
import pathlib
import shlex
import shutil
import subprocess
from typing import Any, Callable

from common import (
    Manifest,
    atomic_write_json,
    ensure_state,
    input_hash,
    load_json,
    resolve_root,
    sha256_file,
    sha256_text,
    stable_id,
)


PROMPT_VERSION = "mk2cmake-unknown-v1"
DEFAULT_OPENCODE_MODEL = "minimax/minimax2.7"
DEFAULT_OPENCODE_AGENT = "mk2cmake-unknown"


Runner = Callable[[dict[str, Any], str, pathlib.Path], dict[str, Any]]


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def normalize_cmake_fragment(value: str) -> str:
    lines = [line.rstrip() for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    normalized: list[str] = []
    previous_blank = False
    for line in lines:
        blank = not line.strip()
        if blank and previous_blank:
            continue
        normalized.append(line)
        previous_blank = blank
    while normalized and not normalized[0].strip():
        normalized.pop(0)
    while normalized and not normalized[-1].strip():
        normalized.pop()
    return "\n".join(normalized) + ("\n" if normalized else "")


def build_unknown_task(source_file: str, item: dict[str, Any]) -> dict[str, Any]:
    body = {
        "schema_version": 1,
        "prompt_version": PROMPT_VERSION,
        "source_file": source_file,
        "line": item.get("line"),
        "end_line": item.get("end_line"),
        "reason": item.get("reason", ""),
        "raw": item.get("raw", ""),
        "text": item.get("text", ""),
        "variable": item.get("variable", ""),
        "value": item.get("value", ""),
        "condition_stack": item.get("condition_stack", []),
    }
    return {**body, "task_id": stable_id(canonical_json(body))}


def task_hash(task: dict[str, Any], provider: str, model: str, agent: str) -> str:
    return sha256_text(canonical_json({"task": task, "provider": provider, "model": model, "agent": agent}))


def build_prompt(task: dict[str, Any]) -> str:
    return "\n".join(
        [
            "Convert this unresolved Make/Android.mk statement into a structured CMake fallback.",
            "Return one JSON object only. Do not return Markdown.",
            "",
            "Allowed schema:",
            canonical_json(
                {
                    "schema_version": 1,
                    "status": "converted|skipped|failed",
                    "confidence": "high|medium|low",
                    "cmake_fragment": "CMake text, empty unless status is converted",
                    "ir_events": [],
                    "risks": [],
                }
            ),
            "",
            "Rules:",
            "- Preserve the source condition semantics; do not simplify if/else logic.",
            "- Do not invent source files, targets, variables, or library names.",
            "- Use ${CMAKE_CURRENT_SOURCE_DIR} for paths belonging to a generated CMakeLists.txt.",
            "- If the statement cannot be converted safely, return status failed or skipped with risks.",
            "",
            "Task JSON:",
            canonical_json(task),
        ]
    )


def _json_objects_in_text(text: str) -> list[Any]:
    decoder = json.JSONDecoder()
    values: list[Any] = []
    index = 0
    while index < len(text):
        start = text.find("{", index)
        if start < 0:
            break
        try:
            value, end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            index = start + 1
            continue
        values.append(value)
        index = start + end
    return values


def extract_result_json(stdout: str) -> dict[str, Any]:
    candidates: list[Any] = []
    for raw in [stdout, *stdout.splitlines()]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            candidates.append(json.loads(raw))
        except json.JSONDecodeError:
            candidates.extend(_json_objects_in_text(raw))

    text_fields = ("content", "message", "text", "result", "output")
    for candidate in reversed(candidates):
        if isinstance(candidate, dict) and "status" in candidate:
            return candidate
        if isinstance(candidate, dict):
            for field in text_fields:
                value = candidate.get(field)
                if isinstance(value, str):
                    nested = _json_objects_in_text(value)
                    for item in reversed(nested):
                        if isinstance(item, dict) and "status" in item:
                            return item
    raise ValueError("opencode output did not contain a result JSON object with status")


def validate_and_normalize_result(value: dict[str, Any]) -> dict[str, Any]:
    status = value.get("status")
    if status not in {"converted", "skipped", "failed"}:
        raise ValueError("AI result status must be converted, skipped, or failed")
    confidence = value.get("confidence", "low")
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"
    fragment = value.get("cmake_fragment", "")
    if not isinstance(fragment, str):
        raise ValueError("AI result cmake_fragment must be a string")
    if "```" in fragment:
        raise ValueError("AI result cmake_fragment must not contain Markdown fences")
    ir_events = value.get("ir_events", [])
    if not isinstance(ir_events, list):
        raise ValueError("AI result ir_events must be a list")
    risks = value.get("risks", [])
    if isinstance(risks, str):
        risks = [risks]
    if not isinstance(risks, list):
        raise ValueError("AI result risks must be a list")
    normalized = {
        "schema_version": 1,
        "status": status,
        "confidence": confidence,
        "cmake_fragment": normalize_cmake_fragment(fragment),
        "ir_events": ir_events,
        "risks": sorted(str(item).strip() for item in risks if str(item).strip()),
    }
    if normalized["status"] != "converted":
        normalized["cmake_fragment"] = ""
    return normalized


def run_opencode(
    task: dict[str, Any],
    root: pathlib.Path,
    command: str = "opencode",
    model: str = DEFAULT_OPENCODE_MODEL,
    agent: str = DEFAULT_OPENCODE_AGENT,
    timeout_sec: int = 300,
) -> dict[str, Any]:
    executable = shlex.split(command)
    if not executable:
        raise ValueError("opencode command is empty")
    if shutil.which(executable[0]) is None and not pathlib.Path(executable[0]).exists():
        raise FileNotFoundError(f"opencode command not found: {executable[0]}")
    cmd = [
        *executable,
        "run",
        "--agent",
        agent,
        "--model",
        model,
        "--format",
        "json",
        "--dir",
        str(root),
        build_prompt(task),
    ]
    cp = subprocess.run(cmd, cwd=str(root), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=timeout_sec)
    if cp.returncode != 0:
        raise RuntimeError((cp.stderr or cp.stdout or f"opencode exited with {cp.returncode}").strip())
    return extract_result_json(cp.stdout)


def result_for_task(
    task: dict[str, Any],
    root: pathlib.Path,
    state: pathlib.Path,
    provider: str,
    model: str,
    agent: str,
    force: bool,
    runner: Runner | None = None,
    opencode_command: str = "opencode",
) -> dict[str, Any]:
    digest = task_hash(task, provider, model, agent)
    cache_path = state / "ai_cache" / f"{digest}.json"
    if not force and cache_path.exists():
        cached = load_json(cache_path)
        return {**cached, "cache_hit": True}
    if provider == "skipped":
        normalized = {"schema_version": 1, "status": "skipped", "confidence": "low", "cmake_fragment": "", "ir_events": [], "risks": ["ai_provider_skipped"]}
    else:
        try:
            raw = runner(task, build_prompt(task), root) if runner else run_opencode(task, root, opencode_command, model, agent)
            normalized = validate_and_normalize_result(raw)
        except Exception as exc:
            normalized = {
                "schema_version": 1,
                "status": "failed",
                "confidence": "low",
                "cmake_fragment": "",
                "ir_events": [],
                "risks": [str(exc)],
            }
    record = {
        "schema_version": 1,
        "task_id": task["task_id"],
        "input_hash": digest,
        "provider": provider,
        "model": model,
        "agent": agent,
        "result": normalized,
        "cache_hit": False,
    }
    atomic_write_json(cache_path, record)
    return record


def ai_unknown_stage(
    root: str | pathlib.Path = ".",
    state_dir: str = "state",
    force: bool = False,
    provider: str = "opencode",
    opencode_command: str = "opencode",
    model: str = DEFAULT_OPENCODE_MODEL,
    agent: str = DEFAULT_OPENCODE_AGENT,
    runner: Runner | None = None,
) -> dict[str, Any]:
    root = resolve_root(root)
    state = ensure_state(root, state_dir)
    manifest = Manifest(root, state)
    output_dir = state / "model_results"
    output_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    task_count = 0
    converted = 0
    failed = 0
    skipped = 0
    cache_hits = 0

    for unknown_path in sorted((state / "unknown").glob("*.unknown.json")):
        task_file = load_json(unknown_path, {"source_file": "", "items": []})
        source_file = task_file.get("source_file", "")
        items = task_file.get("items", [])
        digest = input_hash([sha256_file(unknown_path), provider, model, agent, PROMPT_VERSION])
        output = output_dir / unknown_path.name.replace(".unknown.json", ".model.json")
        if not force and manifest.done("model_unknown", unknown_path.stem, digest, [output]):
            reused = load_json(output, {"tasks": []})
            tasks = reused.get("tasks", [])
            count += 1
            task_count += len(tasks)
            converted += sum(1 for task in tasks if task.get("result", {}).get("status") == "converted")
            failed += sum(1 for task in tasks if task.get("result", {}).get("status") == "failed")
            skipped += sum(1 for task in tasks if task.get("result", {}).get("status") == "skipped")
            cache_hits += len(tasks)
            continue

        records = []
        for item in items:
            task = build_unknown_task(source_file, item)
            record = result_for_task(task, root, state, provider, model, agent, force, runner, opencode_command)
            records.append({**record, "source_file": source_file, "line": item.get("line"), "reason": item.get("reason", "")})
            cache_hits += 1 if record.get("cache_hit") else 0
        result = {
            "schema_version": 2,
            "source_file": source_file,
            "status": "done" if records else "skipped",
            "reason": "" if records else "no_unknown_items",
            "provider": provider,
            "model": model,
            "agent": agent,
            "tasks": records,
            "items": items,
        }
        atomic_write_json(output, result)
        manifest.mark("model_unknown", unknown_path.stem, "done", digest, [output])
        count += 1
        task_count += len(records)
        converted += sum(1 for task in records if task.get("result", {}).get("status") == "converted")
        failed += sum(1 for task in records if task.get("result", {}).get("status") == "failed")
        skipped += sum(1 for task in records if task.get("result", {}).get("status") == "skipped")

    return {
        "stage": "model_unknown",
        "status": "done",
        "result_files": count,
        "tasks": task_count,
        "converted": converted,
        "failed": failed,
        "skipped": skipped,
        "cache_hits": cache_hits,
    }


def accepted_fragments_by_source(state: pathlib.Path) -> dict[str, list[dict[str, Any]]]:
    by_source: dict[str, list[dict[str, Any]]] = {}
    for model_path in sorted((state / "model_results").glob("*.model.json")):
        data = load_json(model_path, {"source_file": "", "tasks": []})
        source_file = data.get("source_file", "")
        for task in data.get("tasks", []):
            result = task.get("result", {})
            fragment = result.get("cmake_fragment", "")
            if result.get("status") != "converted" or not fragment:
                continue
            by_source.setdefault(source_file, []).append(
                {
                    "task_id": task.get("task_id", ""),
                    "line": task.get("line"),
                    "reason": task.get("reason", ""),
                    "confidence": result.get("confidence", "low"),
                    "cmake_fragment": fragment,
                    "risks": result.get("risks", []),
                }
            )
    return by_source

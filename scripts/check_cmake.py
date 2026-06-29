#!/usr/bin/env python3
from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess

from common import atomic_write_text, ensure_state, rel, resolve_root, resolve_under


def run(cmd: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(cwd), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def append_check_result(report: pathlib.Path, text: str) -> None:
    existing = report.read_text(encoding="utf-8", errors="replace") if report.exists() else "# Android MK to CMake Migration Report\n"
    marker = "## CMake 语法检查结果"
    if marker in existing:
        before = existing.split(marker)[0]
        after_marker = existing.split(marker, 1)[1]
        tail = ""
        if "\n## 需要人工确认的点" in after_marker:
            tail = "\n## 需要人工确认的点" + after_marker.split("\n## 需要人工确认的点", 1)[1]
        atomic_write_text(report, before + marker + "\n\n" + text.rstrip() + "\n" + tail)
    else:
        atomic_write_text(report, existing.rstrip() + "\n\n" + marker + "\n\n" + text.rstrip() + "\n")


def check_cmake_stage(
    root: str | pathlib.Path = ".",
    state_dir: str = "state",
    source_dir: str = "",
) -> dict[str, str | int]:
    root = resolve_root(root)
    state = ensure_state(root, state_dir)
    report = state / "report.md"
    cmake = shutil.which("cmake")
    if not cmake:
        append_check_result(report, "SKIPPED: `cmake` command not found.")
        return {"stage": "check_cmake", "status": "skipped", "reason": "cmake not found", "returncode": 0}

    source_path = resolve_under(root, source_dir) if source_dir else state / "generated"
    if not (source_path / "CMakeLists.txt").exists():
        append_check_result(report, f"SKIPPED: no root CMakeLists.txt found at `{rel(source_path, root)}`.")
        return {"stage": "check_cmake", "status": "skipped", "reason": "no root CMakeLists.txt", "returncode": 0}

    build_dir = state / "cmake-check"
    cp = run([cmake, "-S", str(source_path), "-B", str(build_dir)], root)
    log = (state / "logs" / "cmake-check.log")
    log.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(log, cp.stdout + "\n" + cp.stderr)
    if cp.returncode == 0:
        append_check_result(report, f"PASS: `cmake -S {rel(source_path, root)} -B {rel(build_dir, root)}` succeeded.")
        return {"stage": "check_cmake", "status": "pass", "returncode": 0, "log": rel(log, root)}
    append_check_result(
        report,
        "\n".join(
            [
                f"FAIL: `cmake -S {rel(source_path, root)} -B {rel(build_dir, root)}` failed.",
                "",
                f"Log: `{rel(log, root)}`",
                "",
                "If failure is caused by `target_link_libraries(<layer> ...)` before `<layer>` exists, keep the failure visible. This order issue is not fixed in v1.",
            ]
        ),
    )
    return {"stage": "check_cmake", "status": "fail", "returncode": cp.returncode, "log": rel(log, root)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a conservative CMake syntax/configuration check.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--state-dir", default="state")
    parser.add_argument("--source-dir", default="")
    args = parser.parse_args()

    result = check_cmake_stage(args.root, args.state_dir, args.source_dir)
    if result["status"] == "skipped":
        print(f"check_cmake: SKIPPED {result['reason']}")
        return 0
    if result["status"] == "pass":
        print("check_cmake: PASS")
        return 0
    print("check_cmake: FAIL")
    return int(result["returncode"])


if __name__ == "__main__":
    raise SystemExit(main())

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


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a conservative CMake syntax/configuration check.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--state-dir", default="state")
    parser.add_argument("--source-dir", default="")
    args = parser.parse_args()

    root = resolve_root(args.root)
    state = ensure_state(root, args.state_dir)
    report = state / "report.md"
    cmake = shutil.which("cmake")
    if not cmake:
        append_check_result(report, "SKIPPED: `cmake` command not found.")
        print("check_cmake: SKIPPED cmake not found")
        return 0

    source_dir = resolve_under(root, args.source_dir) if args.source_dir else state / "generated"
    if not (source_dir / "CMakeLists.txt").exists():
        append_check_result(report, f"SKIPPED: no root CMakeLists.txt found at `{rel(source_dir, root)}`.")
        print("check_cmake: SKIPPED no root CMakeLists.txt")
        return 0

    build_dir = state / "cmake-check"
    cp = run([cmake, "-S", str(source_dir), "-B", str(build_dir)], root)
    log = (state / "logs" / "cmake-check.log")
    log.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(log, cp.stdout + "\n" + cp.stderr)
    if cp.returncode == 0:
        append_check_result(report, f"PASS: `cmake -S {rel(source_dir, root)} -B {rel(build_dir, root)}` succeeded.")
        print("check_cmake: PASS")
        return 0
    append_check_result(
        report,
        "\n".join(
            [
                f"FAIL: `cmake -S {rel(source_dir, root)} -B {rel(build_dir, root)}` failed.",
                "",
                f"Log: `{rel(log, root)}`",
                "",
                "If failure is caused by `target_link_libraries(<layer> ...)` before `<layer>` exists, keep the failure visible. This order issue is not fixed in v1.",
            ]
        ),
    )
    print("check_cmake: FAIL")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

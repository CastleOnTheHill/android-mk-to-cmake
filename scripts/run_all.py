#!/usr/bin/env python3
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys


SCRIPT_DIR = pathlib.Path(__file__).resolve().parent


def run_step(name: str, args: list[str]) -> int:
    cmd = [sys.executable, "-B", str(SCRIPT_DIR / name), *args]
    print("+ " + " ".join(cmd), flush=True)
    cp = subprocess.run(cmd, text=True, check=False)
    return cp.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the resumable Android.mk/Makefile to CMake conversion pipeline.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--config-dir", default="config")
    parser.add_argument("--scan-dir", default=".")
    parser.add_argument("--state-dir", default="state")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-check", action="store_true")
    args = parser.parse_args()

    common = ["--root", args.root, "--state-dir", args.state_dir]
    force = ["--force"] if args.force else []
    steps = [
        ("parse_kconfig.py", [*common, "--config-dir", args.config_dir, *force]),
        ("scan_mk.py", [*common, "--scan-dir", args.scan_dir, *force]),
        ("parse_include_graph.py", [*common, *force]),
        ("parse_mk.py", [*common, *force]),
        ("ask_model_for_unknown.py", [*common, *force]),
        ("convert_ir.py", [*common, *force]),
        ("render_cmake.py", common),
    ]
    if not args.skip_check:
        steps.append(("check_cmake.py", common))

    for name, step_args in steps:
        code = run_step(name, step_args)
        if code != 0:
            print(f"run_all: stopped at {name} with exit code {code}", file=sys.stderr)
            return code
    print("run_all: done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

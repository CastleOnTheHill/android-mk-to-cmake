---
name: android-mk-to-cmake
description: Convert focused Automake Makefile.am / Makefile.inc inputs to CMake with a lightweight Python DAG executor and static dashboard.
---

# Android MK to CMake

Use this skill for the current lightweight implementation only.

## Workflow

Run the standard-library DAG executor:

```sh
python3 android-mk-to-cmake/lite_dag/run.py \
  --root /path/to/project \
  --state-dir /tmp/mk2cmake-state \
  --focus lib --focus src \
  --force
```

Open `state/dashboard.html` to inspect node status, generated CMake comparison, and configuration switch coverage.

## Rules

- Do not reintroduce LangChain or LangGraph for scheduling.
- Keep deterministic work in `lite_dag/run.py`.
- Keep generated state outside the source tree unless the user explicitly asks to promote it.
- Validate with libcurl when changing parser, conversion, comparison, or switch coverage behavior.
- Prefer explicit JSON artifacts over hidden process state.
- Preserve Makefile condition switches in `MakefileSwitches.cmake` so repeated conversions are stable and reviewable.

## Validation

```sh
python3 -B -m unittest discover -s android-mk-to-cmake/tests -v
python3 -m py_compile android-mk-to-cmake/lite_dag/run.py android-mk-to-cmake/tests/*.py
```

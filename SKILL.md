---
name: android-mk-to-cmake
description: Convert Android.mk, package.mk, and Makefile projects to CMake using LangGraph orchestration, script-first parsing, Kconfig .config awareness, include graph resolution, resumable state, and small cached model fallback only for unknown Makefile blocks.
---

# Android MK to CMake

Use this skill when converting complete `Android.mk`, `package.mk`, or `Makefile` inputs into CMake.

## Workflow

Run the LangGraph workflow first. Do not manually rewrite a long mk file directly.

```sh
python3 android-mk-to-cmake/scripts/run_all.py --root . --config-dir config
```

If the project does not use a `config/` directory, omit `--config-dir`.

For Studio monitoring, use Python 3.11+:

```sh
cd android-mk-to-cmake
pip install -e .
langgraph dev
```

## Rules

- Prefer script output over model output.
- Use model assistance only for files in `state/unknown/`.
- Do not use an agent as the scheduler. LangGraph owns stage scheduling and monitoring.
- AI fallback uses `opencode` by default and must return JSON. Cache and normalize AI output before merging.
- Keep generated CMake close to the original mk statement order for review.
- Preserve `ifeq` / `ifneq` / `ifdef` / `ifndef` / `else` / `endif` structure. Do not merge or reorder conditions.
- Resolve mk include relationships before conversion. Convert each mk file independently after the include graph exists.
- In `CMakeLists.txt`, relative source paths use `${CMAKE_CURRENT_SOURCE_DIR}`.
- In included `.cmake` fragments, relative source paths use `${CMAKE_CURRENT_LIST_DIR}`.
- Convert `include` to `include(...)`; convert `-include` and `sinclude` to `include(... OPTIONAL)`.
- If `LOCAL_LAYER` and `include $(BUILD_STATIC_LIBRARY)` occur in the same module, generate an `OBJECT` library and directly link it to the layer target. Do not generate delayed registration or target-order fallback logic.
- If CMake was not checked, report `SKIPPED`, not `PASS`.

## Resume

The pipeline is resumable. State is written under `state/` by default. Re-running `run_all.py` skips successful tasks whose input hashes did not change.

Use `--force` to rebuild all stages.

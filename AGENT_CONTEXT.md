# Agent Context: android-mk-to-cmake

## Goal

Build a script-first conversion system for complete `Android.mk`, `package.mk`, and ordinary `Makefile` inputs.

The system is designed for weak or constrained LLMs: scripts do as much deterministic parsing and conversion as possible, and model calls are reserved only for small unresolved blocks.

The main workflow is now orchestrated by LangGraph. Agents should not act as the task scheduler. LangGraph schedules deterministic script stages and the small AI fallback stage, records stage summaries, and can be inspected with LangGraph Studio.

This is not a simple snippet replacement tool. It aims to convert full mk-based build descriptions into reviewable CMake output.

## Design Principles

- Prefer scripts over model reasoning.
- Parse include relationships before converting files.
- Convert one mk file at a time after the include graph exists.
- Preserve original statement order as much as possible.
- Preserve `ifeq` / `ifneq` / `ifdef` / `ifndef` / `else` / `endif` structure. Do not merge, simplify, or reorder conditions.
- Make generated CMake easy to review by keeping `# from <file>:<line> <raw mk statement>` comments.
- Support long-running task recovery with content hashes and `state/manifest.json`.
- Use LangGraph for workflow scheduling and monitoring; keep deterministic work in Python stages.
- Treat Kconfig `.config` files as product configuration inputs.
- Do not use build logs in v1.
- Do not hide unresolved cases. Write unknown blocks and skipped or failed model fallback results explicitly.
- AI fallback must be hash-cached, JSON schema checked, and canonicalized before generated CMake uses it.

## Current Pipeline

Run:

```sh
python3 android-mk-to-cmake/scripts/run_all.py --root . --config-dir config
```

Use `--skip-check` when no generated root `CMakeLists.txt` exists yet.

Pipeline order:

1. `parse_kconfig.py`
   - scans one or more product `.config` files
   - parses `CONFIG_X=y`, `CONFIG_X=m`, string values, and `# CONFIG_X is not set`
   - writes `state/products.json`
2. `scan_mk.py`
   - finds `Android.mk`, `package.mk`, `*.mk`, and `Makefile`
   - writes `state/mk_files.json`
3. `parse_include_graph.py`
   - parses `include`, `-include`, and `sinclude`
   - writes `state/include_graph.json`
4. `parse_mk.py`
   - parses each mk file into order-preserving IR
   - writes `state/files/*.ir.json` and `state/unknown/*.unknown.json`
5. `ask_model_for_unknown.py`
   - processes only `state/unknown/*.unknown.json`
   - default backend is `opencode run --agent mk2cmake-unknown --model minimax/minimax2.7 --format json`
   - writes cache entries under `state/ai_cache/`
   - writes `state/model_results/*.model.json`
6. `convert_ir.py`
   - converts IR and accepted AI fallback fragments to CMake under `state/generated/`
7. `render_cmake.py`
   - writes `state/report.md`, including LangGraph and AI fallback summaries
8. `check_cmake.py`
   - runs a conservative CMake configure check when possible

LangGraph entry:

```sh
cd android-mk-to-cmake
python3.11 -m venv .venv
. .venv/bin/activate
pip install -e .
langgraph dev
```

The graph is configured in `langgraph.json` as `mk2cmake`.

## IR Meaning

IR means Intermediate Representation. It is the structured JSON form between Makefile syntax and CMake output.

The important choice is that IR is an event stream, not only a summarized target record. This preserves source order and line mapping.

Example mk:

```make
include $(CLEAR_VARS)
LOCAL_LAYER := example
LOCAL_MODULE := libexample_sub_module
LOCAL_SRC_FILES := src/a.cpp
ifeq ($(CONFIG_NET),y)
LOCAL_SRC_FILES += src/net.cpp
endif
include $(BUILD_STATIC_LIBRARY)
```

Representative IR shape:

```json
{
  "source_file": "device/example/Android.mk",
  "file_type": "target_definition",
  "events": [
    {"kind": "clear_vars", "line": 1},
    {"kind": "assign", "line": 2, "variable": "LOCAL_LAYER", "values": ["example"]},
    {"kind": "assign", "line": 3, "variable": "LOCAL_MODULE", "values": ["libexample_sub_module"]},
    {"kind": "assign", "line": 4, "variable": "LOCAL_SRC_FILES", "values": ["src/a.cpp"]},
    {"kind": "if", "line": 5, "cmake_condition": "CONFIG_NET"},
    {"kind": "append", "line": 6, "variable": "LOCAL_SRC_FILES", "values": ["src/net.cpp"]},
    {"kind": "endif", "line": 7},
    {"kind": "build_rule", "line": 8, "build_rule": "BUILD_STATIC_LIBRARY"}
  ]
}
```

The converter reads events in order and emits corresponding CMake blocks in order.

## Important Conversion Rules

- `include $(BUILD_SHARED_LIBRARY)` -> `add_library(<module> SHARED)`
- `include $(BUILD_STATIC_LIBRARY)` -> `add_library(<module> STATIC)` unless `LOCAL_LAYER` is set.
- `LOCAL_LAYER + BUILD_STATIC_LIBRARY` -> `add_library(<module> OBJECT)` plus direct `target_link_libraries(<layer> PRIVATE <module>)`.
- For object target names, strip a leading `lib` by default: `libexample_sub_module` -> `example_sub_module`.
- Do not add delayed registration, target-order fallback, or automatic reordering for `LOCAL_LAYER`.
- `LOCAL_CFLAGS` / `LOCAL_CPPFLAGS`:
  - `-DXXX` -> `target_compile_definitions(... XXX)`
  - `-Ipath` -> `target_include_directories(...)`
  - `-Wl,...` and `-L...` -> `target_link_options(...)`
  - other flags -> `target_compile_options(...)`
- `LOCAL_SRC_FILES` -> `target_sources(...)`
- `LOCAL_C_INCLUDES` -> `target_include_directories(... PRIVATE ...)`
- `LOCAL_EXPORT_C_INCLUDE_DIRS` -> `target_include_directories(... PUBLIC ...)`
- `LOCAL_SHARED_LIBRARIES` / `LOCAL_STATIC_LIBRARIES` -> `target_link_libraries(...)`
- `LOCAL_WHOLE_STATIC_LIBRARIES` -> `$<LINK_LIBRARY:WHOLE_ARCHIVE,...>`

Path rule:

- Generated `CMakeLists.txt` uses `${CMAKE_CURRENT_SOURCE_DIR}`.
- Generated included `.cmake` fragments use `${CMAKE_CURRENT_LIST_DIR}`.

## Resume Model

The pipeline is resumable by default.

State is stored under `state/`.

`state/manifest.json` records:

- stage
- item id
- input hash
- output paths
- status
- update time
- error text

Each script should:

- compute input hashes from relevant source files
- skip done tasks when hashes and outputs still match
- write outputs atomically
- support `--force`

`run_all.py` invokes the LangGraph workflow when the optional dependencies are available. If LangGraph is not installed, it falls back to running the same imported stage functions in order for local compatibility.

LangGraph checkpoints are for workflow-level recovery and Studio visibility. The durable source of conversion reuse remains `state/manifest.json` plus `state/ai_cache/`.

AI cache keys include the unknown task JSON, provider, model, agent, and prompt version. Re-running the same unresolved fragment should reuse the normalized cached result instead of asking the model again.

## Current Limitations

- AI fallback assumes `opencode` is available on `PATH` in the target environment. Use `--ai-provider skipped` when running without opencode.
- Ordinary non-Android Makefile recipe conversion is not implemented beyond preserving unknowns.
- Complex make functions are not expanded unless explicitly handled by the parser.
- Include resolution handles common literal and `$(LOCAL_PATH)` cases, but variable-heavy include paths may remain unresolved.
- Generated CMake is emitted under `state/generated/`; it is not copied back into the source tree.
- CMake configure check only runs when a root generated `CMakeLists.txt` exists.
- LangGraph Studio requires Python 3.11+. The compatibility fallback keeps basic CLI tests usable on Python 3.10.

## Validation

Run:

```sh
python3 -B -m unittest discover -s android-mk-to-cmake/tests -v
```

Current test coverage verifies:

- Kconfig `.config` parsing
- `LOCAL_LAYER + BUILD_STATIC_LIBRARY` -> object library
- condition structure preservation
- generated source-line comments
- AI unknown cache and canonical fragment normalization
- accepted AI fragments being merged while failed fragments stay out of generated CMake
- report generation
- `run_all.py` resume path

## Next Useful Work

- Add more real-world Android.mk examples.
- Improve make function expansion in `parse_mk.py`.
- Add copying/install mode for promoting `state/generated/` into the project tree.
- Add graph-aware invalidation for include-dependent files.

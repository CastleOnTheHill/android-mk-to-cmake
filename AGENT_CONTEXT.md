# Agent Context: android-mk-to-cmake

## Current Goal

The repository now keeps only the lightweight implementation:

- `lite_dag/run.py`
- `lite_dag/README.md`
- top-level usage docs
- tests for the lightweight DAG

The previous LangGraph / LangChain / opencode scheduling implementation has been removed.

## Architecture

`lite_dag/run.py` is a Python standard-library DAG executor for focused Automake-to-CMake conversion work.

Node order:

1. `discover`
2. `parse_configs`
3. `parse_project_variables`
4. `parse_makefiles`
5. `convert_makefiles`
6. `extract_existing_cmake`
7. `compare_with_existing`
8. `analyze_config_switches`
9. `check_generated_cmake`
10. `render_dashboard`

Durable outputs are written under the selected `--state-dir`:

- `graph_run.json`
- `dot_config.json`
- `project_variables.json`
- `make_ir.json`
- `mk_dependencies.json`
- `generated/`
- `generated_manifest.json`
- `comparison.json`
- `config_switches.json`
- `cmake-check.log`
- `dashboard.html`

## Design Rules

- Do not reintroduce LangChain, LangGraph, or service-based scheduling.
- Do not add AI fallback as a scheduler.
- Keep the DAG deterministic and inspectable through JSON plus static HTML.
- Keep dependencies at Python standard library unless a future task explicitly justifies otherwise.
- Validate source-list comparison and switch coverage when changing parser behavior.
- Preserve config and mk include dependency parsing as deterministic JSON artifacts.
- Preserve makefile classification and target operation ordering when changing conversion behavior.

## libcurl Validation

```sh
python3 android-mk-to-cmake/lite_dag/run.py \
  --root /tmp/curl-src \
  --state-dir /tmp/curl-lite-state \
  --focus lib --focus src \
  --force
```

Expected:

- 5/5 target source comparisons match for curl's `lib` and `src` focus.
- generated CMake configures.
- 19/19 Makefile-used condition switches are represented in generated CMake.

Known existing-CMake gap:

- `CURL_LT_SHLIB_USE_MIMPURE_TEXT` is libtool-specific and has no obvious equivalent in curl's CMake.

## Tests

```sh
python3 -B -m unittest discover -s android-mk-to-cmake/tests -v
python3 -m py_compile android-mk-to-cmake/lite_dag/run.py android-mk-to-cmake/tests/*.py
```

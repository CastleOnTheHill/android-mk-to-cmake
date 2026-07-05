# android-mk-to-cmake

Lightweight Makefile-to-CMake conversion experiments using a small Python DAG executor.

The repository now keeps only the simple implementation:

- no LangChain
- no LangGraph
- no service process
- no AI scheduler
- no Python package dependencies

The active converter is [lite_dag/run.py](lite_dag/run.py). It parses focused Automake `Makefile.am` / `Makefile.inc` inputs, emits generated CMake, records every DAG node in `state/graph_run.json`, and writes a static dashboard at `state/dashboard.html`.

The script conversion output under `state/generated/` is the intermediate CMake result. Fragments the script cannot convert, such as dynamic includes or custom rules, are preserved as ordered TODO comments in the generated `CMakeLists.txt` files for a later AI or manual pass.

## Quick Start

```sh
python3 android-mk-to-cmake/lite_dag/run.py \
  --root /path/to/project \
  --state-dir /tmp/mk2cmake-state \
  --focus lib --focus src \
  --force
```

Open the dashboard:

```text
/tmp/mk2cmake-state/dashboard.html
```

## libcurl Validation

```sh
git clone --depth 1 https://github.com/curl/curl.git /tmp/curl-src

python3 android-mk-to-cmake/lite_dag/run.py \
  --root /tmp/curl-src \
  --state-dir /tmp/curl-lite-state \
  --focus lib --focus src \
  --force
```

Expected checks:

- generated CMake configures successfully
- `libcurl`, `libcurlu`, `curl`, `curlinfo`, and `libcurltool` source lists match curl's existing CMake baseline
- Automake conditional switch coverage is written to `config_switches.json`

See [OPERATING_GUIDE.md](OPERATING_GUIDE.md) for details.

## Test

```sh
python3 -B -m unittest discover -s android-mk-to-cmake/tests -v
python3 -m py_compile android-mk-to-cmake/lite_dag/run.py android-mk-to-cmake/tests/*.py
```

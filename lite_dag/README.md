# Lite DAG Makefile Converter

This directory contains the active implementation.

It uses only Python standard library code:

- deterministic DAG scheduling
- `state/graph_run.json`
- `state/dashboard.html`
- small Automake `Makefile.am` / `Makefile.inc` parsing
- generated CMake under `state/generated/`
- ordered TODO comments for unknown Makefile fragments in generated `CMakeLists.txt`
- comparison against an existing CMake build description
- configuration switch coverage in `state/config_switches.json`

## Run

```sh
python3 lite_dag/run.py --root /path/to/curl --state-dir state-lite --force
```

Useful curl/libcurl validation command:

```sh
python3 lite_dag/run.py \
  --root /tmp/curl-src \
  --state-dir /tmp/curl-lite-state \
  --focus lib --focus src \
  --force
```

Open:

```text
/tmp/curl-lite-state/dashboard.html
```

The dashboard has three useful sections:

- `Task Run`: DAG node status from `graph_run.json`.
- `curl Makefile vs Existing CMake Comparison`: generated target source lists compared with curl's existing CMake targets.
- `Configuration Switch Coverage`: Automake `AM_CONDITIONAL` / Makefile `if` switches, their known CMake aliases, whether existing curl CMake has a match, and whether the generated CMake emits a compatible switch.

## Design

The executor is intentionally small. It does not require a service, server,
LangChain, LangGraph, AI fallback, or a Python version newer than 3.10. This
version is focused on proving that deterministic script stages and static
monitoring are enough for Makefile conversion work.

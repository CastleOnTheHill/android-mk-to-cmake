# Lite DAG Makefile Converter

This directory contains the active implementation.

It uses only Python standard library code:

- deterministic DAG scheduling
- `state/graph_run.json`
- `state/dashboard.html`
- multiple config files such as `.config` and `.euap_config`
- preloaded variables from common mk files such as `TOPDIR`
- small Automake `Makefile.am` / `Makefile.inc` parsing
- resolved mk include dependencies in `state/mk_dependencies.json`
- makefile classification as `target_definition`, `judgment_package`, or `variable_fragment`
- ordered target operations for conditional source/header/flag additions
- generated CMake under `state/generated/`
- ordered TODO comments for unknown Makefile fragments in generated `CMakeLists.txt`
- comparison against an existing CMake build description
- configuration switch coverage in `state/config_switches.json`

## Run

```sh
python3 lite_dag/run.py --root /path/to/curl --state-dir state-lite --force
```

Project-specific config and common mk variables can be supplied explicitly:

```sh
python3 lite_dag/run.py \
  --root /path/to/project \
  --state-dir state-lite \
  --focus app \
  --config-file .config \
  --config-file .euap_config \
  --var-file build/top.mk \
  --makefile-name Makefile.am \
  --force
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

The dashboard has these useful sections:

- `Task Run`: DAG node status from `graph_run.json`.
- `Config Inputs`: parsed config files from `dot_config.json`.
- `MK Include Dependencies`: resolved makefile include edges from `mk_dependencies.json`.
- `Makefile Classification`: entry package kind, included file roles, and target operation counts.
- `curl Makefile vs Existing CMake Comparison`: generated target source lists compared with curl's existing CMake targets.
- `Configuration Switch Coverage`: Automake `AM_CONDITIONAL` / Makefile `if` switches, their known CMake aliases, whether existing curl CMake has a match, and whether the generated CMake emits a compatible switch.

## Design

The executor is intentionally small. It does not require a service, server,
LangChain, LangGraph, AI fallback, or a Python version newer than 3.10. This
version is focused on proving that deterministic script stages and static
monitoring are enough for Makefile conversion work.

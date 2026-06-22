# android-mk-to-cmake

Script-first conversion system for `Android.mk`, `package.mk`, and `Makefile` projects.

The design goal is to keep most conversion deterministic and reviewable. Scripts parse Kconfig `.config` files, mk include graphs, and order-preserving mk IR before generating CMake. Model fallback is reserved for small unresolved blocks only.

## Quick Start

```sh
python3 android-mk-to-cmake/scripts/run_all.py --root . --config-dir config
```

If there is no generated root `CMakeLists.txt` yet:

```sh
python3 android-mk-to-cmake/scripts/run_all.py --root . --config-dir config --skip-check
```

Generated state and outputs are written under `state/` by default.

## Agent Handoff

Read [`AGENT_CONTEXT.md`](AGENT_CONTEXT.md) before modifying the system.

## Test

```sh
python3 -B -m unittest discover -s android-mk-to-cmake/tests -v
```

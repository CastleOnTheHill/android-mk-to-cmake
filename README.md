# android-mk-to-cmake

Script-first conversion system for `Android.mk`, `package.mk`, and `Makefile` projects, orchestrated by LangGraph.

The design goal is to keep most conversion deterministic and reviewable. Python stages parse Kconfig `.config` files, mk include graphs, and order-preserving mk IR before generating CMake. AI fallback is reserved for small unresolved blocks only, and its output is cached, schema-checked, normalized, and then merged by scripts.

## Quick Start

```sh
python3 android-mk-to-cmake/scripts/run_all.py --root . --config-dir config
```

If there is no generated root `CMakeLists.txt` yet:

```sh
python3 android-mk-to-cmake/scripts/run_all.py --root . --config-dir config --skip-check
```

Generated state and outputs are written under `state/` by default.

`run_all.py` uses LangGraph when the optional dependencies are installed. If LangGraph is unavailable, it falls back to the same stage functions so the legacy CLI remains usable.

## LangGraph Studio

LangGraph Studio requires Python 3.11+.

```sh
cd android-mk-to-cmake
python3.11 -m venv .venv
. .venv/bin/activate
pip install -e .
langgraph dev
```

The graph entry is `mk2cmake` from `langgraph.json`. Studio shows each deterministic stage and the AI fallback node separately.

## AI Fallback

Unknown Make statements are written to `state/unknown/*.unknown.json`. The AI node processes only those small tasks.

Default AI command:

```sh
opencode run --agent mk2cmake-unknown --model minimax/minimax2.7 --format json
```

The opencode agent prompt is stored at `.opencode/agent/mk2cmake-unknown.md`. Use `--ai-provider skipped` to disable AI fallback while keeping explicit skipped result files.

## Agent Handoff

Read [`AGENT_CONTEXT.md`](AGENT_CONTEXT.md) before modifying the system.

## Test

```sh
python3 -B -m unittest discover -s android-mk-to-cmake/tests -v
```

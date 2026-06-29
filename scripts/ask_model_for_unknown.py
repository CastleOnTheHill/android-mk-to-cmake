#!/usr/bin/env python3
from __future__ import annotations

import argparse

from ai_unknown import DEFAULT_OPENCODE_AGENT, DEFAULT_OPENCODE_MODEL, ai_unknown_stage


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare resumable model fallback tasks for unknown mk blocks.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--state-dir", default="state")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--ai-provider", choices=["opencode", "skipped"], default="opencode")
    parser.add_argument("--opencode-command", default="opencode")
    parser.add_argument("--opencode-model", default=DEFAULT_OPENCODE_MODEL)
    parser.add_argument("--opencode-agent", default=DEFAULT_OPENCODE_AGENT)
    args = parser.parse_args()

    result = ai_unknown_stage(
        root=args.root,
        state_dir=args.state_dir,
        force=args.force,
        provider=args.ai_provider,
        opencode_command=args.opencode_command,
        model=args.opencode_model,
        agent=args.opencode_agent,
    )
    print(
        "ask_model_for_unknown: prepared "
        f"{result['result_files']} result file(s), "
        f"{result['tasks']} task(s), "
        f"{result['converted']} converted, "
        f"{result['failed']} failed, "
        f"{result['skipped']} skipped"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

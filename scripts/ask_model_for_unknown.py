#!/usr/bin/env python3
from __future__ import annotations

import argparse

from common import Manifest, atomic_write_json, ensure_state, input_hash, load_json, rel, resolve_root, sha256_file


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare resumable model fallback tasks for unknown mk blocks.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--state-dir", default="state")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    root = resolve_root(args.root)
    state = ensure_state(root, args.state_dir)
    manifest = Manifest(root, state)
    output_dir = state / "model_results"
    output_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    skipped = 0

    for unknown_path in sorted((state / "unknown").glob("*.unknown.json")):
        task = load_json(unknown_path, {"items": []})
        digest = input_hash([sha256_file(unknown_path)])
        output = output_dir / unknown_path.name.replace(".unknown.json", ".model.json")
        if not args.force and manifest.done("model_unknown", unknown_path.stem, digest, [output]):
            count += 1
            continue
        result = {
            "schema_version": 1,
            "source_file": task.get("source_file", ""),
            "status": "skipped",
            "reason": "no_model_backend_configured",
            "items": task.get("items", []),
        }
        atomic_write_json(output, result)
        manifest.mark("model_unknown", unknown_path.stem, "done", digest, [output])
        count += 1
        if result["items"]:
            skipped += 1
    print(f"ask_model_for_unknown: prepared {count} result file(s), {skipped} contain skipped unknowns")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

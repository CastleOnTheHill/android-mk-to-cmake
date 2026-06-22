#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import tempfile
import time
from typing import Any


TOOL_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_STATE_DIR = "state"


def resolve_root(value: str | pathlib.Path) -> pathlib.Path:
    return pathlib.Path(value).expanduser().resolve()


def resolve_under(root: pathlib.Path, value: str | pathlib.Path) -> pathlib.Path:
    path = pathlib.Path(value)
    return path.expanduser().resolve() if path.is_absolute() else (root / path).resolve()


def rel(path: pathlib.Path | str, root: pathlib.Path | str) -> str:
    p = pathlib.Path(path).resolve()
    r = pathlib.Path(root).resolve()
    try:
        return p.relative_to(r).as_posix()
    except ValueError:
        return p.as_posix()


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def stable_id(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def load_json(path: pathlib.Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def atomic_write_text(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = pathlib.Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def atomic_write_json(path: pathlib.Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def state_path(root: pathlib.Path, state_dir: str | pathlib.Path) -> pathlib.Path:
    return resolve_under(root, state_dir)


def ensure_state(root: pathlib.Path, state_dir: str | pathlib.Path) -> pathlib.Path:
    path = state_path(root, state_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def input_hash(values: list[str]) -> str:
    digest = hashlib.sha256()
    for value in values:
        digest.update(value.encode("utf-8", errors="replace"))
        digest.update(b"\0")
    return digest.hexdigest()


def output_exists(root: pathlib.Path, outputs: list[str]) -> bool:
    return all(resolve_under(root, item).exists() for item in outputs)


class Manifest:
    def __init__(self, root: pathlib.Path, state_dir: pathlib.Path):
        self.root = root
        self.state_dir = state_dir
        self.path = state_dir / "manifest.json"
        self.data: dict[str, Any] = load_json(self.path, {"schema_version": 1, "tasks": {}})

    def key(self, stage: str, item_id: str) -> str:
        return f"{stage}:{item_id}"

    def get(self, stage: str, item_id: str) -> dict[str, Any] | None:
        return self.data.setdefault("tasks", {}).get(self.key(stage, item_id))

    def done(self, stage: str, item_id: str, digest: str, outputs: list[pathlib.Path]) -> bool:
        task = self.get(stage, item_id)
        if not task or task.get("status") != "done" or task.get("input_hash") != digest:
            return False
        rel_outputs = [rel(path, self.root) for path in outputs]
        return output_exists(self.root, rel_outputs)

    def mark(self, stage: str, item_id: str, status: str, digest: str, outputs: list[pathlib.Path], error: str = "") -> None:
        self.data.setdefault("tasks", {})[self.key(stage, item_id)] = {
            "stage": stage,
            "item_id": item_id,
            "status": status,
            "input_hash": digest,
            "outputs": [rel(path, self.root) for path in outputs],
            "updated_at": now(),
            "error": error,
        }
        atomic_write_json(self.path, self.data)


def split_words(value: str) -> list[str]:
    value = value.replace("\\\n", " ")
    return [part for part in value.replace("\t", " ").split(" ") if part]


def strip_outer_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def logical_lines(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    buffer: list[str] = []
    start_line = 1
    for index, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()
        if not buffer:
            start_line = index
        if line.endswith("\\"):
            buffer.append(line[:-1].rstrip())
            continue
        buffer.append(line)
        rows.append(
            {
                "start_line": start_line,
                "end_line": index,
                "raw": "\n".join(buffer),
                "text": " ".join(part.strip() for part in buffer).strip(),
            }
        )
        buffer = []
    if buffer:
        rows.append(
            {
                "start_line": start_line,
                "end_line": len(text.splitlines()),
                "raw": "\n".join(buffer),
                "text": " ".join(part.strip() for part in buffer).strip(),
            }
        )
    return rows


def strip_comment(line: str) -> str:
    quote = ""
    for index, char in enumerate(line):
        if char in {'"', "'"}:
            quote = "" if quote == char else char if not quote else quote
        if char == "#" and not quote:
            return line[:index].rstrip()
    return line.rstrip()


def generated_rel_for_source(source_rel: str) -> pathlib.Path:
    source = pathlib.Path(source_rel)
    if source.name == "package.mk":
        return source.with_name("package.cmake")
    if source.name in {"Android.mk", "Makefile"}:
        return source.with_name("CMakeLists.txt")
    return source.with_suffix(".cmake")

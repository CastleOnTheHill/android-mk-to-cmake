#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable


SOURCE_EXTS = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".rc"}
FALSE_CONDITIONS = {
    "USE_UNITY",
    "HAVE_WINDRES",
    "DOING_NATIVE_WINDOWS",
    "USE_MANUAL",
    "CURL_CA_EMBED_SET",
    "DEBUGBUILD",
    "USE_CPPFLAG_CURL_STATICLIB",
}
TRUE_CONDITIONS = {"BUILD_UNITTESTS"}
SWITCH_ALIASES = {
    "BUILD_UNITTESTS": ["CURL_BUILD_TESTING", "BUILD_TESTING"],
    "CLANG": ["CMAKE_C_COMPILER_ID", "CURL_CLANG_TIDY"],
    "CURL_CA_EMBED_SET": ["CURL_CA_EMBED_SET", "CURL_CA_EMBED"],
    "CURL_LT_SHLIB_USE_MIMPURE_TEXT": ["mimpure-text"],
    "CURL_LT_SHLIB_USE_NO_UNDEFINED": ["no-undefined", "WIN32"],
    "CURL_LT_SHLIB_USE_VERSIONED_SYMBOLS": ["CURL_LIBCURL_VERSIONED_SYMBOLS", "HAVE_VERSIONED_SYMBOLS"],
    "CURL_LT_SHLIB_USE_VERSION_INFO": ["CURL_LIBCURL_SOVERSION", "VERSIONCHANGE", "VERSIONINFO"],
    "CURL_WERROR": ["CURL_WERROR"],
    "DEBUGBUILD": ["ENABLE_DEBUG", "CURL_DEBUG_MACROS"],
    "DOING_CURL_SYMBOL_HIDING": ["CURL_HIDES_PRIVATE_SYMBOLS", "CURL_HIDDEN_SYMBOLS"],
    "DOING_NATIVE_WINDOWS": ["WIN32"],
    "HAVE_WINDRES": ["WIN32", "CURL_RCFILES"],
    "NOT_CURL_CI": ["CURL_BUILDINFO", "CURL_CI", "CI"],
    "PERL": ["Perl_FOUND", "PERL_EXECUTABLE"],
    "HAVE_LIBZ": ["ZLIB_FOUND", "HAVE_LIBZ"],
    "BUILD_STUB_GSS": ["CURL_USE_GSSAPI", "HAVE_GSSAPI", "GSS_FOUND"],
    "USE_LIBPSL": ["CURL_USE_LIBPSL", "USE_LIBPSL"],
    "USE_GSASL": ["CURL_USE_GSASL", "USE_GSASL"],
    "USE_ZSH_COMPLETION": ["CURL_COMPLETION_ZSH"],
    "USE_FISH_COMPLETION": ["CURL_COMPLETION_FISH"],
    "BUILD_DOCS": ["BUILD_LIBCURL_DOCS", "BUILD_MISC_DOCS"],
    "CROSSCOMPILING": ["CMAKE_CROSSCOMPILING"],
    "USE_CPPFLAG_CURL_STATICLIB": ["CURL_STATICLIB", "BUILD_STATIC_CURL", "BUILD_STATIC_LIBS"],
    "USE_MANUAL": ["ENABLE_CURL_MANUAL"],
    "USE_UNICODE": ["ENABLE_UNICODE"],
    "USE_UNITY": ["UNITY_BUILD", "CMAKE_UNITY_BUILD"],
}


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def read_text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write_text(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: pathlib.Path, value: Any) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def load_json(path: pathlib.Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def rel(path: pathlib.Path, root: pathlib.Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def strip_comment(line: str) -> str:
    quote = ""
    for index, char in enumerate(line):
        if char in {"'", '"'}:
            quote = "" if quote == char else char if not quote else quote
        if char == "#" and not quote:
            return line[:index].rstrip()
    return line.rstrip()


def logical_lines(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    buffer: list[str] = []
    start_line = 1
    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()
        if not buffer:
            start_line = line_no
        if line.endswith("\\"):
            buffer.append(line[:-1].rstrip())
            continue
        buffer.append(line)
        rows.append({"line": start_line, "end_line": line_no, "raw": "\n".join(buffer), "text": " ".join(part.strip() for part in buffer).strip()})
        buffer = []
    if buffer:
        rows.append({"line": start_line, "end_line": len(text.splitlines()), "raw": "\n".join(buffer), "text": " ".join(part.strip() for part in buffer).strip()})
    return rows


def split_words(value: str) -> list[str]:
    return [part for part in value.replace("\t", " ").split(" ") if part]


def condition_default(expr: str) -> bool:
    token = expr.strip().split(" ", 1)[0]
    if token in TRUE_CONDITIONS:
        return True
    if token in FALSE_CONDITIONS:
        return False
    return True


def condition_switch(expr: str) -> str:
    text = expr.strip()
    if text.startswith("if "):
        text = text[3:].strip()
    if text.startswith("if") and len(text) > 2 and text[2].isupper():
        text = text[2:].strip()
    if text.startswith("not "):
        text = text[4:].strip()
    return text.split(" ", 1)[0]


def automake_target_var(target: str) -> str:
    return target.replace(".", "_").replace("-", "_")


def cmake_target_name(target: str) -> str:
    if target.endswith(".la"):
        return target[:-3]
    return target


def target_kind(container: str) -> str:
    if container.endswith("_PROGRAMS"):
        return "executable"
    if container.endswith("_LTLIBRARIES") or container.endswith("_LIBRARIES"):
        return "library"
    return "unknown"


def normalize_source(base_dir: str, value: str) -> str:
    value = value.strip('"')
    value = value.replace("$(srcdir)/", "").replace("${srcdir}/", "")
    value = value.replace("$(top_srcdir)/", "../").replace("${top_srcdir}/", "../")
    value = value.replace("$(top_builddir)/", "../").replace("${top_builddir}/", "../")
    path = pathlib.PurePosixPath(base_dir) / value
    parts: list[str] = []
    for part in path.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


@dataclass
class Assignment:
    file: str
    line: int
    variable: str
    operator: str
    values: list[str]
    raw: str
    conditions: list[str]
    condition_switches: list[str]
    active: bool


@dataclass
class MakefileIR:
    path: str
    directory: str
    assignments: list[Assignment] = field(default_factory=list)
    included_files: list[str] = field(default_factory=list)
    variables: dict[str, list[str]] = field(default_factory=dict)
    targets: list[dict[str, Any]] = field(default_factory=list)
    unknown: list[dict[str, Any]] = field(default_factory=list)
    conditions: list[dict[str, Any]] = field(default_factory=list)


class MakefileParser:
    assign_re = re.compile(r"^(?P<var>[A-Za-z0-9_.$()/{}+-]+)\s*(?P<op>\+=|:=|\?=|=)\s*(?P<value>.*)$")
    include_re = re.compile(r"^include\s+(?P<path>.+)$")

    def __init__(self, root: pathlib.Path):
        self.root = root.resolve()

    def parse(self, rel_path: str) -> MakefileIR:
        path = self.root / rel_path
        ir = MakefileIR(path=rel_path, directory=path.parent.relative_to(self.root).as_posix())
        self._parse_into(ir, path, [])
        ir.variables = self._build_variables(ir.assignments)
        ir.targets = self._build_targets(ir)
        return ir

    def _parse_into(self, ir: MakefileIR, path: pathlib.Path, include_stack: list[pathlib.Path]) -> None:
        if path in include_stack:
            ir.unknown.append({"file": rel(path, self.root), "reason": "include_cycle"})
            return
        include_stack = [*include_stack, path]
        condition_stack: list[dict[str, Any]] = []
        for row in logical_lines(read_text(path)):
            text = strip_comment(row["text"]).strip()
            if not text:
                continue
            if text.startswith("if ") or re.match(r"^if[A-Z0-9_]+\b", text):
                expr = text[3:].strip() if text.startswith("if ") else text[2:].strip()
                switch = condition_switch(expr)
                ir.conditions.append({"file": rel(path, self.root), "line": row["line"], "raw": text, "expr": expr, "switch": switch})
                condition_stack.append({"raw": text, "expr": expr, "switch": switch, "active": condition_default(expr), "negated": False})
                continue
            if text == "else":
                if condition_stack:
                    current = condition_stack[-1]
                    condition_stack[-1] = {
                        **current,
                        "raw": f"else # {current.get('expr', '')}".rstrip(),
                        "active": not bool(current["active"]),
                        "negated": not bool(current.get("negated")),
                    }
                else:
                    ir.unknown.append({"file": rel(path, self.root), "line": row["line"], "reason": "else_without_if"})
                continue
            if text == "endif":
                if condition_stack:
                    condition_stack.pop()
                else:
                    ir.unknown.append({"file": rel(path, self.root), "line": row["line"], "reason": "endif_without_if"})
                continue
            include = self.include_re.match(text)
            if include:
                include_value = include.group("path").strip().strip('"')
                if "$" in include_value:
                    ir.unknown.append({"file": rel(path, self.root), "line": row["line"], "reason": "dynamic_include", "expr": include_value})
                    continue
                include_path = (path.parent / include_value).resolve()
                if include_path.exists():
                    ir.included_files.append(rel(include_path, self.root))
                    self._parse_into(ir, include_path, include_stack)
                else:
                    ir.unknown.append({"file": rel(path, self.root), "line": row["line"], "reason": "missing_include", "expr": include_value})
                continue
            match = self.assign_re.match(text)
            if not match:
                if ":" in text or text.startswith("\t"):
                    ir.unknown.append({"file": rel(path, self.root), "line": row["line"], "reason": "recipe_or_rule", "text": text[:120]})
                continue
            active = all(bool(item["active"]) for item in condition_stack)
            ir.assignments.append(
                Assignment(
                    file=rel(path, self.root),
                    line=row["line"],
                    variable=match.group("var"),
                    operator=match.group("op"),
                    values=split_words(match.group("value")),
                    raw=row["raw"],
                    conditions=[item["raw"] for item in condition_stack],
                    condition_switches=[item["switch"] for item in condition_stack],
                    active=active,
                )
            )

    def _build_variables(self, assignments: list[Assignment]) -> dict[str, list[str]]:
        variables: dict[str, list[str]] = {}
        for item in assignments:
            if not item.active:
                continue
            if item.operator == "+=":
                variables.setdefault(item.variable, []).extend(item.values)
            elif item.operator == "?=":
                variables.setdefault(item.variable, list(item.values))
            else:
                variables[item.variable] = list(item.values)
        expanded: dict[str, list[str]] = {}
        for key in variables:
            expanded[key] = self.expand_values(variables[key], variables)
        return expanded

    def expand_values(self, values: list[str], variables: dict[str, list[str]], stack: tuple[str, ...] = (), generated_refs: set[str] | None = None) -> list[str]:
        expanded: list[str] = []
        for value in values:
            match = re.fullmatch(r"\$\(([^)]+)\)|\$\{([^}]+)\}", value)
            if match:
                name = match.group(1) or match.group(2)
                if name.lower().endswith("_gen") and generated_refs is not None:
                    generated_refs.add(name)
                if name in stack:
                    expanded.append(value)
                elif name in variables:
                    expanded.extend(self.expand_values(variables[name], variables, (*stack, name), generated_refs))
                else:
                    expanded.append(value)
                continue
            replaced = value
            for name in re.findall(r"\$\(([^)]+)\)|\$\{([^}]+)\}", value):
                var_name = name[0] or name[1]
                if var_name in variables and len(variables[var_name]) == 1:
                    replaced = replaced.replace(f"$({var_name})", variables[var_name][0]).replace(f"${{{var_name}}}", variables[var_name][0])
            expanded.append(replaced)
        return expanded

    def _build_targets(self, ir: MakefileIR) -> list[dict[str, Any]]:
        containers = ["lib_LTLIBRARIES", "noinst_LTLIBRARIES", "bin_PROGRAMS", "noinst_PROGRAMS"]
        targets: list[dict[str, Any]] = []
        for container in containers:
            for raw_target in ir.variables.get(container, []):
                if raw_target.startswith("$("):
                    continue
                target_var = automake_target_var(raw_target)
                name = cmake_target_name(raw_target)
                source_values = []
                generated_refs: set[str] = set()
                for source_var in [f"{target_var}_SOURCES", f"nodist_{target_var}_SOURCES"]:
                    source_values.extend(self.expand_values(ir.variables.get(source_var, []), ir.variables, generated_refs=generated_refs))
                generated_values: set[str] = set()
                for ref in generated_refs:
                    generated_values.update(ir.variables.get(ref, []))
                for variable, values in ir.variables.items():
                    if variable.lower().endswith("_gen"):
                        generated_values.update(values)
                sources = sorted(
                    {
                        normalize_source(ir.directory, value)
                        for value in source_values
                        if pathlib.PurePosixPath(value.strip('"')).suffix in SOURCE_EXTS
                        and "$" not in value
                        and "@" not in value
                        and value not in generated_values
                    }
                )
                generated_sources = sorted(
                    {
                        normalize_source(ir.directory, value)
                        for value in generated_values
                        if pathlib.PurePosixPath(value.strip('"')).suffix in SOURCE_EXTS and "$" not in value and "@" not in value
                    }
                )
                cppflags = [*ir.variables.get("AM_CPPFLAGS", []), *ir.variables.get(f"{target_var}_CPPFLAGS", [])]
                include_dirs = []
                definitions = []
                compile_options = []
                for flag in cppflags:
                    if flag.startswith("-I"):
                        include_dirs.append(self._normalize_include(ir.directory, flag[2:]))
                    elif flag.startswith("-D"):
                        definitions.append(flag[2:])
                    elif "$" not in flag and "@" not in flag:
                        compile_options.append(flag)
                targets.append(
                    {
                        "name": name,
                        "raw_name": raw_target,
                        "kind": target_kind(container),
                        "container": container,
                        "directory": ir.directory,
                        "sources": sources,
                        "generated_sources": generated_sources,
                        "include_dirs": sorted(dict.fromkeys(include_dirs)),
                        "compile_definitions": sorted(dict.fromkeys(definitions)),
                        "compile_options": compile_options,
                        "link_items": ir.variables.get(f"{target_var}_LDADD", []),
                    }
                )
        return targets

    def _normalize_include(self, directory: str, value: str) -> str:
        value = value.strip('"')
        if value == "$(srcdir)":
            return directory
        value = value.replace("$(top_srcdir)", "").replace("${top_srcdir}", "")
        value = value.replace("$(top_builddir)", "").replace("${top_builddir}", "")
        value = value.replace("$(srcdir)", directory).replace("${srcdir}", directory)
        value = value.lstrip("/")
        if not value:
            return directory
        if value.startswith(".."):
            return normalize_source(directory, value)
        return value


@dataclass
class Task:
    name: str
    deps: list[str]
    action: Callable[[dict[str, Any]], dict[str, Any]]


class DagExecutor:
    def __init__(self, tasks: list[Task], context: dict[str, Any]):
        self.tasks = {task.name: task for task in tasks}
        self.context = context
        self.results: list[dict[str, Any]] = []

    def run(self) -> dict[str, Any]:
        started = now()
        pending = set(self.tasks)
        completed: set[str] = set()
        failed = False
        while pending and not failed:
            ready = sorted(name for name in pending if all(dep in completed for dep in self.tasks[name].deps))
            if not ready:
                raise RuntimeError(f"DAG has unresolved dependencies: {sorted(pending)}")
            for name in ready:
                task = self.tasks[name]
                start_time = time.time()
                row = {"name": name, "status": "running", "started_at": now(), "deps": task.deps}
                try:
                    result = task.action(self.context)
                    row.update(result)
                    row["status"] = result.get("status", "done")
                except Exception as exc:
                    row["status"] = "failed"
                    row["error"] = str(exc)
                    row["traceback"] = traceback.format_exc()
                    failed = True
                row["duration_sec"] = round(time.time() - start_time, 3)
                row["finished_at"] = now()
                self.results.append(row)
                completed.add(name)
                pending.remove(name)
                if failed:
                    break
        run = {"schema_version": 1, "status": "failed" if failed else "done", "started_at": started, "finished_at": now(), "tasks": self.results}
        write_json(self.context["state_dir"] / "graph_run.json", run)
        return run


def discover(ctx: dict[str, Any]) -> dict[str, Any]:
    root = ctx["root"]
    focus = ctx["focus"]
    makefiles = []
    cmake_files = []
    for item in focus:
        mf = root / item / "Makefile.am"
        cm = root / item / "CMakeLists.txt"
        if mf.exists():
            makefiles.append(rel(mf, root))
        if cm.exists():
            cmake_files.append(rel(cm, root))
    write_json(ctx["state_dir"] / "inputs.json", {"root": root.as_posix(), "makefiles": makefiles, "cmake_files": cmake_files})
    return {"status": "done", "makefiles": len(makefiles), "cmake_files": len(cmake_files), "outputs": ["inputs.json"]}


def parse_makefiles(ctx: dict[str, Any]) -> dict[str, Any]:
    inputs = load_json(ctx["state_dir"] / "inputs.json", {"makefiles": []})
    parser = MakefileParser(ctx["root"])
    irs = []
    for mf in inputs["makefiles"]:
        ir = parser.parse(mf)
        irs.append(
            {
                "path": ir.path,
                "directory": ir.directory,
                "included_files": ir.included_files,
                "variables": ir.variables,
                "targets": ir.targets,
                "unknown": ir.unknown,
                "conditions": ir.conditions,
                "assignments": [item.__dict__ for item in ir.assignments],
            }
        )
    write_json(ctx["state_dir"] / "make_ir.json", {"schema_version": 1, "files": irs})
    return {"status": "done", "files": len(irs), "targets": sum(len(item["targets"]) for item in irs), "outputs": ["make_ir.json"]}


def cmake_path_for_source(source_root_var: str, rel_source: str) -> str:
    return f"${{{source_root_var}}}/{rel_source}"


def render_target(target: dict[str, Any]) -> str:
    lines: list[str] = []
    name = target["name"]
    kind = target["kind"]
    add = "add_executable" if kind == "executable" else "add_library"
    lib_kind = "STATIC" if kind == "library" else ""
    lines.append(f"# from {target['directory']}/Makefile.am target {target['raw_name']}")
    lines.append(f"{add}({name}{(' ' + lib_kind) if lib_kind else ''})")
    if target["sources"]:
        lines.append(f"target_sources({name}")
        lines.append("    PRIVATE")
        for source in target["sources"]:
            lines.append(f"        {cmake_path_for_source('MK_SOURCE_ROOT', source)}")
        lines.append(")")
    if target["include_dirs"]:
        lines.append(f"target_include_directories({name}")
        lines.append("    PRIVATE")
        for item in target["include_dirs"]:
            lines.append(f"        {cmake_path_for_source('MK_SOURCE_ROOT', item)}")
        lines.append(")")
    if target["compile_definitions"]:
        lines.append(f"target_compile_definitions({name}")
        lines.append("    PRIVATE")
        for item in target["compile_definitions"]:
            lines.append(f"        {item}")
        lines.append(")")
    link_targets = []
    comments = []
    for item in target.get("link_items", []):
        if item.endswith("/libcurl.la") or item == "libcurl.la":
            link_targets.append("libcurl")
        elif "$" in item or "@" in item:
            comments.append(item)
        else:
            link_targets.append(item)
    if link_targets:
        lines.append(f"target_link_libraries({name} PRIVATE {' '.join(link_targets)})")
    for item in comments:
        lines.append(f"# unresolved link item from Makefile.am: {item}")
    return "\n".join(lines) + "\n"


def make_switches_from_ir(files: list[dict[str, Any]]) -> list[str]:
    switches = set()
    for file_ir in files:
        for condition in file_ir.get("conditions", []):
            switch = condition.get("switch")
            if switch:
                switches.add(switch)
    return sorted(switches)


def option_name_for_switch(switch: str) -> str | None:
    aliases = SWITCH_ALIASES.get(switch, [switch])
    for alias in aliases:
        if alias in {"WIN32", "CMAKE_C_COMPILER_ID", "Perl_FOUND", "PERL_EXECUTABLE", "ZLIB_FOUND", "HAVE_LIBZ", "CI"}:
            continue
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", alias):
            return alias
    return switch if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", switch) else None


def default_for_switch(switch: str) -> str:
    if switch in TRUE_CONDITIONS:
        return "ON"
    if switch in FALSE_CONDITIONS:
        return "OFF"
    if switch in {"USE_MANUAL"}:
        return "ON"
    return "OFF"


def render_switch_compat(switches: list[str]) -> str:
    lines = [
        "# Generated Automake-to-CMake switch compatibility layer.",
        "# These declarations make converted Makefile condition knobs visible.",
        "# They do not replace project-specific feature detection.",
        "",
    ]
    for switch in switches:
        aliases = SWITCH_ALIASES.get(switch, [switch])
        option_name = option_name_for_switch(switch)
        lines.append(f"# Automake switch: {switch}")
        lines.append(f"# Known CMake aliases: {', '.join(aliases)}")
        if option_name:
            lines.append(f"if(NOT DEFINED {option_name})")
            lines.append(f'    option({option_name} "Compatibility switch for Automake {switch}" {default_for_switch(switch)})')
            lines.append("endif()")
        else:
            lines.append(f"# No standalone CMake cache option generated for {switch}.")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def convert_makefiles(ctx: dict[str, Any]) -> dict[str, Any]:
    data = load_json(ctx["state_dir"] / "make_ir.json", {"files": []})
    output_root = ctx["state_dir"] / "generated"
    if output_root.exists():
        shutil.rmtree(output_root)
    root_lines = [
        "cmake_minimum_required(VERSION 3.16)",
        "project(converted_makefile C)",
        f'set(MK_SOURCE_ROOT "{ctx["root"].as_posix()}")',
        'include("${CMAKE_CURRENT_LIST_DIR}/MakefileSwitches.cmake")',
        "",
    ]
    switches = make_switches_from_ir(data["files"])
    write_text(output_root / "MakefileSwitches.cmake", render_switch_compat(switches))
    manifest = []
    for item in data["files"]:
        if item["targets"]:
            root_lines.append(f"add_subdirectory({item['directory']})")
        out = output_root / item["directory"] / "CMakeLists.txt"
        body = ["# Generated by lite_dag/run.py.", "# Review before using as production CMake.", ""]
        for target in item["targets"]:
            body.append(render_target(target))
            manifest.append({"directory": item["directory"], **target})
        write_text(out, "\n".join(body).rstrip() + "\n")
    write_text(output_root / "CMakeLists.txt", "\n".join(root_lines).rstrip() + "\n")
    write_json(ctx["state_dir"] / "generated_manifest.json", {"schema_version": 1, "targets": manifest})
    return {"status": "done", "targets": len(manifest), "switches": len(switches), "outputs": ["generated", "generated_manifest.json"]}


def extract_existing_cmake(ctx: dict[str, Any]) -> dict[str, Any]:
    root = ctx["root"]
    extracted: dict[str, Any] = {"schema_version": 1, "targets": []}
    for directory in ctx["focus"]:
        cmake_path = root / directory / "CMakeLists.txt"
        if not cmake_path.exists():
            continue
        text = read_text(cmake_path)
        commands = re.findall(r"\b(add_library|add_executable)\s*\(([^)\n]+(?:\n[^)]*)?)\)", text)
        extracted["targets"].extend({"directory": directory, "command": command, "args": " ".join(args.split())} for command, args in commands)
    write_json(ctx["state_dir"] / "existing_cmake.json", extracted)
    return {"status": "done", "commands": len(extracted["targets"]), "outputs": ["existing_cmake.json"]}


def generated_target_map(ctx: dict[str, Any]) -> dict[str, dict[str, Any]]:
    data = load_json(ctx["state_dir"] / "generated_manifest.json", {"targets": []})
    return {f"{item['directory']}:{item['name']}": item for item in data["targets"]}


def expected_cmake_baseline(ctx: dict[str, Any]) -> dict[str, dict[str, Any]]:
    parser = MakefileParser(ctx["root"])
    baseline: dict[str, dict[str, Any]] = {}
    lib_inc = ctx["root"] / "lib" / "Makefile.inc"
    if lib_inc.exists():
        ir = MakefileIR(path="lib/Makefile.inc", directory="lib")
        parser._parse_into(ir, lib_inc, [])
        variables = parser._build_variables(ir.assignments)
        lib_sources = sorted({normalize_source("lib", value) for value in parser.expand_values(["$(CSOURCES)", "$(HHEADERS)"], variables) if pathlib.PurePosixPath(value).suffix in SOURCE_EXTS and "$" not in value})
        baseline["lib:libcurl"] = {"sources": lib_sources, "note": "curl CMake transforms lib/Makefile.inc into CSOURCES/HHEADERS"}
        baseline["lib:libcurlu"] = {"sources": lib_sources, "note": "curl CMake curlu test library uses CSOURCES/HHEADERS"}
    src_inc = ctx["root"] / "src" / "Makefile.inc"
    if src_inc.exists():
        ir = MakefileIR(path="src/Makefile.inc", directory="src")
        parser._parse_into(ir, src_inc, [])
        variables = parser._build_variables(ir.assignments)
        curl_sources = sorted({normalize_source("src", value) for value in parser.expand_values(["$(CURL_CFILES)", "$(CURL_HFILES)", "$(CURLX_CFILES)", "$(CURLX_HFILES)"], variables) if pathlib.PurePosixPath(value).suffix in SOURCE_EXTS and "$" not in value})
        curlinfo_sources = ["src/curlinfo.c"]
        baseline["src:curl"] = {"sources": curl_sources, "note": "curl CMake add_executable uses CURL_* and CURLX_* variables"}
        baseline["src:curlinfo"] = {"sources": curlinfo_sources, "note": "curl CMake has explicit curlinfo.c target"}
        baseline["src:libcurltool"] = {"sources": curl_sources, "note": "curl CMake curltool uses CURL_* and curlx variables"}
    return baseline


def compare_with_existing(ctx: dict[str, Any]) -> dict[str, Any]:
    generated = generated_target_map(ctx)
    baseline = expected_cmake_baseline(ctx)
    comparisons = []
    for key in sorted(set(generated) | set(baseline)):
        gen = generated.get(key, {"sources": []})
        base = baseline.get(key, {"sources": []})
        gen_sources = set(gen.get("sources", []))
        base_sources = set(base.get("sources", []))
        comparisons.append(
            {
                "target": key,
                "generated_sources": len(gen_sources),
                "existing_sources": len(base_sources),
                "missing_from_generated": sorted(base_sources - gen_sources),
                "extra_in_generated": sorted(gen_sources - base_sources),
                "status": "match" if gen_sources == base_sources else "diff",
                "note": base.get("note", ""),
            }
        )
    write_json(ctx["state_dir"] / "comparison.json", {"schema_version": 1, "targets": comparisons})
    return {"status": "done", "matches": sum(1 for item in comparisons if item["status"] == "match"), "diffs": sum(1 for item in comparisons if item["status"] == "diff"), "outputs": ["comparison.json"]}


def extract_cmake_symbols(text: str) -> set[str]:
    symbols: set[str] = set()
    for command in re.finditer(r"\b(?:if|option|cmake_dependent_option)\s*\(([^)\n]+(?:\n[^)]*)?)\)", text):
        body = command.group(1)
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*|-?[A-Za-z0-9_./+-]+", body):
            if token.upper() in {"AND", "OR", "NOT", "ON", "OFF", "TRUE", "FALSE", "STREQUAL", "MATCHES", "DEFINED", "EXISTS"}:
                continue
            symbols.add(token.strip('"'))
    return symbols


def extract_am_conditionals(root: pathlib.Path) -> dict[str, list[str]]:
    paths = [root / "configure.ac", root / "acinclude.m4"]
    m4_dir = root / "m4"
    if m4_dir.exists():
        paths.extend(sorted(m4_dir.glob("*.m4")))
    conditionals: dict[str, list[str]] = {}
    for path in paths:
        if not path.exists():
            continue
        text = read_text(path)
        for match in re.finditer(r"AM_CONDITIONAL\s*\(\s*\[?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\]?", text):
            line = text.count("\n", 0, match.start()) + 1
            conditionals.setdefault(match.group("name"), []).append(f"{rel(path, root)}:{line}")
    return conditionals


def cmake_files_for_analysis(root: pathlib.Path) -> list[pathlib.Path]:
    files = list(root.rglob("CMakeLists.txt"))
    cmake_dir = root / "CMake"
    if cmake_dir.exists():
        files.extend(cmake_dir.rglob("*.cmake"))
    return sorted({path for path in files if path.is_file()})


def generated_cmake_files(root: pathlib.Path) -> list[pathlib.Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and (path.name == "CMakeLists.txt" or path.suffix == ".cmake")
    )


def analyze_config_switches(ctx: dict[str, Any]) -> dict[str, Any]:
    ir = load_json(ctx["state_dir"] / "make_ir.json", {"files": []})
    make_switches: dict[str, dict[str, Any]] = {}
    for file_ir in ir.get("files", []):
        for condition in file_ir.get("conditions", []):
            switch = condition.get("switch", "")
            if not switch:
                continue
            row = make_switches.setdefault(switch, {"switch": switch, "locations": [], "generated_uses": 0})
            row["locations"].append(f"{condition.get('file')}:{condition.get('line')}")
        for assignment in file_ir.get("assignments", []):
            for switch in assignment.get("condition_switches", []):
                if switch in make_switches:
                    make_switches[switch]["generated_uses"] += 1

    configured_switches = extract_am_conditionals(ctx["root"])
    cmake_symbols: set[str] = set()
    cmake_text = ""
    for path in cmake_files_for_analysis(ctx["root"]):
        text = read_text(path)
        cmake_text += "\n" + text
        cmake_symbols.update(extract_cmake_symbols(text))

    generated_text = ""
    for path in generated_cmake_files(ctx["state_dir"] / "generated"):
        generated_text += "\n" + read_text(path)

    rows = []
    all_switches = sorted(set(make_switches) | set(configured_switches))
    for switch in all_switches:
        aliases = SWITCH_ALIASES.get(switch, [switch])
        existing_matches = sorted(alias for alias in aliases if alias in cmake_symbols or alias in cmake_text)
        generated_matches = sorted(alias for alias in aliases if alias in generated_text)
        if switch in generated_text and switch not in generated_matches:
            generated_matches.append(switch)
        in_focus = switch in make_switches
        scope = "makefile_used" if in_focus else "configure_only"
        category = "matched" if existing_matches else "unmatched"
        generated_status = "converted" if generated_matches else "not_converted"
        if not in_focus:
            generated_status = "out_of_scope"
        # Some autotools switches only drive maintenance rules or libtool-specific flags.
        if switch in {"NOT_CURL_CI", "CLANG"}:
            category = "maintenance_only" if existing_matches else "maintenance_unmatched"
        if switch.startswith("CURL_LT_SHLIB_USE_"):
            category = "libtool_mapped" if existing_matches else "libtool_unmatched"
        rows.append(
            {
                "switch": switch,
                "scope": scope,
                "locations": make_switches.get(switch, {}).get("locations", []),
                "configure_locations": configured_switches.get(switch, []),
                "aliases": aliases,
                "existing_cmake_matches": existing_matches,
                "existing_status": category,
                "generated_matches": generated_matches,
                "generated_status": generated_status,
                "conditional_assignment_uses": make_switches.get(switch, {}).get("generated_uses", 0),
            }
        )
    write_json(
        ctx["state_dir"] / "config_switches.json",
        {
            "schema_version": 1,
            "summary": {
                "configure_conditionals": len(configured_switches),
                "makefile_used_switches": sum(1 for item in rows if item["scope"] == "makefile_used"),
                "configure_only_switches": sum(1 for item in rows if item["scope"] == "configure_only"),
                "existing_matched": sum(1 for item in rows if item["existing_status"] in {"matched", "libtool_mapped", "maintenance_only"}),
                "generated_converted": sum(1 for item in rows if item["generated_status"] == "converted"),
            },
            "switches": rows,
        },
    )
    return {
        "status": "done",
        "switches": len(rows),
        "makefile_used": sum(1 for item in rows if item["scope"] == "makefile_used"),
        "configure_only": sum(1 for item in rows if item["scope"] == "configure_only"),
        "existing_matched": sum(1 for item in rows if item["existing_status"] in {"matched", "libtool_mapped", "maintenance_only"}),
        "generated_converted": sum(1 for item in rows if item["generated_status"] == "converted"),
        "outputs": ["config_switches.json"],
    }


def check_generated_cmake(ctx: dict[str, Any]) -> dict[str, Any]:
    cmake = shutil.which("cmake")
    if not cmake:
        return {"status": "skipped", "reason": "cmake not found"}
    source = ctx["state_dir"] / "generated"
    build = ctx["state_dir"] / "cmake-check"
    cp = subprocess.run([cmake, "-S", str(source), "-B", str(build)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    write_text(ctx["state_dir"] / "cmake-check.log", cp.stdout + "\n" + cp.stderr)
    return {"status": "done" if cp.returncode == 0 else "failed", "returncode": cp.returncode, "outputs": ["cmake-check.log"]}


def render_dashboard(ctx: dict[str, Any]) -> dict[str, Any]:
    run = load_json(ctx["state_dir"] / "graph_run.json", {"tasks": []})
    comparison = load_json(ctx["state_dir"] / "comparison.json", {"targets": []})
    switches = load_json(ctx["state_dir"] / "config_switches.json", {"switches": []})
    rows = []
    for task in run.get("tasks", []):
        rows.append(
            "<tr>"
            f"<td>{html.escape(task.get('name', ''))}</td>"
            f"<td class='{html.escape(task.get('status', ''))}'>{html.escape(task.get('status', ''))}</td>"
            f"<td>{task.get('duration_sec', '')}</td>"
            f"<td>{html.escape(json.dumps({k: v for k, v in task.items() if k not in {'traceback'}}, ensure_ascii=False))}</td>"
            "</tr>"
        )
    comparison_rows = []
    for item in comparison.get("targets", []):
        comparison_rows.append(
            "<tr>"
            f"<td>{html.escape(item['target'])}</td>"
            f"<td class='{html.escape(item['status'])}'>{html.escape(item['status'])}</td>"
            f"<td>{item['generated_sources']}</td>"
            f"<td>{item['existing_sources']}</td>"
            f"<td>{html.escape(', '.join(item['missing_from_generated'][:10]))}</td>"
            f"<td>{html.escape(', '.join(item['extra_in_generated'][:10]))}</td>"
            "</tr>"
        )
    switch_rows = []
    for item in switches.get("switches", []):
        switch_rows.append(
            "<tr>"
            f"<td>{html.escape(item['switch'])}</td>"
            f"<td>{html.escape(item['scope'])}</td>"
            f"<td class='{html.escape(item['existing_status'])}'>{html.escape(item['existing_status'])}</td>"
            f"<td class='{html.escape(item['generated_status'])}'>{html.escape(item['generated_status'])}</td>"
            f"<td>{html.escape(', '.join(item['existing_cmake_matches']))}</td>"
            f"<td>{html.escape(', '.join(item['aliases']))}</td>"
            f"<td>{item['conditional_assignment_uses']}</td>"
            f"<td>{html.escape(', '.join((item['locations'] + item.get('configure_locations', []))[:8]))}</td>"
            "</tr>"
        )
    page = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Lite DAG Dashboard</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 24px; color: #1f2933; }}
table {{ border-collapse: collapse; width: 100%; margin: 16px 0 32px; }}
th, td {{ border: 1px solid #d8dee4; padding: 8px; vertical-align: top; font-size: 13px; }}
th {{ background: #f6f8fa; text-align: left; }}
.done, .match {{ color: #116329; font-weight: 700; }}
.failed, .diff, .unmatched, .not_converted, .libtool_unmatched, .maintenance_unmatched {{ color: #b42318; font-weight: 700; }}
.skipped, .maintenance_only, .libtool_mapped, .out_of_scope {{ color: #8a5a00; font-weight: 700; }}
.matched, .converted {{ color: #116329; font-weight: 700; }}
code {{ background: #f6f8fa; padding: 2px 4px; }}
</style>
</head>
<body>
<h1>Lite DAG Dashboard</h1>
<p>Root: <code>{html.escape(ctx['root'].as_posix())}</code></p>
<p>State: <code>{html.escape(ctx['state_dir'].as_posix())}</code></p>
<h2>Task Run</h2>
<table><thead><tr><th>Node</th><th>Status</th><th>Seconds</th><th>Details</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<h2>curl Makefile vs Existing CMake Comparison</h2>
<table><thead><tr><th>Target</th><th>Status</th><th>Generated sources</th><th>Existing CMake sources</th><th>Missing</th><th>Extra</th></tr></thead><tbody>{''.join(comparison_rows)}</tbody></table>
<h2>Configuration Switch Coverage</h2>
<table><thead><tr><th>Makefile switch</th><th>Scope</th><th>Existing CMake</th><th>Generated CMake</th><th>Matched CMake symbols</th><th>Known aliases</th><th>Conditional assignments</th><th>Locations</th></tr></thead><tbody>{''.join(switch_rows)}</tbody></table>
</body>
</html>
"""
    write_text(ctx["state_dir"] / "dashboard.html", page)
    return {"status": "done", "outputs": ["dashboard.html"]}


def build_tasks() -> list[Task]:
    return [
        Task("discover", [], discover),
        Task("parse_makefiles", ["discover"], parse_makefiles),
        Task("convert_makefiles", ["parse_makefiles"], convert_makefiles),
        Task("extract_existing_cmake", ["discover"], extract_existing_cmake),
        Task("compare_with_existing", ["convert_makefiles", "extract_existing_cmake"], compare_with_existing),
        Task("analyze_config_switches", ["convert_makefiles", "extract_existing_cmake"], analyze_config_switches),
        Task("check_generated_cmake", ["convert_makefiles"], check_generated_cmake),
        Task("render_dashboard", ["compare_with_existing", "analyze_config_switches", "check_generated_cmake"], render_dashboard),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run lightweight Python DAG Makefile-to-CMake conversion.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--state-dir", default="state-lite")
    parser.add_argument("--focus", action="append", default=[])
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    root = pathlib.Path(args.root).expanduser().resolve()
    state_dir = pathlib.Path(args.state_dir).expanduser()
    if not state_dir.is_absolute():
        state_dir = root / state_dir
    state_dir = state_dir.resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    focus = args.focus or ["lib", "src"]
    ctx = {"root": root, "state_dir": state_dir, "focus": focus, "force": args.force}
    run = DagExecutor(build_tasks(), ctx).run()
    print(f"lite_dag: {run['status']}")
    print(f"dashboard: {state_dir / 'dashboard.html'}")
    return 0 if run["status"] == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())

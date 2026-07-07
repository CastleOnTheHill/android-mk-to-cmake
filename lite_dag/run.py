#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import pathlib
import re
import shutil
import subprocess
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable


SOURCE_EXTS = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".rc"}
TARGET_CONTAINERS = ["lib_LTLIBRARIES", "noinst_LTLIBRARIES", "bin_PROGRAMS", "noinst_PROGRAMS"]
GENERIC_TARGET_VARS = {"TARGET", "TARGET_NAME", "MODULE", "MODULE_NAME", "LOCAL_MODULE", "PACKAGE", "PACKAGE_NAME"}
SOURCE_VAR_SUFFIXES = ("_SOURCES", "_HEADERS", "_HHEADERS")
CPPFLAGS_VAR_SUFFIXES = ("_CPPFLAGS", "_INCLUDES", "_INCLUDE_DIRS")
COMPILE_OPTION_VAR_SUFFIXES = ("_CFLAGS", "_CXXFLAGS", "_ASFLAGS")
LINK_VAR_SUFFIXES = ("_LDADD", "_LIBADD", "_LDFLAGS", "_LDLIBS")
GENERIC_SOURCE_VARS = {"SOURCES", "SRCS", "CSOURCES", "CXXSOURCES", "HEADERS", "HDRS", "HHEADERS"}
GENERIC_CPPFLAGS_VARS = {"CPPFLAGS", "AM_CPPFLAGS", "INCLUDES", "INCLUDE_DIRS"}
GENERIC_COMPILE_OPTION_VARS = {"CFLAGS", "CXXFLAGS", "ASFLAGS", "AM_CFLAGS", "AM_CXXFLAGS"}
GENERIC_LINK_VARS = {"LDADD", "LIBADD", "LDFLAGS", "LDLIBS"}
TARGET_OPERATION_SUFFIXES = (
    (SOURCE_VAR_SUFFIXES, "sources"),
    (CPPFLAGS_VAR_SUFFIXES, "cppflags"),
    (COMPILE_OPTION_VAR_SUFFIXES, "compile_options"),
    (LINK_VAR_SUFFIXES, "link_items"),
)
GENERIC_OPERATION_VARS = (
    (GENERIC_SOURCE_VARS, "sources", "current"),
    (GENERIC_CPPFLAGS_VARS, "cppflags", "all_if_am"),
    (GENERIC_COMPILE_OPTION_VARS, "compile_options", "current"),
    (GENERIC_LINK_VARS, "link_items", "current"),
)
TARGET_SUMMARY_KINDS = ("sources", "generated_sources", "include_dirs", "compile_definitions", "compile_options", "link_items")
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


def resolve_project_path(root: pathlib.Path, value: str) -> pathlib.Path:
    path = pathlib.Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def merge_variables(*sources: dict[str, list[str]]) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}
    for source in sources:
        for key, values in source.items():
            merged[key] = list(values)
    return merged


def default_make_variables(root: pathlib.Path) -> dict[str, list[str]]:
    root_text = root.as_posix()
    return {
        "TOPDIR": [root_text],
        "CURDIR": [root_text],
        "top_srcdir": [root_text],
        "top_builddir": [root_text],
    }


def expand_make_text(text: str, variables: dict[str, list[str]]) -> tuple[str, list[str]]:
    unresolved: list[str] = []

    def replace(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        values = variables.get(name)
        if values is None:
            unresolved.append(name)
            return match.group(0)
        if not values:
            return ""
        return " ".join(values)

    expanded = re.sub(r"\$\(([^)]+)\)|\$\{([^}]+)\}", replace, text)
    return expanded, unresolved


def variable_truthy(values: list[str] | None) -> bool:
    if not values:
        return False
    text = " ".join(values).strip().strip('"').strip("'").lower()
    return text not in {"", "0", "n", "no", "false", "off", "not_set"}


def update_variable(variables: dict[str, list[str]], variable: str, operator: str, values: list[str]) -> None:
    expanded = []
    for value in values:
        text, unresolved = expand_make_text(value, variables)
        expanded.extend(split_words(text) if not unresolved else [value])
    if operator == "+=":
        variables.setdefault(variable, []).extend(expanded)
    elif operator == "?=":
        if variable not in variables or not variables[variable]:
            variables[variable] = list(expanded)
    else:
        variables[variable] = list(expanded)


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


def ordered_unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def target_definition_kind(variable: str, values: list[str]) -> str | None:
    if variable in TARGET_CONTAINERS and values:
        return "automake"
    if variable in GENERIC_TARGET_VARS and values:
        return "generic"
    return None


def package_role_name(has_target_definition: bool, has_condition_or_include: bool, has_assignments: bool) -> str:
    if has_target_definition:
        return "target_definition"
    if has_condition_or_include:
        return "judgment_package"
    if has_assignments:
        return "variable_fragment"
    return "empty"


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
    seq: int
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
    package_kind: str = "empty"
    file_roles: list[dict[str, Any]] = field(default_factory=list)
    assignments: list[Assignment] = field(default_factory=list)
    included_files: list[str] = field(default_factory=list)
    include_edges: list[dict[str, Any]] = field(default_factory=list)
    variables: dict[str, list[str]] = field(default_factory=dict)
    targets: list[dict[str, Any]] = field(default_factory=list)
    unknown: list[dict[str, Any]] = field(default_factory=list)
    conditions: list[dict[str, Any]] = field(default_factory=list)


class MakefileParser:
    assign_re = re.compile(r"^(?P<var>[A-Za-z0-9_.$()/{}+-]+)\s*(?P<op>\+=|:=|\?=|=)\s*(?P<value>.*)$")
    include_re = re.compile(r"^(?:-?include|sinclude)\s+(?P<path>.+)$")

    def __init__(self, root: pathlib.Path, initial_variables: dict[str, list[str]] | None = None):
        self.root = root.resolve()
        self.initial_variables = merge_variables(default_make_variables(self.root), initial_variables or {})
        self._sequence = 0

    def parse(self, rel_path: str) -> MakefileIR:
        self._sequence = 0
        path = self.root / rel_path
        ir = MakefileIR(path=rel_path, directory=path.parent.relative_to(self.root).as_posix())
        variables = merge_variables(self.initial_variables)
        self._parse_into(ir, path, [], variables)
        ir.variables = self._build_variables(ir.assignments, self.initial_variables)
        ir.targets = self._build_targets(ir)
        ir.file_roles = self._classify_files(ir)
        entry_role = next((item["role"] for item in ir.file_roles if item["file"] == ir.path), "")
        ir.package_kind = entry_role or package_role_name(bool(ir.targets), bool(ir.conditions or ir.include_edges), bool(ir.assignments))
        return ir

    def _next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    def _unknown(self, path: pathlib.Path, row: dict[str, Any] | None, seq: int, reason: str, **extra: Any) -> dict[str, Any]:
        item: dict[str, Any] = {"file": rel(path, self.root), "seq": seq, "reason": reason}
        if row:
            item.update({"line": row["line"], "end_line": row["end_line"], "raw": row["raw"], "text": row["text"]})
        item.update(extra)
        return item

    def _parse_into(self, ir: MakefileIR, path: pathlib.Path, include_stack: list[pathlib.Path], variables: dict[str, list[str]] | None = None) -> None:
        if variables is None:
            variables = merge_variables(self.initial_variables)
        if path in include_stack:
            ir.unknown.append(self._unknown(path, None, self._next_sequence(), "include_cycle"))
            return
        include_stack = [*include_stack, path]
        condition_stack: list[dict[str, Any]] = []
        for row in logical_lines(read_text(path)):
            text = strip_comment(row["text"]).strip()
            if not text:
                continue
            seq = self._next_sequence()
            condition = self._parse_condition(text, variables)
            if condition:
                condition["file"] = rel(path, self.root)
                condition["line"] = row["line"]
                condition["seq"] = seq
                ir.conditions.append(condition)
                condition_stack.append(
                    {
                        "raw": text,
                        "expr": condition["expr"],
                        "switch": condition["switch"],
                        "active": condition["active"],
                        "negated": condition["negated"],
                    }
                )
                continue
            if text.startswith("if ") or re.match(r"^if[A-Z0-9_]+\b", text):
                expr = text[3:].strip() if text.startswith("if ") else text[2:].strip()
                switch = condition_switch(expr)
                active = self._evaluate_symbol_condition(expr, variables)
                ir.conditions.append({"file": rel(path, self.root), "line": row["line"], "seq": seq, "raw": text, "expr": expr, "switch": switch, "active": active, "negated": False})
                condition_stack.append({"raw": text, "expr": expr, "switch": switch, "active": active, "negated": False})
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
                    ir.unknown.append(self._unknown(path, row, seq, "else_without_if"))
                continue
            if text == "endif":
                if condition_stack:
                    condition_stack.pop()
                else:
                    ir.unknown.append(self._unknown(path, row, seq, "endif_without_if"))
                continue
            include = self.include_re.match(text)
            if include:
                active = all(bool(item["active"]) for item in condition_stack)
                if not active:
                    continue
                include_value = include.group("path").strip().strip('"')
                include_paths, unresolved = self._resolve_include_paths(path, include_value, variables)
                if unresolved:
                    ir.unknown.append(self._unknown(path, row, seq, "dynamic_include", expr=include_value, unresolved_variables=unresolved))
                    continue
                if not include_paths:
                    ir.unknown.append(self._unknown(path, row, seq, "empty_include", expr=include_value))
                    continue
                for include_path in include_paths:
                    resolved = rel(include_path, self.root)
                    if include_path.exists():
                        append_unique(ir.included_files, resolved)
                        ir.include_edges.append({"from": rel(path, self.root), "to": resolved, "line": row["line"], "expr": include_value, "resolved": resolved})
                        self._parse_into(ir, include_path, include_stack, variables)
                    else:
                        ir.unknown.append(self._unknown(path, row, seq, "missing_include", expr=include_value, resolved=resolved))
                continue
            match = self.assign_re.match(text)
            if not match:
                if ":" in text or text.startswith("\t"):
                    ir.unknown.append(self._unknown(path, row, seq, "recipe_or_rule", text=text[:120]))
                continue
            active = all(bool(item["active"]) for item in condition_stack)
            assignment = Assignment(
                file=rel(path, self.root),
                line=row["line"],
                seq=seq,
                variable=match.group("var"),
                operator=match.group("op"),
                values=split_words(match.group("value")),
                raw=row["raw"],
                conditions=[item["raw"] for item in condition_stack],
                condition_switches=[item["switch"] for item in condition_stack],
                active=active,
            )
            ir.assignments.append(assignment)
            if active:
                update_variable(variables, assignment.variable, assignment.operator, assignment.values)
            continue

    def _parse_condition(self, text: str, variables: dict[str, list[str]]) -> dict[str, Any] | None:
        if text.startswith("ifeq") or text.startswith("ifneq"):
            keyword, _, body = text.partition(" ")
            left, right = self._condition_args(body.strip())
            left_value = expand_make_text(left, variables)[0].strip()
            right_value = expand_make_text(right, variables)[0].strip()
            active = left_value == right_value
            if keyword == "ifneq":
                active = not active
            switch = self._first_variable_ref(left) or self._first_variable_ref(right) or condition_switch(left)
            return {"raw": text, "expr": body.strip(), "switch": switch, "active": active, "negated": keyword == "ifneq", "left": left_value, "right": right_value}
        if text.startswith("ifdef ") or text.startswith("ifndef "):
            keyword, _, name = text.partition(" ")
            name = name.strip()
            active = variable_truthy(variables.get(name))
            if keyword == "ifndef":
                active = not active
            return {"raw": text, "expr": name, "switch": name, "active": active, "negated": keyword == "ifndef"}
        return None

    def _condition_args(self, body: str) -> tuple[str, str]:
        body = body.strip()
        if body.startswith("(") and body.endswith(")"):
            body = body[1:-1]
            depth = 0
            for index, char in enumerate(body):
                if char in "({":
                    depth += 1
                elif char in ")}" and depth:
                    depth -= 1
                elif char == "," and depth == 0:
                    return strip_quotes(body[:index].strip()), strip_quotes(body[index + 1 :].strip())
        parts = split_words(body)
        if len(parts) >= 2:
            return strip_quotes(parts[0]), strip_quotes(parts[1])
        return body, ""

    def _first_variable_ref(self, text: str) -> str:
        match = re.search(r"\$\(([^)]+)\)|\$\{([^}]+)\}", text)
        return (match.group(1) or match.group(2)) if match else ""

    def _evaluate_symbol_condition(self, expr: str, variables: dict[str, list[str]]) -> bool:
        text = expr.strip()
        negated = text.startswith("not ")
        if negated:
            text = text[4:].strip()
        token = text.split(" ", 1)[0]
        active = variable_truthy(variables.get(token)) if token in variables else condition_default(expr)
        return not active if negated else active

    def _resolve_include_paths(self, source_file: pathlib.Path, include_value: str, variables: dict[str, list[str]]) -> tuple[list[pathlib.Path], list[str]]:
        expanded, unresolved = expand_make_text(include_value, variables)
        if unresolved:
            return [], sorted(set(unresolved))
        paths = []
        for item in split_words(expanded):
            candidate = pathlib.Path(strip_quotes(item))
            if not candidate.is_absolute():
                candidate = source_file.parent / candidate
            paths.append(candidate.resolve())
        return paths, []

    def _build_variables(self, assignments: list[Assignment], initial_variables: dict[str, list[str]] | None = None) -> dict[str, list[str]]:
        variables: dict[str, list[str]] = merge_variables(initial_variables or {})
        for item in assignments:
            if not item.active:
                continue
            update_variable(variables, item.variable, item.operator, item.values)
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
        targets: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        for container in TARGET_CONTAINERS:
            for raw_target in ir.variables.get(container, []):
                if raw_target.startswith("$("):
                    continue
                target_var = automake_target_var(raw_target)
                name = cmake_target_name(raw_target)
                if name in seen_names:
                    continue
                seen_names.add(name)
                targets.append(self._new_target(ir, name, raw_target, target_var, target_kind(container), container, "automake", self._target_sequence(ir, container, raw_target, target_var)))
        for assignment in ir.assignments:
            if not assignment.active or not target_definition_kind(assignment.variable, assignment.values) == "generic":
                continue
            for raw_target in self.expand_values(assignment.values, ir.variables):
                if "$" in raw_target or "@" in raw_target:
                    continue
                name = cmake_target_name(raw_target)
                if name in seen_names:
                    continue
                seen_names.add(name)
                targets.append(self._new_target(ir, name, raw_target, automake_target_var(raw_target), self._generic_target_kind(ir.variables), assignment.variable, "generic", assignment.seq, assignment.file))
        self._attach_target_operations(ir, targets)
        for target in targets:
            self._summarize_target_operations(target)
        return targets

    def _new_target(self, ir: MakefileIR, name: str, raw_name: str, target_var: str, kind: str, container: str, definition_style: str, seq: int, definition_file: str | None = None) -> dict[str, Any]:
        return {
            "name": name,
            "raw_name": raw_name,
            "kind": kind,
            "container": container,
            "definition_style": definition_style,
            "target_var": target_var,
            "directory": ir.directory,
            "definition_file": definition_file or ir.path,
            "seq": seq,
            "sources": [],
            "generated_sources": [],
            "include_dirs": [],
            "compile_definitions": [],
            "compile_options": [],
            "link_items": [],
            "operations": [],
        }

    def _generic_target_kind(self, variables: dict[str, list[str]]) -> str:
        text = " ".join(variables.get("TARGET_TYPE", []) + variables.get("LOCAL_MODULE_CLASS", [])).lower()
        if any(token in text for token in ["executable", "program", "bin"]):
            return "executable"
        return "library"

    def _attach_target_operations(self, ir: MakefileIR, targets: list[dict[str, Any]]) -> None:
        for assignment in ir.assignments:
            if not assignment.active:
                continue
            for target, operation_kind in self._targets_for_assignment(assignment, targets):
                operations = self._operations_for_assignment(ir, target, assignment, operation_kind)
                target["operations"].extend(operations)

    def _targets_for_assignment(self, assignment: Assignment, targets: list[dict[str, Any]]) -> list[tuple[dict[str, Any], str]]:
        variable = assignment.variable
        specific: list[tuple[dict[str, Any], str]] = []
        for target in targets:
            kind = self._target_specific_operation_kind(variable, target)
            if kind:
                specific.append((target, kind))
        if specific:
            return specific
        for variables, kind, scope in GENERIC_OPERATION_VARS:
            if variable not in variables:
                continue
            if scope == "all_if_am" and variable.startswith("AM_"):
                return [(target, kind) for target in targets]
            current = self._current_target_for_seq(targets, assignment.seq)
            return [(current, kind)] if current else []
        return []

    def _target_specific_operation_kind(self, variable: str, target: dict[str, Any]) -> str | None:
        target_var = target["target_var"]
        normalized = variable.removeprefix("nodist_")
        if not normalized.startswith(f"{target_var}_"):
            return None
        for suffixes, kind in TARGET_OPERATION_SUFFIXES:
            if normalized.endswith(suffixes):
                return kind
        return None

    def _current_target_for_seq(self, targets: list[dict[str, Any]], seq: int) -> dict[str, Any] | None:
        candidates = [target for target in targets if int(target.get("seq", 0)) <= seq]
        if not candidates:
            return None
        return sorted(candidates, key=lambda target: int(target.get("seq", 0)))[-1]

    def _operations_for_assignment(self, ir: MakefileIR, target: dict[str, Any], assignment: Assignment, operation_kind: str) -> list[dict[str, Any]]:
        base = {
            "seq": assignment.seq,
            "file": assignment.file,
            "line": assignment.line,
            "variable": assignment.variable,
            "conditions": assignment.conditions,
            "condition_switches": assignment.condition_switches,
        }
        if operation_kind == "sources":
            sources, generated_sources = self._source_values_for_assignment(ir, target, assignment)
            operations = []
            if sources:
                operations.append({**base, "kind": "sources", "values": sources})
            if generated_sources:
                operations.append({**base, "kind": "generated_sources", "values": generated_sources})
            return operations
        if operation_kind == "cppflags":
            include_dirs, definitions, compile_options = self._split_cppflags(ir, assignment)
            operations = []
            if include_dirs:
                operations.append({**base, "kind": "include_dirs", "values": include_dirs})
            if definitions:
                operations.append({**base, "kind": "compile_definitions", "values": definitions})
            if compile_options:
                operations.append({**base, "kind": "compile_options", "values": compile_options})
            return operations
        if operation_kind == "compile_options":
            values = [value for value in self.expand_values(assignment.values, ir.variables) if "$" not in value and "@" not in value]
            return [{**base, "kind": "compile_options", "values": values}] if values else []
        if operation_kind == "link_items":
            values = self.expand_values(assignment.values, ir.variables)
            return [{**base, "kind": "link_items", "values": values}] if values else []
        return []

    def _source_values_for_assignment(self, ir: MakefileIR, target: dict[str, Any], assignment: Assignment) -> tuple[list[str], list[str]]:
        generated_refs: set[str] = set()
        source_values = self.expand_values(assignment.values, ir.variables, generated_refs=generated_refs)
        generated_values: set[str] = set()
        for ref in generated_refs:
            generated_values.update(ir.variables.get(ref, []))
        for variable, values in ir.variables.items():
            if variable.lower().endswith("_gen"):
                generated_values.update(values)
        sources = ordered_unique(
            [
                normalize_source(target["directory"], value)
                for value in source_values
                if pathlib.PurePosixPath(value.strip('"')).suffix in SOURCE_EXTS
                and "$" not in value
                and "@" not in value
                and value not in generated_values
            ]
        )
        generated_sources = ordered_unique(
            [
                normalize_source(target["directory"], value)
                for value in generated_values
                if pathlib.PurePosixPath(value.strip('"')).suffix in SOURCE_EXTS and "$" not in value and "@" not in value
            ]
        )
        return sources, generated_sources

    def _split_cppflags(self, ir: MakefileIR, assignment: Assignment) -> tuple[list[str], list[str], list[str]]:
        include_dirs: list[str] = []
        definitions: list[str] = []
        compile_options: list[str] = []
        for flag in self.expand_values(assignment.values, ir.variables):
            if flag.startswith("-I"):
                include_dirs.append(self._normalize_include(pathlib.PurePosixPath(assignment.file).parent.as_posix(), flag[2:]))
            elif flag.startswith("-D"):
                definitions.append(flag[2:])
            elif "$" not in flag and "@" not in flag:
                compile_options.append(flag)
        return ordered_unique(include_dirs), ordered_unique(definitions), ordered_unique(compile_options)

    def _summarize_target_operations(self, target: dict[str, Any]) -> None:
        target["operations"] = sorted(target["operations"], key=lambda item: int(item.get("seq", 0)))
        for kind in TARGET_SUMMARY_KINDS:
            target[kind] = ordered_unique([value for op in target["operations"] if op["kind"] == kind for value in op["values"]])

    def _classify_files(self, ir: MakefileIR) -> list[dict[str, Any]]:
        files = {ir.path}
        assignment_files: set[str] = set()
        condition_or_include_files: set[str] = set()
        target_definition_files = {target.get("definition_file", ir.path) for target in ir.targets}
        for assignment in ir.assignments:
            files.add(assignment.file)
            assignment_files.add(assignment.file)
            if target_definition_kind(assignment.variable, assignment.values):
                target_definition_files.add(assignment.file)
        for condition in ir.conditions:
            file = condition.get("file", ir.path)
            files.add(file)
            condition_or_include_files.add(file)
        for edge in ir.include_edges:
            files.add(edge.get("from", ir.path))
            files.add(edge.get("to", ir.path))
            condition_or_include_files.add(edge.get("from", ir.path))
        for target in ir.targets:
            for operation in target.get("operations", []):
                file = operation.get("file", ir.path)
                files.add(file)
                target_definition_files.add(file)
        for item in ir.unknown:
            files.add(item.get("file", ir.path))
            if item.get("reason") in {"dynamic_include", "missing_include", "empty_include", "include_cycle"}:
                condition_or_include_files.add(item.get("file", ir.path))
        return [
            {
                "file": file,
                "role": package_role_name(file in target_definition_files, file in condition_or_include_files, file in assignment_files),
            }
            for file in sorted(files)
        ]

    def _target_sequence(self, ir: MakefileIR, container: str, raw_target: str, target_var: str) -> int:
        container_seqs = [
            item.seq
            for item in ir.assignments
            if item.active
            and item.variable == container
            and raw_target in self.expand_values(item.values, ir.variables)
        ]
        if container_seqs:
            return min(container_seqs)
        related_vars = {
            container,
            f"{target_var}_SOURCES",
            f"nodist_{target_var}_SOURCES",
            "AM_CPPFLAGS",
            f"{target_var}_CPPFLAGS",
            f"{target_var}_LDADD",
        }
        related_seqs = [item.seq for item in ir.assignments if item.active and item.variable in related_vars]
        return min(related_seqs) if related_seqs else 0

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
    makefile_names = ctx.get("makefile_names") or ["Makefile.am"]
    makefiles = []
    cmake_files = []
    for item in focus:
        for name in makefile_names:
            mf = root / item / name
            if mf.exists():
                append_unique(makefiles, rel(mf, root))
        cm = root / item / "CMakeLists.txt"
        if cm.exists():
            cmake_files.append(rel(cm, root))
    for item in ctx.get("makefiles", []):
        path = resolve_project_path(root, item)
        if path.exists():
            append_unique(makefiles, rel(path, root))
    write_json(ctx["state_dir"] / "inputs.json", {"root": root.as_posix(), "makefiles": makefiles, "cmake_files": cmake_files})
    return {"status": "done", "makefiles": len(makefiles), "cmake_files": len(cmake_files), "outputs": ["inputs.json"]}


def parse_config_line(path: pathlib.Path, root: pathlib.Path, line_no: int, raw: str) -> dict[str, Any] | None:
    disabled = re.match(r"^#\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s+is\s+not\s+set\s*$", raw)
    if disabled:
        return {"name": disabled.group("name"), "value": "", "state": "disabled", "file": rel(path, root), "line": line_no}
    text = strip_comment(raw).strip()
    if not text:
        return None
    match = re.match(r"^(?:export\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*)$", text)
    if not match:
        return None
    value = strip_quotes(match.group("value").strip())
    state = "enabled" if value in {"y", "m"} else "set"
    return {"name": match.group("name"), "value": value, "state": state, "file": rel(path, root), "line": line_no}


def variables_from_config(config: dict[str, Any]) -> dict[str, list[str]]:
    values = config.get("values", {})
    return {name: [item.get("value", "")] for name, item in values.items()}


def parse_configs(ctx: dict[str, Any]) -> dict[str, Any]:
    root = ctx["root"]
    config_inputs = list(ctx.get("config_files", []))
    if not config_inputs:
        config_inputs = [name for name in [".config", ".euap_config"] if (root / name).exists()]
    files = []
    values: dict[str, dict[str, Any]] = {}
    for item in config_inputs:
        path = resolve_project_path(root, item)
        file_result: dict[str, Any] = {"path": rel(path, root), "exists": path.exists(), "values": {}, "enabled": [], "disabled": []}
        if path.exists():
            for line_no, raw in enumerate(read_text(path).splitlines(), start=1):
                row = parse_config_line(path, root, line_no, raw)
                if not row:
                    continue
                name = row["name"]
                file_result["values"][name] = {"value": row["value"], "state": row["state"], "line": line_no}
                if row["state"] == "disabled":
                    file_result["disabled"].append(name)
                elif row["state"] == "enabled":
                    file_result["enabled"].append(name)
                values[name] = row
        files.append(file_result)
    payload = {"schema_version": 1, "files": files, "values": values, "variables": variables_from_config({"values": values})}
    write_json(ctx["state_dir"] / "dot_config.json", payload)
    return {"status": "done", "files": len(files), "values": len(values), "outputs": ["dot_config.json"]}


def parse_project_variables(ctx: dict[str, Any]) -> dict[str, Any]:
    config = load_json(ctx["state_dir"] / "dot_config.json", {"variables": {}})
    parser = MakefileParser(ctx["root"], variables_from_config(config))
    files = []
    merged = merge_variables(default_make_variables(ctx["root"]), variables_from_config(config))
    for item in ctx.get("var_files", []):
        path = resolve_project_path(ctx["root"], item)
        file_result: dict[str, Any] = {"path": rel(path, ctx["root"]), "exists": path.exists(), "variables": {}, "included_files": [], "include_edges": [], "unknown": []}
        if path.exists():
            ir = parser.parse(rel(path, ctx["root"]))
            file_result.update(
                {
                    "variables": ir.variables,
                    "included_files": ir.included_files,
                    "include_edges": ir.include_edges,
                    "unknown": ir.unknown,
                }
            )
            merged = merge_variables(merged, ir.variables)
        files.append(file_result)
    payload = {"schema_version": 1, "files": files, "variables": merged}
    write_json(ctx["state_dir"] / "project_variables.json", payload)
    return {"status": "done", "files": len(files), "variables": len(merged), "outputs": ["project_variables.json"]}


def parse_makefiles(ctx: dict[str, Any]) -> dict[str, Any]:
    inputs = load_json(ctx["state_dir"] / "inputs.json", {"makefiles": []})
    config = load_json(ctx["state_dir"] / "dot_config.json", {"variables": {}})
    project_variables = load_json(ctx["state_dir"] / "project_variables.json", {"variables": {}})
    initial_variables = merge_variables(default_make_variables(ctx["root"]), variables_from_config(config), project_variables.get("variables", {}))
    parser = MakefileParser(ctx["root"], initial_variables)
    irs = []
    edges = []
    for mf in inputs["makefiles"]:
        ir = parser.parse(mf)
        edges.extend(ir.include_edges)
        irs.append(
            {
                "path": ir.path,
                "directory": ir.directory,
                "package_kind": ir.package_kind,
                "file_roles": ir.file_roles,
                "included_files": ir.included_files,
                "include_edges": ir.include_edges,
                "variables": ir.variables,
                "targets": ir.targets,
                "unknown": ir.unknown,
                "conditions": ir.conditions,
                "assignments": [item.__dict__ for item in ir.assignments],
            }
        )
    write_json(ctx["state_dir"] / "make_ir.json", {"schema_version": 1, "files": irs})
    write_json(ctx["state_dir"] / "mk_dependencies.json", {"schema_version": 1, "edges": edges})
    return {"status": "done", "files": len(irs), "targets": sum(len(item["targets"]) for item in irs), "include_edges": len(edges), "outputs": ["make_ir.json", "mk_dependencies.json"]}


def cmake_path_for_source(source_root_var: str, rel_source: str) -> str:
    return f"${{{source_root_var}}}/{rel_source}"


def render_target_declaration(target: dict[str, Any]) -> str:
    lines: list[str] = []
    name = target["name"]
    kind = target["kind"]
    add = "add_executable" if kind == "executable" else "add_library"
    lib_kind = "STATIC" if kind == "library" else ""
    lines.append(f"# target from {target.get('definition_file', target['directory'])}: {target['raw_name']}")
    lines.append(f"{add}({name}{(' ' + lib_kind) if lib_kind else ''})")
    return "\n".join(lines) + "\n"


def render_private_block(command: str, target: str, values: list[str], mapper: Callable[[str], str] | None = None) -> list[str]:
    mapper = mapper or (lambda value: value)
    return [f"{command}({target}", "    PRIVATE", *(f"        {mapper(value)}" for value in values), ")"]


def render_link_operation(name: str, values: list[str]) -> list[str]:
    link_targets = []
    comments = []
    for item in values:
        if item.endswith("/libcurl.la") or item == "libcurl.la":
            link_targets.append("libcurl")
        elif "$" in item or "@" in item:
            comments.append(item)
        else:
            link_targets.append(item)
    lines = [f"target_link_libraries({name} PRIVATE {' '.join(link_targets)})"] if link_targets else []
    lines.extend(f"# unresolved link item from Makefile.am: {item}" for item in comments)
    return lines


def render_target_operation(target: dict[str, Any], operation: dict[str, Any]) -> str:
    lines: list[str] = []
    name = target["name"]
    kind = operation["kind"]
    values = operation.get("values", [])
    lines.append(f"# from {operation.get('file', '?')}:{operation.get('line', '?')} {operation.get('variable', '')}")
    if operation.get("conditions"):
        lines.append(f"# active Make conditions: {'; '.join(operation['conditions'])}")
    if kind == "sources":
        lines.extend(render_private_block("target_sources", name, values, lambda value: cmake_path_for_source("MK_SOURCE_ROOT", value)))
    elif kind == "generated_sources":
        for value in values:
            lines.append(f"# generated source omitted from target_sources: {value}")
    elif kind == "include_dirs":
        lines.extend(render_private_block("target_include_directories", name, values, lambda value: cmake_path_for_source("MK_SOURCE_ROOT", value)))
    elif kind == "compile_definitions":
        lines.extend(render_private_block("target_compile_definitions", name, values))
    elif kind == "compile_options":
        lines.extend(render_private_block("target_compile_options", name, values))
    elif kind == "link_items":
        lines.extend(render_link_operation(name, values))
    return "\n".join(lines) + "\n"


def render_unknown_fragment(fragment: dict[str, Any]) -> str:
    reason = fragment.get("reason", "unknown")
    file = fragment.get("file", "?")
    line = fragment.get("line")
    location = f"{file}:{line}" if line else file
    lines = [f"# TODO(ai/manual): convert unknown Makefile fragment ({reason}) from {location}."]
    expr = fragment.get("expr")
    if expr:
        lines.append(f"# expr: {expr}")
    raw = fragment.get("raw") or fragment.get("text") or ""
    for raw_line in raw.splitlines():
        lines.append(f"#   {raw_line}")
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
    unknown_count = 0
    for item in data["files"]:
        if item["targets"] or item.get("unknown"):
            root_lines.append(f"add_subdirectory({item['directory']})")
        out = output_root / item["directory"] / "CMakeLists.txt"
        body = [
            "# Generated by lite_dag/run.py.",
            "# Review before using as production CMake.",
            f"# package kind: {item.get('package_kind', 'unknown')}",
            "",
        ]
        events: list[tuple[int, int, int, str, dict[str, Any]]] = []
        for index, target in enumerate(item["targets"]):
            target_seq = int(target.get("seq", 0))
            events.append((target_seq, 0, index, "target_declaration", target))
            for op_index, operation in enumerate(target.get("operations", [])):
                if operation.get("kind") == "generated_sources":
                    continue
                op_seq = max(int(operation.get("seq", 0)), target_seq)
                events.append((op_seq, 2, op_index, "target_operation", {"target": target, "operation": operation}))
        for index, fragment in enumerate(item.get("unknown", [])):
            unknown_count += 1
            events.append((int(fragment.get("seq", 0)), 1, index, "unknown", fragment))
        for _seq, _phase, _index, kind, payload in sorted(events, key=lambda event: (event[0], event[1], event[2])):
            if kind == "target_declaration":
                body.append(render_target_declaration(payload))
                manifest.append({"directory": item["directory"], **payload})
            elif kind == "target_operation":
                body.append(render_target_operation(payload["target"], payload["operation"]))
            else:
                body.append(render_unknown_fragment(payload))
        write_text(out, "\n".join(body).rstrip() + "\n")
    write_text(output_root / "CMakeLists.txt", "\n".join(root_lines).rstrip() + "\n")
    write_json(ctx["state_dir"] / "generated_manifest.json", {"schema_version": 1, "targets": manifest})
    return {"status": "done", "targets": len(manifest), "switches": len(switches), "unknown_comments": unknown_count, "outputs": ["generated", "generated_manifest.json"]}


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


def html_cell(value: Any, class_name: str = "") -> str:
    class_attr = f" class='{html.escape(class_name)}'" if class_name else ""
    return f"<td{class_attr}>{html.escape(str(value))}</td>"


def html_row(cells: list[Any]) -> str:
    rendered = []
    for cell in cells:
        if isinstance(cell, tuple):
            rendered.append(html_cell(cell[0], cell[1]))
        else:
            rendered.append(html_cell(cell))
    return f"<tr>{''.join(rendered)}</tr>"


def html_table(headers: list[str], rows: list[list[Any]]) -> str:
    head = "".join(f"<th>{html.escape(header)}</th>" for header in headers)
    body = "".join(html_row(row) for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def join_limited(values: list[str], limit: int) -> str:
    return ", ".join(values[:limit])


def render_dashboard(ctx: dict[str, Any]) -> dict[str, Any]:
    run = load_json(ctx["state_dir"] / "graph_run.json", {"tasks": []})
    dot_config = load_json(ctx["state_dir"] / "dot_config.json", {"files": []})
    deps = load_json(ctx["state_dir"] / "mk_dependencies.json", {"edges": []})
    make_ir = load_json(ctx["state_dir"] / "make_ir.json", {"files": []})
    comparison = load_json(ctx["state_dir"] / "comparison.json", {"targets": []})
    switches = load_json(ctx["state_dir"] / "config_switches.json", {"switches": []})
    task_rows = [
        [
            task.get("name", ""),
            (task.get("status", ""), task.get("status", "")),
            task.get("duration_sec", ""),
            json.dumps({k: v for k, v in task.items() if k != "traceback"}, ensure_ascii=False),
        ]
        for task in run.get("tasks", [])
    ]
    config_rows = [
        [
            item.get("path", ""),
            (item.get("exists", False), "done" if item.get("exists") else "failed"),
            len(item.get("values", {})),
            join_limited(item.get("enabled", []), 12),
            join_limited(item.get("disabled", []), 12),
        ]
        for item in dot_config.get("files", [])
    ]
    dep_rows = [[edge.get("from", ""), edge.get("to", ""), edge.get("line", ""), edge.get("expr", "")] for edge in deps.get("edges", [])]
    classification_rows = [
        [
            item.get("path", ""),
            item.get("package_kind", ""),
            len(item.get("targets", [])),
            sum(len(target.get("operations", [])) for target in item.get("targets", [])),
            ", ".join(f"{role.get('file')}={role.get('role')}" for role in item.get("file_roles", [])),
        ]
        for item in make_ir.get("files", [])
    ]
    comparison_rows = [
        [
            item["target"],
            (item["status"], item["status"]),
            item["generated_sources"],
            item["existing_sources"],
            join_limited(item["missing_from_generated"], 10),
            join_limited(item["extra_in_generated"], 10),
        ]
        for item in comparison.get("targets", [])
    ]
    switch_rows = [
        [
            item["switch"],
            item["scope"],
            (item["existing_status"], item["existing_status"]),
            (item["generated_status"], item["generated_status"]),
            ", ".join(item["existing_cmake_matches"]),
            ", ".join(item["aliases"]),
            item["conditional_assignment_uses"],
            join_limited(item["locations"] + item.get("configure_locations", []), 8),
        ]
        for item in switches.get("switches", [])
    ]
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
	{html_table(["Node", "Status", "Seconds", "Details"], task_rows)}
	<h2>Config Inputs</h2>
	{html_table(["Path", "Exists", "Values", "Enabled", "Disabled"], config_rows)}
	<h2>MK Include Dependencies</h2>
	{html_table(["From", "To", "Line", "Expression"], dep_rows)}
	<h2>Makefile Classification</h2>
	{html_table(["Entry", "Package Kind", "Targets", "Target Operations", "File Roles"], classification_rows)}
	<h2>curl Makefile vs Existing CMake Comparison</h2>
{html_table(["Target", "Status", "Generated sources", "Existing CMake sources", "Missing", "Extra"], comparison_rows)}
<h2>Configuration Switch Coverage</h2>
{html_table(["Makefile switch", "Scope", "Existing CMake", "Generated CMake", "Matched CMake symbols", "Known aliases", "Conditional assignments", "Locations"], switch_rows)}
</body>
</html>
"""
    write_text(ctx["state_dir"] / "dashboard.html", page)
    return {"status": "done", "outputs": ["dashboard.html"]}


def build_tasks() -> list[Task]:
    return [
        Task("discover", [], discover),
        Task("parse_configs", [], parse_configs),
        Task("parse_project_variables", ["parse_configs"], parse_project_variables),
        Task("parse_makefiles", ["discover", "parse_configs", "parse_project_variables"], parse_makefiles),
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
    parser.add_argument("--config-file", "--config", action="append", dest="config_files", default=[])
    parser.add_argument("--var-file", "--preload-mk", action="append", dest="var_files", default=[])
    parser.add_argument("--makefile", action="append", dest="makefiles", default=[])
    parser.add_argument("--makefile-name", action="append", dest="makefile_names", default=[])
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    root = pathlib.Path(args.root).expanduser().resolve()
    state_dir = pathlib.Path(args.state_dir).expanduser()
    if not state_dir.is_absolute():
        state_dir = root / state_dir
    state_dir = state_dir.resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    focus = args.focus or ["lib", "src"]
    ctx = {
        "root": root,
        "state_dir": state_dir,
        "focus": focus,
        "force": args.force,
        "config_files": args.config_files,
        "var_files": args.var_files,
        "makefiles": args.makefiles,
        "makefile_names": args.makefile_names or ["Makefile.am"],
    }
    run = DagExecutor(build_tasks(), ctx).run()
    print(f"lite_dag: {run['status']}")
    print(f"dashboard: {state_dir / 'dashboard.html'}")
    return 0 if run["status"] == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())

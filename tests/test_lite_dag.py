from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


TOOL_ROOT = pathlib.Path(__file__).resolve().parents[1]


def write_text(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class LiteDagTest(unittest.TestCase):
    def make_curl_like_fixture(self, root: pathlib.Path) -> None:
        write_text(
            root / "configure.ac",
            'AM_CONDITIONAL(DEBUGBUILD, test "$want_debug" = "yes")\n',
        )
        write_text(root / "lib" / "a.c", "int curl_a(void) { return 0; }\n")
        write_text(root / "lib" / "a.h", "#pragma once\n")
        write_text(root / "lib" / "generated.c", "int generated(void) { return 0; }\n")
        write_text(
            root / "lib" / "Makefile.inc",
            "\n".join(
                [
                    "CSOURCES = a.c",
                    "HHEADERS = a.h",
                    "libcurl_gen = generated.c",
                    "",
                ]
            ),
        )
        write_text(
            root / "lib" / "Makefile.am",
            "\n".join(
                [
                    "include Makefile.inc",
                    "include $(generated_include)",
                    "lib_LTLIBRARIES = libcurl.la",
                    "stamp-custom:",
                    "noinst_LTLIBRARIES = libcurlu.la",
                    "libcurl_la_SOURCES = $(CSOURCES) $(HHEADERS) $(libcurl_gen)",
                    "libcurlu_la_SOURCES = $(CSOURCES) $(HHEADERS)",
                    "if DEBUGBUILD",
                    "AM_CPPFLAGS += -DDEBUGBUILD",
                    "endif",
                    "",
                ]
            ),
        )
        write_text(
            root / "CMakeLists.txt",
            "\n".join(
                [
                    "cmake_minimum_required(VERSION 3.16)",
                    "project(fixture C)",
                    'option(ENABLE_DEBUG "debug" OFF)',
                    "add_subdirectory(lib)",
                    "",
                ]
            ),
        )
        write_text(
            root / "lib" / "CMakeLists.txt",
            "\n".join(
                [
                    "add_library(libcurl)",
                    "add_library(libcurlu)",
                    "",
                ]
            ),
        )

    def test_lite_dag_generates_dashboard_and_switch_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "fixture"
            state = pathlib.Path(tmp) / "state"
            self.make_curl_like_fixture(root)

            cp = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(TOOL_ROOT / "lite_dag" / "run.py"),
                    "--root",
                    str(root),
                    "--state-dir",
                    str(state),
                    "--focus",
                    "lib",
                    "--force",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(cp.returncode, 0, msg=f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}")

            graph_run = json.loads((state / "graph_run.json").read_text(encoding="utf-8"))
            self.assertEqual(graph_run["status"], "done")
            self.assertEqual(graph_run["tasks"][-1]["name"], "render_dashboard")
            self.assertTrue((state / "dashboard.html").exists())

            comparison = json.loads((state / "comparison.json").read_text(encoding="utf-8"))
            self.assertEqual({item["status"] for item in comparison["targets"]}, {"match"})
            self.assertEqual({item["target"] for item in comparison["targets"]}, {"lib:libcurl", "lib:libcurlu"})

            switches = json.loads((state / "config_switches.json").read_text(encoding="utf-8"))
            debug = next(item for item in switches["switches"] if item["switch"] == "DEBUGBUILD")
            self.assertEqual(debug["existing_status"], "matched")
            self.assertEqual(debug["generated_status"], "converted")
            self.assertIn("ENABLE_DEBUG", debug["existing_cmake_matches"])

            generated = (state / "generated" / "lib" / "CMakeLists.txt").read_text(encoding="utf-8")
            self.assertIn("${MK_SOURCE_ROOT}/lib/a.c", generated)
            self.assertIn("${MK_SOURCE_ROOT}/lib/a.h", generated)
            self.assertNotIn("generated.c", generated)

            self.assertIn("unknown Makefile fragment (dynamic_include) from lib/Makefile.am:2", generated)
            self.assertIn("#   include $(generated_include)", generated)
            self.assertIn("unknown Makefile fragment (recipe_or_rule) from lib/Makefile.am:4", generated)
            self.assertIn("#   stamp-custom:", generated)
            self.assertLess(generated.index("dynamic_include"), generated.index("add_library(libcurl STATIC)"))
            self.assertLess(generated.index("add_library(libcurl STATIC)"), generated.index("stamp-custom:"))
            self.assertLess(generated.index("stamp-custom:"), generated.index("add_library(libcurlu STATIC)"))

            convert_task = next(item for item in graph_run["tasks"] if item["name"] == "convert_makefiles")
            self.assertEqual(convert_task["unknown_comments"], 2)

    def test_config_and_preloaded_variables_resolve_mk_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "fixture"
            state = pathlib.Path(tmp) / "state"
            write_text(root / ".config", "CONFIG_PLATFORM=shared\n# CONFIG_DISABLED is not set\n")
            write_text(root / ".euap_config", 'EUAP_FLAVOR="feature"\n')
            write_text(root / "build" / "top.mk", f"TOPDIR := {root.as_posix()}\n")
            write_text(root / "app" / "main.c", "int app_main(void) { return 0; }\n")
            write_text(root / "shared" / "feature.c", "int feature(void) { return 0; }\n")
            write_text(root / "shared" / "feature.mk", "FEATURE_SOURCES = ../shared/feature.c\n")
            write_text(
                root / "rules" / "package.mk",
                "\n".join(
                    [
                        "ifeq ($(CONFIG_PLATFORM),shared)",
                        "SELECTED_PACKAGE = y",
                        "endif",
                        "",
                    ]
                ),
            )
            write_text(
                root / "app" / "Makefile.am",
                "\n".join(
                    [
                        "ifeq ($(CONFIG_PLATFORM),shared)",
                        "include $(TOPDIR)/$(CONFIG_PLATFORM)/$(EUAP_FLAVOR).mk",
                        "endif",
                        "lib_LTLIBRARIES = libapp.la",
                        "libapp_la_SOURCES = main.c $(FEATURE_SOURCES)",
                        "",
                    ]
                ),
            )

            cp = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(TOOL_ROOT / "lite_dag" / "run.py"),
                    "--root",
                    str(root),
                    "--state-dir",
                    str(state),
                    "--focus",
                    "app",
                    "--config-file",
                    ".config",
                    "--config-file",
                    ".euap_config",
                    "--var-file",
                    "build/top.mk",
                    "--makefile",
                    "rules/package.mk",
                    "--force",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(cp.returncode, 0, msg=f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}")

            dot_config = json.loads((state / "dot_config.json").read_text(encoding="utf-8"))
            self.assertEqual(dot_config["values"]["CONFIG_PLATFORM"]["value"], "shared")
            self.assertEqual(dot_config["values"]["EUAP_FLAVOR"]["value"], "feature")
            self.assertEqual(dot_config["values"]["CONFIG_DISABLED"]["state"], "disabled")

            project_variables = json.loads((state / "project_variables.json").read_text(encoding="utf-8"))
            self.assertEqual(project_variables["variables"]["TOPDIR"], [root.as_posix()])

            deps = json.loads((state / "mk_dependencies.json").read_text(encoding="utf-8"))
            self.assertEqual(len(deps["edges"]), 1)
            self.assertEqual(deps["edges"][0]["from"], "app/Makefile.am")
            self.assertEqual(deps["edges"][0]["to"], "shared/feature.mk")

            make_ir = json.loads((state / "make_ir.json").read_text(encoding="utf-8"))
            package_kinds = {item["path"]: item["package_kind"] for item in make_ir["files"]}
            self.assertEqual(package_kinds["app/Makefile.am"], "target_definition")
            self.assertEqual(package_kinds["rules/package.mk"], "judgment_package")

            app_ir = next(item for item in make_ir["files"] if item["path"] == "app/Makefile.am")
            self.assertIn("shared/feature.mk", app_ir["included_files"])
            self.assertFalse([item for item in app_ir["unknown"] if item["reason"] == "dynamic_include"])

            generated = (state / "generated" / "app" / "CMakeLists.txt").read_text(encoding="utf-8")
            self.assertIn("${MK_SOURCE_ROOT}/app/main.c", generated)
            self.assertIn("${MK_SOURCE_ROOT}/shared/feature.c", generated)

    def test_target_definition_package_keeps_conditional_target_operations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "fixture"
            state = pathlib.Path(tmp) / "state"
            write_text(root / ".config", "CONFIG_EXTRA=y\n")
            write_text(root / "pkg" / "base.c", "int base(void) { return 0; }\n")
            write_text(root / "pkg" / "extra.c", "int extra(void) { return 0; }\n")
            write_text(root / "pkg" / "extra.h", "#pragma once\n")
            write_text(
                root / "pkg" / "package.mk",
                "\n".join(
                    [
                        "lib_LTLIBRARIES = libpkg.la",
                        "libpkg_la_SOURCES = base.c",
                        "if CONFIG_EXTRA",
                        "libpkg_la_SOURCES += extra.c extra.h",
                        "libpkg_la_CPPFLAGS += -Iinclude -DEXTRA_FEATURE -Wall",
                        "endif",
                        "",
                    ]
                ),
            )

            cp = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(TOOL_ROOT / "lite_dag" / "run.py"),
                    "--root",
                    str(root),
                    "--state-dir",
                    str(state),
                    "--focus",
                    "pkg",
                    "--makefile-name",
                    "package.mk",
                    "--config-file",
                    ".config",
                    "--force",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(cp.returncode, 0, msg=f"STDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}")

            make_ir = json.loads((state / "make_ir.json").read_text(encoding="utf-8"))
            pkg_ir = make_ir["files"][0]
            self.assertEqual(pkg_ir["package_kind"], "target_definition")
            self.assertEqual(pkg_ir["file_roles"][0]["role"], "target_definition")
            target = pkg_ir["targets"][0]
            self.assertEqual(target["name"], "libpkg")
            operation_kinds = [operation["kind"] for operation in target["operations"]]
            self.assertEqual(operation_kinds, ["sources", "sources", "include_dirs", "compile_definitions", "compile_options"])

            generated = (state / "generated" / "pkg" / "CMakeLists.txt").read_text(encoding="utf-8")
            self.assertIn("# package kind: target_definition", generated)
            self.assertLess(generated.index("add_library(libpkg STATIC)"), generated.index("${MK_SOURCE_ROOT}/pkg/base.c"))
            self.assertLess(generated.index("${MK_SOURCE_ROOT}/pkg/base.c"), generated.index("${MK_SOURCE_ROOT}/pkg/extra.c"))
            self.assertLess(generated.index("${MK_SOURCE_ROOT}/pkg/extra.h"), generated.index("target_include_directories(libpkg"))
            self.assertIn("target_compile_definitions(libpkg", generated)
            self.assertIn("EXTRA_FEATURE", generated)
            self.assertIn("target_compile_options(libpkg", generated)
            self.assertIn("-Wall", generated)


if __name__ == "__main__":
    unittest.main()

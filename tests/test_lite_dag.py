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
                    "lib_LTLIBRARIES = libcurl.la",
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


if __name__ == "__main__":
    unittest.main()

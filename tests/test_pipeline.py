#!/usr/bin/env python3
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


TOOL_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT_DIR = TOOL_ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from ai_unknown import ai_unknown_stage, validate_and_normalize_result
from convert_ir import convert_ir_stage


def run_script(name: str, root: pathlib.Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", str(TOOL_ROOT / "scripts" / name), "--root", str(root), "--state-dir", "state", *extra],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


class PipelineTest(unittest.TestCase):
    def write_ai_failure_fixture(self, root: pathlib.Path) -> pathlib.Path:
        state = root / "state"
        (state / "unknown").mkdir(parents=True)
        (state / "files").mkdir(parents=True)
        unknown = {
            "schema_version": 1,
            "source_file": "device/example/Android.mk",
            "items": [
                {
                    "line": 7,
                    "end_line": 7,
                    "reason": "unparsed_statement",
                    "raw": "$(call custom-rule,foo)",
                    "condition_stack": [],
                }
            ],
        }
        ir = {
            "schema_version": 1,
            "source_file": "device/example/Android.mk",
            "source_sha256": "source",
            "file_type": "connector",
            "events": [],
            "targets": [],
            "unknown": unknown["items"],
        }
        (state / "unknown" / "abc.unknown.json").write_text(json.dumps(unknown), encoding="utf-8")
        (state / "files" / "abc.ir.json").write_text(json.dumps(ir), encoding="utf-8")
        return state

    def test_kconfig_local_layer_and_condition_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "config" / "product_a").mkdir(parents=True)
            (root / "config" / "product_b").mkdir(parents=True)
            (root / "config" / "product_a" / ".config").write_text(
                "CONFIG_NET=y\n# CONFIG_DEBUG is not set\n",
                encoding="utf-8",
            )
            (root / "config" / "product_b" / ".config").write_text(
                "CONFIG_NET=m\nCONFIG_DEBUG=y\n",
                encoding="utf-8",
            )
            (root / "device" / "example").mkdir(parents=True)
            (root / "device" / "example" / "Android.mk").write_text(
                "\n".join(
                    [
                        "include $(CLEAR_VARS)",
                        "LOCAL_LAYER := example",
                        "LOCAL_MODULE := libexample_sub_module",
                        "LOCAL_SRC_FILES := src/a.cpp",
                        "LOCAL_C_INCLUDES := include",
                        "LOCAL_CFLAGS := -DEXAMPLE_SUB_MODULE -Werror",
                        "ifeq ($(CONFIG_NET),y)",
                        "LOCAL_SRC_FILES += src/net.cpp",
                        "LOCAL_CFLAGS += -DENABLE_NET",
                        "else",
                        "LOCAL_CFLAGS += -DNO_NET",
                        "endif",
                        "include $(BUILD_STATIC_LIBRARY)",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            for name, extra in [
                ("parse_kconfig.py", ["--config-dir", "config"]),
                ("scan_mk.py", []),
                ("parse_include_graph.py", []),
                ("parse_mk.py", []),
                ("ask_model_for_unknown.py", []),
                ("convert_ir.py", []),
                ("render_cmake.py", []),
            ]:
                cp = run_script(name, root, *extra)
                self.assertEqual(cp.returncode, 0, msg=f"{name}\nSTDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}")

            products = json.loads((root / "state" / "products.json").read_text(encoding="utf-8"))
            self.assertIn("CONFIG_NET", products["symbols"])
            self.assertEqual(products["symbols"]["CONFIG_DEBUG"]["products"]["product_a"], "n")

            ir_files = list((root / "state" / "files").glob("*.ir.json"))
            self.assertEqual(len(ir_files), 1)
            ir = json.loads(ir_files[0].read_text(encoding="utf-8"))
            self.assertEqual(ir["file_type"], "target_definition")
            self.assertEqual(ir["targets"][0]["cmake_kind"], "object_library")
            self.assertEqual(ir["targets"][0]["cmake_target"], "example_sub_module")

            cmake = (root / "state" / "generated" / "device" / "example" / "CMakeLists.txt").read_text(encoding="utf-8")
            self.assertIn("add_library(example_sub_module OBJECT)", cmake)
            self.assertIn("if(CONFIG_NET)", cmake)
            self.assertIn("else()", cmake)
            self.assertIn("endif()", cmake)
            self.assertIn("target_link_libraries(example", cmake)
            self.assertIn("# from device/example/Android.mk:8", cmake)

            report = (root / "state" / "report.md").read_text(encoding="utf-8")
            self.assertIn("LOCAL_LAYER 静态库", report)
            self.assertIn("libexample_sub_module", report)

            cp = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(TOOL_ROOT / "scripts" / "run_all.py"),
                    "--root",
                    str(root),
                    "--state-dir",
                    "state",
                    "--config-dir",
                    "config",
                    "--skip-check",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(cp.returncode, 0, msg=f"run_all resume\nSTDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}")

    def test_ai_unknown_cache_and_canonical_fragment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            state = root / "state"
            (state / "unknown").mkdir(parents=True)
            unknown = {
                "schema_version": 1,
                "source_file": "device/example/Android.mk",
                "items": [
                    {
                        "line": 7,
                        "end_line": 7,
                        "reason": "unparsed_statement",
                        "raw": "$(call custom-rule,foo)",
                        "condition_stack": [],
                    }
                ],
            }
            (state / "unknown" / "abc.unknown.json").write_text(json.dumps(unknown), encoding="utf-8")
            calls = {"count": 0}

            def fake_runner(task, prompt, run_root):
                calls["count"] += 1
                return {
                    "schema_version": 1,
                    "status": "converted",
                    "confidence": "medium",
                    "cmake_fragment": "\n  # custom rule fallback  \n\n\n",
                    "ir_events": [],
                    "risks": [" review custom rule ", "review custom rule"],
                }

            result = ai_unknown_stage(root, provider="opencode", runner=fake_runner)
            self.assertEqual(result["tasks"], 1)
            self.assertEqual(result["converted"], 1)
            self.assertEqual(calls["count"], 1)
            result = ai_unknown_stage(root, provider="opencode", runner=fake_runner)
            self.assertEqual(result["cache_hits"], 1)
            self.assertEqual(calls["count"], 1)
            model = json.loads((state / "model_results" / "abc.model.json").read_text(encoding="utf-8"))
            fragment = model["tasks"][0]["result"]["cmake_fragment"]
            self.assertEqual(fragment, "  # custom rule fallback\n")

    def test_convert_ir_appends_only_accepted_ai_fragments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            state = root / "state"
            (state / "files").mkdir(parents=True)
            (state / "model_results").mkdir(parents=True)
            ir = {
                "schema_version": 1,
                "source_file": "device/example/Android.mk",
                "source_sha256": "source",
                "file_type": "connector",
                "events": [],
                "targets": [],
                "unknown": [],
            }
            (state / "files" / "abc.ir.json").write_text(json.dumps(ir), encoding="utf-8")
            model = {
                "schema_version": 2,
                "source_file": "device/example/Android.mk",
                "tasks": [
                    {
                        "task_id": "task-ok",
                        "line": 9,
                        "reason": "unparsed_statement",
                        "result": {
                            "status": "converted",
                            "confidence": "high",
                            "cmake_fragment": "message(STATUS \"custom\")\n",
                            "risks": [],
                        },
                    },
                    {
                        "task_id": "task-failed",
                        "line": 10,
                        "result": {"status": "failed", "cmake_fragment": "", "risks": ["no safe conversion"]},
                    },
                ],
            }
            (state / "model_results" / "abc.model.json").write_text(json.dumps(model), encoding="utf-8")

            result = convert_ir_stage(root)
            self.assertEqual(result["ai_fragments"], 1)
            cmake = (state / "generated" / "device" / "example" / "CMakeLists.txt").read_text(encoding="utf-8")
            self.assertIn("AI fallback fragments", cmake)
            self.assertIn("message(STATUS \"custom\")", cmake)
            self.assertNotIn("no safe conversion", cmake)

    def test_ai_result_rejects_markdown_fences(self) -> None:
        with self.assertRaises(ValueError):
            validate_and_normalize_result(
                {
                    "schema_version": 1,
                    "status": "converted",
                    "confidence": "high",
                    "cmake_fragment": "```cmake\nmessage(STATUS bad)\n```",
                    "ir_events": [],
                    "risks": [],
                }
            )

    def test_opencode_failures_do_not_break_conversion(self) -> None:
        cases = {
            "error": lambda task, prompt, run_root: (_ for _ in ()).throw(RuntimeError("opencode exited with 1")),
            "timeout": lambda task, prompt, run_root: (_ for _ in ()).throw(subprocess.TimeoutExpired(["opencode"], timeout=1)),
            "bad_result": lambda task, prompt, run_root: {
                "schema_version": 1,
                "status": "converted",
                "confidence": "high",
                "cmake_fragment": "```cmake\nmessage(STATUS bad)\n```",
                "ir_events": [],
                "risks": [],
            },
        }
        for name, runner in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = pathlib.Path(tmp)
                state = self.write_ai_failure_fixture(root)

                ai_result = ai_unknown_stage(root, provider="opencode", runner=runner)
                self.assertEqual(ai_result["status"], "done")
                self.assertEqual(ai_result["tasks"], 1)
                self.assertEqual(ai_result["converted"], 0)
                self.assertEqual(ai_result["failed"], 1)

                model = json.loads((state / "model_results" / "abc.model.json").read_text(encoding="utf-8"))
                task_result = model["tasks"][0]["result"]
                self.assertEqual(task_result["status"], "failed")
                self.assertEqual(task_result["cmake_fragment"], "")
                self.assertTrue(task_result["risks"])

                convert_result = convert_ir_stage(root)
                self.assertEqual(convert_result["status"], "done")
                self.assertEqual(convert_result["ai_fragments"], 0)
                cmake = (state / "generated" / "device" / "example" / "CMakeLists.txt").read_text(encoding="utf-8")
                self.assertIn("# connector from device/example/Android.mk", cmake)
                self.assertNotIn("AI fallback fragments", cmake)
                self.assertNotIn("message(STATUS bad)", cmake)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


TOOL_ROOT = pathlib.Path(__file__).resolve().parents[1]


def run_script(name: str, root: pathlib.Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", str(TOOL_ROOT / "scripts" / name), "--root", str(root), "--state-dir", "state", *extra],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


class PipelineTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()

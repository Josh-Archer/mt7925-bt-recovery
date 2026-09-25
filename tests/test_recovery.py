#!/usr/bin/env python3
"""
Automated tests for MT7925 Bluetooth recovery driver (issue #2 fix).

Validates:
1. C retry logic compiles and runs all test cases (verifying that failed firmware
   download does not spend the only retry and retry flag is kept until setup completes).
2. Source-level checks on btmtk.c and btmtk.h to verify that:
   - test_and_set_bit is NOT used on the firmware download failure path.
   - set_bit(BTMTK_FIRMWARE_DL_RETRY, ...) and btmtk_reset_sync(hdev) are called.
   - test_and_clear_bit(BTMTK_FIRMWARE_DL_RETRY, ...) clears the retry flag upon setup completion.
3. Clean compilation against the installed kernel headers.
"""

import os
import re
import subprocess
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class TestSourceIntegrity(unittest.TestCase):
    """Verifies btmtk.c source implementation matches issue requirements."""

    @classmethod
    def setUpClass(cls):
        btmtk_c_path = os.path.join(REPO_ROOT, "btmtk.c")
        with open(btmtk_c_path, "r", encoding="utf-8") as f:
            cls.btmtk_c = f.read()

        btmtk_h_path = os.path.join(REPO_ROOT, "btmtk.h")
        with open(btmtk_h_path, "r", encoding="utf-8") as f:
            cls.btmtk_h = f.read()

    def test_retry_flag_defined_in_header(self):
        self.assertIn(
            "BTMTK_FIRMWARE_DL_RETRY",
            self.btmtk_h,
            "BTMTK_FIRMWARE_DL_RETRY must be defined in btmtk.h enum",
        )

    def test_no_test_and_set_bit_on_fw_download_failure(self):
        """Ensure test_and_set_bit is not used to prematurely spend the only retry."""
        fw_setup_call = self.btmtk_c.find("btmtk_setup_firmware_79xx")
        self.assertNotEqual(fw_setup_call, -1, "btmtk_setup_firmware_79xx call should exist")

        block = self.btmtk_c[fw_setup_call : fw_setup_call + 400]
        self.assertNotIn(
            "test_and_set_bit(BTMTK_FIRMWARE_DL_RETRY",
            block,
            "Firmware download failure path must not use test_and_set_bit to spend the only retry",
        )

    def test_retry_bit_set_and_reset_called_on_mt7925_fw_failure(self):
        """Ensure MT7925 sets retry bit and invokes reset on firmware download failure."""
        pattern = re.compile(
            r"if\s*\(\s*dev_id\s*==\s*0x7925\s*\)\s*\{[^}]*set_bit\s*\(\s*BTMTK_FIRMWARE_DL_RETRY\s*,\s*&btmtk_data->flags\s*\)\s*;[^}]*btmtk_reset_sync\s*\(\s*hdev\s*\)\s*;",
            re.DOTALL,
        )
        self.assertRegex(
            self.btmtk_c,
            pattern,
            "MT7925 failure path must set BTMTK_FIRMWARE_DL_RETRY and call btmtk_reset_sync",
        )

    def test_retry_bit_cleared_on_setup_completion(self):
        """Ensure retry bit is cleared once setup completes after WMT func-ctrl."""
        self.assertIn(
            "test_and_clear_bit(BTMTK_FIRMWARE_DL_RETRY, &btmtk_data->flags);",
            self.btmtk_c,
            "Setup completion path must clear BTMTK_FIRMWARE_DL_RETRY",
        )


class TestCUnitTests(unittest.TestCase):
    """Builds and executes the C unit test runner."""

    def test_c_retry_logic(self):
        c_test_src = os.path.join(REPO_ROOT, "tests", "test_retry_logic.c")
        c_test_bin = os.path.join(REPO_ROOT, "tests", "test_retry_logic")

        build_cmd = ["gcc", "-Wall", "-Wextra", "-Werror", "-O2", c_test_src, "-o", c_test_bin]
        compile_res = subprocess.run(build_cmd, capture_output=True, text=True)
        self.assertEqual(
            compile_res.returncode,
            0,
            f"Failed to compile C unit tests:\n{compile_res.stderr}",
        )

        try:
            run_res = subprocess.run([c_test_bin], capture_output=True, text=True)
            self.assertEqual(
                run_res.returncode,
                0,
                f"C unit test failed:\n{run_res.stdout}\n{run_res.stderr}",
            )
        finally:
            if os.path.exists(c_test_bin):
                os.remove(c_test_bin)


class TestKernelModuleBuild(unittest.TestCase):
    """Verifies that the out-of-tree module builds cleanly against current kernel headers."""

    def test_module_compilation(self):
        kver = subprocess.check_output(["uname", "-r"], text=True).strip()
        kbuild_dir = f"/lib/modules/{kver}/build"

        if not os.path.isdir(kbuild_dir):
            self.skipTest(f"Kernel build headers not found at {kbuild_dir}")

        build_res = subprocess.run(
            ["make", "-C", kbuild_dir, f"M={REPO_ROOT}", "modules"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        self.assertEqual(
            build_res.returncode,
            0,
            f"Kernel module build failed:\nSTDOUT:\n{build_res.stdout}\nSTDERR:\n{build_res.stderr}",
        )

        ko_file = os.path.join(REPO_ROOT, "btmtk.ko")
        self.assertTrue(os.path.isfile(ko_file), "btmtk.ko was not produced")

        clean_res = subprocess.run(
            ["make", "-C", kbuild_dir, f"M={REPO_ROOT}", "clean"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        self.assertEqual(clean_res.returncode, 0, f"Clean failed:\n{clean_res.stderr}")


if __name__ == "__main__":
    unittest.main()

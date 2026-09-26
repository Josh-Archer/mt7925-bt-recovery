#!/usr/bin/env python3
"""
Automated tests for MT7925 Bluetooth recovery driver (issue #2 fix).

Validates:
1. C retry logic compiles and runs all test cases (verifying that repeated firmware
   download failures reset at most MAX times, then stop, and successful setup re-arms).
2. Source-level checks on btmtk.c and btmtk.h to verify that:
   - BTMTK_FW_DL_MAX_RETRIES is defined in btmtk.h.
   - fw_dl_retries counter is defined in struct btmtk_data.
   - Firmware download failure path bounds reset attempts by BTMTK_FW_DL_MAX_RETRIES.
   - Counter is incremented only when a reset is requested.
   - Clear log message is issued when retry limit is reached.
   - Setup completion resets the retry counter to 0 (re-arming recovery).
   - Old single-bit test_and_set_bit retry spending is eliminated.
3. Python behavioral simulation modeling bounded counter and re-arm logic.
4. Clean compilation against installed kernel headers without loading modules
   or touching the host's Bluetooth system.
"""

import os
import re
import subprocess
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class TestSourceIntegrity(unittest.TestCase):
    """Verifies btmtk.c and btmtk.h source implementation matches bounded recovery requirements."""

    @classmethod
    def setUpClass(cls):
        btmtk_c_path = os.path.join(REPO_ROOT, "btmtk.c")
        with open(btmtk_c_path, "r", encoding="utf-8") as f:
            cls.btmtk_c = f.read()

        btmtk_h_path = os.path.join(REPO_ROOT, "btmtk.h")
        with open(btmtk_h_path, "r", encoding="utf-8") as f:
            cls.btmtk_h = f.read()

    def test_max_retries_defined_in_header(self):
        """Ensure BTMTK_FW_DL_MAX_RETRIES is defined as a bounded integer in btmtk.h."""
        match = re.search(r"#define\s+BTMTK_FW_DL_MAX_RETRIES\s+(\d+)", self.btmtk_h)
        self.assertIsNotNone(match, "BTMTK_FW_DL_MAX_RETRIES must be defined in btmtk.h")
        self.assertEqual(int(match.group(1)), 3, "BTMTK_FW_DL_MAX_RETRIES should be 3")

    def test_counter_in_btmtk_data(self):
        """Ensure fw_dl_retries counter is a member of struct btmtk_data."""
        struct_match = re.search(
            r"struct\s+btmtk_data\s*\{([^}]+)\};", self.btmtk_h, re.DOTALL
        )
        self.assertIsNotNone(struct_match, "struct btmtk_data definition should exist in btmtk.h")
        struct_body = struct_match.group(1)
        self.assertIn(
            "fw_dl_retries",
            struct_body,
            "fw_dl_retries counter must be a field in struct btmtk_data",
        )

    def test_no_test_and_set_bit_on_fw_download_failure(self):
        """Ensure the old single-bit test_and_set_bit is not used on firmware download failure."""
        fw_setup_call = self.btmtk_c.find("btmtk_setup_firmware_79xx")
        self.assertNotEqual(fw_setup_call, -1, "btmtk_setup_firmware_79xx call should exist")

        block = self.btmtk_c[fw_setup_call : fw_setup_call + 600]
        self.assertNotIn(
            "test_and_set_bit",
            block,
            "Firmware download failure path must not use test_and_set_bit",
        )

    def test_fw_failure_bounded_reset(self):
        """Ensure MT7925 firmware download failure checks retry limit before reset."""
        pattern = re.compile(
            r"if\s*\(\s*dev_id\s*==\s*0x7925\s*\)\s*\{[^}]*fw_dl_retries\s*<\s*BTMTK_FW_DL_MAX_RETRIES[^}]*btmtk_reset_sync\s*\(\s*hdev\s*\)\s*;",
            re.DOTALL,
        )
        self.assertRegex(
            self.btmtk_c,
            pattern,
            "MT7925 failure path must guard btmtk_reset_sync with fw_dl_retries < BTMTK_FW_DL_MAX_RETRIES",
        )

    def test_fw_failure_increments_counter(self):
        """Ensure fw_dl_retries is incremented when requesting a reset."""
        pattern = re.compile(
            r"fw_dl_retries\s*\+\+|fw_dl_retries\s*\+=\s*1",
        )
        self.assertRegex(
            self.btmtk_c,
            pattern,
            "fw_dl_retries must be incremented when reset is requested",
        )

    def test_limit_reached_logging(self):
        """Ensure clear logging when firmware download retry limit is reached."""
        self.assertIn(
            "max retries",
            self.btmtk_c.lower(),
            "btmtk.c must log clearly when max retries is reached",
        )

    def test_counter_reset_on_setup_completion(self):
        """Ensure retry counter is reset to 0 once setup fully completes."""
        pattern = re.compile(
            r"btmtk_data->fw_dl_retries\s*=\s*0\s*;",
        )
        self.assertRegex(
            self.btmtk_c,
            pattern,
            "Setup completion path must reset btmtk_data->fw_dl_retries to 0",
        )


class TestBoundedRetryLogicModel(unittest.TestCase):
    """Python behavioral simulation of the bounded retry logic."""

    MAX_RETRIES = 3

    class BtkMtkDevice:
        def __init__(self, dev_id=0x7925):
            self.dev_id = dev_id
            self.fw_dl_retries = 0
            self.reset_count = 0

        def setup_fw_handler(self, fw_err):
            if fw_err < 0:
                if self.dev_id == 0x7925:
                    if self.fw_dl_retries < TestBoundedRetryLogicModel.MAX_RETRIES:
                        self.fw_dl_retries += 1
                        self.reset_count += 1
                    else:
                        pass  # Limit reached: no reset
                return fw_err
            return 0

        def setup_complete(self):
            self.fw_dl_retries = 0

    def test_repeated_failures_bounded_at_max(self):
        dev = self.BtkMtkDevice()
        for i in range(1, self.MAX_RETRIES + 1):
            ret = dev.setup_fw_handler(-110)
            self.assertEqual(ret, -110)
            self.assertEqual(dev.reset_count, i)
            self.assertEqual(dev.fw_dl_retries, i)

        # Additional failures after reaching max do not trigger further resets
        for _ in range(5):
            ret = dev.setup_fw_handler(-110)
            self.assertEqual(ret, -110)
            self.assertEqual(dev.reset_count, self.MAX_RETRIES)
            self.assertEqual(dev.fw_dl_retries, self.MAX_RETRIES)

    def test_setup_success_rearms_recovery(self):
        dev = self.BtkMtkDevice()
        # Fail twice
        dev.setup_fw_handler(-110)
        dev.setup_fw_handler(-110)
        self.assertEqual(dev.reset_count, 2)
        self.assertEqual(dev.fw_dl_retries, 2)

        # Setup succeeds
        self.assertEqual(dev.setup_fw_handler(0), 0)
        dev.setup_complete()
        self.assertEqual(dev.fw_dl_retries, 0)
        self.assertEqual(dev.reset_count, 2)

        # Recovery is re-armed: next failures trigger resets up to MAX
        for i in range(1, self.MAX_RETRIES + 1):
            dev.setup_fw_handler(-110)
            self.assertEqual(dev.reset_count, 2 + i)
            self.assertEqual(dev.fw_dl_retries, i)

        # Stops after MAX
        dev.setup_fw_handler(-110)
        self.assertEqual(dev.reset_count, 2 + self.MAX_RETRIES)

    def test_exhaustion_then_success_rearms(self):
        dev = self.BtkMtkDevice()
        for _ in range(self.MAX_RETRIES + 2):
            dev.setup_fw_handler(-110)
        self.assertEqual(dev.reset_count, self.MAX_RETRIES)

        dev.setup_fw_handler(0)
        dev.setup_complete()
        self.assertEqual(dev.fw_dl_retries, 0)

        dev.setup_fw_handler(-110)
        self.assertEqual(dev.reset_count, self.MAX_RETRIES + 1)
        self.assertEqual(dev.fw_dl_retries, 1)

    def test_other_devices_do_not_reset(self):
        dev = self.BtkMtkDevice(dev_id=0x7961)
        ret = dev.setup_fw_handler(-110)
        self.assertEqual(ret, -110)
        self.assertEqual(dev.reset_count, 0)
        self.assertEqual(dev.fw_dl_retries, 0)


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

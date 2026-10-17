#!/usr/bin/env python3
"""
Automated tests for MT7925 Bluetooth recovery driver (issues #2, #3, and #4).

Validates:
1. C retry logic compiles and runs all test cases (verifying that repeated firmware
   download failures and WMT timeouts reset at most MAX times, then stop, and successful
   setup re-arms).
2. Source-level checks on btmtk.c and btmtk.h to verify that:
   - BTMTK_FW_DL_MAX_RETRIES and BTMTK_WMT_MAX_RETRIES are defined in btmtk.h.
   - fw_dl_retries and wmt_retries counters are defined in struct btmtk_data.
   - Firmware download failure path bounds reset attempts by BTMTK_FW_DL_MAX_RETRIES.
   - WMT func-ctrl timeout path bounds reset attempts by BTMTK_WMT_MAX_RETRIES.
   - Counters are incremented only when a reset is requested.
   - Clear log messages are issued when retry limits are reached.
   - Setup completion resets both retry counters to 0 (re-arming recovery).
   - Old single-bit test_and_set_bit retry spending is eliminated.
   - <linux/unaligned.h> include is guarded with LINUX_VERSION_CODE for pre-6.12 kernels.
   - Preprocessor stub test proves asm/unaligned.h is selected for <6.12 and linux/unaligned.h for >=6.12.
   - kmalloc_obj is guarded for pre-7.0 kernels.
   - Compile-time #error guard enforces Linux 6.4+ floor (devcoredump API).
   - dkms.conf BUILD_EXCLUSIVE_KERNEL regex matches 6.4+ and rejects older kernels.
3. Python behavioral simulation modeling bounded counters and re-arm logic for both
   firmware download failures and WMT func-ctrl timeouts.
4. Clean compilation against installed kernel headers (and older [6.4, 6.12) kernels if present)
   without loading modules or touching the host's Bluetooth system.
"""

import lzma
import os
import re
import subprocess
import tempfile
import textwrap
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

        dkms_conf_path = os.path.join(REPO_ROOT, "dkms.conf")
        with open(dkms_conf_path, "r", encoding="utf-8") as f:
            cls.dkms_conf = f.read()

    def test_max_retries_defined_in_header(self):
        """Ensure BTMTK_FW_DL_MAX_RETRIES is defined as a bounded integer in btmtk.h."""
        match = re.search(r"#define\s+BTMTK_FW_DL_MAX_RETRIES\s+(\d+)", self.btmtk_h)
        self.assertIsNotNone(match, "BTMTK_FW_DL_MAX_RETRIES must be defined in btmtk.h")
        self.assertEqual(int(match.group(1)), 3, "BTMTK_FW_DL_MAX_RETRIES should be 3")

    def test_wmt_max_retries_defined_in_header(self):
        """Ensure BTMTK_WMT_MAX_RETRIES is defined as a bounded integer in btmtk.h."""
        match = re.search(r"#define\s+BTMTK_WMT_MAX_RETRIES\s+(\d+)", self.btmtk_h)
        self.assertIsNotNone(match, "BTMTK_WMT_MAX_RETRIES must be defined in btmtk.h")
        self.assertEqual(int(match.group(1)), 3, "BTMTK_WMT_MAX_RETRIES should be 3")

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

    def test_wmt_counter_in_btmtk_data(self):
        """Ensure wmt_retries counter is a member of struct btmtk_data."""
        struct_match = re.search(
            r"struct\s+btmtk_data\s*\{([^}]+)\};", self.btmtk_h, re.DOTALL
        )
        self.assertIsNotNone(struct_match, "struct btmtk_data definition should exist in btmtk.h")
        struct_body = struct_match.group(1)
        self.assertIn(
            "wmt_retries",
            struct_body,
            "wmt_retries counter must be a field in struct btmtk_data",
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

    def test_wmt_timeout_bounded_reset(self):
        """Ensure MT7925 WMT func-ctrl timeout checks retry limit before reset."""
        pattern = re.compile(
            r"if\s*\(\s*dev_id\s*==\s*0x7925\s*&&\s*err\s*==\s*-ETIMEDOUT\s*\)\s*\{[^}]*wmt_retries\s*<\s*BTMTK_WMT_MAX_RETRIES[^}]*btmtk_reset_sync\s*\(\s*hdev\s*\)\s*;",
            re.DOTALL,
        )
        self.assertRegex(
            self.btmtk_c,
            pattern,
            "MT7925 WMT func ctrl timeout path must guard btmtk_reset_sync with wmt_retries < BTMTK_WMT_MAX_RETRIES",
        )

    def test_wmt_timeout_increments_counter(self):
        """Ensure wmt_retries is incremented when requesting a reset on WMT func ctrl timeout."""
        pattern = re.compile(
            r"wmt_retries\s*\+\+|wmt_retries\s*\+=\s*1",
        )
        self.assertRegex(
            self.btmtk_c,
            pattern,
            "wmt_retries must be incremented when reset is requested on WMT timeout",
        )

    def test_limit_reached_logging(self):
        """Ensure clear logging when firmware download and WMT retry limits are reached."""
        self.assertIn(
            "firmware download failed: max retries",
            self.btmtk_c.lower(),
            "btmtk.c must log clearly when firmware download max retries is reached",
        )
        self.assertIn(
            "wmt func ctrl failed: max retries",
            self.btmtk_c.lower(),
            "btmtk.c must log clearly when WMT func ctrl max retries is reached",
        )

    def test_counter_reset_on_setup_completion(self):
        """Ensure retry counters are reset to 0 once setup fully completes."""
        pattern_fw = re.compile(
            r"btmtk_data->fw_dl_retries\s*=\s*0\s*;",
        )
        self.assertRegex(
            self.btmtk_c,
            pattern_fw,
            "Setup completion path must reset btmtk_data->fw_dl_retries to 0",
        )
        pattern_wmt = re.compile(
            r"btmtk_data->wmt_retries\s*=\s*0\s*;",
        )
        self.assertRegex(
            self.btmtk_c,
            pattern_wmt,
            "Setup completion path must reset btmtk_data->wmt_retries to 0",
        )

    def test_unaligned_include_guarded(self):
        """Ensure btmtk.c includes <linux/version.h> and guards unaligned.h for pre-6.12 kernels."""
        self.assertIn(
            "#include <linux/version.h>",
            self.btmtk_c,
            "btmtk.c must include <linux/version.h>",
        )

        pattern = re.compile(
            r"#if\s+LINUX_VERSION_CODE\s*>=\s*KERNEL_VERSION\s*\(\s*6\s*,\s*12\s*,\s*0\s*\)\s*"
            r"#\s*include\s*<linux/unaligned\.h>\s*"
            r"#\s*else\s*"
            r"#\s*include\s*<asm/unaligned\.h>\s*"
            r"#\s*endif",
            re.MULTILINE,
        )
        self.assertRegex(
            self.btmtk_c,
            pattern,
            "btmtk.c must guard <linux/unaligned.h> with LINUX_VERSION_CODE >= KERNEL_VERSION(6, 12, 0) and fallback to <asm/unaligned.h>",
        )

    def test_unaligned_preprocessor_branch_selection(self):
        """Prove the unaligned.h guard selects asm/ vs linux/ without needing older kernel headers."""
        match = re.search(
            r"(#if\s+LINUX_VERSION_CODE\s*>=\s*KERNEL_VERSION\s*\(\s*6\s*,\s*12\s*,\s*0\s*\)\s*"
            r"#\s*include\s*<linux/unaligned\.h>\s*"
            r"#\s*else\s*"
            r"#\s*include\s*<asm/unaligned\.h>\s*"
            r"#\s*endif)",
            self.btmtk_c,
            re.MULTILINE,
        )
        self.assertIsNotNone(match, "unaligned.h version guard block must be present in btmtk.c")
        guard_block = match.group(1)

        # KERNEL_VERSION(a,b,c) == ((a)<<16) + ((b)<<8) + (c)
        cases = [
            ("6.11.0", (6 << 16) + (11 << 8) + 0, "asm/unaligned.h", "linux/unaligned.h"),
            ("6.12.0", (6 << 16) + (12 << 8) + 0, "linux/unaligned.h", "asm/unaligned.h"),
        ]

        with tempfile.TemporaryDirectory(prefix="mt7925-unaligned-") as tmp:
            linux_dir = os.path.join(tmp, "linux")
            asm_dir = os.path.join(tmp, "asm")
            os.makedirs(linux_dir)
            os.makedirs(asm_dir)
            with open(os.path.join(linux_dir, "unaligned.h"), "w", encoding="utf-8") as f:
                f.write("int mt7925_unaligned_stub_linux;\n")
            with open(os.path.join(asm_dir, "unaligned.h"), "w", encoding="utf-8") as f:
                f.write("int mt7925_unaligned_stub_asm;\n")

            for label, version_code, expect_path, forbid_path in cases:
                with self.subTest(kernel=label, expect=expect_path):
                    src = os.path.join(tmp, f"probe_{label.replace('.', '_')}.c")
                    with open(src, "w", encoding="utf-8") as f:
                        f.write(
                            textwrap.dedent(
                                f"""\
                                #define LINUX_VERSION_CODE {version_code}
                                #define KERNEL_VERSION(a,b,c) (((a) << 16) + ((b) << 8) + (c))
                                {guard_block}
                                """
                            )
                        )
                    # Keep line markers so the chosen include path appears in stdout.
                    res = subprocess.run(
                        ["gcc", "-E", f"-I{tmp}", src],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(
                        res.returncode,
                        0,
                        f"gcc -E failed for simulated {label}:\n{res.stderr}",
                    )
                    self.assertIn(
                        expect_path,
                        res.stdout,
                        f"Simulated {label} must expand include of <{expect_path}>",
                    )
                    self.assertNotIn(
                        forbid_path,
                        res.stdout,
                        f"Simulated {label} must not expand include of <{forbid_path}>",
                    )
                    if expect_path.startswith("asm/"):
                        self.assertIn("mt7925_unaligned_stub_asm", res.stdout)
                        self.assertNotIn("mt7925_unaligned_stub_linux", res.stdout)
                    else:
                        self.assertIn("mt7925_unaligned_stub_linux", res.stdout)
                        self.assertNotIn("mt7925_unaligned_stub_asm", res.stdout)

    def test_kmalloc_obj_guarded(self):
        """Ensure kmalloc_obj is gated for Linux 7.0+ with kmalloc fallback on older kernels."""
        pattern = re.compile(
            r"#if\s+LINUX_VERSION_CODE\s*>=\s*KERNEL_VERSION\s*\(\s*7\s*,\s*0\s*,\s*0\s*\)\s*"
            r"dr\s*=\s*kmalloc_obj\s*\(\s*\*dr\s*\)\s*;\s*"
            r"#\s*else\s*"
            r"dr\s*=\s*kmalloc\s*\(\s*sizeof\s*\(\s*\*dr\s*\)\s*,\s*GFP_KERNEL\s*\)\s*;\s*"
            r"#\s*endif",
            re.MULTILINE,
        )
        self.assertRegex(
            self.btmtk_c,
            pattern,
            "btmtk.c must guard kmalloc_obj with LINUX_VERSION_CODE >= KERNEL_VERSION(7, 0, 0) "
            "and fall back to kmalloc(sizeof(*dr), GFP_KERNEL)",
        )

    def test_min_kernel_version_error_guard(self):
        """Ensure btmtk.c guards against kernels older than 6.4 with a compile-time #error."""
        pattern = re.compile(
            r"#if\s+LINUX_VERSION_CODE\s*<\s*KERNEL_VERSION\s*\(\s*6\s*,\s*4\s*,\s*0\s*\)\s*"
            r"#\s*error\s+\"mt7925-bt-recovery requires Linux 6\.4 or newer \(Bluetooth devcoredump API\)\"\s*"
            r"#\s*endif",
            re.MULTILINE,
        )
        self.assertRegex(
            self.btmtk_c,
            pattern,
            "btmtk.c must include compile-time guard for LINUX_VERSION_CODE < KERNEL_VERSION(6, 4, 0)",
        )

    def test_dkms_build_exclusive_kernel(self):
        """Ensure dkms.conf defines BUILD_EXCLUSIVE_KERNEL matching 6.4+ and rejecting older kernels."""
        match = re.search(r'BUILD_EXCLUSIVE_KERNEL=["\']([^"\']+)["\']', self.dkms_conf)
        self.assertIsNotNone(
            match, "BUILD_EXCLUSIVE_KERNEL must be defined in dkms.conf"
        )
        pattern_str = match.group(1)
        regex = re.compile(pattern_str)

        test_cases = [
            ("6.1.0-53-amd64", False),
            ("6.3.9", False),
            ("6.4.0", True),
            ("6.8.0-146-generic", True),
            ("6.12.1", True),
            ("6.17.0-42-generic", True),
            ("7.0.0-38-generic", True),
            ("10.1.0", True),
            ("5.15.0-76-generic", False),
            ("6.0.0", False),
            ("6.10.0", True),
            ("6.19.0", True),
        ]

        for ver_str, should_accept in test_cases:
            with self.subTest(version=ver_str, should_accept=should_accept):
                # re.search mimics bash [[ $kernelver =~ $BUILD_EXCLUSIVE_KERNEL ]]
                matched = bool(regex.search(ver_str))
                self.assertEqual(
                    matched,
                    should_accept,
                    f"BUILD_EXCLUSIVE_KERNEL regex '{pattern_str}' "
                    f"{'should accept' if should_accept else 'should reject'} '{ver_str}'",
                )


class TestBoundedRetryLogicModel(unittest.TestCase):
    """Python behavioral simulation of the bounded retry logic."""

    MAX_RETRIES = 3

    class BtkMtkDevice:
        def __init__(self, dev_id=0x7925):
            self.dev_id = dev_id
            self.fw_dl_retries = 0
            self.wmt_retries = 0
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

        def setup_wmt_handler(self, wmt_err):
            if wmt_err < 0:
                if self.dev_id == 0x7925 and wmt_err == -110:
                    if self.wmt_retries < TestBoundedRetryLogicModel.MAX_RETRIES:
                        self.wmt_retries += 1
                        self.reset_count += 1
                    else:
                        pass  # Limit reached: no reset
                return wmt_err
            return 0

        def setup_complete(self):
            self.fw_dl_retries = 0
            self.wmt_retries = 0

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

    def test_wmt_repeated_timeouts_bounded_at_max(self):
        dev = self.BtkMtkDevice()
        for i in range(1, self.MAX_RETRIES + 1):
            ret = dev.setup_wmt_handler(-110)
            self.assertEqual(ret, -110)
            self.assertEqual(dev.reset_count, i)
            self.assertEqual(dev.wmt_retries, i)

        # Additional timeouts after reaching max do not trigger further resets
        for _ in range(5):
            ret = dev.setup_wmt_handler(-110)
            self.assertEqual(ret, -110)
            self.assertEqual(dev.reset_count, self.MAX_RETRIES)
            self.assertEqual(dev.wmt_retries, self.MAX_RETRIES)

    def test_wmt_non_timeout_does_not_reset(self):
        dev = self.BtkMtkDevice()
        ret = dev.setup_wmt_handler(-22)
        self.assertEqual(ret, -22)
        self.assertEqual(dev.reset_count, 0)
        self.assertEqual(dev.wmt_retries, 0)

        ret = dev.setup_wmt_handler(-5)
        self.assertEqual(ret, -5)
        self.assertEqual(dev.reset_count, 0)
        self.assertEqual(dev.wmt_retries, 0)

    def test_wmt_setup_success_rearms_recovery(self):
        dev = self.BtkMtkDevice()
        # Timeout twice
        dev.setup_wmt_handler(-110)
        dev.setup_wmt_handler(-110)
        self.assertEqual(dev.reset_count, 2)
        self.assertEqual(dev.wmt_retries, 2)

        # Setup succeeds
        self.assertEqual(dev.setup_wmt_handler(0), 0)
        dev.setup_complete()
        self.assertEqual(dev.wmt_retries, 0)
        self.assertEqual(dev.reset_count, 2)

        # Recovery is re-armed: next timeouts trigger resets up to MAX
        for i in range(1, self.MAX_RETRIES + 1):
            dev.setup_wmt_handler(-110)
            self.assertEqual(dev.reset_count, 2 + i)
            self.assertEqual(dev.wmt_retries, i)

        # Stops after MAX
        dev.setup_wmt_handler(-110)
        self.assertEqual(dev.reset_count, 2 + self.MAX_RETRIES)

    def test_wmt_exhaustion_then_success_rearms(self):
        dev = self.BtkMtkDevice()
        for _ in range(self.MAX_RETRIES + 2):
            dev.setup_wmt_handler(-110)
        self.assertEqual(dev.reset_count, self.MAX_RETRIES)

        dev.setup_wmt_handler(0)
        dev.setup_complete()
        self.assertEqual(dev.wmt_retries, 0)

        dev.setup_wmt_handler(-110)
        self.assertEqual(dev.reset_count, self.MAX_RETRIES + 1)
        self.assertEqual(dev.wmt_retries, 1)

    def test_wmt_other_devices_do_not_reset(self):
        dev = self.BtkMtkDevice(dev_id=0x7961)
        ret = dev.setup_wmt_handler(-110)
        self.assertEqual(ret, -110)
        self.assertEqual(dev.reset_count, 0)
        self.assertEqual(dev.wmt_retries, 0)


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


class TestModuleSigning(unittest.TestCase):
    """Exercises the signing and MOK enrollment helper with temporary credentials."""

    @classmethod
    def setUpClass(cls):
        cls.script = os.path.join(REPO_ROOT, "scripts", "sign-module.sh")

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="mt7925-signing-test-")
        self.addCleanup(self.tempdir.cleanup)
        self.tmp = self.tempdir.name
        self.config = os.path.join(self.tmp, "empty.conf")
        with open(self.config, "w", encoding="utf-8"):
            pass
        self.key = os.path.join(self.tmp, "mok.key")
        self.cert = os.path.join(self.tmp, "mok.crt")
        keygen = subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-keyout", self.key, "-out", self.cert, "-days", "1",
                "-subj", "/CN=Temporary MT7925 signing test/",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(keygen.returncode, 0, f"Temporary test key generation failed: {keygen.stderr}")
        os.chmod(self.key, 0o600)
        self.module = os.path.join(self.tmp, "btmtk.ko")
        with open(self.module, "wb") as module_file:
            module_file.write(b"temporary module fixture")
        self.env = os.environ.copy()
        for name in ("MOK_KEY", "MOK_CERT", "MOK_SIGNING_KEY", "MOK_CERTIFICATE", "KBUILD_SIGN_KEY", "KBUILD_SIGN_PIN", "SIGN_CONF", "HASH_ALGO", "SIGN_FILE", "kernelver", "MODULES_DIR"):
            self.env.pop(name, None)

    def run_signer(self, *args, env=None):
        return subprocess.run(
            [self.script, "--config", self.config, *args],
            capture_output=True,
            text=True,
            env=env or self.env,
            check=False,
        )

    def make_fake_sign_file(self):
        idx = len(os.listdir(self.tmp))
        log = os.path.join(self.tmp, f"sign-file-args-{idx}")
        fake_sign_file = os.path.join(self.tmp, f"sign-file-{idx}")
        with open(fake_sign_file, "w", encoding="utf-8") as tool:
            tool.write("#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$SIGN_TEST_LOG\"\n")
        os.chmod(fake_sign_file, 0o755)
        return fake_sign_file, log

    def test_signing_skips_with_warning_when_not_configured(self):
        result = self.run_signer("sign", "--kernel", "6.12.0", "--module", self.module)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Secure Boot signing is not configured", result.stderr)
        self.assertIn("Skipping module signing", result.stderr)

    def test_configured_signing_calls_kernel_sign_file(self):
        fake_sign_file, log = self.make_fake_sign_file()
        env = self.env.copy()
        env["SIGN_TEST_LOG"] = log
        result = self.run_signer(
            "sign", "--key", self.key, "--cert", self.cert, "--kernel", "6.12.0",
            "--module", self.module, "--sign-file", fake_sign_file, env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Successfully signed", result.stdout)
        with open(log, encoding="utf-8") as recorded:
            self.assertEqual(recorded.read().splitlines(), ["sha256", self.key, self.cert, self.module])

    def test_incomplete_signing_configuration_fails_clearly(self):
        result = self.run_signer("sign", "--cert", self.cert)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("MOK_KEY is missing", result.stderr)

    def test_missing_configured_key_fails_clearly(self):
        missing_key = os.path.join(self.tmp, "missing.key")
        result = self.run_signer("sign", "--key", missing_key, "--cert", self.cert)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("MOK private key file not found", result.stderr)
        self.assertNotIn(missing_key, result.stderr)
        self.assertNotIn(missing_key, result.stdout)

    def test_private_key_permissions_refuses_insecure_modes(self):
        fake_sign_file, log = self.make_fake_sign_file()
        env = self.env.copy()
        env["SIGN_TEST_LOG"] = log
        for mode in (0o644, 0o666, 0o640, 0o700, 0o755):
            with self.subTest(mode=oct(mode)):
                os.chmod(self.key, mode)
                result = self.run_signer(
                    "sign", "--key", self.key, "--cert", self.cert, "--kernel", "6.12.0",
                    "--module", self.module, "--sign-file", fake_sign_file, env=env,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("insecure permissions", result.stderr)
                self.assertNotIn(self.key, result.stderr)
                self.assertNotIn(self.key, result.stdout)

    def test_private_key_permissions_accepts_0600_and_0400(self):
        fake_sign_file, log = self.make_fake_sign_file()
        env = self.env.copy()
        env["SIGN_TEST_LOG"] = log
        for mode in (0o600, 0o400):
            with self.subTest(mode=oct(mode)):
                os.chmod(self.key, mode)
                result = self.run_signer(
                    "sign", "--key", self.key, "--cert", self.cert, "--kernel", "6.12.0",
                    "--module", self.module, "--sign-file", fake_sign_file, env=env,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Successfully signed", result.stdout)

    def test_private_key_path_never_printed_on_missing_key(self):
        missing_key = os.path.join(self.tmp, "super-secret-key-path.priv")
        result = self.run_signer("sign", "--key", missing_key, "--cert", self.cert)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("MOK private key file not found", result.stderr)
        self.assertNotIn("super-secret-key-path", result.stderr)
        self.assertNotIn("super-secret-key-path", result.stdout)

    def test_kbuild_sign_pin_never_used_as_cert_and_never_printed(self):
        env = self.env.copy()
        env["KBUILD_SIGN_PIN"] = "PIN-MARKER-secret-passphrase-value"
        result = self.run_signer("sign", "--key", self.key, env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("MOK_CERT is missing", result.stderr)
        self.assertNotIn("PIN-MARKER-secret-passphrase-value", result.stderr)
        self.assertNotIn("PIN-MARKER-secret-passphrase-value", result.stdout)

    def test_secure_boot_enabled_fails_when_signing_not_configured(self):
        bin_dir = os.path.join(self.tmp, "bin-sb-on")
        os.makedirs(bin_dir, exist_ok=True)
        mock_mokutil = os.path.join(bin_dir, "mokutil")
        with open(mock_mokutil, "w", encoding="utf-8") as tool:
            tool.write("#!/bin/sh\necho 'SecureBoot enabled'\nexit 0\n")
        os.chmod(mock_mokutil, 0o755)
        env = self.env.copy()
        env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
        result = self.run_signer("sign", "--kernel", "6.12.0", "--module", self.module, env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Secure Boot is enabled, but module signing is not configured", result.stderr)

    def test_secure_boot_disabled_skips_when_signing_not_configured(self):
        bin_dir = os.path.join(self.tmp, "bin-sb-off")
        os.makedirs(bin_dir, exist_ok=True)
        mock_mokutil = os.path.join(bin_dir, "mokutil")
        with open(mock_mokutil, "w", encoding="utf-8") as tool:
            tool.write("#!/bin/sh\necho 'SecureBoot disabled'\nexit 0\n")
        os.chmod(mock_mokutil, 0o755)
        env = self.env.copy()
        env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
        result = self.run_signer("sign", "--kernel", "6.12.0", "--module", self.module, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Secure Boot signing is not configured", result.stderr)
        self.assertIn("Skipping module signing", result.stderr)

    def test_dkms_framework_conf_signing(self):
        fake_sign_file, log = self.make_fake_sign_file()
        conf = os.path.join(self.tmp, "framework.conf")
        with open(conf, "w", encoding="utf-8") as f:
            f.write(f'mok_signing_key="{self.key}"\n'
                    f'mok_certificate="{self.cert}"\n')
        env = self.env.copy()
        env["SIGN_TEST_LOG"] = log
        result = self.run_signer(
            "sign", "--config", conf, "--kernel", "6.12.0",
            "--module", self.module, "--sign-file", fake_sign_file, env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Successfully signed", result.stdout)
        with open(log, encoding="utf-8") as recorded:
            self.assertEqual(recorded.read().splitlines(), ["sha256", self.key, self.cert, self.module])

    def test_older_dkms_positional_arguments_supported(self):
        fake_sign_file, log = self.make_fake_sign_file()
        env = self.env.copy()
        env["SIGN_TEST_LOG"] = log
        result = self.run_signer(
            "6.12.0", self.module, "--key", self.key, "--cert", self.cert,
            "--sign-file", fake_sign_file, env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Successfully signed", result.stdout)
        with open(log, encoding="utf-8") as recorded:
            self.assertEqual(recorded.read().splitlines(), ["sha256", self.key, self.cert, self.module])

    def test_installed_module_search_order_prioritizes_updates_dkms(self):
        fake_sign_file, log = self.make_fake_sign_file()
        modules_dir = os.path.join(self.tmp, "lib", "modules")
        installed = os.path.join(modules_dir, "6.12.0", "updates", "dkms", "btmtk.ko")
        os.makedirs(os.path.dirname(installed), exist_ok=True)
        with open(installed, "wb") as f:
            f.write(b"installed module fixture")
        dummy_local = os.path.join(self.tmp, "btmtk.ko")
        with open(dummy_local, "wb") as f:
            f.write(b"local uninstalled fixture")

        env = self.env.copy()
        env["MODULES_DIR"] = modules_dir
        env["SIGN_TEST_LOG"] = log
        result = subprocess.run(
            [self.script, "--config", self.config, "sign", "--key", self.key, "--cert", self.cert,
             "--kernel", "6.12.0", "--sign-file", fake_sign_file],
            capture_output=True,
            text=True,
            cwd=self.tmp,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Successfully signed", result.stdout)
        with open(log, encoding="utf-8") as recorded:
            self.assertEqual(recorded.read().splitlines(), ["sha256", self.key, self.cert, installed])

    def test_sign_compressed_xz_module(self):
        fake_sign_file = os.path.join(self.tmp, "sign-file-append")
        with open(fake_sign_file, "w", encoding="utf-8") as tool:
            tool.write("#!/bin/sh\nprintf 'SIGNED_MARKER' >> \"$4\"\n")
        os.chmod(fake_sign_file, 0o755)

        xz_mod = os.path.join(self.tmp, "btmtk.ko.xz")
        with lzma.open(xz_mod, "wb") as f:
            f.write(b"original raw kernel module")

        result = self.run_signer(
            "sign", "--key", self.key, "--cert", self.cert, "--kernel", "6.12.0",
            "--module", xz_mod, "--sign-file", fake_sign_file,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Successfully signed", result.stdout)

        with lzma.open(xz_mod, "rb") as f:
            content = f.read()
        self.assertEqual(content, b"original raw kernel moduleSIGNED_MARKER")

    def test_sign_compressed_zst_module(self):
        bin_dir = os.path.join(self.tmp, "bin-zstd")
        os.makedirs(bin_dir, exist_ok=True)
        mock_zstd = os.path.join(bin_dir, "zstd")
        with open(mock_zstd, "w", encoding="utf-8") as tool:
            tool.write(
                "#!/bin/sh\n"
                "mode=''\n"
                "out=''\n"
                "inp=''\n"
                "while [ $# -gt 0 ]; do\n"
                "  case \"$1\" in\n"
                "    -d) mode='decompress'; shift ;;\n"
                "    -o) out=\"$2\"; shift 2 ;;\n"
                "    -q|-f|-c) shift ;;\n"
                "    *) inp=\"$1\"; shift ;;\n"
                "  esac\n"
                "done\n"
                "if [ \"$mode\" = 'decompress' ]; then\n"
                "  cat \"$inp\"\n"
                "else\n"
                "  cat \"$inp\" > \"$out\"\n"
                "fi\n"
            )
        os.chmod(mock_zstd, 0o755)

        fake_sign_file = os.path.join(self.tmp, "sign-file-zst-append")
        with open(fake_sign_file, "w", encoding="utf-8") as tool:
            tool.write("#!/bin/sh\nprintf 'ZST_SIGNED' >> \"$4\"\n")
        os.chmod(fake_sign_file, 0o755)

        zst_mod = os.path.join(self.tmp, "btmtk.ko.zst")
        with open(zst_mod, "wb") as f:
            f.write(b"zstd payload")

        env = self.env.copy()
        env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
        result = self.run_signer(
            "sign", "--key", self.key, "--cert", self.cert, "--kernel", "6.12.0",
            "--module", zst_mod, "--sign-file", fake_sign_file, env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Successfully signed", result.stdout)
        with open(zst_mod, "rb") as f:
            content = f.read()
        self.assertEqual(content, b"zstd payloadZST_SIGNED")

    def test_hash_algo_allowlist_and_rejection(self):
        fake_sign_file, _ = self.make_fake_sign_file()
        for algo in ("sha256", "sha384", "sha512"):
            with self.subTest(algo=algo):
                log = os.path.join(self.tmp, f"log-hash-{algo}")
                env = self.env.copy()
                env["SIGN_TEST_LOG"] = log
                result = self.run_signer(
                    "sign", "--key", self.key, "--cert", self.cert, "--kernel", "6.12.0",
                    "--module", self.module, "--sign-file", fake_sign_file, "--hash", algo, env=env,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                with open(log, encoding="utf-8") as recorded:
                    self.assertEqual(recorded.read().splitlines()[0], algo)

        for bad in ("md5", "sha1", "$(touch /tmp/bad)"):
            with self.subTest(bad=bad):
                result = self.run_signer(
                    "sign", "--key", self.key, "--cert", self.cert, "--kernel", "6.12.0",
                    "--module", self.module, "--sign-file", fake_sign_file, "--hash", bad,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Invalid hash algorithm", result.stderr)

    def test_strip_inline_comments_from_config(self):
        fake_sign_file, log = self.make_fake_sign_file()
        conf = os.path.join(self.tmp, "commented.conf")
        with open(conf, "w", encoding="utf-8") as f:
            f.write(
                f"MOK_KEY={self.key} # inline private key comment\n"
                f"MOK_CERT=\"{self.cert}\" # inline certificate comment\n"
                f"HASH_ALGO=sha384 # inline hash comment\n"
            )
        env = self.env.copy()
        env["SIGN_TEST_LOG"] = log
        result = self.run_signer(
            "sign", "--config", conf, "--kernel", "6.12.0",
            "--module", self.module, "--sign-file", fake_sign_file, env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Successfully signed", result.stdout)
        with open(log, encoding="utf-8") as recorded:
            self.assertEqual(recorded.read().splitlines(), ["sha384", self.key, self.cert, self.module])

    def test_configured_sign_file_not_executable_fails(self):
        bad_sf = os.path.join(self.tmp, "non-exec-sign-file")
        with open(bad_sf, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(bad_sf, 0o644)
        result = self.run_signer(
            "sign", "--key", self.key, "--cert", self.cert, "--kernel", "6.12.0",
            "--module", self.module, "--sign-file", bad_sf,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Configured sign-file binary is not executable", result.stderr)

    def test_require_consistent_credential_source(self):
        env = self.env.copy()
        env["MOK_CERT"] = self.cert
        result = self.run_signer("sign", "--key", self.key, "--module", self.module, env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("MOK_CERT is missing", result.stderr)

        env2 = self.env.copy()
        env2["MOK_KEY"] = self.key
        result = self.run_signer("sign", "--cert", self.cert, "--module", self.module, env=env2)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("MOK_KEY is missing", result.stderr)

        partial_conf = os.path.join(self.tmp, "partial.conf")
        with open(partial_conf, "w", encoding="utf-8") as f:
            f.write(f"MOK_KEY={self.key}\n")
        result = self.run_signer("sign", "--config", partial_conf, "--module", self.module)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("MOK_CERT is missing", result.stderr)

    def test_enroll_submits_certificate_to_mokutil(self):
        bin_dir = os.path.join(self.tmp, "bin")
        os.makedirs(bin_dir)
        log = os.path.join(self.tmp, "mokutil-args")
        cert_copy = os.path.join(self.tmp, "mokutil-cert.der")
        mock_mokutil = os.path.join(bin_dir, "mokutil")
        with open(mock_mokutil, "w", encoding="utf-8") as tool:
            tool.write("#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$MOK_TEST_LOG\"\ncp \"$2\" \"$MOK_TEST_COPY\"\n")
        os.chmod(mock_mokutil, 0o755)
        env = self.env.copy()
        env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
        env["MOK_TEST_LOG"] = log
        env["MOK_TEST_COPY"] = cert_copy
        result = self.run_signer("enroll", "--cert", self.cert, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("MOK certificate staged for enrollment", result.stdout)
        with open(log, encoding="utf-8") as recorded:
            args = recorded.read().splitlines()
        self.assertEqual(args[0], "--import")
        self.assertTrue(args[1].endswith(".der"))
        self.assertTrue(os.path.isfile(cert_copy))
        self.assertGreater(os.path.getsize(cert_copy), 0)


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

    def test_older_kernel_module_compilation(self):
        """Verifies compile-only build against older kernel headers in [6.4, 6.12) if available."""
        import glob

        candidates = set()
        for p in glob.glob("/lib/modules/*/build"):
            if os.path.isdir(p):
                candidates.add(os.path.realpath(p))
        for p in glob.glob("/usr/src/linux-headers-*"):
            if os.path.isdir(p):
                candidates.add(os.path.realpath(p))

        older_headers = []
        for path in sorted(candidates):
            ver = None
            for vh in [
                os.path.join(path, "include", "generated", "uapi", "linux", "version.h"),
                os.path.join(path, "include", "linux", "version.h"),
            ]:
                if os.path.isfile(vh):
                    with open(vh, "r", encoding="utf-8", errors="ignore") as f:
                        for line in f:
                            m_code = re.search(r"#define\s+LINUX_VERSION_CODE\s+(\d+)", line)
                            if m_code:
                                code = int(m_code.group(1))
                                ver = ((code >> 16) & 0xFF, (code >> 8) & 0xFF, code & 0xFF)
                                break
                    if ver:
                        break

            if not ver:
                name = os.path.basename(path)
                m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", name)
                if m:
                    ver = (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))

            if ver and (6, 4, 0) <= ver < (6, 12, 0):
                older_headers.append((ver, path))

        if not older_headers:
            self.skipTest(
                "No kernel headers for older kernels in [6.4, 6.12) found under "
                "/lib/modules/*/build or /usr/src/linux-headers-*"
            )

        for ver, header_dir in older_headers:
            with self.subTest(kernel_version=ver, header_dir=header_dir):
                build_res = subprocess.run(
                    ["make", "-C", header_dir, f"M={REPO_ROOT}", "modules"],
                    capture_output=True,
                    text=True,
                    cwd=REPO_ROOT,
                )
                self.assertEqual(
                    build_res.returncode,
                    0,
                    f"Compile-only build failed for older kernel {ver} at {header_dir}:\n"
                    f"STDOUT:\n{build_res.stdout}\nSTDERR:\n{build_res.stderr}",
                )

                ko_file = os.path.join(REPO_ROOT, "btmtk.ko")
                self.assertTrue(
                    os.path.isfile(ko_file),
                    f"btmtk.ko was not produced for {header_dir}",
                )

                clean_res = subprocess.run(
                    ["make", "-C", header_dir, f"M={REPO_ROOT}", "clean"],
                    capture_output=True,
                    text=True,
                    cwd=REPO_ROOT,
                )
                self.assertEqual(
                    clean_res.returncode,
                    0,
                    f"Clean failed for {header_dir}:\n{clean_res.stderr}",
                )


if __name__ == "__main__":
    unittest.main()

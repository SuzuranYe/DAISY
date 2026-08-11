"""档案数据核验命令入口、控制协议与只读发布专项测试。"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import threading
import time
import unittest
import zipfile


_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPT_DIR = os.path.dirname(_TEST_DIR)
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
_LIB_DIR = os.path.join(_SCRIPT_DIR, "Lib")
_MODULE_DIR = os.path.join(_SCRIPT_DIR, "Module")
sys.path[:0] = [_TEST_DIR, _SCRIPT_DIR, _LIB_DIR, _MODULE_DIR]

import Script_DAISY_Lib_Snapshot_Core as core
import Script_DAISY_Lib_Scan_Runtime as dbrun
import Script_DAISY_Lib_Verify_Runtime as verifyrun
import Script_DAISY_CLI as entry
import Script_DAISY_Module_Verify as verifycli
import Script_DAISY_Test_Verify_Unified as unified_fixture


class TestVerificationCLIOptions(unittest.TestCase):
    def _args(self, *extra: str):
        return verifycli.build_parser().parse_args([
            "--snapshot", "fixture.sqlite",
            "--root", "夹具=Current",
            *extra,
        ])

    def test_defaults_and_independent_sample_percentages(self) -> None:
        options = verifycli.verification_options(self._args())
        self.assertEqual(options.hash_mode, "sample")
        self.assertEqual(options.hash_sample_percent, 1.0)
        self.assertEqual(options.format_mode, "off")
        self.assertEqual(options.format_sample_percent, 10.0)
        self.assertEqual(options.timeout_decision, "continue_waiting")

        options = verifycli.verification_options(self._args(
            "--hash", "all",
            "--format", "sample",
            "--format-sample-percent", "17.5",
            "--timeout-action", "skip_and_record",
        ))
        self.assertEqual(options.hash_mode, "all")
        self.assertEqual(options.format_mode, "sample")
        self.assertEqual(options.format_sample_percent, 17.5)
        self.assertEqual(options.timeout_decision, "skip_and_record")

    def test_irrelevant_options_are_rejected_before_run(self) -> None:
        invalid = (
            ("--hash", "all", "--hash-sample-percent", "1"),
            ("--format", "all", "--format-sample-percent", "10"),
            ("--format", "off", "--exiftool-path", "tool.exe"),
            ("--hash", "off", "--powershell-path", "pwsh.exe"),
            (
                "--hash", "off", "--format", "off",
                "--timeout-action", "continue_waiting",
            ),
        )
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                with self.assertRaises(core.PreflightError):
                    verifycli.verification_options(self._args(*arguments))

    def test_direct_mode_defaults_hash_off_and_requires_no_snapshot(self) -> None:
        args = verifycli.build_parser().parse_args([
            "--direct", "--root", "Current",
        ])
        options = verifycli.verification_options(args)
        self.assertTrue(args.direct)
        self.assertIsNone(args.snapshot)
        self.assertEqual(options.hash_mode, "off")


class TestVerificationCLIControl(unittest.TestCase):
    def test_router_rejects_save_exit_and_supports_pause_continue(self) -> None:
        control = verifyrun.UnifiedVerificationControl()
        receipts: list[dbrun.ControlReceipt] = []
        router = verifycli.VerificationCommandRouter(
            control, on_receipt=receipts.append)

        rejected = router.route(dbrun.ControlCommand(1, "save_exit"))
        self.assertFalse(rejected.accepted)
        self.assertEqual(rejected.reason, "verification_not_resumable")

        paused = router.route(dbrun.ControlCommand(2, "pause"))
        self.assertTrue(paused.accepted)
        result: list[str] = []
        thread = threading.Thread(
            target=lambda: result.append(control.wait_after_pause(0.01)),
            daemon=True,
        )
        thread.start()
        deadline = time.monotonic() + 5.0
        while control.state != "paused" and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(control.state, "paused")

        continued = router.route(dbrun.ControlCommand(3, "continue"))
        self.assertTrue(continued.accepted)
        thread.join(timeout=5.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result, ["continue"])
        self.assertEqual([item.sequence for item in receipts], [1, 2, 3])
        control.finish()


class TestVerificationCLIEndToEnd(unified_fixture._Fixture):
    def _published_json(self) -> str:
        names = sorted(
            name for name in os.listdir(self.reports)
            if name.endswith(".json") and ".partial." not in name
        )
        self.assertEqual(len(names), 1)
        return os.path.join(self.reports, names[0])

    def test_main_dispatch_stat_only_is_readonly(self) -> None:
        snapshot = self.snapshot({"中文.txt": (b"plain", "other")})
        baseline = unified_fixture._identity(snapshot)
        runtime_temp = os.path.join(self.base, "RuntimeTemp")
        os.makedirs(runtime_temp)
        environment = os.environ.copy()
        environment.update({
            "PYTHONDONTWRITEBYTECODE": "1",
            "TEMP": runtime_temp,
            "TMP": runtime_temp,
            "TMPDIR": runtime_temp,
        })
        command = [
            sys.executable,
            "-B",
            os.path.join(_SCRIPT_DIR, "Script_DAISY_CLI.py"),
            "verify",
            "--snapshot", snapshot,
            "--root", f"夹具={self.current}",
            "--hash", "off",
            "--format", "off",
            "--report-dir", self.reports,
            "--force",
            "--quiet",
        ]
        completed = subprocess.run(
            command,
            cwd=_REPO_ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        self.assertEqual(
            completed.returncode, 0,
            msg=f"stdout={completed.stdout}\nstderr={completed.stderr}",
        )
        self.assertEqual(unified_fixture._identity(snapshot), baseline)
        with open(self._published_json(), encoding="utf-8") as handle:
            report = json.load(handle)
        self.assertEqual(report["conclusion"], "passed")
        self.assertEqual(report["sections"]["hash"]["state"], "NULL")
        self.assertEqual(report["sections"]["format"]["state"], "NULL")
        self.assertIn(
            "哈希：NULL（未执行）｜已处理 NULL｜不可核验 NULL｜受影响文件 NULL",
            completed.stdout,
        )
        self.assertIn(
            "格式校验：NULL（未执行）｜已处理 NULL｜不支持 NULL｜受影响文件 NULL",
            completed.stdout,
        )
        self.assertIn(
            "RAW 深度校验：NULL（未执行）｜已处理 NULL｜不支持 NULL｜受影响文件 NULL",
            completed.stdout,
        )
        self.assertIn("Markdown 报告：", completed.stdout)
        self.assertIn("技术证据：", completed.stdout)

    def test_missing_file_returns_one_and_publishes_issues(self) -> None:
        snapshot = self.snapshot({"missing.bin": (b"data", "other")})
        baseline = unified_fixture._identity(snapshot)
        os.remove(os.path.join(self.current, "missing.bin"))
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), \
                contextlib.redirect_stderr(stderr):
            return_code = verifycli.main([
                "--snapshot", snapshot,
                "--root", f"夹具={self.current}",
                "--hash", "off",
                "--format", "off",
                "--report-dir", self.reports,
                "--force",
                "--quiet",
            ])
        self.assertEqual(return_code, 1)
        self.assertEqual(unified_fixture._identity(snapshot), baseline)
        with open(self._published_json(), encoding="utf-8") as handle:
            report = json.load(handle)
        self.assertEqual(report["conclusion"], "issues_found")
        self.assertEqual(report["sections"]["stat"]["counts"]["missing"], 1)
        self.assertIn("发现需要处理或复核的问题", stderr.getvalue())

    def test_database_unchanged_path_uses_recorded_root_without_mapping(
        self,
    ) -> None:
        snapshot = self.snapshot(
            {"原路径.txt": (b"plain", "other")},
            recorded_path=self.current,
        )
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            return_code = verifycli.main([
                "--snapshot", snapshot,
                "--hash", "off",
                "--format", "off",
                "--report-dir", self.reports,
                "--force",
                "--quiet",
            ])
        self.assertEqual(return_code, 0)
        with open(self._published_json(), encoding="utf-8") as handle:
            report = json.load(handle)
        self.assertEqual(report["conclusion"], "passed")
        self.assertTrue(report["input_unchanged"])

    def test_direct_mode_checks_current_files_without_database(self) -> None:
        archive = os.path.join(self.current, "可读.zip")
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("内容.txt", "hello")
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), \
                contextlib.redirect_stderr(stderr):
            return_code = verifycli.main([
                "--direct",
                "--root", self.current,
                "--format", "all",
                "--format-tool", "builtin",
                "--report-dir", self.reports,
                "--quiet",
            ])
        self.assertEqual(
            return_code, 0,
            msg=f"stdout={stdout.getvalue()}\nstderr={stderr.getvalue()}",
        )
        with open(self._published_json(), encoding="utf-8") as handle:
            report = json.load(handle)
        self.assertEqual(report["snapshot"]["input_mode"], "direct")
        self.assertEqual(report["input_identity"]["enumerated_files"], 1)
        self.assertIsNone(report["input_unchanged"])
        self.assertEqual(report["sections"]["hash"]["state"], "NULL")
        self.assertEqual(report["sections"]["format"]["valid"], 1)
        self.assertEqual(report["conclusion"], "passed")
        markdown = next(
            os.path.join(self.reports, name)
            for name in os.listdir(self.reports)
            if name.endswith(".md")
        )
        with open(markdown, encoding="utf-8") as handle:
            report_text = handle.read()
        self.assertIn("无数据库直接核验", report_text)
        self.assertIn("本报告不宣称文件哈希一致", report_text)

    def test_snapshot_without_hash_evidence_is_incomplete(self) -> None:
        snapshot = self.snapshot(
            {"plain.txt": (b"plain", "other")}, hash_coverage="none")
        baseline = unified_fixture._identity(snapshot)
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            return_code = verifycli.main([
                "--snapshot", snapshot,
                "--root", f"夹具={self.current}",
                "--hash", "all",
                "--format", "off",
                "--report-dir", self.reports,
                "--force",
                "--quiet",
            ])
        self.assertEqual(return_code, 1)
        self.assertEqual(unified_fixture._identity(snapshot), baseline)
        with open(self._published_json(), encoding="utf-8") as handle:
            report = json.load(handle)
        self.assertEqual(report["conclusion"], "incomplete")
        self.assertEqual(report["sections"]["hash"]["state"], "unavailable")

    def test_entry_command_registers_unified_verify_module(self) -> None:
        module = "Script_DAISY_Module_Verify"
        self.assertEqual(entry.COMMANDS["verify"][0], module)
        self.assertTrue(os.path.isfile(os.path.join(
            _MODULE_DIR, module + ".py")))


if __name__ == "__main__":
    unittest.main(verbosity=2)

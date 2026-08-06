"""v1.6.0 统一核验外部工具句柄监督与格式分类专项测试。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock


_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPT_DIR = os.path.dirname(_TEST_DIR)
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
_LIB_DIR = os.path.join(_SCRIPT_DIR, "Lib")
sys.path[:0] = [_TEST_DIR, _SCRIPT_DIR, _LIB_DIR]

import Script_DAISY_Lib_DBS_03_Hash as dbhash
import Script_DAISY_Lib_DBS_06_Verify as legacy
import Script_DAISY_Lib_DBS_12_Verify_Tools as verifytools


_RUNTIME_ROOT = os.path.join(
    _REPO_ROOT, ".test_runtime", "v1_6_0", "verify_tools")


def _python_command(source: str) -> list[str]:
    return [sys.executable, "-B", "-c", source]


def _outcome(
    *,
    returncode: int = 0,
    stdout: bytes = b"",
    stderr: bytes = b"",
    outcome: str = "completed",
    pid: int = 12345,
    threshold_count: int = 0,
    events: tuple[dict[str, object], ...] = (),
    stdout_truncated: bool = False,
    stderr_truncated: bool = False,
    worker_reaped: bool = True,
) -> verifytools.ControlledToolOutcome:
    return verifytools.ControlledToolOutcome(
        outcome=outcome,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        decision="none",
        decision_source="none",
        elapsed_seconds=0.01,
        threshold_count=threshold_count,
        worker_pid=pid,
        worker_reaped=worker_reaped,
        events=events,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
    )


class _Fixture(unittest.TestCase):
    def setUp(self) -> None:
        os.makedirs(_RUNTIME_ROOT, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(
            prefix="case_", dir=_RUNTIME_ROOT)
        self.base = self._temporary.name

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def file(self, name: str, payload: bytes = b"fixture") -> str:
        path = os.path.join(self.base, name)
        with open(path, "wb") as handle:
            handle.write(payload)
        return path


class TestControlledExternalTool(_Fixture):
    def test_large_stdout_stderr_are_drained_but_capture_is_bounded(self) \
            -> None:
        command = _python_command(
            "import os;"
            "os.write(1,b'A'*(512*1024));"
            "os.write(2,b'B'*(512*1024))")
        with mock.patch.object(
                verifytools, "_MAX_CAPTURE_BYTES", 128 * 1024):
            result = verifytools.run_controlled_tool(
                command,
                expected_size=1,
                timeout_seconds=10.0,
                display_name="long-output.fixture",
                poll_seconds=0.01,
            )
        self.assertEqual(result.outcome, "completed")
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.worker_reaped)
        self.assertEqual(len(result.stdout), 128 * 1024)
        self.assertEqual(len(result.stderr), 128 * 1024)
        self.assertTrue(result.stdout_truncated)
        self.assertTrue(result.stderr_truncated)

    def test_timeout_continue_then_skip_reaps_exact_child(self) -> None:
        decisions: list[int] = []

        def threshold(payload, arbiter) -> None:
            count = int(payload["threshold_count"])
            decisions.append(count)
            arbiter.choose(
                "continue_waiting" if count == 1 else "skip_and_record",
                "user",
            )

        result = verifytools.run_controlled_tool(
            _python_command("import time; time.sleep(30)"),
            expected_size=1,
            timeout_seconds=0.05,
            default_decision="continue_waiting",
            display_name="timeout.fixture",
            on_threshold=threshold,
            poll_seconds=0.005,
        )
        self.assertEqual(decisions, [1, 2])
        self.assertEqual(result.outcome, "timeout")
        self.assertEqual(result.decision, "skip_and_record")
        self.assertEqual(result.decision_source, "user")
        self.assertEqual(result.threshold_count, 2)
        self.assertTrue(result.worker_reaped)
        self.assertIsNotNone(result.returncode)

    def test_default_continue_waiting_can_complete_after_threshold(self) \
            -> None:
        result = verifytools.run_controlled_tool(
            _python_command("import time; time.sleep(0.08)"),
            expected_size=1,
            timeout_seconds=0.02,
            default_decision="continue_waiting",
            display_name="default-continue.fixture",
            poll_seconds=0.005,
        )
        self.assertEqual(result.outcome, "completed")
        self.assertEqual(result.returncode, 0)
        self.assertGreaterEqual(result.threshold_count, 1)
        self.assertEqual(result.decision, "continue_waiting")
        self.assertEqual(result.decision_source, "default")
        self.assertTrue(result.worker_reaped)

    def test_advanced_default_stop_returns_stopped_and_reaped(self) -> None:
        result = verifytools.run_controlled_tool(
            _python_command("import time; time.sleep(30)"),
            expected_size=1,
            timeout_seconds=0.03,
            default_decision="stop_and_resume",
            display_name="default-stop.fixture",
            poll_seconds=0.005,
        )
        self.assertEqual(result.outcome, "stopped")
        self.assertEqual(result.decision, "stop_and_resume")
        self.assertEqual(result.decision_source, "advanced_policy")
        self.assertEqual(result.threshold_count, 1)
        self.assertTrue(result.worker_reaped)

    def test_pause_terminates_current_child_and_returns_paused(self) -> None:
        control = dbhash.HashWorkerControl()
        worker_started = threading.Event()
        results: list[verifytools.ControlledToolOutcome] = []
        errors: list[BaseException] = []

        def event(name: str, **_payload: object) -> None:
            if name == "worker_started":
                worker_started.set()

        def target() -> None:
            try:
                results.append(verifytools.run_controlled_tool(
                    _python_command("import time; time.sleep(30)"),
                    expected_size=1,
                    timeout_seconds=10.0,
                    display_name="pause.fixture",
                    control=control,
                    on_event=event,
                    poll_seconds=0.005,
                ))
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        self.assertTrue(worker_started.wait(timeout=5.0))
        self.assertTrue(control.request_pause("user"))
        thread.join(timeout=5.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results[0].outcome, "paused")
        self.assertEqual(results[0].decision_source, "user")
        self.assertTrue(results[0].worker_reaped)

    def test_callback_error_still_reaps_and_closes_owned_process(self) \
            -> None:
        held: dict[str, subprocess.Popen] = {}

        def factory(command, **kwargs):
            process = subprocess.Popen(command, **kwargs)
            held["process"] = process
            return process

        def event(name: str, **_payload: object) -> None:
            if name == "worker_started":
                raise RuntimeError("synthetic callback failure")

        with self.assertRaisesRegex(RuntimeError, "callback failure"):
            verifytools.run_controlled_tool(
                _python_command("import time; time.sleep(30)"),
                expected_size=1,
                timeout_seconds=10.0,
                display_name="callback.fixture",
                on_event=event,
                poll_seconds=0.005,
                _popen_factory=factory,
            )
        process = held["process"]
        self.assertIsNotNone(process.poll())
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)

    def test_control_binding_failure_reaps_new_child_only(self) -> None:
        control = dbhash.HashWorkerControl()
        control.bind_worker(777777)
        held: dict[str, subprocess.Popen] = {}

        def factory(command, **kwargs):
            process = subprocess.Popen(command, **kwargs)
            held["process"] = process
            return process

        try:
            with self.assertRaisesRegex(RuntimeError, "另一个 worker"):
                verifytools.run_controlled_tool(
                    _python_command("import time; time.sleep(30)"),
                    expected_size=1,
                    timeout_seconds=10.0,
                    display_name="bind.fixture",
                    control=control,
                    poll_seconds=0.005,
                    _popen_factory=factory,
                )
        finally:
            control.unbind_worker(777777)
        self.assertIsNotNone(held["process"].poll())


class TestExternalFormatClassification(_Fixture):
    def test_sevenzip_valid_password_and_corrupt_are_distinct(self) -> None:
        path = self.file("archive.7z")
        cases = (
            (_outcome(returncode=0), "valid"),
            (_outcome(returncode=2, stderr=b"Wrong password"),
             "unsupported"),
            (_outcome(returncode=2, stderr=b"CRC Failed"), "invalid"),
            (_outcome(returncode=8, stderr=b"Not enough memory"), "error"),
            (_outcome(returncode=0xC0000005), "error"),
        )
        for result, expected in cases:
            with self.subTest(expected=expected):
                outcome = verifytools.run_external_format_validator(
                    path,
                    "archive",
                    legacy.FormatValidatorSpec("7z", "7-Zip", "fixture"),
                    {"sevenzip": {"path": "X:/fixture/7z.exe"}},
                    expected_size=os.path.getsize(path),
                    _direct_runner=lambda *_args, **_kwargs: result,
                )
                self.assertEqual(outcome.status, expected)

    def test_exiftool_start_failure_is_a_tool_error(self) -> None:
        path = self.file("image.jpg")

        def runner(_command, **_kwargs):
            raise FileNotFoundError(2, "synthetic missing executable")

        outcome = verifytools.run_external_format_validator(
            path,
            "photo_jpeg",
            legacy.FormatValidatorSpec("media", "exiftool", "fixture"),
            {"exiftool": {"path": "X:/fixture/exiftool.exe"}},
            expected_size=os.path.getsize(path),
            _direct_runner=runner,
        )
        self.assertEqual(outcome.outcome, "tool_error")
        self.assertEqual(outcome.status, "error")
        self.assertIsNone(outcome.worker_pid)
        self.assertIn("ExifTool 启动失败", outcome.detail)

    def test_single_exiftool_result_has_no_duplicate_events(self) -> None:
        path = self.file("image.jpg")
        event = {"event": "worker_started", "worker_pid": 101}
        calls: list[list[str]] = []

        def runner(command, **_kwargs):
            calls.append(command)
            return _outcome(
                pid=101, threshold_count=1, events=(event,))

        outcome = verifytools.run_external_format_validator(
            path,
            "image",
            legacy.FormatValidatorSpec("media", "exiftool", "fixture"),
            {"exiftool": {"path": "X:/fixture/exiftool.exe"}},
            expected_size=os.path.getsize(path),
            _direct_runner=runner,
        )
        self.assertEqual(outcome.status, "valid")
        self.assertEqual(outcome.events, (event,))
        self.assertEqual(outcome.threshold_count, 1)
        self.assertEqual(len(calls), 1)
        self.assertIn("-validate", calls[0])

    def test_gif_combines_exiftool_and_ffprobe_events_once(self) -> None:
        path = self.file("image.gif")
        exif_event = {"event": "worker_started", "worker_pid": 101}
        ffprobe_event = {"event": "worker_started", "worker_pid": 102}
        results = [
            _outcome(pid=101, threshold_count=1, events=(exif_event,)),
            _outcome(
                pid=102,
                stdout=json.dumps({
                    "streams": [{"codec_type": "video"}],
                    "format": {},
                }).encode("utf-8"),
                threshold_count=2,
                events=(ffprobe_event,),
            ),
        ]

        def runner(_command, **_kwargs):
            return results.pop(0)

        outcome = verifytools.run_external_format_validator(
            path,
            "image_gif",
            legacy.FormatValidatorSpec(
                "gif", "exiftool+ffprobe", "fixture"),
            {
                "exiftool": {"path": "X:/fixture/exiftool.exe"},
                "ffprobe": {"path": "X:/fixture/ffprobe.exe"},
            },
            expected_size=os.path.getsize(path),
            _direct_runner=runner,
        )
        self.assertEqual(outcome.status, "valid")
        self.assertEqual(outcome.events, (exif_event, ffprobe_event))
        self.assertEqual(outcome.threshold_count, 3)
        self.assertEqual(results, [])

    def test_exiftool_truncation_is_error_not_false_valid(self) -> None:
        path = self.file("image.jpg")
        outcome = verifytools.run_external_format_validator(
            path,
            "image",
            legacy.FormatValidatorSpec("media", "exiftool", "fixture"),
            {"exiftool": {"path": "X:/fixture/exiftool.exe"}},
            expected_size=os.path.getsize(path),
            _direct_runner=lambda *_args, **_kwargs: _outcome(
                stdout_truncated=True),
        )
        self.assertEqual(outcome.status, "error")
        self.assertIn("输出超过", outcome.detail)

    def test_audio_disappearing_during_ffprobe_is_classified(self) -> None:
        path = self.file("audio.wav", b"RIFF" + b"\x00" * 40)
        results = [
            _outcome(pid=101),
            _outcome(
                pid=102,
                stdout=json.dumps({
                    "streams": [{"codec_type": "audio"}],
                    "format": {"duration": "0"},
                }).encode("utf-8"),
            ),
        ]

        def runner(_command, **_kwargs):
            result = results.pop(0)
            if result.worker_pid == 102:
                os.remove(path)
            return result

        outcome = verifytools.run_external_format_validator(
            path,
            "audio",
            legacy.FormatValidatorSpec(
                "media", "exiftool+ffprobe", "fixture"),
            {
                "exiftool": {"path": "X:/fixture/exiftool.exe"},
                "ffprobe": {"path": "X:/fixture/ffprobe.exe"},
            },
            expected_size=44,
            _direct_runner=runner,
        )
        self.assertEqual(outcome.status, "invalid")
        self.assertIn("无法读取文件大小", outcome.detail)

    def test_ffprobe_invalid_json_shape_is_not_false_valid(self) -> None:
        path = self.file("image.gif")
        results = [
            _outcome(pid=101),
            _outcome(pid=102, stdout=b'{"streams":"not-a-list"}'),
        ]
        outcome = verifytools.run_external_format_validator(
            path,
            "image_gif",
            legacy.FormatValidatorSpec(
                "gif", "exiftool+ffprobe", "fixture"),
            {
                "exiftool": {"path": "X:/fixture/exiftool.exe"},
                "ffprobe": {"path": "X:/fixture/ffprobe.exe"},
            },
            expected_size=os.path.getsize(path),
            _direct_runner=lambda *_args, **_kwargs: results.pop(0),
        )
        self.assertEqual(outcome.status, "invalid")
        self.assertIn("JSON 结构无效", outcome.detail)

    def test_ffprobe_native_exit_is_tool_error_not_file_damage(self) -> None:
        path = self.file("image.gif")
        exif_event = {"event": "worker_started", "worker_pid": 101}
        crash_event = {"event": "worker_started", "worker_pid": 102}
        results = [
            _outcome(pid=101, events=(exif_event,)),
            _outcome(
                pid=102,
                returncode=0xC0000005,
                events=(crash_event,),
            ),
        ]
        outcome = verifytools.run_external_format_validator(
            path,
            "image_gif",
            legacy.FormatValidatorSpec(
                "gif", "exiftool+ffprobe", "fixture"),
            {
                "exiftool": {"path": "X:/fixture/exiftool.exe"},
                "ffprobe": {"path": "X:/fixture/ffprobe.exe"},
            },
            expected_size=os.path.getsize(path),
            _direct_runner=lambda *_args, **_kwargs: results.pop(0),
        )
        self.assertEqual(outcome.status, "error")
        self.assertIn("0xC0000005", outcome.detail)
        self.assertEqual(outcome.events, (exif_event, crash_event))

    def test_non_ole_document_is_unsupported_without_tool_start(self) -> None:
        path = self.file("legacy.doc", b"not-ole")
        outcome = verifytools.run_external_format_validator(
            path,
            "document",
            legacy.FormatValidatorSpec("ole", "7-Zip", "fixture"),
            {},
            expected_size=os.path.getsize(path),
            _direct_runner=lambda *_args, **_kwargs: self.fail(
                "非 OLE 文件不应启动 7-Zip"),
        )
        self.assertEqual(outcome.status, "unsupported")
        self.assertIsNone(outcome.worker_pid)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""DAISY v1.6.8 统一扫描 GUI 控制链测试。

测试只使用工作区内合成路径和内存管道；不枚举、附加或终止其它进程。
真实 Tcl/Tk 桌面测试代码已移除，界面视觉验收按需手动进行。
"""
from __future__ import annotations

import io
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch


_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPT_DIR = os.path.dirname(_TEST_DIR)
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
_LIB_DIR = os.path.join(_SCRIPT_DIR, "Lib")
sys.path[:0] = [_TEST_DIR, _SCRIPT_DIR, _LIB_DIR]

import Script_DAISY_GUI as gui
import Script_DAISY_Lib_Snapshot_Core as core
import Script_DAISY_Lib_Scan_Runtime as dbrun
import Script_DAISY_Test_Tree as tree_fixture


_RUNTIME_ROOT = os.path.join(
    _REPO_ROOT, ".test_runtime", "v1_6_1", "gui_scan")
_CLI = os.path.join(_SCRIPT_DIR, "Script_DAISY_CLI.py")
_HASH_WORKER_FIXTURE = os.path.join(
    _TEST_DIR, "Fixtures", "Hash_Worker_Fixture.py")
_GUI_PREFIX = b"@@DAISY_GUI@@"


class _ButtonProbe:
    def __init__(self) -> None:
        self.options: dict[str, object] = {}

    def configure(self, **options: object) -> None:
        self.options.update(options)


class _OutputProbe:
    def __init__(self, chunks: tuple[bytes, ...] = ()) -> None:
        self._chunks = list(chunks)
        self.closed = False

    def read1(self, _size: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""

    def close(self) -> None:
        self.closed = True


class _ProcessProbe:
    def __init__(self, *, with_stdin: bool = True) -> None:
        self.pid = 4312
        self.stdin = io.BytesIO() if with_stdin else None
        self.stdout = _OutputProbe()

    @staticmethod
    def wait(timeout: float | None = None) -> int:
        del timeout
        return 0


class TestScanArgumentMapping(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.makedirs(_RUNTIME_ROOT, exist_ok=True)

    def test_full_defaults_use_schema4_controlled_scan(self) -> None:
        args = gui.build_tool_args(
            "full_scan", {"roots": _RUNTIME_ROOT})
        self.assertEqual(args[:3], ["scan", "--mode", "full"])
        self.assertEqual(
            args[args.index("--format-validation") + 1], "off")
        self.assertEqual(
            args[args.index("--timeout-action") + 1],
            "continue_waiting",
        )
        self.assertIn("--control-stdin", args)
        self.assertNotIn("full-scan", args)
        self.assertNotIn("--raw-deep-validation", args)

    def test_full_resume_does_not_override_frozen_configuration(self) -> None:
        partial = os.path.join(_RUNTIME_ROOT, "Saved.partial.sqlite")
        args = gui.build_tool_args("full_scan", {
            "start_mode": "resume",
            "resume": partial,
            "roots": _RUNTIME_ROOT,
            "hash_mode": "none",
            "metadata_storage": "normalized",
            "format_validation": "all",
            "powershell_path": os.path.join(_RUNTIME_ROOT, "pwsh.exe"),
            "retry_mode": "transient",
            "show_current_file": True,
        })
        self.assertEqual(args, [
            "scan", "--mode", "full",
            "--resume", os.path.abspath(partial),
            "--manual-resume",
            "--retry-mode", "transient",
            "--show-current-file",
            "--control-stdin",
        ])

    def test_quick_new_and_resume_share_control_protocol(self) -> None:
        fresh = gui.build_tool_args(
            "quick_scan", {"roots": _RUNTIME_ROOT})
        self.assertEqual(fresh[:3], ["scan", "--mode", "quick"])
        self.assertIn("--control-stdin", fresh)
        partial = os.path.join(_RUNTIME_ROOT, "Quick.partial.sqlite")
        resumed = gui.build_tool_args("quick_scan", {
            "start_mode": "resume", "resume": partial})
        self.assertEqual(resumed, [
            "scan", "--mode", "quick",
            "--resume", os.path.abspath(partial),
            "--manual-resume", "--control-stdin",
        ])

    def test_format_sample_and_nonfinite_values_are_validated(self) -> None:
        args = gui.build_tool_args("full_scan", {
            "roots": _RUNTIME_ROOT,
            "format_validation": "sample",
            "format_sample_percent": "12.5",
        })
        self.assertEqual(
            args[args.index("--format-sample-percent") + 1], "12.5")
        issues = gui.validate_values("full_scan", {
            "roots": _RUNTIME_ROOT,
            "format_validation": "sample",
            "format_sample_percent": "nan",
        })
        self.assertTrue(any("有限数字" in issue for issue in issues))

    def test_unified_scan_excludes_validation_and_maps_hash_timeout(
        self,
    ) -> None:
        args = gui.build_tool_args("scan", {
            "scan_mode": "full",
            "start_mode": "new",
            "roots": _RUNTIME_ROOT,
            "hash_mode": "full",
            "format_validation": "all",
            "raw_deep_validation": True,
            "timeout_action": "skip_and_record",
        })
        self.assertNotIn("--format-validation", args)
        self.assertNotIn("--raw-deep-validation", args)
        self.assertEqual(
            "skip_and_record",
            args[args.index("--timeout-action") + 1],
        )
        field_keys = {spec.key for spec in gui.TASK_BY_KEY["scan"].fields}
        self.assertTrue({
            "format_validation", "format_sample_percent",
            "raw_deep_validation",
        }.isdisjoint(field_keys))


class TestRawCapabilityPresentation(unittest.TestCase):
    @staticmethod
    def available_payload() -> dict[str, object]:
        return {
            "id": gui.envcap.RAW_CAPABILITY_ID,
            "title": gui.envcap.RAW_CAPABILITY_TITLE,
            "state": "available",
            "available": True,
            "version": "0.synthetic",
            "reason": None,
            "provider": "rawpy/LibRaw",
            "isolated": True,
            "details": {
                "worker_reaped": True,
                "libraw_version": "0.libraw",
            },
        }

    def test_missing_nonisolated_and_available_have_direct_reasons(self) -> None:
        available, reason = gui.raw_runtime_capability_status({})
        self.assertFalse(available)
        self.assertIn("尚未检测", reason)
        payload = self.available_payload()
        payload["isolated"] = False
        available, reason = gui.raw_runtime_capability_status({
            gui.envcap.RAW_CAPABILITY_ID: payload,
        })
        self.assertFalse(available)
        self.assertIn("隔离能力证据不完整", reason)
        payload = self.available_payload()
        available, reason = gui.raw_runtime_capability_status({
            gui.envcap.RAW_CAPABILITY_ID: payload,
        })
        self.assertTrue(available)
        self.assertIn("rawpy 0.synthetic", reason)
        self.assertIn("LibRaw 0.libraw", reason)

    def test_runtime_event_updates_allowlisted_capability(self) -> None:
        app = object.__new__(gui.DaisyApp)
        app.runtime_capabilities = {}
        app.saved_values = {}
        app.task = types.SimpleNamespace(key="verify")
        app.form_inner = object()
        app.values = {}
        app._refresh_environment_status_buttons = Mock()
        app._build_form = Mock()
        app._update_preview = Mock()
        app._apply_runtime_capabilities({
            "capabilities": {
                gui.envcap.RAW_CAPABILITY_ID: self.available_payload(),
                "arbitrary": {"state": "available"},
            },
        })
        self.assertEqual(
            {gui.envcap.RAW_CAPABILITY_ID},
            set(app.runtime_capabilities),
        )
        app._refresh_environment_status_buttons.assert_called_once_with()
        app._build_form.assert_called_once_with()
        app._update_preview.assert_not_called()

    def test_verification_external_tools_require_completed_inventory(
        self,
    ) -> None:
        pending = gui.verification_tool_availability(
            inventory_received=False,
            detected_tools={
                "exiftool": {
                    "path": r"C:\Stale\exiftool.exe",
                    "verified": True,
                },
            },
            runtime_capabilities={
                gui.envcap.RAW_CAPABILITY_ID: self.available_payload(),
            },
        )
        self.assertTrue(all(
            not available and reason == "需要先运行环境检测"
            for available, reason in pending.values()
        ))

        completed = gui.verification_tool_availability(
            inventory_received=True,
            detected_tools={
                "exiftool": {
                    "path": r"C:\Tools\exiftool.exe",
                    "version": "13.59",
                    "verified": True,
                },
                "ffprobe": {
                    "path": r"C:\Stale\ffprobe.exe",
                    "version": "8.0",
                    "verified": True,
                },
            },
            missing_names=("ffprobe", "sevenzip"),
            missing_reasons={"ffprobe": "合成缺失"},
            runtime_capabilities={
                gui.envcap.RAW_CAPABILITY_ID: self.available_payload(),
            },
        )
        self.assertTrue(completed["verify_exiftool"][0])
        self.assertFalse(completed["verify_ffprobe"][0])
        self.assertEqual(completed["verify_ffprobe"][1], "合成缺失")
        self.assertFalse(completed["verify_sevenzip"][0])
        self.assertTrue(completed["raw_deep_validation"][0])


class TestScanControlProtocol(unittest.TestCase):
    def _control_app(self) -> tuple[gui.DaisyApp, _ProcessProbe]:
        app = object.__new__(gui.DaisyApp)
        process = _ProcessProbe()
        app.process = process
        app.process_task_key = "full_scan"
        app.scan_control_sequence = 0
        app._append_log = Mock()
        app._set_status = Mock()
        return app, process

    def test_control_messages_are_strict_monotonic_utf8_jsonl(self) -> None:
        app, process = self._control_app()
        self.assertEqual(app._send_scan_control("pause"), 1)
        self.assertEqual(app._send_scan_control(
            "timeout_decision",
            worker_pid=9182,
            decision="skip_and_record",
        ), 2)
        payloads = [
            json.loads(line)
            for line in process.stdin.getvalue().decode("utf-8").splitlines()
        ]
        self.assertEqual([item["sequence"] for item in payloads], [1, 2])
        self.assertEqual(
            payloads[0]["protocol"], dbrun.CONTROL_PROTOCOL)
        self.assertEqual(payloads[1]["worker_pid"], 9182)
        self.assertEqual(payloads[1]["decision"], "skip_and_record")

    def test_control_messages_are_isolated_between_gui_instances(self) -> None:
        first, first_process = self._control_app()
        second, second_process = self._control_app()
        second.process_task_key = "verify"
        self.assertEqual(first._send_scan_control("stop"), 1)
        self.assertEqual(second._send_scan_control("pause"), 1)
        first_command = dbrun.decode_control_line(first_process.stdin.getvalue())
        second_command = dbrun.decode_control_line(
            second_process.stdin.getvalue())
        self.assertEqual(first_command.action, "stop")
        self.assertEqual(second_command.action, "pause")
        self.assertEqual(first.scan_control_sequence, 1)
        self.assertEqual(second.scan_control_sequence, 1)

    def test_lifecycle_buttons_follow_running_and_paused_states(self) -> None:
        app = object.__new__(gui.DaisyApp)
        app.process = object()
        app.process_task_key = "full_scan"
        app.pause_scan_button = _ButtonProbe()
        app.mini_pause_button = _ButtonProbe()
        app.save_scan_button = _ButtonProbe()
        app.mini_save_button = _ButtonProbe()
        app.run_button = _ButtonProbe()
        app.stop_button = _ButtonProbe()
        app.mini_stop_button = _ButtonProbe()
        app._layout_action_buttons = Mock()
        for state, pause_text, pause_state in (
                ("running", "暂停", "normal"),
                ("pause_requested", "继续", "disabled"),
                ("paused", "继续", "normal"),
                ("save_exit_requested", "暂停", "disabled")):
            app.scan_control_state = state
            app._refresh_scan_controls()
            self.assertEqual(
                app.pause_scan_button.options["text"], pause_text)
            self.assertEqual(
                app.pause_scan_button.options["state"], pause_state)
            if state == "pause_requested":
                self.assertEqual(
                    app.save_scan_button.options["state"], "disabled")
        app.scan_control_state = "paused"
        app._refresh_scan_controls()
        self.assertEqual(app.save_scan_button.options["state"], "normal")
        self.assertEqual(app.stop_button.options["state"], "normal")

    def test_verify_can_pause_and_stop_but_cannot_save_resume_state(self) \
            -> None:
        app = object.__new__(gui.DaisyApp)
        app.process = object()
        app.process_task_key = "verify"
        app.pause_scan_button = _ButtonProbe()
        app.mini_pause_button = _ButtonProbe()
        app.save_scan_button = _ButtonProbe()
        app.mini_save_button = _ButtonProbe()
        app.run_button = _ButtonProbe()
        app.stop_button = _ButtonProbe()
        app.mini_stop_button = _ButtonProbe()
        app._layout_action_buttons = Mock()
        app.scan_control_state = "running"
        app._refresh_scan_controls()
        self.assertEqual(app.pause_scan_button.options["state"], "normal")
        self.assertEqual(app.stop_button.options["state"], "normal")
        self.assertEqual(app.save_scan_button.options["state"], "disabled")

    def test_terminal_event_closes_owned_control_pipe(self) -> None:
        app, process = self._control_app()
        app.scan_control_state = "running"
        app.save_exit_requested = False
        app._set_status = Mock()
        app._refresh_scan_controls = Mock()
        app._apply_gui_event({
            "event": "run_saved", "state": "save_exit", "stage": "hash"})
        self.assertTrue(process.stdin.closed)
        self.assertTrue(app.save_exit_requested)

    def test_worker_opens_stdin_only_for_controlled_scan(self) -> None:
        captures: list[object] = []

        def popen_probe(*_args, **kwargs):
            captures.append(kwargs["stdin"])
            process = _ProcessProbe(
                with_stdin=kwargs["stdin"] == subprocess.PIPE)
            return process

        app = object.__new__(gui.DaisyApp)
        app.process_task_key = "full_scan"
        app.stop_requested = False
        app.events = queue.Queue()
        app._terminate_process = Mock()
        with patch.object(gui.subprocess, "Popen", side_effect=popen_probe):
            app._worker(["python", "scan"], {}, True)
            app.process_task_key = "env_check"
            app._worker(["python", "env-check"], {}, False)
        self.assertEqual(captures, [subprocess.PIPE, subprocess.DEVNULL])
        app._terminate_process.assert_not_called()

    def test_scan_stop_uses_owned_stdin_not_process_signal(self) -> None:
        app, process = self._control_app()
        app.root = object()
        app.run_jobs = [gui.RunJob("Archive", {})]
        app.scan_control_state = "running"
        app.scan_control_previous_state = "idle"
        app.stop_requested = False
        app._set_stop_state = Mock()
        app._refresh_scan_controls = Mock()
        app._set_status = Mock()
        app._terminate_process = Mock()
        with patch.object(gui.messagebox, "askyesno", return_value=True):
            app._stop()
        command = dbrun.decode_control_line(process.stdin.getvalue())
        self.assertEqual(command.action, "stop")
        app._terminate_process.assert_not_called()
        self.assertTrue(app.stop_requested)

    def test_runtime_close_requests_safe_save_not_signal(self) -> None:
        app, _process = self._control_app()
        app.root = types.SimpleNamespace(destroy=Mock())
        app.run_jobs = [gui.RunJob("Archive", {})]
        app.worker_starting = False
        app.confirm_close_when_idle = False
        app.stop_requested = False
        app.save_exit_requested = False
        app.scan_control_state = "running"
        app.close_after_stop = False
        app._set_stop_state = Mock()
        app._save_gui_preferences = Mock()
        app._request_save_scan_progress = Mock(return_value=True)
        app._terminate_process = Mock()
        with patch.object(
                gui.messagebox, "askyesno", side_effect=(True, True)):
            app._on_close()
        app._request_save_scan_progress.assert_called_once_with()
        app._terminate_process.assert_not_called()
        self.assertTrue(app.close_after_stop)


class TestControlledScanSubprocess(unittest.TestCase):
    """仅管理本测试直接创建且持有句柄的 Quick 扫描子进程。"""

    def setUp(self) -> None:
        os.makedirs(_RUNTIME_ROOT, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(
            prefix="process_", dir=_RUNTIME_ROOT)
        self.base = self._temporary.name
        self.source = os.path.join(self.base, "Archive")
        self.output = os.path.join(self.base, "Snapshots")
        self.temp = os.path.join(self.base, "temp")
        for path in (self.source, self.output, self.temp):
            os.makedirs(path)
        self.processes: list[subprocess.Popen[bytes]] = []
        self.reader_threads: list[threading.Thread] = []

    def tearDown(self) -> None:
        for process in self.processes:
            if process.poll() is None:
                # 只回收本测试通过 Popen 精确创建并仍持有句柄的子进程。
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()
        for thread in self.reader_threads:
            thread.join(timeout=2)
        self._temporary.cleanup()

    def _populate(self, count: int) -> None:
        for index in range(count):
            path = os.path.join(self.source, f"文件_{index:05d}.txt")
            with open(path, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(f"synthetic {index}\n")

    def _environment(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update({
            "DAISY_GUI_PROGRESS": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUTF8": "1",
            "PYTHONUNBUFFERED": "1",
            "TEMP": self.temp,
            "TMP": self.temp,
        })
        return env

    def _start(self) -> tuple[
            subprocess.Popen[bytes], queue.Queue[dict[str, object]]]:
        process = subprocess.Popen(
            [
                sys.executable, "-B", _CLI, "scan",
                "--mode", "quick",
                "--root", f"档案={self.source}",
                "--output-dir", self.output,
                "--control-stdin", "--quiet",
            ],
            cwd=_REPO_ROOT,
            env=self._environment(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self.processes.append(process)
        events: queue.Queue[dict[str, object]] = queue.Queue()
        assert process.stdout is not None

        def read_events() -> None:
            for line in iter(process.stdout.readline, b""):
                if not line.startswith(_GUI_PREFIX):
                    continue
                try:
                    payload = json.loads(
                        line[len(_GUI_PREFIX):].decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if isinstance(payload, dict):
                    events.put(payload)

        reader = threading.Thread(
            target=read_events,
            name="DAISY-Test-Owned-Stdout",
            daemon=True,
        )
        self.reader_threads.append(reader)
        reader.start()
        return process, events

    def _wait_event(
        self,
        events: queue.Queue[dict[str, object]],
        name: str,
        *,
        timeout: float = 30.0,
        predicate=None,
    ) -> dict[str, object]:
        deadline = time.monotonic() + timeout
        observed: list[str] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.fail(
                    f"等待 {name} 超时；已观察：{observed[-20:]}")
            payload = events.get(timeout=remaining)
            event_name = str(payload.get("event") or "")
            observed.append(event_name)
            if event_name == name and (
                    predicate is None or predicate(payload)):
                return payload

    @staticmethod
    def _send(
        process: subprocess.Popen[bytes],
        sequence: int,
        action: str,
    ) -> None:
        assert process.stdin is not None
        process.stdin.write(dbrun.encode_control_command(
            dbrun.ControlCommand(sequence, action)))
        process.stdin.flush()

    def _resume_to_publication(self, partial: str) -> None:
        completed = subprocess.run(
            [
                sys.executable, "-B", _CLI, "scan",
                "--mode", "quick", "--resume", partial,
                "--manual-resume", "--quiet",
            ],
            cwd=_REPO_ROOT,
            env=self._environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=60,
            check=False,
        )
        self.assertEqual(
            completed.returncode, 0,
            completed.stdout.decode("utf-8", errors="replace"))
        published = [
            name for name in os.listdir(self.output)
            if name.endswith(".sqlite")
            and not name.endswith(".partial.sqlite")
        ]
        self.assertEqual(len(published), 1, published)

    def test_pause_save_exit_and_new_process_resume(self) -> None:
        self._populate(1200)
        process, events = self._start()
        self._wait_event(events, "run_started")
        self._send(process, 1, "pause")
        self._wait_event(events, "run_paused")
        self._send(process, 2, "save_exit")
        result = self._wait_event(
            events, "run_result",
            predicate=lambda payload: payload.get("state") == "save_exit",
        )
        assert process.stdin is not None
        process.stdin.close()
        self.assertEqual(process.wait(timeout=30), 75)
        partial = str(result["partial"])
        self.assertTrue(os.path.isfile(partial))
        self._resume_to_publication(partial)

    def test_sudden_owned_child_termination_is_recoverable(self) -> None:
        self._populate(1800)
        process, events = self._start()
        self._wait_event(events, "run_started")
        self._wait_event(events, "progress_start")
        # 模拟突然终止时只操作上方 _start 返回的精确子进程句柄。
        process.terminate()
        process.wait(timeout=10)
        partials = [
            os.path.join(self.output, name)
            for name in os.listdir(self.output)
            if name.endswith(".partial.sqlite")
        ]
        self.assertEqual(len(partials), 1, partials)
        self._resume_to_publication(partials[0])

    def test_exited_owned_process_is_not_a_live_lease_owner(self) -> None:
        process = subprocess.Popen(
            [sys.executable, "-B", "-c", "pass"],
            cwd=_REPO_ROOT,
            env=self._environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.processes.append(process)
        process.wait(timeout=10)
        self.assertFalse(
            core._pid_alive(process.pid),
            "Windows 进程对象仍有父端句柄时，已退出 PID 也不能算 active",
        )

    def test_open_control_pipe_does_not_block_spawn_hash_worker(self) -> None:
        """父端保持控制管道打开时，spawn worker 仍须启动并干净退出。"""
        process = subprocess.Popen(
            [sys.executable, "-B", _HASH_WORKER_FIXTURE],
            cwd=_REPO_ROOT,
            env=self._environment(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self.processes.append(process)
        output = process.stdout.read() if process.stdout is not None else b""
        returncode = process.wait(timeout=15)
        self.assertEqual(
            returncode,
            0,
            output.decode("utf-8", errors="replace"),
        )
        self.assertIn(b"outcome=completed worker_reaped=True", output)


class TestRecoveryPreferences(unittest.TestCase):
    def test_recovery_pointer_round_trip_does_not_persist_form_values(self) \
            -> None:
        os.makedirs(_RUNTIME_ROOT, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=_RUNTIME_ROOT) as directory:
            path = os.path.join(directory, "GUI_Settings.json")
            partial = os.path.join(directory, "Saved.partial.sqlite")
            preferences = gui.default_gui_preferences()
            preferences["last_task_key"] = "full_scan"
            preferences["recovery_scans"] = [{
                "task_key": "full_scan", "partial": partial}]
            gui.save_gui_preferences(preferences, path)
            loaded = gui.load_gui_preferences(path)
        self.assertEqual(loaded["recovery_scans"], [{
            "task_key": "scan", "scan_mode": "full",
            "partial": partial,
        }])
        self.assertNotIn("saved_values", loaded)
        self.assertNotIn("roots", loaded)


if __name__ == "__main__":
    unittest.main(verbosity=2)

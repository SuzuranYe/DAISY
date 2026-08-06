"""DAISY v1.6.0 统一扫描 GUI 控制链测试。

只使用工作区内合成路径、内存管道和本测试精确创建的 Tcl/Tk 窗口；不枚举、
附加或终止其它进程。
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
import Script_DAISY_Lib_DBS_01_Core as core
import Script_DAISY_Lib_DBS_09_Run as dbrun


_RUNTIME_ROOT = os.path.join(
    _REPO_ROOT, ".test_runtime", "v1_6_0", "gui_scan")
_MAIN = os.path.join(_SCRIPT_DIR, "Script_DAISY_MAIN.py")
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

    def test_raw_is_format_subordinate_and_maps_timeout_without_hash(
        self,
    ) -> None:
        args = gui.build_tool_args("full_scan", {
            "roots": _RUNTIME_ROOT,
            "hash_mode": "none",
            "format_validation": "all",
            "raw_deep_validation": True,
            "timeout_action": "skip_and_record",
        })
        self.assertIn("--raw-deep-validation", args)
        self.assertEqual(
            "skip_and_record",
            args[args.index("--timeout-action") + 1],
        )
        issues = gui.validate_values("full_scan", {
            "roots": _RUNTIME_ROOT,
            "format_validation": "off",
            "raw_deep_validation": True,
        })
        self.assertTrue(any("必须先启用" in issue for issue in issues))


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
        app.environment_capability_label = None
        app.task = types.SimpleNamespace(key="full_scan")
        app._refresh_scan_advanced_values = Mock()
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
        app._refresh_scan_advanced_values.assert_called_once_with()
        app._update_preview.assert_called_once_with()


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

    def test_lifecycle_buttons_follow_running_and_paused_states(self) -> None:
        app = object.__new__(gui.DaisyApp)
        app.process = object()
        app.process_task_key = "full_scan"
        app.pause_scan_button = _ButtonProbe()
        app.mini_pause_button = _ButtonProbe()
        app.save_scan_button = _ButtonProbe()
        app.mini_save_button = _ButtonProbe()
        app.stop_button = _ButtonProbe()
        app.mini_stop_button = _ButtonProbe()
        for state, pause_text, pause_state in (
                ("running", "暂停", "normal"),
                ("pause_requested", "暂停", "disabled"),
                ("paused", "继续", "normal"),
                ("save_exit_requested", "暂停", "disabled")):
            app.scan_control_state = state
            app._refresh_scan_controls()
            self.assertEqual(
                app.pause_scan_button.options["text"], pause_text)
            self.assertEqual(
                app.pause_scan_button.options["state"], pause_state)
        app.scan_control_state = "paused"
        app._refresh_scan_controls()
        self.assertEqual(app.save_scan_button.options["state"], "normal")
        self.assertEqual(app.stop_button.options["state"], "normal")

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
                sys.executable, "-B", _MAIN, "scan",
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
                sys.executable, "-B", _MAIN, "scan",
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


@unittest.skipUnless(os.name == "nt", "DAISY GUI 只支持 Windows")
class TestRealTkScanControls(unittest.TestCase):
    def setUp(self) -> None:
        preferences = gui.default_gui_preferences()
        self.preference_patch = patch.object(
            gui, "load_gui_preferences", return_value=preferences)
        self.preference_patch.start()
        self.root = gui.tk.Tk()
        self.root.withdraw()
        self.app = gui.DaisyApp(self.root)
        self.root.update()

    def tearDown(self) -> None:
        self.app._close_timeout_dialog()
        self.app._destroy_root()
        self.preference_patch.stop()

    def test_current_file_uses_middle_ellipsis_and_full_tooltip(self) -> None:
        value = "前" * 90 + "\\中间\\" + "后" * 90 + ".dng"
        self.app._set_current_file(value)
        self.root.update_idletasks()
        shown = self.app.current_file_label.cget("text")
        self.assertLessEqual(len(shown), 110)
        self.assertIn("…", shown)
        self.assertTrue(shown.startswith("前"))
        self.assertTrue(shown.endswith(".dng"))
        self.assertEqual(self.app.current_file_tooltip.text, value)

    def test_raw_capability_gates_menu_preview_and_environment_card(
        self,
    ) -> None:
        self.app._select_task("full_scan", save_current=False)
        self.root.update_idletasks()
        menu = self.app.scan_format_sample_menu
        index = self.app.scan_raw_menu_index
        self.assertEqual(menu.entrycget(index, "state"), "disabled")
        self.assertIn("尚未检测", menu.entrycget(index, "label"))

        available = TestRawCapabilityPresentation.available_payload()
        self.app._apply_runtime_capabilities({
            "capabilities": {
                gui.envcap.RAW_CAPABILITY_ID: available,
            },
        })
        self.app._set_scan_advanced_value("format_validation", "all")
        self.root.update_idletasks()
        self.assertEqual(menu.entrycget(index, "state"), "normal")
        self.assertIn("隔离探测通过", menu.entrycget(index, "label"))

        self.app._set_scan_advanced_value("raw_deep_validation", True)
        self.root.update_idletasks()
        self.assertTrue(self.app.scan_raw_deep_validation_var.get())
        self.assertIs(
            self.app.saved_values["full_scan"]["raw_deep_validation"],
            True,
        )
        self.assertIn("--raw-deep-validation", self.app.preview_var.get())

        self.app._select_task("env_check", save_current=False)
        self.root.update_idletasks()
        self.assertIn(
            "RAW 深度校验：可用",
            self.app.environment_capability_label.cget("text"),
        )
        unavailable = dict(available)
        unavailable.update({
            "state": "unavailable",
            "available": False,
            "reason": "合成能力失效",
        })
        self.app._apply_runtime_capabilities({
            "capabilities": {
                gui.envcap.RAW_CAPABILITY_ID: unavailable,
            },
        })
        self.root.update_idletasks()
        self.assertIn(
            "合成能力失效",
            self.app.environment_capability_label.cget("text"),
        )

        self.app._select_task("full_scan", save_current=False)
        self.root.update_idletasks()
        self.assertEqual(menu.entrycget(index, "state"), "disabled")
        self.assertIn("合成能力失效", menu.entrycget(index, "label"))
        self.assertIs(
            self.app.saved_values["full_scan"]["raw_deep_validation"],
            False,
        )
        self.assertNotIn("--raw-deep-validation", self.app.preview_var.get())

    def test_default_continue_keeps_timeout_choice_available(self) -> None:
        payload = {
            "event": "threshold_reached",
            "file": "素材/大文件.mov",
            "worker_pid": 7001,
            "threshold_seconds": 90,
            "threshold_count": 1,
        }
        self.app._apply_gui_event(payload)
        self.root.update_idletasks()
        self.assertIsNotNone(self.app.timeout_dialog)
        self.assertIsNone(self.root.grab_current())
        self.app._apply_gui_event({
            "event": "threshold_decided",
            "worker_pid": 7001,
            "decision": "continue_waiting",
            "decision_source": "default",
        })
        self.assertIsNotNone(self.app.timeout_dialog)
        self.app._apply_gui_event({
            "event": "stage_finished", "stage": "hash"})
        self.assertIsNone(self.app.timeout_dialog)

    def test_recovery_card_requires_explicit_user_action(self) -> None:
        partial = os.path.join(_RUNTIME_ROOT, "Resume.partial.sqlite")
        with patch.object(gui, "save_gui_preferences"):
            self.app._add_recovery_scan("full_scan", partial)
        self.root.update_idletasks()
        self.assertTrue(self.app.recovery_card.winfo_manager())
        self.assertEqual(self.app.saved_values, {})
        with (
            patch.object(gui.messagebox, "askyesno", return_value=True),
            patch.object(gui, "save_gui_preferences"),
        ):
            self.app._prepare_latest_recovery()
        self.assertEqual(self.app.task.key, "full_scan")
        self.assertEqual(
            self.app.saved_values["full_scan"]["start_mode"], "resume")
        self.assertEqual(
            self.app.saved_values["full_scan"]["resume"],
            os.path.abspath(partial),
        )

    def test_inline_recovery_card_keeps_full_page_fit_at_1080p(self) -> None:
        partial = os.path.join(_RUNTIME_ROOT, "Long_Restart.partial.sqlite")
        self.app._select_task("full_scan", save_current=False)
        with patch.object(gui, "save_gui_preferences"):
            self.app._add_recovery_scan("full_scan", partial)
        self.root.update()
        bounds = self.app.form_canvas.bbox("all")
        content_height = (
            0 if bounds is None else int(bounds[3]) - int(bounds[1]))
        self.assertLessEqual(
            content_height, self.app.form_canvas.winfo_height())
        self.assertFalse(self.app.form_scroll.winfo_manager())
        self.assertEqual(self.app.recovery_path_tooltip.text, partial)

    def test_saved_queue_does_not_draw_incomplete_stage_as_complete(self) \
            -> None:
        self.app.run_jobs = [
            gui.RunJob("A", {}), gui.RunJob("B", {})]
        self.app.run_results = [75]
        self.app.run_queue_started = time.monotonic() - 1
        self.app.save_exit_requested = True
        self.app.stop_requested = False
        self.app.progress_stage_bar.configure(value=37)
        self.app._finish_queue_progress()
        self.assertEqual(
            float(self.app.progress_stage_bar.cget("value")), 37.0)
        self.assertEqual(self.app.progress_percent_label.cget("text"), "已保存")
        self.assertEqual(
            float(self.app.queue_progress_bar.cget("value")), 50.0)


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
            "task_key": "full_scan", "partial": partial}])
        self.assertNotIn("saved_values", loaded)
        self.assertNotIn("roots", loaded)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""DAISY v1.6.1 统一扫描 GUI 控制链测试。

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
import Script_DAISY_Test_Tree as tree_fixture


_RUNTIME_ROOT = os.path.join(
    _REPO_ROOT, ".test_runtime", "v1_6_1", "gui_scan")
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

    def test_verify_can_pause_and_stop_but_cannot_save_resume_state(self) \
            -> None:
        app = object.__new__(gui.DaisyApp)
        app.process = object()
        app.process_task_key = "verify"
        app.pause_scan_button = _ButtonProbe()
        app.mini_pause_button = _ButtonProbe()
        app.save_scan_button = _ButtonProbe()
        app.mini_save_button = _ButtonProbe()
        app.stop_button = _ButtonProbe()
        app.mini_stop_button = _ButtonProbe()
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

    def test_top_six_buttons_share_one_row_size_and_stay_stable(self) \
            -> None:
        try:
            self.root.attributes("-alpha", 0.0)
        except gui.tk.TclError:
            pass
        self.root.geometry("1840x1020+0+0")
        self.root.deiconify()
        self.root.update()
        self.app._preferred_normal_size = (1840, 1020)
        self.app._monitor_signature = None
        self.app._refresh_monitor_layout()
        self.root.update()
        buttons = self.app.task_toolbar_buttons
        self.assertEqual(tuple(buttons), gui._TASK_TOOLBAR_KEYS)
        self.assertTrue(all(
            len(button.cget("text")) == gui._FORM_FIELD_TITLE_MAX_CHARS
            for button in buttons.values()
        ))
        self.assertEqual(
            {int(button.grid_info()["row"]) for button in buttons.values()},
            {0},
        )
        initial_sizes = {
            key: (button.winfo_width(), button.winfo_height())
            for key, button in buttons.items()
        }
        widths = [size[0] for size in initial_sizes.values()]
        heights = [size[1] for size in initial_sizes.values()]
        self.assertLessEqual(max(widths) - min(widths), 1)
        self.assertEqual(len(set(heights)), 1)
        self.assertGreaterEqual(min(heights), 42)
        for key in gui._TASK_TOOLBAR_KEYS:
            self.app._select_task_from_toolbar(key)
            self.root.update()
            self.assertEqual(
                initial_sizes,
                {
                    item_key: (
                        button.winfo_width(), button.winfo_height())
                    for item_key, button in buttons.items()
                },
                key,
            )

    def test_raw_capability_gates_verify_button_preview_and_environment_status(
        self,
    ) -> None:
        self.app._select_task("verify", save_current=False)
        self.root.update()
        tools = self.app.values["verify_builtin"]
        self.assertIsInstance(tools, gui.VerificationToolButtonGroup)
        raw_control = tools.controls["raw_deep_validation"]
        self.assertFalse(raw_control.enabled)
        self.assertFalse(raw_control.get())

        available = TestRawCapabilityPresentation.available_payload()
        self.app._apply_runtime_capabilities({
            "capabilities": {
                gui.envcap.RAW_CAPABILITY_ID: available,
            },
        })
        self.root.update()
        tools = self.app.values["verify_builtin"]
        raw_control = tools.controls["raw_deep_validation"]
        self.assertTrue(raw_control.enabled)
        raw_control._toggle()
        self.root.update_idletasks()
        self.assertTrue(raw_control.get())
        self.assertIn("--raw-deep-validation", self.app.preview_var.get())

        self.app._select_task("env_check", save_current=False)
        self.root.update()
        raw_status = self.app.environment_status_buttons["rawpy"]
        self.assertIn("可用", raw_status.cget("text"))
        self.assertEqual(raw_status.cget("background"), gui._GREEN_DARK)
        self.assertIn(
            "rawpy 0.synthetic",
            self.app.environment_status_tooltips["rawpy"].text,
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
        self.root.update()
        raw_status = self.app.environment_status_buttons["rawpy"]
        self.assertIn("缺失", raw_status.cget("text"))
        self.assertEqual(raw_status.cget("background"), gui._DANGER_SOFT)
        self.assertIn(
            "合成能力失效",
            self.app.environment_status_tooltips["rawpy"].text,
        )

        self.app._select_task("verify", save_current=False)
        self.root.update()
        tools = self.app.values["verify_builtin"]
        raw_control = tools.controls["raw_deep_validation"]
        self.assertFalse(raw_control.enabled)
        self.assertFalse(raw_control.get())
        self.assertIs(
            self.app.saved_values["verify"]["raw_deep_validation"],
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
        self.assertEqual(self.app.task.key, "scan")
        self.assertEqual(
            self.app.saved_values["scan"]["scan_mode"], "full")
        self.assertEqual(
            self.app.saved_values["scan"]["start_mode"], "resume")
        self.assertEqual(
            self.app.saved_values["scan"]["resume"],
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

    def test_database_parse_detection_populates_modules_and_refits_1080p(
        self,
    ) -> None:
        temporary = tempfile.TemporaryDirectory(
            prefix="parse_gui_", dir=_RUNTIME_ROOT)
        self.addCleanup(temporary.cleanup)
        tree = os.path.join(temporary.name, "Tree")
        snapshots = os.path.join(temporary.name, "Snapshots")
        os.makedirs(tree)
        os.makedirs(snapshots)
        tree_fixture.write(tree, "中文目录/样本.txt", b"gui-parse")
        snapshot = tree_fixture.build_snapshot(
            tree, snapshots, "GuiParse", label="解析夹具",
            hash_mode="none",
        )

        self.app._select_task("parse_db", save_current=False)
        self.app.values["database"].set(snapshot)
        self.app._detect_parse_database()
        deadline = time.monotonic() + 10
        while self.app.parse_detection_active \
                and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.root.update()

        self.assertFalse(self.app.parse_detection_active)
        self.assertIsNotNone(self.app.parse_inspection)
        self.assertEqual(
            self.app.parse_inspection.compatibility_mode,
            "v1.4.1-compatible",
        )
        self.assertTrue(self.app.settings_expanded)
        self.assertFalse(self.app.progress_expanded)
        self.assertFalse(self.app.log_expanded)
        pool = self.app.values["parse_modules"]
        self.assertIsInstance(pool, gui.ParseModulePool)
        self.assertFalse(pool.editable)
        self.assertIn("--preset human-summary", self.app.preview_var.get())
        self.assertIn("--format html", self.app.preview_var.get())
        self.assertIn("--format xlsx", self.app.preview_var.get())
        bounds = self.app.form_canvas.bbox("all")
        content_height = 0 if bounds is None else int(bounds[3] - bounds[1])
        self.assertLessEqual(
            content_height, int(self.app.form_canvas.winfo_height()))

        values = self.app._collect_values()
        self.assertEqual(
            gui.validate_values(
                "parse_db", values,
                parse_inspection=self.app.parse_inspection,
            ),
            [],
        )

        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)

        preset = next(
            widget for widget in descendants(self.app.form_inner)
            if (isinstance(widget, gui.ttk.Combobox)
                and getattr(widget, "_daisy_field_key", None) == "preset")
        )
        preset.current(2)
        preset.event_generate("<<ComboboxSelected>>")
        self.root.update()
        pool = self.app.values["parse_modules"]
        self.assertTrue(pool.editable)
        pool.clear_selection()
        self.assertTrue(gui.validate_values(
            "parse_db", self.app._collect_values(),
            parse_inspection=self.app.parse_inspection,
        ))
        pool.select_all()
        self.assertEqual(gui.validate_values(
            "parse_db", self.app._collect_values(),
            parse_inspection=self.app.parse_inspection,
        ), [])
        self.assertIn("--include", self.app.preview_var.get())

        base_scaling = float(self.root.tk.call("tk", "scaling"))
        families = self.app._available_ui_font_families()[:2]
        geometries = ((1840, 1020), (1440, 900), (1280, 720), (1100, 850))
        combinations = 0
        try:
            for scaling in (1.0, 1.25, 1.5):
                self.root.tk.call("tk", "scaling", base_scaling * scaling)
                for family in families:
                    for _label, size_delta in gui._UI_FONT_SIZE_OPTIONS:
                        self.app._set_ui_font(
                            family=family, size_delta=size_delta,
                            persist=False,
                        )
                        for width, height in geometries:
                            self.root.geometry(f"{width}x{height}+0+0")
                            self.root.update()
                            context = (
                                f"scale={scaling} family={family} "
                                f"size={size_delta} geometry={width}x{height}"
                            )
                            content_height = self.app._form_content_height()
                            viewport_height = self.app.form_canvas.winfo_height()
                            if content_height <= viewport_height:
                                self.assertFalse(
                                    self.app.form_scroll.winfo_manager(), context)
                            else:
                                self.assertEqual(
                                    self.app.form_scroll.winfo_manager(),
                                    "pack", context,
                                )
                                self.app.form_canvas.yview_moveto(1.0)
                                self.root.update_idletasks()
                                self.assertGreater(
                                    float(self.app.form_canvas.yview()[0]),
                                    0.0, context,
                                )
                                self.app.form_canvas.yview_moveto(0.0)
                            pool = self.app.values["parse_modules"]
                            host_left = pool.card_host.winfo_rootx()
                            host_right = host_left + pool.card_host.winfo_width()
                            for card in pool.cards:
                                self.assertGreaterEqual(
                                    card.winfo_rootx(), host_left, context)
                                self.assertLessEqual(
                                    card.winfo_rootx() + card.winfo_width(),
                                    host_right + 1, context,
                                )
                                checkbox = next(
                                    child for child in card.winfo_children()
                                    if isinstance(child, gui.tk.Checkbutton)
                                )
                                self.assertGreaterEqual(
                                    checkbox.winfo_width() + 1,
                                    checkbox.winfo_reqwidth(), context,
                                )
                            combinations += 1
        finally:
            self.root.tk.call("tk", "scaling", base_scaling)
            self.app._set_ui_font(
                family=gui._UI_FONT_FAMILY, size_delta=0,
                persist=False,
            )
        self.assertEqual(
            combinations,
            3 * len(families) * len(gui._UI_FONT_SIZE_OPTIONS)
            * len(geometries),
        )

    def test_database_parse_failure_clears_stale_modules_and_keeps_diagnostics(
        self,
    ) -> None:
        temporary = tempfile.TemporaryDirectory(
            prefix="parse_failure_", dir=_RUNTIME_ROOT)
        self.addCleanup(temporary.cleanup)
        tree = os.path.join(temporary.name, "Tree")
        snapshots = os.path.join(temporary.name, "Snapshots")
        os.makedirs(tree)
        os.makedirs(snapshots)
        tree_fixture.write(tree, "有效.txt", b"valid")
        snapshot = tree_fixture.build_snapshot(
            tree, snapshots, "ParseFailure", hash_mode="none")
        invalid = tree_fixture.write(
            temporary.name, "Invalid.sqlite", b"not a sqlite database")

        def wait_for_detection() -> None:
            deadline = time.monotonic() + 10
            while self.app.parse_detection_active \
                    and time.monotonic() < deadline:
                self.root.update()
                time.sleep(0.01)
            self.root.update()
            self.assertFalse(self.app.parse_detection_active)

        self.app._select_task("parse_db", save_current=False)
        self.app.values["database"].set(snapshot)
        self.app._detect_parse_database()
        wait_for_detection()
        self.assertIsNotNone(self.app.parse_inspection)
        self.assertTrue(self.app.values["parse_modules"].cards)

        self.app.values["database"].set(invalid)
        with patch.object(gui.messagebox, "showerror") as shown:
            self.app._detect_parse_database()
            wait_for_detection()
        shown.assert_called_once()
        self.assertIsNone(self.app.parse_inspection)
        pool = self.app.values["parse_modules"]
        self.assertIsInstance(pool, gui.ParseModulePool)
        self.assertIsNone(pool.inspection)
        self.assertEqual(pool.cards, [])
        self.assertNotIn(
            "parse_modules", self.app.saved_values.get("parse_db", {}))
        self.assertTrue(self.app.settings_expanded)
        self.assertTrue(self.app.progress_expanded)
        self.assertTrue(self.app.log_expanded)
        self.assertEqual(
            self.app.progress_stage_label.cget("text"), "数据库识别失败")

    def test_database_parse_detection_shows_progress_before_result(self) \
            -> None:
        temporary = tempfile.TemporaryDirectory(
            prefix="parse_pending_", dir=_RUNTIME_ROOT)
        self.addCleanup(temporary.cleanup)
        tree = os.path.join(temporary.name, "Tree")
        snapshots = os.path.join(temporary.name, "Snapshots")
        os.makedirs(tree)
        os.makedirs(snapshots)
        tree_fixture.write(tree, "等待.txt", b"pending")
        snapshot = tree_fixture.build_snapshot(
            tree, snapshots, "ParsePending", hash_mode="none")
        inspection = gui.dbparse.inspect_parse_database(
            snapshot, verify_integrity=False)
        entered = threading.Event()
        release = threading.Event()

        def delayed_inspection(_path, *, verify_integrity):
            self.assertFalse(verify_integrity)
            entered.set()
            if not release.wait(5):
                raise RuntimeError("测试未释放数据库识别线程")
            return inspection

        self.app._select_task("parse_db", save_current=False)
        self.app.values["database"].set(snapshot)
        with patch.object(
                gui.dbparse, "inspect_parse_database",
                side_effect=delayed_inspection):
            self.app._detect_parse_database()
            self.assertTrue(entered.wait(2))
            self.root.update()
            self.assertTrue(self.app.parse_detection_active)
            self.assertFalse(self.app.settings_expanded)
            self.assertTrue(self.app.progress_expanded)
            self.assertTrue(self.app.log_expanded)
            self.assertEqual(str(self.app.run_button.cget("state")), "disabled")
            release.set()
            deadline = time.monotonic() + 10
            while self.app.parse_detection_active \
                    and time.monotonic() < deadline:
                self.root.update()
                time.sleep(0.01)
            self.root.update()
        self.assertFalse(self.app.parse_detection_active)
        self.assertTrue(self.app.settings_expanded)
        self.assertFalse(self.app.progress_expanded)
        self.assertFalse(self.app.log_expanded)

    def test_visible_binary_fields_use_toggle_buttons_not_comboboxes(
        self,
    ) -> None:
        self.assertEqual(self.app.binary_control_style, "buttons")
        self.app._apply_runtime_capabilities({
            "capabilities": {
                gui.envcap.RAW_CAPABILITY_ID:
                TestRawCapabilityPresentation.available_payload(),
            },
        })

        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)

        self.app.saved_values["scan"] = {
            "scan_mode": "full", "start_mode": "new"}
        self.app._select_task("scan", save_current=False)
        self.root.update()
        file_id = self.app.values["collect_file_id"]
        self.assertIsInstance(file_id, gui.BooleanToggleButton)
        self.assertEqual(
            file_id.button.cget("background"),
            gui._GREEN_DARK if file_id.get() else gui._AMBER,
        )

        self.app._select_task("verify", save_current=False)
        self.root.update()
        verify_tools = self.app.values["verify_builtin"]
        self.assertIsInstance(verify_tools, gui.VerificationToolButtonGroup)
        self.assertEqual(len(verify_tools.controls), 5)
        self.assertTrue(all(
            isinstance(control, gui.BooleanToggleButton)
            for control in verify_tools.controls.values()
        ))
        for control in verify_tools.controls.values():
            self.assertEqual(
                control.button.cget("background"),
                gui._GREEN_DARK if control.get() else gui._AMBER,
            )

        self.app._select_task("storage_collect", save_current=False)
        self.root.update()
        summary = self.app.values["summary_txt"]
        self.assertIsInstance(summary, gui.BooleanToggleButton)
        self.assertEqual(
            summary.button.cget("background"),
            gui._GREEN_DARK if summary.get() else gui._AMBER,
        )

        widgets = list(descendants(self.app.form_inner))
        self.assertFalse(any(
            isinstance(widget, gui.ttk.Combobox)
            and getattr(widget, "_daisy_field_key", None) == "summary_txt"
            for widget in widgets
        ))
    def test_scan_requires_mode_choice_before_expanding_settings(self) \
            -> None:
        self.app.saved_values.pop("scan", None)
        self.app._select_task("scan", save_current=False)
        self.root.update()

        self.assertEqual(set(self.app.values), {"scan_mode"})
        mode = self.app.values["scan_mode"]
        self.assertIsInstance(mode, gui.ChoiceButtonGroup)
        self.assertEqual(mode.get(), "")
        self.assertEqual(
            tuple(button.cget("text") for button in mode.buttons.values()),
            ("完整扫描", "快速扫描"),
        )

        mode.buttons["full"].invoke()
        self.root.update()
        self.assertEqual(self.app._collect_values()["scan_mode"], "full")
        selected_mode = self.app.values["scan_mode"].buttons["full"]
        self.assertEqual(
            selected_mode.cget("background"),
            gui._BLOCK_SELECTION_BACKGROUND,
        )
        self.assertEqual(
            selected_mode.cget("foreground"),
            gui._BLOCK_SELECTION_FOREGROUND,
        )
        self.assertEqual(set(self.app.values), {"scan_mode", "start_mode"})
        start_mode = self.app.values["start_mode"]
        self.assertIsInstance(start_mode, gui.ChoiceButtonGroup)
        start_mode.buttons["new"].invoke()
        self.root.update()
        self.assertIn("metadata_exiftool", self.app.values)
        self.assertIn("hash_mode", self.app.values)
        self.assertIsInstance(
            self.app.values["root_batch_mode"], gui.ChoiceButtonGroup)
        for key in ("metadata_storage", "hash_mode"):
            self.assertIsInstance(
                self.app.values[key], gui.ValueToggleButton, key)
        self.assertIsInstance(
            self.app.values["collect_file_id"], gui.BooleanToggleButton)
        self.assertNotIn("format_validation", self.app.values)
        self.assertNotIn("raw_deep_validation", self.app.values)
        self.assertNotIn("format_sample_percent", self.app.values)
        self.assertNotIn("previous_snapshot", self.app.values)
        self.assertNotIn("map_root", self.app.values)
        self.assertLessEqual(
            self.app._form_content_height(),
            self.app.form_canvas.winfo_height(),
            "完整扫描模式的默认设置应在 1080P 可视区内完整显示",
        )
        self.assertFalse(self.app.form_scroll.winfo_manager())

        mode = self.app.values["scan_mode"]
        mode.buttons["quick"].invoke()
        self.root.update()
        self.assertEqual(self.app._collect_values()["scan_mode"], "quick")
        self.assertIn("roots", self.app.values)
        self.assertIn("collect_file_id", self.app.values)
        self.assertNotIn("metadata_exiftool", self.app.values)
        self.assertNotIn("format_validation", self.app.values)
        self.assertNotIn("hash_mode", self.app.values)
        self.assertLessEqual(
            self.app._form_content_height(),
            self.app.form_canvas.winfo_height(),
            "快速扫描模式的默认设置应在 1080P 可视区内完整显示",
        )
        self.assertFalse(self.app.form_scroll.winfo_manager())

    def test_metadata_tool_buttons_are_independent_and_size_stable(
        self,
    ) -> None:
        self.app.saved_values["scan"] = {
            "scan_mode": "full", "start_mode": "new"}
        self.app._select_task("scan", save_current=False)
        self.root.update()
        tools = self.app.values["metadata_exiftool"]
        self.assertIsInstance(tools, gui.MetadataToolButtonGroup)
        before = {
            "exif": (
                tools.exiftool_button.button.winfo_width(),
                tools.exiftool_button.button.winfo_height(),
            ),
            "ffprobe": (
                tools.ffprobe_button.button.winfo_width(),
                tools.ffprobe_button.button.winfo_height(),
            ),
        }
        gap = (
            tools.ffprobe_button.button.winfo_rootx()
            - tools.exiftool_button.button.winfo_rootx()
            - tools.exiftool_button.button.winfo_width()
        )
        self.assertEqual(gap, 8)
        self.assertNotIn("--no-metadata-exiftool", self.app.preview_var.get())
        self.assertNotIn("--no-metadata-ffprobe", self.app.preview_var.get())

        tools.exiftool_button._toggle()
        self.root.update_idletasks()
        self.assertFalse(tools.exiftool_button.get())
        self.assertTrue(tools.ffprobe_button.get())
        self.assertIn("--no-metadata-exiftool", self.app.preview_var.get())
        self.assertNotIn("--no-metadata-ffprobe", self.app.preview_var.get())
        self.assertEqual(
            before["exif"],
            (tools.exiftool_button.button.winfo_width(),
             tools.exiftool_button.button.winfo_height()),
        )

        self.app._apply_runtime_capabilities({
            "capabilities": {
                gui.envcap.RAW_CAPABILITY_ID:
                TestRawCapabilityPresentation.available_payload(),
            },
        })
        self.root.update()
        tools = self.app.values["metadata_exiftool"]
        self.assertFalse(tools.exiftool_button.get())
        self.assertTrue(tools.ffprobe_button.get())

        tools.ffprobe_button._toggle()
        self.root.update_idletasks()
        self.assertIn("--no-metadata-ffprobe", self.app.preview_var.get())
        self.assertEqual(
            before["ffprobe"],
            (tools.ffprobe_button.button.winfo_width(),
             tools.ffprobe_button.button.winfo_height()),
        )

    def test_scan_two_state_values_use_fixed_buttons_without_sampling(
        self,
    ) -> None:
        self.app.saved_values["scan"] = {
            "scan_mode": "full", "start_mode": "new"}
        self.app._select_task("scan", save_current=False)
        self.root.update()
        expected = {
            "metadata_storage": "complete",
            "hash_mode": "full",
        }
        for key, value in expected.items():
            control = self.app.values[key]
            self.assertIsInstance(control, gui.ValueToggleButton, key)
            self.assertEqual(control.get(), value, key)
            expected_colour = (
                gui._GREEN_DARK
                if value in ("complete", "full", "combined", "all")
                else gui._AMBER
            )
            self.assertEqual(
                control.button.cget("background"), expected_colour, key)

        metadata_control = self.app.values["metadata_storage"]
        metadata_size = (
            metadata_control.button.winfo_width(),
            metadata_control.button.winfo_height(),
        )
        metadata_control._toggle()
        self.root.update_idletasks()
        self.assertEqual(metadata_control.get(), "normalized")
        self.assertEqual(
            metadata_size,
            (metadata_control.button.winfo_width(),
             metadata_control.button.winfo_height()),
        )
        self.assertIn(
            "--metadata-storage normalized", self.app.preview_var.get())

        hash_control = self.app.values["hash_mode"]
        hash_control._toggle()
        self.root.update_idletasks()
        self.assertEqual(hash_control.get(), "none")
        self.assertIn("--hash none", self.app.preview_var.get())

        self.app.values["roots"].add_value(r"C:\ArchiveA")
        self.app.values["roots"].add_value(r"C:\ArchiveB")
        generation_control = self.app.values["root_batch_mode"]
        self.assertIsInstance(generation_control, gui.ChoiceButtonGroup)
        generation_control.buttons["combined"].invoke()
        self.root.update()
        self.assertEqual(generation_control.get(), "combined")
        combined_values = self.app._collect_values()
        self.assertEqual(
            len(gui.build_run_jobs("scan", combined_values)), 1)
        self.assertEqual(self.app.preview_var.get().count("--root "), 2)

        self.assertNotIn("format_validation", self.app.values)
        self.assertNotIn("raw_deep_validation", self.app.values)
        self.assertNotIn("--format-validation", self.app.preview_var.get())
        self.assertNotIn("--format-sample-percent", self.app.preview_var.get())

    def test_verify_tools_are_independent_same_row_and_default_off(
        self,
    ) -> None:
        self.app._select_task("verify", save_current=False)
        self.root.update()
        tools = self.app.values["verify_builtin"]
        self.assertIsInstance(tools, gui.VerificationToolButtonGroup)
        self.assertEqual(tuple(tools.controls), (
            "verify_builtin", "verify_exiftool", "verify_ffprobe",
            "verify_sevenzip", "raw_deep_validation",
        ))
        self.assertTrue(all(not control.get()
                            for control in tools.controls.values()))
        buttons = [control.button for control in tools.controls.values()]
        self.assertEqual({button.winfo_height() for button in buttons},
                         {buttons[0].winfo_height()})
        button_widths = [button.winfo_width() for button in buttons]
        self.assertLessEqual(max(button_widths) - min(button_widths), 1)
        self.assertEqual({button.winfo_y() for button in buttons},
                         {buttons[0].winfo_y()})
        self.assertIn("--hash off", self.app.preview_var.get())
        self.assertIn("--format off", self.app.preview_var.get())

        self.app._apply_runtime_capabilities({
            "capabilities": {
                gui.envcap.RAW_CAPABILITY_ID:
                TestRawCapabilityPresentation.available_payload(),
            },
        })
        self.root.update()
        tools = self.app.values["verify_builtin"]
        tools.controls["verify_exiftool"]._toggle()
        tools.controls["raw_deep_validation"]._toggle()
        self.root.update_idletasks()
        preview = self.app.preview_var.get()
        self.assertIn("--format all", preview)
        self.assertIn("--format-tool exiftool", preview)
        self.assertNotIn("--format-tool ffprobe", preview)
        self.assertNotIn("--format-tool sevenzip", preview)
        self.assertIn("--raw-deep-validation", preview)

        tools.controls["verify_exiftool"]._toggle()
        self.root.update_idletasks()
        preview = self.app.preview_var.get()
        self.assertIn("--format off", preview)
        self.assertIn("--raw-deep-validation", preview)

    def test_binary_style_switch_preserves_value_preview_and_same_selection(
        self,
    ) -> None:
        self.app.saved_values["scan"] = {
            "scan_mode": "full", "start_mode": "new"}
        self.app._select_task("scan", save_current=False)
        self.root.update()
        toggle = self.app.values["collect_file_id"]
        self.assertIsInstance(toggle, gui.BooleanToggleButton)
        original_size = (
            toggle.button.winfo_width(), toggle.button.winfo_height())
        toggle._toggle()
        self.root.update_idletasks()
        self.assertFalse(toggle.get())
        self.assertEqual(
            original_size,
            (toggle.button.winfo_width(), toggle.button.winfo_height()),
        )
        preview = self.app.preview_var.get()
        self.assertIn("--no-file-id", preview)

        with patch.object(gui, "save_gui_preferences") as saved:
            self.app._set_binary_control_style("dropdowns")
        self.root.update()
        saved.assert_called_once()
        self.assertEqual(self.app.binary_control_style, "dropdowns")
        self.assertEqual(self.app.binary_control_style_var.get(), "dropdowns")
        self.assertIsInstance(
            self.app.values["collect_file_id"], gui.tk.StringVar)
        self.assertFalse(self.app._collect_values()["collect_file_id"])
        self.assertEqual(self.app.preview_var.get(), preview)

        combobox = next(
            widget for widget in self.app.form_inner.winfo_children()
            for widget in self._descendants(widget)
            if (isinstance(widget, gui.ttk.Combobox)
                and getattr(widget, "_daisy_field_key", None)
                == "collect_file_id")
        )
        displayed = combobox.get()
        self.assertTrue(displayed)
        selected_index = combobox.current()
        self.assertGreaterEqual(selected_index, 0)
        combobox.current(selected_index)
        combobox.event_generate("<<ComboboxSelected>>")
        self.root.update()
        self.assertTrue(combobox.winfo_exists())
        self.assertEqual(combobox.get(), displayed)
        self.assertEqual(self.app.preview_var.get(), preview)

        self.app._set_binary_control_style("buttons", persist=False)
        self.root.update()
        restored = self.app.values["collect_file_id"]
        self.assertIsInstance(restored, gui.BooleanToggleButton)
        self.assertFalse(restored.get())
        self.assertEqual(self.app.preview_var.get(), preview)

    def test_dropdown_style_keeps_verification_buttons_and_raw_gate(self) \
            -> None:
        self.app._set_binary_control_style("dropdowns", persist=False)
        self.app._select_task("verify", save_current=False)
        self.root.update()
        tools = self.app.values["verify_builtin"]
        self.assertIsInstance(tools, gui.VerificationToolButtonGroup)
        self.assertFalse(tools.controls["raw_deep_validation"].enabled)
        self.assertFalse(any(
            isinstance(widget, gui.ttk.Combobox)
            and getattr(widget, "_daisy_field_key", None)
            == "raw_deep_validation"
            for widget in self._descendants(self.app.form_inner)
        ))

        self.app._apply_runtime_capabilities({
            "capabilities": {
                gui.envcap.RAW_CAPABILITY_ID:
                TestRawCapabilityPresentation.available_payload(),
            },
        })
        self.root.update()
        tools = self.app.values["verify_builtin"]
        self.assertIsInstance(tools, gui.VerificationToolButtonGroup)
        self.assertTrue(tools.controls["raw_deep_validation"].enabled)

    def test_binary_styles_font_size_geometry_and_scaling_matrix(self) -> None:
        self.app._apply_runtime_capabilities({
            "capabilities": {
                gui.envcap.RAW_CAPABILITY_ID:
                TestRawCapabilityPresentation.available_payload(),
            },
        })
        base_scaling = float(self.root.tk.call("tk", "scaling"))
        geometries = ((1840, 1020), (1366, 768), (1100, 850))
        pages = (
            ("scan", "collect_file_id", {
                "scan_mode": "full", "start_mode": "new"}),
            ("storage_collect", "summary_txt", {}),
        )
        checks = 0
        try:
            for _style_label, style in gui._BINARY_CONTROL_STYLE_OPTIONS:
                self.app._set_binary_control_style(style, persist=False)
                for scaling in (1.0, 1.5):
                    self.root.tk.call(
                        "tk", "scaling", base_scaling * scaling)
                    for _size_label, size_delta in gui._UI_FONT_SIZE_OPTIONS:
                        self.app._set_ui_font(
                            size_delta=size_delta, persist=False)
                        for width, height in geometries:
                            self.root.geometry(f"{width}x{height}+0+0")
                            for task_key, field_key, saved in pages:
                                self.app.saved_values[task_key] = dict(saved)
                                self.app._select_task(
                                    task_key, save_current=False)
                                self.root.update()
                                context = (
                                    f"style={style} scale={scaling} "
                                    f"size={size_delta} geometry={width}x{height} "
                                    f"task={task_key}"
                                )
                                value_source = self.app.values[field_key]
                                if style == "buttons":
                                    self.assertIsInstance(
                                        value_source,
                                        gui.BooleanToggleButton,
                                        context,
                                    )
                                    control = value_source.button
                                else:
                                    self.assertIsInstance(
                                        value_source, gui.tk.StringVar, context)
                                    control = next(
                                        widget for widget in self._descendants(
                                            self.app.form_inner)
                                        if (isinstance(widget, gui.ttk.Combobox)
                                            and getattr(
                                                widget,
                                                "_daisy_field_key",
                                                None,
                                            ) == field_key)
                                    )
                                    self.assertTrue(control.get(), context)
                                self.assertGreaterEqual(
                                    control.winfo_width() + 1,
                                    control.winfo_reqwidth(),
                                    context,
                                )
                                content_height = (
                                    self.app._form_content_height())
                                viewport_height = (
                                    self.app.form_canvas.winfo_height())
                                if content_height <= viewport_height:
                                    self.assertFalse(
                                        self.app.form_scroll.winfo_manager(),
                                        context,
                                    )
                                else:
                                    self.assertEqual(
                                        self.app.form_scroll.winfo_manager(),
                                        "pack",
                                        context,
                                    )
                                    self.app.form_canvas.yview_moveto(1.0)
                                    self.root.update_idletasks()
                                    self.assertGreater(
                                        float(self.app.form_canvas.yview()[0]),
                                        0.0,
                                        context,
                                    )
                                checks += 1
        finally:
            self.root.tk.call("tk", "scaling", base_scaling)
            self.app._set_ui_font(size_delta=0, persist=False)
            self.app._set_binary_control_style("buttons", persist=False)
            self.root.geometry("1840x1020+0+0")
            self.root.update()
        self.assertEqual(
            checks,
            len(gui._BINARY_CONTROL_STYLE_OPTIONS)
            * 2
            * len(gui._UI_FONT_SIZE_OPTIONS)
            * len(geometries)
            * len(pages),
        )

    @staticmethod
    def _descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from TestRealTkScanControls._descendants(child)


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

"""v1.6.0 RAW 隔离能力探测与每文件深度解码 worker 专项测试。"""
from __future__ import annotations

import ast
import os
import sys
import tempfile
import time
import types
import unittest


_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPT_DIR = os.path.dirname(_TEST_DIR)
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
_LIB_DIR = os.path.join(_SCRIPT_DIR, "Lib")
sys.path[:0] = [_TEST_DIR, _SCRIPT_DIR, _LIB_DIR]

import Script_DAISY_Lib_File_Hash as dbhash
import Script_DAISY_Lib_Raw_Verify as dbraw
import Script_DAISY_Lib_Environment_Capabilities as envcap


_RUNTIME_ROOT = os.path.join(
    _REPO_ROOT, ".test_runtime", "v1_6_0", "raw_worker")


def _send_probe(connection, payload: dict[str, object]) -> None:
    try:
        connection.send(payload)
    finally:
        connection.close()


def _probe_available(connection) -> None:
    _send_probe(connection, {
        "state": "available",
        "version": "0.test",
        "libraw_version": "0.synthetic",
    })


def _probe_unavailable(connection) -> None:
    _send_probe(connection, {
        "state": "unavailable",
        "reason": "synthetic not installed",
    })


def _probe_incompatible(connection) -> None:
    _send_probe(connection, {
        "state": "incompatible",
        "version": "0.broken",
        "reason": "synthetic API mismatch",
    })


def _probe_crashed(_connection) -> None:
    os._exit(37)


def _probe_slow(_connection) -> None:
    time.sleep(30.0)


def _worker_send(connection, payload: dict[str, object]) -> None:
    try:
        connection.send({"kind": "ready"})
        connection.recv()
        connection.send({"kind": "result", **payload})
    finally:
        connection.close()


def _worker_valid(connection) -> None:
    _worker_send(connection, {
        "status": "valid",
        "code": None,
        "detail": None,
        "rawpy_version": "0.test",
        "libraw_version": "0.synthetic",
        "width": 12,
        "height": 8,
        "channels": 3,
        "pixel_count": 288,
        "decoded_bytes": 288,
    })


def _worker_unsupported(connection) -> None:
    _worker_send(connection, {
        "status": "unsupported",
        "code": "raw_unsupported",
        "detail": None,
        "rawpy_version": "0.test",
        "libraw_version": "0.synthetic",
    })


def _worker_invalid(connection) -> None:
    _worker_send(connection, {
        "status": "invalid",
        "code": "decode_error",
        "detail": "synthetic truncated RAW",
        "rawpy_version": "0.test",
        "libraw_version": "0.synthetic",
    })


def _worker_memory_error(connection) -> None:
    _worker_send(connection, {
        "status": "error",
        "code": "memory_error",
        "detail": "synthetic MemoryError",
        "rawpy_version": "0.test",
        "libraw_version": "0.synthetic",
    })


def _worker_crashed(connection) -> None:
    connection.send({"kind": "ready"})
    connection.recv()
    os._exit(39)


def _worker_slow(connection) -> None:
    try:
        connection.send({"kind": "ready"})
        connection.recv()
        time.sleep(30.0)
    finally:
        connection.close()


def _worker_delayed_valid(connection) -> None:
    try:
        connection.send({"kind": "ready"})
        connection.recv()
        time.sleep(0.16)
        connection.send({
            "kind": "result",
            "status": "valid",
            "code": None,
            "detail": None,
            "rawpy_version": "0.test",
            "libraw_version": "0.synthetic",
            "width": 2,
            "height": 2,
            "channels": 3,
            "pixel_count": 12,
            "decoded_bytes": 12,
        })
    finally:
        connection.close()


def _worker_with_fake_rawpy(connection) -> None:
    """走生产 child 入口，证明成功必须实际调用 postprocess。"""
    class Pixels:
        shape = (5, 7, 3)
        size = 105
        nbytes = 105

    class Raw:
        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _traceback):
            return False

        @staticmethod
        def postprocess():
            return Pixels()

    fake = types.SimpleNamespace(
        __version__="0.fake",
        libraw_version=(0, 1, 2),
        imread=lambda _path: Raw(),
    )
    previous = sys.modules.get("rawpy")
    sys.modules["rawpy"] = fake
    try:
        dbraw._raw_decode_worker_child(connection)
    finally:
        if previous is None:
            sys.modules.pop("rawpy", None)
        else:
            sys.modules["rawpy"] = previous


class TestRuntimeCapabilities(unittest.TestCase):
    def test_structured_available_unavailable_and_incompatible(self) -> None:
        before = "rawpy" in sys.modules
        cases = (
            (_probe_available, "available", True),
            (_probe_unavailable, "unavailable", False),
            (_probe_incompatible, "incompatible", False),
        )
        for target, state, available in cases:
            with self.subTest(state=state):
                result = envcap.probe_rawpy_capability(
                    timeout_seconds=2.0,
                    poll_seconds=0.01,
                    _probe_target=target,
                )
                self.assertEqual(result.state, state)
                self.assertEqual(result.available, available)
                self.assertTrue(result.isolated)
                self.assertTrue(result.details["worker_reaped"])
                self.assertEqual(result.details["worker_exitcode"], 0)
        self.assertEqual("rawpy" in sys.modules, before)

    def test_crash_and_timeout_are_isolated_and_reaped(self) -> None:
        crashed = envcap.probe_rawpy_capability(
            timeout_seconds=2.0,
            poll_seconds=0.01,
            _probe_target=_probe_crashed,
        )
        self.assertEqual(crashed.state, "crashed")
        self.assertTrue(crashed.details["worker_reaped"])
        self.assertEqual(crashed.details["worker_exitcode"], 37)

        timed_out = envcap.probe_rawpy_capability(
            timeout_seconds=0.1,
            poll_seconds=0.01,
            _probe_target=_probe_slow,
        )
        self.assertEqual(timed_out.state, "timeout")
        self.assertTrue(timed_out.details["worker_reaped"])
        self.assertIsNotNone(timed_out.details["worker_exitcode"])

    def test_registry_rejects_unknown_capability(self) -> None:
        with self.assertRaises(KeyError):
            envcap.probe_runtime_capabilities(("unknown",))

    def test_rawpy_imports_are_not_top_level(self) -> None:
        for module in (envcap, dbraw):
            with open(module.__file__, encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
            top_level_rawpy = []
            for node in tree.body:
                if isinstance(node, ast.Import):
                    top_level_rawpy.extend(
                        alias.name for alias in node.names
                        if alias.name == "rawpy")
                elif isinstance(node, ast.ImportFrom) \
                        and node.module == "rawpy":
                    top_level_rawpy.append(node.module)
            self.assertEqual(top_level_rawpy, [])


class TestRawWorker(unittest.TestCase):
    def setUp(self) -> None:
        os.makedirs(_RUNTIME_ROOT, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(
            prefix="case_", dir=_RUNTIME_ROOT)
        self.path = os.path.join(self._temporary.name, "fixture.dng")
        with open(self.path, "wb") as handle:
            handle.write(b"synthetic-raw-worker-input")

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def run_worker(self, target, **kwargs) -> dbraw.RawDecodeOutcome:
        return dbraw.run_raw_decode_worker(
            self.path,
            expected_size=os.path.getsize(self.path),
            poll_seconds=0.01,
            worker_start_timeout_seconds=2.0,
            _worker_target=target,
            **kwargs,
        )

    def test_candidate_set_and_timeout_policy(self) -> None:
        self.assertTrue(dbraw.is_raw_candidate("DNG"))
        self.assertTrue(dbraw.is_raw_candidate(".dng"))
        self.assertTrue(dbraw.is_raw_candidate("folder/photo.CR3"))
        self.assertFalse(dbraw.is_raw_candidate("photo.jpg"))
        step = 9 * 1024 ** 3
        self.assertEqual(dbraw.raw_timeout_for_size(0), 90)
        self.assertEqual(dbraw.raw_timeout_for_size(step), 90)
        self.assertEqual(dbraw.raw_timeout_for_size(step + 1), 180)

    def test_valid_decode_requires_nonempty_pixels_and_clean_reap(self) -> None:
        before = "rawpy" in sys.modules
        outcome = self.run_worker(_worker_valid)
        self.assertTrue(outcome.succeeded)
        self.assertEqual(outcome.status, "valid")
        self.assertEqual((outcome.width, outcome.height, outcome.channels),
                         (12, 8, 3))
        self.assertEqual(outcome.pixel_count, 288)
        self.assertTrue(outcome.worker_reaped)
        self.assertEqual(outcome.worker_exitcode, 0)
        self.assertEqual("rawpy" in sys.modules, before)

    def test_production_child_calls_postprocess_and_discards_pixels(self) -> None:
        outcome = self.run_worker(_worker_with_fake_rawpy)
        self.assertTrue(outcome.succeeded)
        self.assertEqual(outcome.rawpy_version, "0.fake")
        self.assertEqual(outcome.libraw_version, "0.1.2")
        self.assertEqual((outcome.width, outcome.height, outcome.channels),
                         (7, 5, 3))
        self.assertEqual(outcome.pixel_count, 105)

    def test_unsupported_invalid_and_memory_error_remain_distinct(self) -> None:
        cases = (
            (_worker_unsupported, "unsupported", "raw_unsupported"),
            (_worker_invalid, "invalid", "decode_error"),
            (_worker_memory_error, "error", "memory_error"),
        )
        for target, status, code in cases:
            with self.subTest(status=status, code=code):
                outcome = self.run_worker(target)
                self.assertEqual(outcome.outcome, "completed")
                self.assertEqual(outcome.status, status)
                self.assertEqual(outcome.code, code)
                self.assertTrue(outcome.worker_reaped)
                self.assertEqual(outcome.worker_exitcode, 0)

    def test_native_like_exit_is_tool_error_not_parent_crash(self) -> None:
        outcome = self.run_worker(_worker_crashed)
        self.assertEqual(outcome.outcome, "crashed")
        self.assertEqual(outcome.status, "error")
        self.assertEqual(outcome.code, "worker_crashed")
        self.assertTrue(outcome.worker_reaped)
        self.assertEqual(outcome.worker_exitcode, 39)

    def test_timeout_skip_and_stop_reap_exact_worker(self) -> None:
        skipped = self.run_worker(
            _worker_slow,
            timeout_seconds=0.1,
            default_decision="skip_and_record",
        )
        self.assertEqual(skipped.outcome, "timeout")
        self.assertEqual(skipped.status, "timeout")
        self.assertEqual(skipped.decision_source, "advanced_policy")
        self.assertTrue(skipped.worker_reaped)

        stopped = self.run_worker(
            _worker_slow,
            timeout_seconds=0.1,
            default_decision="stop_and_resume",
        )
        self.assertEqual(stopped.outcome, "stopped")
        self.assertEqual(stopped.decision, "stop_and_resume")
        self.assertTrue(stopped.worker_reaped)

    def test_default_continue_waiting_allows_late_success(self) -> None:
        outcome = self.run_worker(
            _worker_delayed_valid,
            timeout_seconds=0.05,
            default_decision="continue_waiting",
        )
        self.assertTrue(outcome.succeeded)
        self.assertGreaterEqual(outcome.threshold_count, 1)
        self.assertEqual(outcome.decision, "continue_waiting")
        self.assertEqual(outcome.decision_source, "default")

    def test_pause_terminates_current_worker_for_file_restart(self) -> None:
        control = dbhash.HashWorkerControl()
        self.assertTrue(control.request_pause("user"))
        outcome = self.run_worker(
            _worker_slow,
            timeout_seconds=5.0,
            control=control,
        )
        self.assertEqual(outcome.outcome, "paused")
        self.assertEqual(outcome.control_action, "pause")
        self.assertTrue(outcome.worker_reaped)

    def test_save_exit_is_distinct_from_in_process_pause(self) -> None:
        control = dbhash.HashWorkerControl()
        self.assertTrue(control.request_save_exit("user"))
        outcome = self.run_worker(
            _worker_slow,
            timeout_seconds=5.0,
            control=control,
        )
        self.assertEqual(outcome.outcome, "save_exit")
        self.assertEqual(outcome.control_action, "save_exit")
        self.assertTrue(outcome.worker_reaped)


if __name__ == "__main__":
    unittest.main(verbosity=2)

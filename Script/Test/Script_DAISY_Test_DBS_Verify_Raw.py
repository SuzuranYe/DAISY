"""v1.6.0 统一核验中的 RAW 从属阶段、能力预检与只读报告测试。"""
from __future__ import annotations

import json
import os
import sys
import unittest


_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPT_DIR = os.path.dirname(_TEST_DIR)
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
_LIB_DIR = os.path.join(_SCRIPT_DIR, "Lib")
_MODULE_DIR = os.path.join(_SCRIPT_DIR, "Module")
sys.path[:0] = [_TEST_DIR, _SCRIPT_DIR, _LIB_DIR, _MODULE_DIR]

import Script_DAISY_Lib_DBS_01_Core as core
import Script_DAISY_Lib_DBS_11_Verify_Run as verifyrun
import Script_DAISY_Lib_DBS_13_Raw as dbraw
import Script_DAISY_Lib_ENV_01_Capabilities as envcap
import Script_DAISY_Module_DBS_30_Verify as verifycli
import Script_DAISY_Test_DBS_Verify_Unified as unified_fixture


def _capability(state: str = "available") -> envcap.RuntimeCapability:
    return envcap.RuntimeCapability(
        envcap.RAW_CAPABILITY_ID,
        envcap.RAW_CAPABILITY_TITLE,
        state,
        version="0.test" if state == "available" else None,
        reason=None if state == "available" else "synthetic unavailable",
        provider="rawpy/LibRaw",
        isolated=True,
        details={
            "libraw_version": "0.synthetic",
            "worker_reaped": True,
        },
    )


def _format_outcome(path: str, status: str = "valid") \
        -> verifyrun.FormatWorkerOutcome:
    return verifyrun.FormatWorkerOutcome(
        outcome="completed",
        status=status,
        detail=None,
        decision="none",
        decision_source="none",
        size_bytes=os.path.getsize(path),
        elapsed_seconds=0.01,
        threshold_count=0,
        worker_pid=12345,
        worker_exitcode=0,
        worker_reaped=True,
        events=(),
    )


def _raw_outcome(path: str, status: str) -> dbraw.RawDecodeOutcome:
    valid = status == "valid"
    outcome = (
        "timeout" if status == "timeout"
        else "crashed" if status == "error"
        else "completed"
    )
    return dbraw.RawDecodeOutcome(
        outcome=outcome,
        status=status,
        code=(
            None if valid
            else "raw_unsupported" if status == "unsupported"
            else "decode_error" if status == "invalid"
            else "raw_no_progress_timeout" if status == "timeout"
            else "worker_crashed"
        ),
        detail=(
            None if status in ("valid", "unsupported")
            else f"synthetic {status}"),
        decision=("skip_and_record" if status == "timeout" else "none"),
        decision_source=("advanced_policy" if status == "timeout" else "none"),
        control_action=None,
        size_bytes=os.path.getsize(path),
        elapsed_seconds=0.01,
        threshold_seconds=90.0,
        threshold_count=1 if status == "timeout" else 0,
        worker_pid=12345,
        worker_exitcode=39 if status == "error" else 0,
        worker_reaped=True,
        rawpy_version="0.test",
        libraw_version="0.synthetic",
        width=12 if valid else None,
        height=8 if valid else None,
        channels=3 if valid else None,
        pixel_count=288 if valid else None,
        decoded_bytes=288 if valid else None,
        events=(),
        events_truncated=False,
    )


class TestVerifyRawOptions(unittest.TestCase):
    def test_raw_is_independent_and_own_timeout_requires_raw(self) -> None:
        raw_only = verifyrun.VerificationOptions(
            hash_mode="off", format_mode="off", raw_deep_validation=True)
        self.assertTrue(raw_only.raw_deep_validation)
        with self.assertRaises(ValueError):
            verifyrun.VerificationOptions(
                format_mode="all", raw_timeout_seconds=90.0)

        args = verifycli.build_parser().parse_args([
            "--snapshot", "fixture.sqlite",
            "--root", "夹具=Current",
            "--raw-deep-validation",
            "--raw-timeout-seconds", "180",
        ])
        options = verifycli.verification_options(args)
        self.assertEqual(options.format_mode, "off")
        self.assertTrue(options.raw_deep_validation)
        self.assertEqual(options.raw_timeout_seconds, 180.0)

    def test_unavailable_capability_precedes_database_or_source_read(self) -> None:
        options = verifyrun.VerificationOptions(
            hash_mode="off",
            format_mode="off",
            raw_deep_validation=True,
        )
        with self.assertRaisesRegex(
                core.PreflightError, "synthetic unavailable"):
            verifyrun.run_unified_verification(
                os.path.join(_REPO_ROOT, "does_not_exist.sqlite"),
                ["夹具=does_not_exist"],
                options=options,
                _raw_capability_probe=lambda: _capability("unavailable"),
            )

        with self.assertRaisesRegex(core.PreflightError, "不是隔离探测"):
            verifyrun.run_unified_verification(
                os.path.join(_REPO_ROOT, "does_not_exist.sqlite"),
                ["夹具=does_not_exist"],
                options=options,
                _raw_capability_probe=lambda: {
                    "state": "available",
                    "version": "0.fake",
                    "isolated": False,
                    "details": {"worker_reaped": True},
                },
            )


class TestUnifiedVerifyRaw(unified_fixture._Fixture):
    @staticmethod
    def _tool_resolver(name, _explicit):
        return {
            "path": f"X:/Synthetic/{name}.exe",
            "version": "0.synthetic",
        }

    def verify_raw(self, snapshot: str, *, raw_runner, **kwargs):
        return verifyrun.run_unified_verification(
            snapshot,
            [f"夹具={self.current}"],
            options=kwargs.pop("options"),
            force=True,
            _raw_capability_probe=lambda: _capability(),
            _tool_resolver=self._tool_resolver,
            _format_runner=kwargs.pop(
                "format_runner",
                lambda path, _kind, _spec, _tools, **_rest:
                    _format_outcome(path),
            ),
            _raw_runner=raw_runner,
            **kwargs,
        )

    def test_disabled_raw_does_not_probe_or_run(self) -> None:
        snapshot = self.snapshot({"image.dng": (b"raw", "photo_raw")})
        baseline = unified_fixture._identity(snapshot)

        def unexpected(*_args, **_kwargs):
            raise AssertionError("RAW 关闭时不应调用能力或 worker")

        report = verifyrun.run_unified_verification(
            snapshot,
            [f"夹具={self.current}"],
            options=verifyrun.VerificationOptions(
                hash_mode="off", format_mode="off"),
            force=True,
            _raw_capability_probe=unexpected,
            _raw_runner=unexpected,
        )
        self.assertEqual(report["sections"]["raw"]["state"], "NULL")
        self.assertEqual(unified_fixture._identity(snapshot), baseline)

    def test_raw_checks_all_candidates_independently_of_format_sample(
        self,
    ) -> None:
        files = {
            f"set/file_{index:03d}.dng": (
                f"payload-{index:03d}".encode("ascii"), "photo_raw")
            for index in range(240)
        }
        snapshot = self.snapshot(files)
        baseline = unified_fixture._identity(snapshot)
        format_paths: list[str] = []
        raw_paths: list[str] = []

        def format_runner(path, _kind, _spec, _tools, **_kwargs):
            format_paths.append(os.path.relpath(path, self.current))
            return _format_outcome(path)

        def raw_runner(path, **_kwargs):
            raw_paths.append(os.path.relpath(path, self.current))
            return _raw_outcome(path, "valid")

        report = self.verify_raw(
            snapshot,
            options=verifyrun.VerificationOptions(
                hash_mode="off",
                format_mode="sample",
                format_sample_percent=10.0,
                raw_deep_validation=True,
            ),
            format_runner=format_runner,
            raw_runner=raw_runner,
        )
        self.assertEqual(len(format_paths), 100)
        self.assertEqual(len(raw_paths), 240)
        self.assertTrue(set(format_paths).issubset(raw_paths))
        raw = report["sections"]["raw"]
        self.assertEqual(raw["raw_candidate_total"], 240)
        self.assertEqual(raw["selected"], 240)
        self.assertEqual(raw["valid"], 240)
        self.assertEqual(raw["problems"], [])
        self.assertEqual(report["conclusion"], "passed")
        self.assertEqual(unified_fixture._identity(snapshot), baseline)

    def test_raw_problem_projection_and_unsupported_privacy(self) -> None:
        snapshot = self.snapshot({
            "valid.dng": (b"valid", "photo_raw"),
            "unsupported.dng": (b"unsupported", "photo_raw"),
            "invalid.dng": (b"invalid", "photo_raw"),
            "timeout.dng": (b"timeout", "photo_raw"),
            "crash.dng": (b"crash", "photo_raw"),
            "ordinary.jpg": (b"jpeg", "photo_jpeg"),
        })
        baseline = unified_fixture._identity(snapshot)
        called: list[str] = []

        def raw_runner(path, **_kwargs):
            name = os.path.basename(path)
            called.append(name)
            status = {
                "valid.dng": "valid",
                "unsupported.dng": "unsupported",
                "invalid.dng": "invalid",
                "timeout.dng": "timeout",
                "crash.dng": "error",
            }[name]
            return _raw_outcome(path, status)

        report = self.verify_raw(
            snapshot,
            options=verifyrun.VerificationOptions(
                hash_mode="off",
                format_mode="off",
                raw_deep_validation=True,
            ),
            raw_runner=raw_runner,
        )
        self.assertEqual(len(called), 5)
        self.assertNotIn("ordinary.jpg", called)
        raw = report["sections"]["raw"]
        self.assertEqual(raw["selected"], 5)
        self.assertEqual(raw["valid"], 1)
        self.assertEqual(raw["unsupported"], 1)
        self.assertEqual(raw["counts"], {
            "error": 1,
            "invalid": 1,
            "timeout": 1,
        })
        self.assertEqual(
            {row["status"] for row in raw["problems"]},
            {"invalid", "timeout", "error"},
        )
        raw_json = json.dumps(raw, ensure_ascii=False)
        self.assertNotIn('"rel_path": "unsupported.dng"', raw_json)
        self.assertNotIn('"rel_path": "valid.dng"', raw_json)
        markdown = verifyrun.render_verification_markdown(report)
        self.assertIn("## RAW 深度校验问题", markdown)
        self.assertIn("invalid.dng", markdown)
        self.assertNotIn("unsupported.dng", markdown)
        self.assertEqual(report["conclusion"], "issues_found")
        self.assertEqual(unified_fixture._identity(snapshot), baseline)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""v1.5.1 兼容核验入口与档案数据核验的业务投影对照。"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock


_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPT_DIR = os.path.dirname(_TEST_DIR)
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
_LIB_DIR = os.path.join(_SCRIPT_DIR, "Lib")
sys.path[:0] = [_TEST_DIR, _SCRIPT_DIR, _LIB_DIR]

import Script_DAISY_Lib_DBS_01_Core as core
import Script_DAISY_Lib_DBS_03_Hash as dbhash
import Script_DAISY_Lib_DBS_06_Verify as legacy
import Script_DAISY_Lib_DBS_11_Verify_Run as verifyrun
import Script_DAISY_Test_DBS_Verify_Unified as unified_fixture


def _relative(path: str, root: str) -> str:
    normalized = path
    if normalized.startswith("\\\\?\\UNC\\"):
        normalized = "\\\\" + normalized[8:]
    elif normalized.startswith("\\\\?\\"):
        normalized = normalized[4:]
    return os.path.relpath(normalized, root).replace("/", "\\")


def _hash_outcome(
    path: str,
    *,
    digest: str | None,
    error: str | None = None,
) -> dbhash.IndependentHashOutcome:
    size = os.path.getsize(path)
    completed = digest is not None
    return dbhash.IndependentHashOutcome(
        outcome="completed" if completed else "error",
        hash_hex=digest,
        error=error,
        decision="none",
        decision_source="none",
        size_bytes=size,
        bytes_read=size if completed else 0,
        final_offset=size if completed else 0,
        elapsed_seconds=0.01,
        active_read_seconds=0.01 if completed else 0.0,
        stall_count=0,
        longest_stall_seconds=0.0,
        first_stall_offset=None,
        last_stall_offset=None,
        threshold_count=0,
        worker_pid=12345,
        worker_exitcode=0 if completed else 1,
        worker_reaped=True,
        events=(),
    )


def _format_outcome(
    path: str,
    status: str,
    detail: str | None,
) -> verifyrun.FormatWorkerOutcome:
    return verifyrun.FormatWorkerOutcome(
        outcome="completed",
        status=status,
        detail=detail,
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


class TestLegacyUnifiedProjection(unified_fixture._Fixture):
    @staticmethod
    def _resolver(_explicit):
        return {
            "path": "X:/Synthetic/powershell.exe",
            "version": "7.4-synthetic",
        }

    def _root_specs(self) -> list[str]:
        return [f"夹具={self.current}"]

    def test_hash_sample_keeps_v151_selected_files(self) -> None:
        files = {
            f"set/file_{index:03d}.bin": (
                f"payload-{index:03d}".encode("ascii"), "other")
            for index in range(240)
        }
        snapshot = self.snapshot(files)
        baseline = unified_fixture._identity(snapshot)
        legacy_paths: list[str] = []
        unified_paths: list[str] = []

        def legacy_batch(paths, **_kwargs):
            legacy_paths.extend(paths)
            return [core.sha256_file(path) for path in paths]

        def unified_runner(path, _powershell, **_kwargs):
            unified_paths.append(path)
            return _hash_outcome(path, digest=core.sha256_file(path))

        with mock.patch.object(
            legacy.dbh, "discover_powershell",
            return_value=("X:/Synthetic/powershell.exe", "7.4-synthetic"),
        ), mock.patch.object(
            legacy.dbh, "get_filehash_batch", side_effect=legacy_batch,
        ):
            old = legacy.patrol_hash(
                snapshot,
                sample_percent=10.0,
                full=False,
                force=True,
                root_specs=self._root_specs(),
            )
        new = verifyrun.run_unified_verification(
            snapshot,
            self._root_specs(),
            options=verifyrun.VerificationOptions(
                hash_mode="sample",
                hash_sample_percent=10.0,
                format_mode="off",
            ),
            force=True,
            _powershell_resolver=self._resolver,
            _hash_runner=unified_runner,
        )
        old_selected = sorted(_relative(path, self.current)
                              for path in legacy_paths)
        new_selected = sorted(_relative(path, self.current)
                              for path in unified_paths)
        self.assertEqual(len(old_selected), 100)
        self.assertEqual(new_selected, old_selected)
        self.assertEqual(new["sections"]["hash"]["checked"],
                         old["hash_checked"])
        self.assertEqual(unified_fixture._identity(snapshot), baseline)

    def test_format_sample_keeps_v151_selected_files(self) -> None:
        files = {
            f"set/file_{index:03d}.zip": (
                f"payload-{index:03d}".encode("ascii"), "archive")
            for index in range(240)
        }
        snapshot = self.snapshot(files)
        baseline = unified_fixture._identity(snapshot)
        legacy_paths: list[str] = []
        unified_paths: list[str] = []

        def legacy_zip(path):
            legacy_paths.append(path)
            return "valid", None

        def unified_runner(path, _kind, _spec, _tools, **_kwargs):
            unified_paths.append(path)
            return _format_outcome(path, "valid", None)

        with mock.patch.object(
                legacy, "validate_zip", side_effect=legacy_zip):
            old = legacy.validate_format_snapshot(
                snapshot,
                sample_percent=10.0,
                report_dir=os.path.join(self.reports, "LegacySample"),
                force=True,
                root_specs=self._root_specs(),
            )
        new = verifyrun.run_unified_verification(
            snapshot,
            self._root_specs(),
            options=verifyrun.VerificationOptions(
                hash_mode="off",
                format_mode="sample",
                format_sample_percent=10.0,
            ),
            force=True,
            _format_runner=unified_runner,
        )
        old_selected = sorted(_relative(path, self.current)
                              for path in legacy_paths)
        new_selected = sorted(_relative(path, self.current)
                              for path in unified_paths)
        self.assertEqual(len(old_selected), 100)
        self.assertEqual(new_selected, old_selected)
        self.assertEqual(new["sections"]["format"]["selected"],
                         old["checked"])
        self.assertEqual(unified_fixture._identity(snapshot), baseline)

    def test_hash_problem_projection_matches_legacy_entry(self) -> None:
        snapshot = self.snapshot({
            "good.bin": (b"good", "other"),
            "mismatch.bin": (b"base", "other"),
            "tool.bin": (b"tool", "other"),
            "changed.bin": (b"same", "other"),
            "missing.bin": (b"gone", "other"),
        })
        baseline = unified_fixture._identity(snapshot)
        with open(os.path.join(self.current, "changed.bin"), "ab") as handle:
            handle.write(b"-changed")
        os.remove(os.path.join(self.current, "missing.bin"))
        legacy_hashed: list[str] = []
        unified_hashed: list[str] = []

        def result_for(path: str) -> str | None:
            name = os.path.basename(path)
            if name == "mismatch.bin":
                return "0" * 64
            if name == "tool.bin":
                return None
            return core.sha256_file(path)

        def legacy_batch(paths, **_kwargs):
            legacy_hashed.extend(paths)
            return [result_for(path) for path in paths]

        def unified_runner(path, _powershell, **_kwargs):
            unified_hashed.append(path)
            digest = result_for(path)
            return _hash_outcome(
                path,
                digest=digest,
                error=None if digest is not None else "synthetic tool error",
            )

        with mock.patch.object(
            legacy.dbh, "discover_powershell",
            return_value=("X:/Synthetic/powershell.exe", "7.4-synthetic"),
        ), mock.patch.object(
            legacy.dbh, "get_filehash_batch", side_effect=legacy_batch,
        ):
            old = legacy.patrol_hash(
                snapshot,
                full=True,
                force=True,
                root_specs=self._root_specs(),
            )
        new = verifyrun.run_unified_verification(
            snapshot,
            self._root_specs(),
            options=verifyrun.VerificationOptions(
                hash_mode="all", format_mode="off"),
            force=True,
            _powershell_resolver=self._resolver,
            _hash_runner=unified_runner,
        )

        old_stat = {
            (row["rel_path"], "missing") for row in old["stat_missing"]
        } | {
            (row["rel_path"], "changed") for row in old["stat_changed"]
        }
        new_stat = {
            (row["rel_path"], row["status"])
            for row in new["sections"]["stat"]["problems"]
        }
        old_hash = {
            (row["rel_path"], "mismatched")
            for row in old["hash_mismatched"]
        } | {
            (row["rel_path"], "tool_error")
            for row in old["hash_tool_error"]
        }
        new_hash = {
            (row["rel_path"], row["status"])
            for row in new["sections"]["hash"]["problems"]
        }
        self.assertEqual(new_stat, old_stat)
        self.assertEqual(new_hash, old_hash)
        self.assertEqual(
            sorted(_relative(path, self.current) for path in unified_hashed),
            sorted(_relative(path, self.current) for path in legacy_hashed),
        )
        self.assertEqual(new["conclusion"], "issues_found")
        self.assertFalse(old["ok"])
        self.assertEqual(unified_fixture._identity(snapshot), baseline)

    def test_format_problem_projection_matches_legacy_entry(self) -> None:
        snapshot = self.snapshot({
            "good.zip": (b"good", "archive"),
            "bad.zip": (b"bad", "archive"),
            "unknown.bin": (b"unknown", "other"),
            "missing.zip": (b"gone", "archive"),
        })
        baseline = unified_fixture._identity(snapshot)
        os.remove(os.path.join(self.current, "missing.zip"))

        def status_for(path: str) -> tuple[str, str | None]:
            if os.path.basename(path) == "bad.zip":
                return "invalid", "synthetic invalid archive"
            return "valid", None

        with mock.patch.object(
                legacy, "validate_zip", side_effect=status_for):
            old = legacy.validate_format_snapshot(
                snapshot,
                sample_percent=100.0,
                report_dir=os.path.join(self.reports, "LegacyFull"),
                force=True,
                root_specs=self._root_specs(),
            )

        def unified_runner(path, _kind, _spec, _tools, **_kwargs):
            status, detail = status_for(path)
            return _format_outcome(path, status, detail)

        new = verifyrun.run_unified_verification(
            snapshot,
            self._root_specs(),
            options=verifyrun.VerificationOptions(
                hash_mode="off", format_mode="all"),
            force=True,
            _format_runner=unified_runner,
        )
        old_problems = {
            (row["rel_path"], row["status"])
            for row in old["rows"]
            if row["status"] in ("invalid", "missing")
        }
        formatted = new["sections"]["format"]
        new_problems = {
            (row["rel_path"], row["status"])
            for row in formatted["problems"]
        }
        self.assertEqual(new_problems, old_problems)
        self.assertEqual(formatted["selected"], old["checked"])
        self.assertEqual(formatted["valid"], old["counts"]["valid"])
        self.assertEqual(
            formatted["unsupported"], old["counts"]["unsupported"])
        self.assertEqual(new["conclusion"], "issues_found")
        self.assertFalse(old["ok"])
        self.assertEqual(unified_fixture._identity(snapshot), baseline)


if __name__ == "__main__":
    unittest.main(verbosity=2)

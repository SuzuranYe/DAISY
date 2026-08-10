"""DAISY v1.6.0 schema 3/4 问题报告分板块与只读兼容测试。"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest


_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPT_DIR = os.path.dirname(_TEST_DIR)
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
_LIB_DIR = os.path.join(_SCRIPT_DIR, "Lib")
sys.path[:0] = [_TEST_DIR, _SCRIPT_DIR, _LIB_DIR]

import Script_DAISY_Lib_Snapshot_Core as core
import Script_DAISY_Lib_Scan_State as dbstate
import Script_DAISY_Lib_Scan_Runtime as dbrun
import Script_DAISY_Lib_Snapshot_Issues as dbissues


_RUNTIME_ROOT = os.path.join(
    _REPO_ROOT, ".test_runtime", "v1_6_0", "issues")


def _identity(path: str) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    stat_result = os.stat(path)
    return digest.hexdigest(), stat_result.st_size, stat_result.st_mtime_ns


class _IssuesFixture(unittest.TestCase):
    def setUp(self) -> None:
        os.makedirs(_RUNTIME_ROOT, exist_ok=True)
        self._td = tempfile.TemporaryDirectory(
            prefix="case_", dir=_RUNTIME_ROOT)
        self.base = self._td.name
        self.root = os.path.join(self.base, "Archive")
        self.output = os.path.join(self.base, "Snapshots")
        os.makedirs(self.root)
        os.makedirs(self.output)

    def tearDown(self) -> None:
        self._td.cleanup()

    def schema3_snapshot(
        self,
        *,
        real_issue: bool,
        diagnostics: tuple[tuple[str, str, str], ...] = (),
        additional_real_issues: int = 0,
    ) -> str:
        files = [
            ("unknown.bin", b"unknown"),
            ("broken.jpg", b"not-a-jpeg"),
        ]
        files.extend(
            (f"broken_{index}.jpg", f"broken-{index}".encode("ascii"))
            for index in range(additional_real_issues)
        )
        for name, payload in files:
            with open(os.path.join(self.root, name), "wb") as stream:
                stream.write(payload)
        partial = os.path.join(self.output, "Legacy.partial.sqlite")
        con = core.create_partial_snapshot(
            partial,
            [("档案", self.root)],
            {
                "phase": "full",
                "hash": "none",
                "metadata_storage": "complete",
            },
        )
        core.enumerate_and_reconcile(con)
        con.execute(
            "UPDATE entries SET meta_status='done',hash_status='skipped'")
        unknown_id = int(con.execute(
            "SELECT entry_id FROM entries WHERE rel_path='unknown.bin'"
        ).fetchone()[0])
        con.execute(
            "UPDATE entries SET meta_status='error' WHERE entry_id=?",
            (unknown_id,),
        )
        con.execute(
            "INSERT INTO errors"
            " (entry_id,stage,error_code,message,occurred_at_utc)"
            " VALUES (?,'metadata','exiftool_reported_error',"
            " 'Unknown file type',?)",
            (unknown_id, core.now_utc_iso()),
        )
        broken_id = int(con.execute(
            "SELECT entry_id FROM entries WHERE rel_path='broken.jpg'"
        ).fetchone()[0])
        if real_issue:
            issue_paths = ["broken.jpg"] + [
                f"broken_{index}.jpg"
                for index in range(additional_real_issues)
            ]
            for issue_path in issue_paths:
                entry_id = int(con.execute(
                    "SELECT entry_id FROM entries WHERE rel_path=?",
                    (issue_path,),
                ).fetchone()[0])
                con.execute(
                    "UPDATE entries SET meta_status='error' WHERE entry_id=?",
                    (entry_id,),
                )
                con.execute(
                    "INSERT INTO errors"
                    " (entry_id,stage,error_code,message,occurred_at_utc)"
                    " VALUES (?,'metadata','exiftool_reported_error',"
                    " 'JPEG format error',?)",
                    (entry_id, core.now_utc_iso()),
                )
            core.ensure_metadata_diagnostics_table(con)
            con.execute(
                "INSERT INTO metadata_diagnostics"
                " (entry_id,provider,severity,diagnostic_code,field_name,"
                " message,raw_value,observed_at_utc)"
                " VALUES (?,'exiftool','warning',"
                " 'ExifTool:Main:Copy123:Warning','Copy123',"
                " 'JPEG format error',NULL,?)",
                (broken_id, core.now_utc_iso()),
            )
        if diagnostics:
            core.ensure_metadata_diagnostics_table(con)
            con.executemany(
                "INSERT INTO metadata_diagnostics"
                " (entry_id,provider,severity,diagnostic_code,field_name,"
                " message,raw_value,observed_at_utc)"
                " VALUES (?,'exiftool',?,?,NULL,?,NULL,?)",
                [
                    (broken_id, severity, code, message, core.now_utc_iso())
                    for severity, code, message in diagnostics
                ],
            )
        con.commit()
        return core.finalize_snapshot(
            con,
            partial,
            "none",
            publish_stem_path=os.path.join(self.output, "Legacy"),
        )

    def schema3_quick_snapshot(self) -> str:
        with open(os.path.join(self.root, "file.bin"), "wb") as stream:
            stream.write(b"quick")
        partial = os.path.join(self.output, "Quick.partial.sqlite")
        con = core.create_partial_snapshot(
            partial,
            [("档案", self.root)],
            {
                "phase": "quick",
                "hash": "none",
                "metadata_storage": "normalized",
            },
        )
        try:
            core.enumerate_and_reconcile(con)
            con.execute(
                "UPDATE entries SET meta_status='skipped',"
                "hash_status='skipped'")
            con.commit()
            return core.finalize_snapshot(
                con,
                partial,
                "none",
                publish_stem_path=os.path.join(self.output, "Quick"),
            )
        except BaseException:
            con.close()
            core.release_scan_lock(partial)
            raise

    def schema4_snapshot(
        self,
        *,
        format_enabled: bool = True,
        format_issue: bool = True,
        performance_confidence: str = "high",
        prior_hash_failure: bool = False,
        runtime_tool_failure: bool = False,
    ) -> str:
        if performance_confidence not in ("none", "low", "high"):
            raise ValueError("测试夹具性能置信度无效")
        for name, payload in (
            ("broken.pdf", b"%PDF-1.4\nmissing trailer"),
            ("unknown.bin", b"unknown"),
        ):
            with open(os.path.join(self.root, name), "wb") as stream:
                stream.write(payload)
        partial = os.path.join(self.output, "Modern.partial.sqlite")
        handle = dbrun.create_run(
            partial,
            [("档案", self.root)],
            {
                "phase": "full",
                "hash": "full",
                "metadata_storage": "complete",
                "format_validation": "all" if format_enabled else "off",
            },
            output_dir=self.output,
            publish_stem_path=os.path.join(self.output, "Modern"),
            tool_versions={
                "exiftool": {"path": "fixture", "version": "13.fixture"},
                "ffprobe": {"path": "fixture", "version": "7.fixture"},
                "sevenzip": {"path": "fixture", "version": "24.fixture"},
            },
        )
        lease_path = handle.lease_path
        lease_id = handle.lease.lease_id
        con = handle.connection
        core.enumerate_and_reconcile(con)
        con.execute("UPDATE entries SET meta_status='done'")
        broken_id = int(con.execute(
            "SELECT entry_id FROM entries WHERE rel_path='broken.pdf'"
        ).fetchone()[0])
        unknown_id = int(con.execute(
            "SELECT entry_id FROM entries WHERE rel_path='unknown.bin'"
        ).fetchone()[0])

        if prior_hash_failure:
            failed_attempt = dbstate.start_attempt(
                con,
                broken_id,
                "hash",
                tool_name="hashlib",
                tool_version="fixture",
            )
            dbstate.finish_attempt(
                con,
                failed_attempt,
                "error",
                error_code="fixture_transient",
                error_message="合成瞬时读取失败",
            )
        hash_attempt = dbstate.start_attempt(
            con,
            broken_id,
            "hash",
            tool_name="hashlib",
            tool_version="fixture",
        )
        dbstate.finish_attempt(
            con,
            hash_attempt,
            "succeeded",
            performance={
                "origin": "computed",
                "size_bytes": os.path.getsize(
                    os.path.join(self.root, "broken.pdf")),
                "bytes_read": 0,
                "elapsed_seconds": 5.0,
                "active_read_seconds": 5.0,
                "stall_count": 1,
                "longest_stall_seconds": 4.0,
                "first_stall_offset": 0,
                "last_stall_offset": 0,
                "final_offset": 0,
                "ended_reason": "completed",
                "candidate_confidence": performance_confidence,
                "candidate_reason": (
                    None if performance_confidence == "none" else
                    "读取性能异常候选：合成置信度夹具"
                ),
            },
        )
        con.execute(
            "UPDATE entries SET hash_status='skipped' WHERE entry_id=?",
            (unknown_id,),
        )
        if format_enabled:
            format_attempt = dbstate.start_attempt(
                con,
                broken_id,
                "format",
                coverage="full",
                validator="pdf",
                tool_name="daisy-format",
                tool_version="daisy-format-v1",
            )
            dbstate.finish_attempt(
                con,
                format_attempt,
                "invalid" if format_issue else "succeeded",
                end_reason=(
                    "format_invalid" if format_issue else "format_valid"),
                stat_match=True,
                detail="缺少 %%EOF 尾" if format_issue else None,
            )
            unsupported_attempt = dbstate.start_attempt(
                con,
                unknown_id,
                "format",
                coverage="full",
                validator="none",
                tool_name="daisy-format",
                tool_version="daisy-format-v1",
            )
            dbstate.finish_attempt(
                con,
                unsupported_attempt,
                "unsupported",
                end_reason="format_unsupported",
                stat_match=True,
            )
        con.execute(
            "UPDATE snapshot_info SET hash_coverage='full' WHERE id=1")
        if runtime_tool_failure:
            dbstate.fail_run(
                con,
                recoverable=True,
                error_code="metadata_tool_circuit_open",
                error_message="ExifTool 连续工具故障，元数据阶段已熔断",
                payload={
                    "reason": "metadata_tool_circuit_open",
                    "tool": "exiftool",
                    "failure_kind": "native_crash",
                    "consecutive_failures": 3,
                    "not_processed": 204913,
                    "first_unprocessed_entry_id": 6194,
                    "last_unprocessed_entry_id": 233079,
                    "not_processed_by_media_kind": {"image": 204913},
                },
            )
            dbstate.start_resume_session(
                con,
                config={
                    "phase": "full",
                    "hash": "full",
                    "metadata_storage": "complete",
                    "format_validation": (
                        "all" if format_enabled else "off"),
                },
                tools={
                    "exiftool": "fixture",
                    "ffprobe": "fixture",
                    "sevenzip": "fixture",
                },
                lease_id=lease_id,
            )
        con.commit()
        dbstate.begin_sealing(con)
        dbstate.mark_sealed_unpublished(con)
        dbrun.close_handle(handle, release_lease=False)
        staging = os.path.join(self.output, "Modern.publish.sqlite")
        result = dbstate.publish_sealed_snapshot(
            partial,
            staging,
            lease_path=lease_path,
            lease_id=lease_id,
            issue_report_builder=(
                dbissues.build_snapshot_issue_report_from_connection),
        )
        return result.final_path

    def schema4_quick_snapshot(self) -> str:
        with open(os.path.join(self.root, "quick.bin"), "wb") as stream:
            stream.write(b"quick-v4")
        partial = os.path.join(self.output, "QuickV4.partial.sqlite")
        handle = dbrun.create_run(
            partial,
            [("档案", self.root)],
            {
                "phase": "quick",
                "quick": True,
                "hash": "none",
                "metadata_storage": "normalized",
                "format_validation": "off",
            },
            output_dir=self.output,
            publish_stem_path=os.path.join(self.output, "QuickV4"),
            tool_versions={},
        )
        lease_path = handle.lease_path
        lease_id = handle.lease.lease_id
        con = handle.connection
        core.enumerate_and_reconcile(con)
        con.execute(
            "UPDATE entries SET meta_status='skipped',hash_status='skipped'")
        con.commit()
        dbstate.begin_sealing(con)
        dbstate.mark_sealed_unpublished(con)
        dbrun.close_handle(handle, release_lease=False)
        result = dbstate.publish_sealed_snapshot(
            partial,
            os.path.join(self.output, "QuickV4.publish.sqlite"),
            lease_path=lease_path,
            lease_id=lease_id,
            issue_report_builder=(
                dbissues.build_snapshot_issue_report_from_connection),
        )
        return result.final_path


class TestIssueSections(_IssuesFixture):
    @staticmethod
    def by_id(analysis: dict[str, object]) -> dict[str, dict[str, object]]:
        return {
            str(section["id"]): section
            for section in analysis["sections"]
        }

    def test_compound_machine_statuses_are_replaced_as_complete_tokens(
        self,
    ) -> None:
        rendered = dbissues._display_statuses(
            "run_state=failed_recoverable；format_status=invalid")
        self.assertEqual(
            "run_state=失败但可续传；format_status=校验失败",
            rendered,
        )
        self.assertNotIn("失败_recoverable", rendered)

    def test_schema3_unsupported_only_is_counted_but_not_reported(self) \
            -> None:
        snapshot = self.schema3_snapshot(real_issue=False)
        baseline = _identity(snapshot)
        analysis = dbissues.analyze_snapshot_issues(snapshot)
        sections = self.by_id(analysis)
        self.assertFalse(analysis["has_reportable_issues"])
        self.assertEqual(("executed", 0, 1), (
            sections["metadata"]["execution"],
            sections["metadata"]["issue_files"],
            sections["metadata"]["information"][
                "unsupported_or_unrecognized_files"],
        ))
        for section_id in ("hash", "format", "performance", "runtime"):
            self.assertEqual("null", sections[section_id]["execution"])
            self.assertIsNone(sections[section_id]["issue_files"])
        self.assertIsNone(dbissues.render_snapshot_issues(analysis))
        clean = dbissues.render_snapshot_issues(
            analysis, include_clean=True)
        self.assertIn("## 格式校验问题", clean)
        self.assertIn("受影响文件：NULL", clean)
        self.assertEqual(baseline, _identity(snapshot))

    def test_schema3_real_issue_normalizes_copy_family_and_hides_unknown_path(
        self,
    ) -> None:
        snapshot = self.schema3_snapshot(real_issue=True)
        baseline = _identity(snapshot)
        analysis = dbissues.analyze_snapshot_issues(snapshot)
        sections = self.by_id(analysis)
        self.assertTrue(analysis["has_reportable_issues"])
        self.assertEqual(1, sections["metadata"]["issue_files"])
        self.assertEqual(2, sections["metadata"]["issue_records"])
        self.assertEqual(1, sections["metadata"]["information"][
            "diagnostic_records"])
        self.assertEqual(1, sections["metadata"]["information"][
            "diagnostic_total_files"])
        self.assertEqual(1, sections["metadata"]["information"][
            "reportable_diagnostic_files"])
        self.assertEqual(1, sections["metadata"]["information"][
            "normalized_diagnostic_families"])
        report = dbissues.render_snapshot_issues(analysis)
        self.assertIn("broken.jpg", report)
        self.assertNotIn("unknown.bin", report)
        self.assertIn("Copy#", report)
        self.assertNotIn("Copy123", report)
        self.assertIn("### 需要处理", report)
        self.assertIn("状态汇总", report)
        self.assertIn("建议操作", report)
        self.assertIn("详细证据表", report)
        for _section_id, title in dbissues.ISSUE_SECTIONS:
            self.assertIn(f"## {title}", report)
        self.assertEqual(baseline, _identity(snapshot))

    def test_minor_and_validation_are_folded_without_triggering_report(
        self,
    ) -> None:
        snapshot = self.schema3_snapshot(
            real_issue=False,
            diagnostics=(
                ("warning", "ExifTool:Main:Minor", "[minor] cosmetic"),
                ("warning", "exiftool_reported_warning",
                 "Missing required JPEG ExifIFD tag"),
                ("validation", "normalized_null", "无效占位值已转 NULL"),
            ),
        )
        baseline = _identity(snapshot)
        analysis = dbissues.analyze_snapshot_issues(snapshot)
        metadata = self.by_id(analysis)["metadata"]
        self.assertFalse(analysis["has_reportable_issues"])
        self.assertEqual(0, metadata["issue_files"])
        self.assertEqual(3, metadata["information"]["diagnostic_records"])
        self.assertEqual(1, metadata["information"]["folded_minor_records"])
        self.assertEqual(1, metadata["information"]["folded_warning_records"])
        self.assertEqual(
            1, metadata["information"]["folded_validation_records"])
        self.assertIsNone(dbissues.render_snapshot_issues(analysis))
        clean = dbissues.render_snapshot_issues(
            analysis, include_clean=True)
        self.assertIn("### 补充统计", clean)
        self.assertNotIn("broken.jpg", clean)
        self.assertNotIn("unknown.bin", clean)
        self.assertEqual(baseline, _identity(snapshot))

    def test_severe_warnings_are_candidates_and_copy_numbers_collapse(
        self,
    ) -> None:
        snapshot = self.schema3_snapshot(
            real_issue=False,
            diagnostics=(
                ("warning", "ExifTool:Main:Copy1:Warning", "JPEG truncated"),
                ("warning", "ExifTool:Main:Copy999999:Warning",
                 "JPEG truncated again"),
            ),
        )
        analysis = dbissues.analyze_snapshot_issues(snapshot)
        metadata = self.by_id(analysis)["metadata"]
        self.assertEqual((1, 2, 1), (
            metadata["issue_files"],
            metadata["issue_records"],
            metadata["information"]["normalized_diagnostic_families"],
        ))
        self.assertEqual("candidate", metadata["details"][0]["level"])
        report = dbissues.render_snapshot_issues(analysis)
        self.assertIn("### 待复核候选", report)
        self.assertIn("Copy#", report)
        self.assertNotIn("Copy999999", report)

    def test_high_density_minor_warnings_create_one_bounded_candidate(
        self,
    ) -> None:
        diagnostics = tuple(
            (
                "warning",
                f"ExifTool:Main:Copy{index}:Warning",
                "[minor] duplicate metadata field",
            )
            for index in range(dbissues.WARNING_DENSITY_THRESHOLD)
        )
        snapshot = self.schema3_snapshot(
            real_issue=False, diagnostics=diagnostics)
        analysis = dbissues.analyze_snapshot_issues(snapshot, row_limit=1)
        metadata = self.by_id(analysis)["metadata"]
        self.assertEqual((1, 1, 0, 1), (
            metadata["issue_files"],
            metadata["issue_records"],
            metadata["information"]["reportable_diagnostic_records"],
            metadata["information"]["high_density_warning_files"],
        ))
        self.assertEqual(1, len(metadata["details"]))
        report = dbissues.render_snapshot_issues(analysis)
        self.assertIn("同一文件未展开的普通或次要警告", report)
        self.assertIn("已展示 1，共 1", report)
        self.assertLess(len(report.splitlines()), 120)

    def test_schema4_format_and_high_performance_have_separate_sections(
        self,
    ) -> None:
        snapshot = self.schema4_snapshot()
        baseline = _identity(snapshot)
        analysis = dbissues.analyze_snapshot_issues(snapshot)
        sections = self.by_id(analysis)
        self.assertEqual(("executed", 1, 1), (
            sections["format"]["execution"],
            sections["format"]["issue_files"],
            sections["format"]["information"]["unsupported_files"],
        ))
        self.assertEqual(1, sections["performance"]["issue_files"])
        self.assertEqual(("executed", 0), (
            sections["runtime"]["execution"],
            sections["runtime"]["issue_records"],
        ))
        issue_path = core.artifact_issue_report_path(snapshot)
        self.assertTrue(os.path.isfile(issue_path))
        with open(issue_path, "rb") as handle:
            issue_bytes = handle.read()
        self.assertFalse(issue_bytes.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r\n", issue_bytes)
        self.assertIn(os.path.basename(snapshot), issue_bytes.decode("utf-8"))
        report = dbissues.render_snapshot_issues(analysis)
        self.assertIn("broken.pdf", report)
        self.assertNotIn("unknown.bin", report)
        self.assertIn("读取性能异常候选", report)
        self.assertIn("不支持的文件（仅统计）：1", report)
        self.assertIn("平均吞吐", report)
        self.assertIn("不能据此认定物理坏区或设备故障", report)
        self.assertEqual(baseline, _identity(snapshot))

    def test_performance_candidate_combinations_do_not_promote_low_samples(
        self,
    ) -> None:
        cases = (
            ("none", False, False),
            ("low", False, False),
            ("high", False, True),
            ("high", True, True),
        )
        for confidence, format_issue, expected_report in cases:
            with self.subTest(
                    confidence=confidence, format_issue=format_issue):
                snapshot = self.schema4_snapshot(
                    format_issue=format_issue,
                    performance_confidence=confidence,
                )
                analysis = dbissues.analyze_snapshot_issues(snapshot)
                sections = self.by_id(analysis)
                self.assertEqual(
                    int(confidence == "high"),
                    sections["performance"]["issue_files"],
                )
                self.assertEqual(
                    int(confidence == "low"),
                    sections["performance"]["information"][
                        "low_confidence_files"],
                )
                self.assertEqual(
                    expected_report, analysis["has_reportable_issues"])
                self.assertEqual(
                    expected_report,
                    os.path.isfile(core.artifact_issue_report_path(snapshot)),
                )

    def test_execution_state_matrix_distinguishes_null_from_zero(self) \
            -> None:
        quick = self.by_id(dbissues.analyze_snapshot_issues(
            self.schema3_quick_snapshot()))
        self.assertEqual("null", quick["metadata"]["execution"])
        self.assertEqual("null", quick["hash"]["execution"])

        quick_v4 = self.by_id(dbissues.analyze_snapshot_issues(
            self.schema4_quick_snapshot()))
        for section_id in ("metadata", "hash", "format", "performance"):
            self.assertEqual("null", quick_v4[section_id]["execution"])
        self.assertEqual(("executed", 0), (
            quick_v4["runtime"]["execution"],
            quick_v4["runtime"]["issue_records"],
        ))

        legacy_full = self.by_id(dbissues.analyze_snapshot_issues(
            self.schema3_snapshot(real_issue=False)))
        self.assertEqual(("executed", 0), (
            legacy_full["metadata"]["execution"],
            legacy_full["metadata"]["issue_files"],
        ))
        self.assertEqual("null", legacy_full["format"]["execution"])

        format_off = self.by_id(dbissues.analyze_snapshot_issues(
            self.schema4_snapshot(
                format_enabled=False,
                format_issue=False,
                performance_confidence="none",
            )))
        self.assertEqual("null", format_off["format"]["execution"])

        format_on = self.by_id(dbissues.analyze_snapshot_issues(
            self.schema4_snapshot(
                format_issue=False,
                performance_confidence="none",
            )))
        self.assertEqual(("executed", 0), (
            format_on["format"]["execution"],
            format_on["format"]["issue_files"],
        ))

    def test_recovered_hash_attempt_does_not_mix_old_failure_into_current(
        self,
    ) -> None:
        snapshot = self.schema4_snapshot(
            format_issue=False,
            performance_confidence="none",
            prior_hash_failure=True,
        )
        analysis = dbissues.analyze_snapshot_issues(snapshot)
        hash_section = self.by_id(analysis)["hash"]
        self.assertEqual(("executed", 0, 0), (
            hash_section["execution"],
            hash_section["issue_files"],
            hash_section["information"]["current_attempt_records"],
        ))
        self.assertFalse(analysis["has_reportable_issues"])
        self.assertFalse(os.path.exists(
            core.artifact_issue_report_path(snapshot)))

    def test_tool_circuit_is_one_aggregate_runtime_issue(self) -> None:
        snapshot = self.schema4_snapshot(
            format_issue=False,
            performance_confidence="none",
            runtime_tool_failure=True,
        )
        baseline = _identity(snapshot)
        analysis = dbissues.analyze_snapshot_issues(snapshot)
        runtime = self.by_id(analysis)["runtime"]
        self.assertEqual(("executed", 1, 1, 1), (
            runtime["execution"],
            runtime["issue_records"],
            runtime["information"]["tool_failure_events"],
            len(runtime["details"]),
        ))
        self.assertEqual(
            "tool_failure_aggregated",
            runtime["details"][0]["status"],
        )
        report = dbissues.render_snapshot_issues(analysis)
        self.assertIn("## 运行与证据问题", report)
        self.assertIn("exiftool 连续发生 3 次工具故障，阶段已停止", report)
        self.assertIn("未处理条目：204913", report)
        self.assertIn("条目 ID 范围：6194～233079", report)
        self.assertNotIn("204913 个文件错误", report)
        self.assertEqual(baseline, _identity(snapshot))

    def test_row_limit_changes_details_not_totals(self) -> None:
        snapshot = self.schema3_snapshot(
            real_issue=True, additional_real_issues=2)
        analysis = dbissues.analyze_snapshot_issues(
            snapshot, row_limit=1)
        metadata = self.by_id(analysis)["metadata"]
        self.assertEqual(3, metadata["issue_files"])
        self.assertEqual(1, len(metadata["details"]))
        report = dbissues.render_snapshot_issues(analysis)
        self.assertIn("已展示 1，共 3", report)
        self.assertIn(
            "`entries`、`errors`、`metadata_diagnostics`", report)
        for value in (0, -1, True, 1.5, "1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                dbissues.analyze_snapshot_issues(
                    snapshot, row_limit=value)


if __name__ == "__main__":
    unittest.main()

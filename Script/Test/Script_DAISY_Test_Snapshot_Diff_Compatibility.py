"""DAISY v1.6.0 跨版本 Diff 规范化投影与只读兼容测试。

所有数据库、目录和发布产物都位于工作区 ``.test_runtime``；测试只操作自己
创建的合成快照，不枚举、附加或终止其它进程。
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPT_DIR = os.path.dirname(_TEST_DIR)
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
_LIB_DIR = os.path.join(_SCRIPT_DIR, "Lib")
sys.path[:0] = [_TEST_DIR, _SCRIPT_DIR, _LIB_DIR]

import Script_DAISY_Lib_Snapshot_Core as core
import Script_DAISY_Lib_File_Hash as hash_stage
import Script_DAISY_Lib_Snapshot_Diff as dbdiff
import Script_DAISY_Lib_Database_Reader as reader
import Script_DAISY_Lib_Scan_State as state
import Script_DAISY_Test_Tree as tree_fixture


_RUNTIME_ROOT = os.path.join(
    _REPO_ROOT, ".test_runtime", "v1_6_0", "diff_compatibility")
_CONTRACT_PATH = os.path.join(
    _TEST_DIR, "Fixtures", "Snapshot_v1_4_1_Schema3_Contract.json")
_DIFF_MODULE = os.path.join(
    _SCRIPT_DIR, "Module", "Script_DAISY_Module_Snapshot_Diff.py")
_BUSINESS_COPY_TABLES = (
    "roots",
    "dirs",
    "entries",
    "photo_metadata",
    "video_metadata",
    "video_gps_points",
    "video_streams",
    "audio_streams",
    "working_metadata",
    "document_metadata",
    "archive_metadata",
    "archive_members",
    "raw_payloads",
    "metadata_diagnostics",
    "hashes",
    "errors",
    "snapshot_manifest",
    "run_events",
)


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _file_identity(path: str) -> tuple[str, int, int]:
    stat_result = os.stat(path)
    return _sha256(path), stat_result.st_size, stat_result.st_mtime_ns


class TestCrossVersionDiff(unittest.TestCase):
    def setUp(self) -> None:
        os.makedirs(_RUNTIME_ROOT, exist_ok=True)
        self._td = tempfile.TemporaryDirectory(
            prefix="case_", dir=_RUNTIME_ROOT)
        self.base = self._td.name
        self.snapshots = os.path.join(self.base, "Snapshots")
        os.makedirs(self.snapshots)

    def tearDown(self) -> None:
        self._td.cleanup()

    @staticmethod
    def _copy_rows(
        source: sqlite3.Connection,
        target: sqlite3.Connection,
        table: str,
    ) -> None:
        columns = tuple(
            str(row[1]) for row in source.execute(
                f'PRAGMA table_info("{table}")'))
        if not columns:
            return
        selected = ",".join(f'"{column}"' for column in columns)
        rows = source.execute(
            f'SELECT {selected} FROM "{table}"').fetchall()
        if not rows:
            return
        placeholders = ",".join("?" for _column in columns)
        target.executemany(
            f'INSERT INTO "{table}" ({selected}) VALUES ({placeholders})',
            [tuple(row) for row in rows],
        )

    def _to_schema4(
        self,
        schema3_path: str,
        stem: str,
        *,
        format_validation: str = "off",
    ) -> str:
        source_uri = Path(schema3_path).resolve(strict=True).as_uri() \
            + "?mode=ro"
        source = sqlite3.connect(source_uri, uri=True)
        try:
            source.execute("PRAGMA query_only=ON")
            snapshot_uuid, hash_coverage = source.execute(
                "SELECT snapshot_uuid,hash_coverage FROM snapshot_info"
                " WHERE id=1").fetchone()
            roots = [
                (str(label), str(path))
                for _root_id, path, label in source.execute(
                    "SELECT root_id,root_path,root_label FROM roots"
                    " ORDER BY root_id")
            ]
            summary = source.execute(
                "SELECT has_file_issues,has_unstable_entries,"
                " has_enumeration_gaps,counts_json FROM snapshot_info"
                " WHERE id=1").fetchone()

            partial = os.path.join(
                self.snapshots, f"{stem}.partial.sqlite")
            publish_stem = os.path.join(self.snapshots, stem)
            event_log = os.path.join(self.snapshots, f"{stem}.events.jsonl")
            target = sqlite3.connect(partial)
            try:
                session_id = hashlib.sha256(
                    (stem + ":session").encode("utf-8")).hexdigest()[:32]
                lease_id = hashlib.sha256(
                    (stem + ":lease").encode("utf-8")).hexdigest()[:32]
                state.initialize_v4_connection(
                    target,
                    roots,
                    {
                        "phase": "full",
                        "hash": str(hash_coverage),
                        "metadata_storage": "complete",
                        "format_validation": format_validation,
                    },
                    output_dir=self.snapshots,
                    partial_path=partial,
                    publish_stem_path=publish_stem,
                    event_log_path=event_log,
                    snapshot_uuid=str(snapshot_uuid),
                    session_id=session_id,
                    lease_id=lease_id,
                    hostname="diff-fixture",
                    pid=4242,
                    process_start_token="synthetic",
                    now_utc="2026-08-07T00:00:00.000000Z",
                )
                with target:
                    target.execute("DELETE FROM roots")
                    for table in _BUSINESS_COPY_TABLES:
                        self._copy_rows(source, target, table)
                    target.execute(
                        "UPDATE snapshot_info SET hash_coverage=?,"
                        " has_file_issues=?,has_unstable_entries=?,"
                        " has_enumeration_gaps=?,counts_json=? WHERE id=1",
                        (
                            hash_coverage,
                            int(summary[0]),
                            int(summary[1]),
                            int(summary[2]),
                            summary[3],
                        ),
                    )
                    target.execute(
                        "UPDATE stage_checkpoints SET state='completed',"
                        " current_entry_id=NULL,finished_at_utc=?,"
                        " updated_at_utc=?",
                        (
                            "2026-08-07T00:00:30.000000Z",
                            "2026-08-07T00:00:30.000000Z",
                        ),
                    )
                state.begin_sealing(
                    target, now_utc="2026-08-07T00:00:40.000000Z")
                state.mark_sealed_unpublished(
                    target, now_utc="2026-08-07T00:00:50.000000Z")
            finally:
                target.close()
        finally:
            source.close()
        staging = os.path.join(
            self.snapshots, f"{stem}.publishing.sqlite")
        return state.publish_sealed_snapshot(
            partial,
            staging,
            now_utc="2026-08-07T00:01:00.000000Z",
        ).final_path

    def _build_matrix_pair(self) -> dict[str, str]:
        old_tree = os.path.join(self.base, "OldTree")
        new_tree = os.path.join(self.base, "NewTree")
        os.makedirs(old_tree)
        tree_fixture.write(old_tree, "same.bin", b"same")
        tree_fixture.write(old_tree, "changed.bin", b"AAAA")
        tree_fixture.write(old_tree, "gone.bin", b"gone")
        tree_fixture.write(old_tree, "move_old.bin", b"move")
        tree_fixture.write(old_tree, "copy_source.bin", b"copy")
        tree_fixture.write(old_tree, "hard_source.bin", b"hardlink")
        shutil.copytree(old_tree, new_tree)
        tree_fixture.write(new_tree, "changed.bin", b"BBBB")
        os.remove(os.path.join(new_tree, "gone.bin"))
        os.makedirs(os.path.join(new_tree, "folder"))
        os.rename(
            os.path.join(new_tree, "move_old.bin"),
            os.path.join(new_tree, "folder", "move_new.bin"),
        )
        tree_fixture.write(new_tree, "fresh.bin", b"fresh")
        tree_fixture.write(new_tree, "copy_new.bin", b"copy")
        os.link(
            os.path.join(new_tree, "hard_source.bin"),
            os.path.join(new_tree, "hard_link.bin"),
        )

        old3 = tree_fixture.build_snapshot(
            old_tree, self.snapshots, "MatrixOld", label="档案")
        new3 = tree_fixture.build_snapshot(
            new_tree, self.snapshots, "MatrixNew", label="档案")
        return {
            "old3": old3,
            "new3": new3,
            "old4": self._to_schema4(old3, "MatrixOldV4"),
            "new4": self._to_schema4(new3, "MatrixNewV4"),
        }

    def _build_multi_root_snapshot(
        self,
        name: str,
        roots: list[tuple[str, str]],
    ) -> str:
        partial = os.path.join(
            self.snapshots, f"Scan_{name}.partial.sqlite")
        con = core.create_partial_snapshot(
            partial,
            roots,
            config={
                "phase": "full",
                "hash": "full",
                "metadata_storage": "complete",
            },
        )
        core.enumerate_and_reconcile(con)
        hash_stage.process_hash_stage(con, "full")
        con.execute(
            "UPDATE entries SET meta_status='skipped'"
            " WHERE meta_status IN ('pending','processing')")
        con.commit()
        core.rescan_check(con)
        return core.finalize_snapshot(con, partial, "full")

    @staticmethod
    def _canonical_diff(path: str) -> dict[str, object]:
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row
        try:
            groups = {
                int(row["group_id"]): str(row["hash_hex"])
                for row in con.execute(
                    "SELECT group_id,hash_hex FROM diff_hash_groups")
            }

            def normalized_rows(table: str, id_column: str) -> list[dict]:
                rows = []
                for raw in con.execute(f'SELECT * FROM "{table}"'):
                    row = dict(raw)
                    row.pop(id_column, None)
                    if table == "diff_entries":
                        group_id = row.pop("group_id", None)
                        row["group_hash"] = groups.get(group_id)
                    rows.append(row)
                return sorted(
                    rows,
                    key=lambda item: json.dumps(
                        item, ensure_ascii=False, sort_keys=True),
                )

            mapping_text, = con.execute(
                "SELECT root_mapping_json FROM diff_info").fetchone()
            return {
                "entries": normalized_rows(
                    "diff_entries", "diff_entry_id"),
                "directories": normalized_rows(
                    "diff_dirs", "diff_dir_id"),
                "groups": normalized_rows(
                    "diff_hash_groups", "group_id"),
                "subtrees": normalized_rows(
                    "diff_subtrees", "subtree_id"),
                "root_mapping": json.loads(mapping_text),
            }
        finally:
            con.close()

    @staticmethod
    def _entry(path: str, rel_path: str) -> dict[str, object]:
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row
        try:
            rows = [
                dict(row) for row in con.execute(
                    "SELECT * FROM diff_entries"
                    " WHERE old_rel_path=? OR new_rel_path=?",
                    (rel_path, rel_path),
                )
            ]
        finally:
            con.close()
        if len(rows) != 1:
            raise AssertionError(f"{rel_path!r} 匹配到 {rows!r}")
        return rows[0]

    def test_four_schema_directions_share_one_business_projection(self) \
            -> None:
        snapshots = self._build_matrix_pair()
        combinations = (
            ("33", snapshots["old3"], snapshots["new3"], 3, 3),
            ("34", snapshots["old3"], snapshots["new4"], 3, 4),
            ("43", snapshots["old4"], snapshots["new3"], 4, 3),
            ("44", snapshots["old4"], snapshots["new4"], 4, 4),
        )
        identities = {
            path: _file_identity(path) for path in snapshots.values()}
        canonical = None
        for token, old_path, new_path, old_schema, new_schema in combinations:
            with self.subTest(direction=token):
                output = os.path.join(self.base, f"matrix_{token}.sqlite")
                result = dbdiff.compare(old_path, new_path, output)
                current = self._canonical_diff(output)
                if canonical is None:
                    canonical = current
                else:
                    self.assertEqual(canonical, current)
                con = sqlite3.connect(output)
                info = con.execute(
                    "SELECT schema_version,old_schema_version,"
                    " new_schema_version,counts_json FROM diff_info"
                ).fetchone()
                con.close()
                self.assertEqual(info[:3], (3, old_schema, new_schema))
                counts = json.loads(info[3])
                self.assertEqual(
                    counts["projection"],
                    {
                        "old": reader.SNAPSHOT_DIFF_PROJECTION,
                        "new": reader.SNAPSHOT_DIFF_PROJECTION,
                    },
                )
                self.assertEqual(
                    result["capabilities"]["hashes"]["state"],
                    "comparable",
                )
                hardlink = self._entry(output, "hard_link.bin")
                self.assertEqual(hardlink["status"], "added")
                self.assertIn("hardlink", str(hardlink["reason"]))
        for path, identity in identities.items():
            self.assertEqual(_file_identity(path), identity)

    def test_reverse_direction_swaps_paths_and_added_deleted(self) -> None:
        snapshots = self._build_matrix_pair()
        forward = os.path.join(self.base, "forward.sqlite")
        reverse = os.path.join(self.base, "reverse.sqlite")
        dbdiff.compare(snapshots["old3"], snapshots["new4"], forward)
        dbdiff.compare(snapshots["new4"], snapshots["old3"], reverse)

        self.assertEqual(self._entry(forward, "fresh.bin")["status"], "added")
        self.assertEqual(
            self._entry(reverse, "fresh.bin")["status"], "deleted")
        self.assertEqual(self._entry(forward, "gone.bin")["status"], "deleted")
        self.assertEqual(self._entry(reverse, "gone.bin")["status"], "added")
        moved_forward = self._entry(forward, "move_old.bin")
        moved_reverse = self._entry(reverse, "move_old.bin")
        self.assertEqual(
            (moved_forward["old_rel_path"], moved_forward["new_rel_path"]),
            ("move_old.bin", "folder\\move_new.bin"),
        )
        self.assertEqual(
            (moved_reverse["old_rel_path"], moved_reverse["new_rel_path"]),
            ("folder\\move_new.bin", "move_old.bin"),
        )
        con = sqlite3.connect(reverse)
        schemas = con.execute(
            "SELECT old_schema_version,new_schema_version FROM diff_info"
        ).fetchone()
        con.close()
        self.assertEqual(schemas, (4, 3))

    def test_missing_hash_raw_and_format_evidence_stays_explicit(self) \
            -> None:
        archive = os.path.join(self.base, "EvidenceTree")
        os.makedirs(archive)
        tree_fixture.write(archive, "same.bin", b"same")
        no_hash = tree_fixture.build_snapshot(
            archive,
            self.snapshots,
            "NoHash",
            label="档案",
            hash_mode="none",
        )
        full = tree_fixture.build_snapshot(
            archive, self.snapshots, "FullHash", label="档案")
        full_v4 = self._to_schema4(
            full, "FullHashV4", format_validation="all")
        output = os.path.join(self.base, "missing_hash.sqlite")
        result = dbdiff.compare(no_hash, full_v4, output)
        row = self._entry(output, "same.bin")
        self.assertEqual(row["status"], "hash_missing")
        self.assertEqual(
            result["capabilities"]["hashes"]["state"], "unavailable")
        self.assertEqual(
            result["capabilities"]["format_checks"]["state"],
            "unavailable",
        )
        self.assertFalse([
            item for item in self._canonical_diff(output)["entries"]
            if item["status"] in ("added", "deleted")
        ])

        def normalized_only(con: sqlite3.Connection) -> None:
            config_text, = con.execute(
                "SELECT config_json FROM snapshot_info WHERE id=1"
            ).fetchone()
            config = json.loads(config_text)
            config["metadata_storage"] = "normalized"
            con.execute(
                "UPDATE snapshot_info SET config_json=? WHERE id=1",
                (json.dumps(config, ensure_ascii=False),),
            )

        normalized = tree_fixture.build_snapshot(
            archive,
            self.snapshots,
            "NormalizedOnly",
            label="档案",
            pre_finalize=normalized_only,
        )
        raw_output = os.path.join(self.base, "missing_raw.sqlite")
        raw_result = dbdiff.compare(normalized, full_v4, raw_output)
        raw_row = self._entry(raw_output, "same.bin")
        self.assertEqual(raw_row["status"], "unchanged")
        self.assertIsNone(raw_row["metadata_changed"])
        raw_capability = raw_result["capabilities"]["raw_payloads"]
        self.assertEqual(raw_capability["state"], "unavailable")
        self.assertEqual(raw_capability["old"], "unavailable")
        self.assertIn("未保留原始", raw_capability["reason"])

    def test_projection_and_diff_ddl_contract_remain_frozen(self) -> None:
        snapshots = self._build_matrix_pair()
        con, descriptor = reader.open_database(
            snapshots["old4"], expected_type="snapshot")
        try:
            projection = reader.snapshot_diff_projection(con, descriptor)
        finally:
            con.close()
        self.assertEqual(
            projection["projection"], reader.SNAPSHOT_DIFF_PROJECTION)
        self.assertEqual(projection["schema"], 4)
        self.assertEqual(
            projection["capabilities"]["run_sessions"]["state"],
            "available",
        )
        load_source = inspect.getsource(dbdiff.load_side)
        self.assertIn("snapshot_diff_projection", load_source)
        self.assertNotIn(".execute(", load_source)
        with open(_CONTRACT_PATH, encoding="utf-8") as handle:
            contract = json.load(handle)
        self.assertEqual(
            hashlib.sha256(dbdiff.DIFF_DDL.encode("utf-8")).hexdigest(),
            contract["diff_ddl_sha256"],
        )

    def test_missing_optional_evidence_tables_do_not_block_structure(self) \
            -> None:
        archive = os.path.join(self.base, "OptionalEvidenceTree")
        os.makedirs(archive)
        tree_fixture.write(archive, "same.bin", b"same")
        complete = tree_fixture.build_snapshot(
            archive, self.snapshots, "Complete", label="档案")
        complete_v4 = self._to_schema4(complete, "CompleteV4")
        missing = os.path.join(self.snapshots, "MissingEvidence.sqlite")
        shutil.copyfile(complete, missing)
        con = sqlite3.connect(missing)
        try:
            con.execute("DROP TABLE hashes")
            con.execute("DROP TABLE raw_payloads")
            con.commit()
        finally:
            con.close()
        identity = _file_identity(missing)
        output = os.path.join(self.base, "optional_missing.sqlite")
        result = dbdiff.compare(
            missing, complete_v4, output, force=True)
        self.assertEqual(_file_identity(missing), identity)
        self.assertEqual(self._entry(output, "same.bin")["status"],
                         "hash_missing")
        self.assertEqual(
            result["capabilities"]["hashes"]["old"], "unavailable")
        self.assertEqual(
            result["capabilities"]["raw_payloads"]["old"],
            "unavailable",
        )
        self.assertEqual(
            self._canonical_diff(output)["directories"][0]["status"],
            "unchanged",
        )

    def test_cli_publishes_cross_version_diff_and_explains_evidence(self) \
            -> None:
        snapshots = self._build_matrix_pair()
        identities = {
            path: _file_identity(path)
            for path in (snapshots["old3"], snapshots["new4"])
        }
        output_dir = os.path.join(self.base, "PublishedDiff")
        environment = dict(os.environ)
        environment.update({
            "TEMP": self.base,
            "TMP": self.base,
            "TMPDIR": self.base,
        })
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                _DIFF_MODULE,
                "--old",
                snapshots["old3"],
                "--new",
                snapshots["new4"],
                "--output-dir",
                output_dir,
            ],
            cwd=_REPO_ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=120,
        )
        stdout = completed.stdout.decode("utf-8", "replace")
        stderr = completed.stderr.decode("utf-8", "replace")
        self.assertEqual(completed.returncode, 0, stderr)
        self.assertIn("数据库结构版本：基准侧为 3；对比侧为 4", stdout)
        self.assertIn(reader.SNAPSHOT_DIFF_PROJECTION, stdout)
        self.assertIn("证据可用性：", stdout)
        databases = [
            os.path.join(output_dir, name)
            for name in os.listdir(output_dir)
            if name.endswith(".sqlite")
        ]
        self.assertEqual(len(databases), 1)
        descriptor = reader.inspect_database(
            databases[0], expected_type="diff")
        self.assertEqual(
            (
                descriptor.identity["old_schema_version"],
                descriptor.identity["new_schema_version"],
            ),
            (3, 4),
        )
        for path, identity in identities.items():
            self.assertEqual(_file_identity(path), identity)

    def test_enumeration_gap_remains_unknown_in_both_directions(self) \
            -> None:
        archive = os.path.join(self.base, "GapTree")
        os.makedirs(archive)
        tree_fixture.write(archive, "root.bin", b"root")
        tree_fixture.write(archive, os.path.join("blocked", "a.bin"), b"a")
        tree_fixture.write(archive, os.path.join("blocked", "b.bin"), b"b")
        old3 = tree_fixture.build_snapshot(
            archive, self.snapshots, "GapOld", label="档案")
        denied = os.path.normcase(core.to_extended_path(
            os.path.join(archive, "blocked")))
        real_scandir = os.scandir

        def deny_exact(path: object):
            current = os.path.normcase(os.path.abspath(os.fspath(path)))
            if current == denied:
                raise PermissionError(13, "合成拒绝访问", os.fspath(path))
            return real_scandir(path)

        with mock.patch.object(
                core.os, "scandir", side_effect=deny_exact):
            failed3 = tree_fixture.build_snapshot(
                archive, self.snapshots, "GapNew", label="档案")
        failed4 = self._to_schema4(failed3, "GapNewV4")
        identities = {
            path: _file_identity(path) for path in (old3, failed4)}

        forward = os.path.join(self.base, "gap_forward.sqlite")
        reverse = os.path.join(self.base, "gap_reverse.sqlite")
        forward_result = dbdiff.compare(old3, failed4, forward)
        reverse_result = dbdiff.compare(failed4, old3, reverse)
        for path in (forward, reverse):
            canonical = self._canonical_diff(path)
            blocked = [
                row for row in canonical["entries"]
                if (row["old_rel_path"] or row["new_rel_path"] or "")
                .startswith("blocked\\")
            ]
            self.assertEqual(len(blocked), 2)
            self.assertEqual({row["status"] for row in blocked}, {"unknown"})
            self.assertFalse([
                row for row in blocked
                if row["status"] in ("added", "deleted")
            ])
        self.assertEqual(forward_result["counts"]["subtrees"]["new"], 1)
        self.assertEqual(reverse_result["counts"]["subtrees"]["old"], 1)
        for path, identity in identities.items():
            self.assertEqual(_file_identity(path), identity)

    def test_cross_version_explicit_multi_root_mapping_and_unpaired_roots(
        self,
    ) -> None:
        root_names = (
            "OldPhotos", "OldDocs", "OldOnly",
            "NewPhotos", "NewDocs", "NewOnly",
        )
        roots = {
            name: os.path.join(self.base, name) for name in root_names}
        for path in roots.values():
            os.makedirs(path)
        tree_fixture.write(roots["OldPhotos"], "photo.bin", b"photo")
        tree_fixture.write(roots["OldDocs"], "doc.bin", b"document")
        tree_fixture.write(roots["OldOnly"], "old_only.bin", b"old")
        shutil.copy2(
            os.path.join(roots["OldPhotos"], "photo.bin"),
            os.path.join(roots["NewPhotos"], "photo.bin"),
        )
        shutil.copy2(
            os.path.join(roots["OldDocs"], "doc.bin"),
            os.path.join(roots["NewDocs"], "doc.bin"),
        )
        tree_fixture.write(roots["NewOnly"], "new_only.bin", b"new")
        old3 = self._build_multi_root_snapshot(
            "RootsOld",
            [
                ("照片库", roots["OldPhotos"]),
                ("文档库", roots["OldDocs"]),
                ("仅旧", roots["OldOnly"]),
            ],
        )
        new3 = self._build_multi_root_snapshot(
            "RootsNew",
            [
                ("Photos", roots["NewPhotos"]),
                ("Docs", roots["NewDocs"]),
                ("仅新", roots["NewOnly"]),
            ],
        )
        new4 = self._to_schema4(new3, "RootsNewV4")
        identities = {
            path: _file_identity(path) for path in (old3, new4)}

        forward = os.path.join(self.base, "roots_forward.sqlite")
        reverse = os.path.join(self.base, "roots_reverse.sqlite")
        forward_result = dbdiff.compare(
            old3,
            new4,
            forward,
            map_root={"照片库": "Photos", "文档库": "Docs"},
        )
        reverse_result = dbdiff.compare(
            new4,
            old3,
            reverse,
            map_root={"Photos": "照片库", "Docs": "文档库"},
        )
        self.assertEqual(
            {tuple(pair) for pair in forward_result["root_mapping"]["pairs"]},
            {("照片库", "Photos"), ("文档库", "Docs")},
        )
        self.assertEqual(
            forward_result["root_mapping"]["unpaired_old"], ["仅旧"])
        self.assertEqual(
            forward_result["root_mapping"]["unpaired_new"], ["仅新"])
        forward_rows = self._canonical_diff(forward)["entries"]
        self.assertEqual(
            {
                row["status"] for row in forward_rows
                if row["old_rel_path"] in ("photo.bin", "doc.bin")
            },
            {"unchanged"},
        )
        self.assertEqual(self._entry(forward, "old_only.bin")["status"],
                         "deleted")
        self.assertEqual(self._entry(forward, "new_only.bin")["status"],
                         "added")
        self.assertEqual(self._entry(reverse, "old_only.bin")["status"],
                         "added")
        self.assertEqual(self._entry(reverse, "new_only.bin")["status"],
                         "deleted")
        self.assertEqual(
            reverse_result["root_mapping"]["unpaired_old"], ["仅新"])
        self.assertEqual(
            reverse_result["root_mapping"]["unpaired_new"], ["仅旧"])
        for path, identity in identities.items():
            self.assertEqual(_file_identity(path), identity)


if __name__ == "__main__":
    unittest.main(verbosity=2)

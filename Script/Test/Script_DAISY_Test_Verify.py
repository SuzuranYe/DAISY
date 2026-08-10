"""哈希／格式核验的输入、业务服务和兼容入口行为保持测试。"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from unittest.mock import patch


_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPT_DIR = os.path.dirname(_TEST_DIR)
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
_LIB_DIR = os.path.join(_SCRIPT_DIR, "Lib")
_MODULE_DIR = os.path.join(_SCRIPT_DIR, "Module")
sys.path[:0] = [_TEST_DIR, _SCRIPT_DIR, _LIB_DIR, _MODULE_DIR]

import Script_DAISY_Lib_Snapshot_Core as core
import Script_DAISY_Lib_Metadata as meta
import Script_DAISY_Lib_File_Hash as dbh
import Script_DAISY_Lib_Snapshot_Verify as dbverify
import Script_DAISY_Lib_Tool_Runtime as toolruntime
import Script_DAISY_Module_Check_Hash as hashcheck
import Script_DAISY_Module_Check_Format as formatcheck
import Script_DAISY_Test_Tree as test_tree


_MODULE = _MODULE_DIR
Validate = formatcheck
tt = test_tree


_RUNTIME_ROOT = os.path.join(
    _REPO_ROOT, ".test_runtime", "v1_6_0", "verify")


def _identity(path: str) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    stat_result = os.stat(path)
    return digest.hexdigest(), stat_result.st_size, stat_result.st_mtime_ns


class TestVerificationInputModel(unittest.TestCase):
    def setUp(self) -> None:
        os.makedirs(_RUNTIME_ROOT, exist_ok=True)
        self._td = tempfile.TemporaryDirectory(
            prefix="case_", dir=_RUNTIME_ROOT)
        self.base = self._td.name
        self.current_root = os.path.join(self.base, "current")
        os.makedirs(self.current_root)
        self.current_file = os.path.join(self.current_root, "file.bin")
        with open(self.current_file, "wb") as handle:
            handle.write(b"data")
        fixed_ns = 1_700_000_000_123_456_700
        os.utime(self.current_file, ns=(fixed_ns, fixed_ns))

    def tearDown(self) -> None:
        self._td.cleanup()

    def snapshot(
        self,
        name: str = "fixture.sqlite",
        *,
        scan_kind: str = "full",
        hash_coverage: str = "full",
    ) -> str:
        path = os.path.join(self.base, name)
        stat_result = os.stat(self.current_file)
        modified_at = core.ns_to_utc_iso(stat_result.st_mtime_ns)
        config_object = {
            "phase": scan_kind,
            "quick": scan_kind == "quick",
            "hash": hash_coverage,
            "metadata_storage": "complete",
            "data_contract": "daisy-snapshot-v3",
            "min_reader_version": "1.4.1",
        }
        connection = sqlite3.connect(path)
        try:
            connection.executescript(core.SNAPSHOT_DDL)
            connection.execute(
                "INSERT INTO snapshot_info"
                " (id,snapshot_uuid,schema_version,path_key_rule,scan_status,"
                " database_integrity,hash_coverage,started_at_utc,"
                " local_utc_offset_min,hostname,os_version,scanner_version,"
                " config_json) VALUES"
                " (1,'verify-fixture',3,1,'complete','ok',?,"
                " '2026-01-01T00:00:00.0000000Z',0,'fixture','Windows',"
                " '1.4.1',?)",
                (hash_coverage, json.dumps(config_object, ensure_ascii=False)),
            )
            connection.execute(
                "INSERT INTO roots"
                " (root_id,root_path,root_label,enum_status)"
                " VALUES (1,'X:/Recorded','夹具','ok')")
            connection.execute(
                "INSERT INTO dirs"
                " (dir_id,root_id,rel_path,path_key,enum_status,observed_at_utc)"
                " VALUES (1,1,'','','ok','2026-01-01T00:00:00.0000000Z')")
            connection.execute(
                "INSERT INTO entries"
                " (entry_id,root_id,dir_id,rel_path,path_key,name,extension,"
                " media_kind,size_bytes,modified_at_utc,attributes,"
                " observed_at_utc,meta_status,hash_status) VALUES"
                " (1,1,1,'file.bin','file.bin','file.bin','bin','other',4,?,0,"
                " '2026-01-01T00:00:00.0000000Z','not_applicable',?)",
                (modified_at,
                 "skipped" if hash_coverage == "none" else "done"),
            )
            if hash_coverage != "none":
                connection.execute(
                    "INSERT INTO hashes"
                    " (entry_id,algorithm,hash_hex,origin,size_bytes,bytes_read,"
                    " status,tool,tool_version) VALUES"
                    " (1,'sha256',?,'computed',4,4,'valid','hashlib','1')",
                    (hashlib.sha256(b"data").hexdigest(),),
                )
            manifest = {
                "data_contract": "daisy-snapshot-v3",
                "min_reader_version": "1.4.1",
                "config": config_object,
                "effective_profile": {
                    "scan_kind": scan_kind,
                    "hash_coverage_actual": hash_coverage,
                    "metadata_storage": "complete",
                    "raw_payload_retained": scan_kind == "full",
                },
            }
            connection.execute(
                "INSERT INTO snapshot_manifest"
                " (id,manifest_version,manifest_json,embedded_at_utc)"
                " VALUES (1,1,?,'2026-01-01T00:00:01.0000000Z')",
                (json.dumps(manifest, ensure_ascii=False),),
            )
            connection.execute(
                "INSERT INTO run_events"
                " (event_seq,occurred_at_utc,event,payload_json) VALUES"
                " (1,'2026-01-01T00:00:01.0000000Z','snapshot_sealed','{}')")
            counts = core.collect_snapshot_counts(connection)
            connection.execute(
                "UPDATE snapshot_info SET counts_json=? WHERE id=1",
                (json.dumps(counts, ensure_ascii=False),),
            )
            connection.commit()
        finally:
            connection.close()
        return path

    def test_common_admission_maps_roots_and_closes_connection(self) -> None:
        path = self.snapshot()
        baseline = _identity(path)
        verification = dbverify.open_verification_snapshot(
            path,
            root_map={"夹具": self.current_root},
            force=True,
            required_capabilities=("files", "hashes"),
        )
        connection = verification.connection
        with verification:
            self.assertEqual(verification.snapshot_uuid, "verify-fixture")
            self.assertEqual(verification.hash_coverage, "full")
            self.assertEqual(verification.root_labels, ("夹具",))
            self.assertEqual(
                verification.physical_path(1, "file.bin"), self.current_file)
            self.assertEqual(
                verification.logical_path(1, "file.bin"), "夹具\\file.bin")
            self.assertEqual(
                connection.execute("PRAGMA query_only").fetchone()[0], 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")
        self.assertEqual(_identity(path), baseline)

    def test_hash_and_format_use_the_same_admission_entrypoint(self) -> None:
        path = self.snapshot()
        baseline = _identity(path)
        digest = hashlib.sha256(b"data").hexdigest()
        with mock.patch.object(
            dbverify, "open_verification_snapshot",
            wraps=dbverify.open_verification_snapshot,
        ) as shared_open, mock.patch.object(
            hashcheck.dbh, "discover_powershell",
            return_value=("fixture-powershell.exe", "7.5.0"),
        ), mock.patch.object(
            hashcheck.dbh, "get_filehash_batch", return_value=[digest],
        ):
            hash_report = hashcheck.patrol(
                path,
                root_map={"夹具": self.current_root},
                full=True,
                force=True,
            )
            format_report = formatcheck.validate_snapshot(
                path,
                root_map={"夹具": self.current_root},
                report_dir=os.path.join(self.base, "format_report"),
                force=True,
            )
        self.assertEqual(shared_open.call_count, 2)
        self.assertEqual(
            shared_open.call_args_list[0].kwargs["required_capabilities"],
            ("files", "hashes"),
        )
        self.assertEqual(
            shared_open.call_args_list[1].kwargs["required_capabilities"],
            ("files",),
        )
        self.assertTrue(hash_report["ok"])
        self.assertEqual(hash_report["hash_checked"], 1)
        self.assertTrue(format_report["ok"])
        self.assertEqual(format_report["counts"], {"unsupported": 1})
        self.assertEqual(_identity(path), baseline)

    def test_quick_no_hash_rejects_hash_but_allows_format_input(self) -> None:
        path = self.snapshot(
            "quick.sqlite", scan_kind="quick", hash_coverage="none")
        with self.assertRaises(core.PreflightError) as caught:
            dbverify.open_verification_snapshot(
                path,
                root_map={"夹具": self.current_root},
                force=True,
                required_capabilities=("files", "hashes"),
            )
        self.assertIn("hashes 为 unavailable", str(caught.exception))
        with dbverify.open_verification_snapshot(
            path,
            root_map={"夹具": self.current_root},
            force=True,
            required_capabilities=("files",),
        ) as verification:
            self.assertEqual(verification.hash_coverage, "none")

    def test_legacy_modules_delegate_to_shared_services(self) -> None:
        self.assertIs(hashcheck.patrol, dbverify.patrol_hash)
        self.assertIs(hashcheck.dbh, dbverify.dbh)
        self.assertIs(formatcheck.validate_snapshot,
                      dbverify.validate_format_snapshot)
        self.assertIs(formatcheck.validate_zip, dbverify.validate_zip)
        self.assertIs(formatcheck.validate_pdf, dbverify.validate_pdf)
        self.assertIs(formatcheck.validate_sevenzip,
                      dbverify.validate_sevenzip)
        self.assertIs(formatcheck.validate_media, dbverify.validate_media)
        self.assertIs(formatcheck._pick_validator,
                      dbverify.pick_format_validator)
        self.assertIs(formatcheck.subprocess, dbverify.subprocess)

        ole_path = os.path.join(self.base, "legacy.doc")
        with open(ole_path, "wb") as handle:
            handle.write(dbverify._OLE_MAGIC + b"\x00" * 16)
        with mock.patch.object(
            formatcheck,
            "validate_sevenzip",
            return_value=("valid", None),
        ) as validator:
            self.assertEqual(
                formatcheck.validate_legacy_office(ole_path, "7z"),
                ("valid", None),
            )
        validator.assert_called_once_with(ole_path, "7z")

    def test_mapping_failure_closes_reader_connection(self) -> None:
        path = self.snapshot()
        captured = []
        original_open = dbverify.dbreader.open_database

        def capture_open(*args, **kwargs):
            result = original_open(*args, **kwargs)
            captured.append(result[0])
            return result

        missing_root = os.path.join(self.base, "missing")
        with mock.patch.object(
            dbverify.dbreader, "open_database", side_effect=capture_open,
        ), self.assertRaises(core.PreflightError):
            dbverify.open_verification_snapshot(
                path,
                root_map={"夹具": missing_root},
                force=True,
            )
        self.assertEqual(len(captured), 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            captured[0].execute("SELECT 1")

    def test_fingerprint_mismatch_is_hard_failure_even_with_force(self) -> None:
        source = self.snapshot()
        mismatched = os.path.join(self.base, "Fixture_00000000.sqlite")
        shutil.copy2(source, mismatched)
        with self.assertRaises(core.PreflightError) as caught:
            dbverify.open_verification_snapshot(
                mismatched,
                root_map={"夹具": self.current_root},
                force=True,
            )
        self.assertIn("文件名指纹不符", str(caught.exception))


class TestVerifyHashPatrol(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(
            prefix="patrol_", dir=_RUNTIME_ROOT)
        self.arch = os.path.join(self._td.name, "Arch巡检")
        os.makedirs(self.arch)
        for name, data in [("p.bin", b"P" * 4096), ("q.bin", b"q-data"),
                           ("r.bin", b"r-content-7")]:
            with open(os.path.join(self.arch, name), "wb") as f:
                f.write(data)
        out = os.path.join(self._td.name, "Snap")
        os.makedirs(out)
        partial = os.path.join(out, "Scan_P.partial.sqlite")
        con = core.create_partial_snapshot(partial, [("Arch", self.arch)],
                                           config={"phase": "test"})
        core.enumerate_and_reconcile(con)
        dbh.process_hash_stage(con, "full")
        con.execute("UPDATE entries SET meta_status='skipped'"
                    " WHERE meta_status='pending'")
        con.commit()
        self.final = core.finalize_snapshot(con, partial, "full")

    def tearDown(self):
        self._td.cleanup()

    def test_patrol_ok_then_detects_injection(self):
        vh = importlib.import_module(
            "Script_DAISY_Module_Check_Hash")
        rep = vh.patrol(self.final, {"Arch": self.arch},
                        sample_percent=100.0, full=True)
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["stat_missing"], [])
        self.assertEqual(rep["stat_changed"], [])
        self.assertEqual(rep["hash_mismatched"], [])
        self.assertEqual(rep["hash_checked"], 3)
        # 注入①：同尺寸改内容＋回拨 mtime——stat 层不可见，仅哈希可检出
        target = os.path.join(self.arch, "p.bin")
        st = os.stat(target)
        with open(target, "rb") as f:
            data = bytearray(f.read())
        data[0] ^= 0xFF
        with open(target, "wb") as f:
            f.write(bytes(data))
        os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns))
        # 注入②：文件消失
        os.remove(os.path.join(self.arch, "q.bin"))
        rep2 = vh.patrol(self.final, {"Arch": self.arch},
                         sample_percent=100.0, full=True)
        self.assertFalse(rep2["ok"])
        self.assertEqual([m["rel_path"] for m in rep2["stat_missing"]],
                         ["q.bin"])
        self.assertEqual([m["rel_path"] for m in rep2["hash_mismatched"]],
                         ["p.bin"])

    def test_cli_creates_explicit_report_parent(self):
        report = os.path.join(
            self._td.name, "new", "nested", "hash_report.json")
        script = os.path.join(
            _MODULE, "Script_DAISY_Module_Check_Hash.py")
        result = subprocess.run(
            [
                sys.executable, "-B", script,
                "--snapshot", self.final,
                "--root", f"Arch={self.arch}",
                "--full",
                "--report", report,
            ],
            capture_output=True, timeout=120,
        )
        self.assertEqual(
            result.returncode, 0,
            result.stderr.decode("utf-8", "replace"))
        self.assertTrue(os.path.isfile(report))
        with open(report, encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertTrue(payload["ok"])
        self.assertEqual(
            payload["report_metadata"],
            core.report_metadata("哈希核验"),
        )

    def test_cli_problem_adds_clean_named_markdown_report(self):
        os.remove(os.path.join(self.arch, "q.bin"))
        report = os.path.join(self._td.name, "hash_report.json")
        script = os.path.join(
            _MODULE, "Script_DAISY_Module_Check_Hash.py")
        result = subprocess.run(
            [
                sys.executable, "-B", script,
                "--snapshot", self.final,
                "--root", f"Arch={self.arch}",
                "--full", "--report", report,
            ],
            capture_output=True, timeout=120,
        )
        self.assertEqual(result.returncode, 1)
        issue_report = os.path.splitext(report)[0] + "_Issues.md"
        self.assertTrue(os.path.isfile(report))
        self.assertTrue(os.path.isfile(issue_report))
        with open(issue_report, encoding="utf-8") as handle:
            self.assertIn("q.bin", handle.read())


class TestValidators(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(
            prefix="validator_", dir=_RUNTIME_ROOT)
        self.dir = self._td.name

    def tearDown(self):
        self._td.cleanup()

    def test_zip_good_truncated_crc_ole(self):
        import zipfile as _zf
        payload = b"KNOWN-PAYLOAD-BYTES-" * 8
        good = os.path.join(self.dir, "good.zip")
        with _zf.ZipFile(good, "w") as z:
            z.writestr("a.bin", payload, compress_type=_zf.ZIP_STORED)
            z.writestr("b.txt", b"hello" * 20)
        self.assertEqual(Validate.validate_zip(good), ("valid", None))
        with open(good, "rb") as f:
            raw = f.read()
        trunc = os.path.join(self.dir, "trunc.zip")     # 截断→中央目录坏
        with open(trunc, "wb") as f:
            f.write(raw[:-30])
        st, detail = Validate.validate_zip(trunc)
        self.assertEqual(st, "invalid")
        self.assertTrue(detail)
        crcbad = os.path.join(self.dir, "crc.zip")      # 数据区翻转→CRC 层检出
        pos = raw.find(payload)
        mut = bytearray(raw)
        mut[pos] ^= 0xFF
        with open(crcbad, "wb") as f:
            f.write(bytes(mut))
        st, detail = Validate.validate_zip(crcbad)
        self.assertEqual(st, "invalid")
        self.assertIn("a.bin", detail)
        ole = os.path.join(self.dir, "enc.docx")        # OLE 魔数→unsupported
        with open(ole, "wb") as f:
            f.write(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)
        st, _ = Validate.validate_zip(ole)
        self.assertEqual(st, "unsupported")

    def test_pdf_head_tail_xref(self):
        good = os.path.join(self.dir, "good.pdf")
        with open(good, "wb") as f:
            f.write(b"%PDF-1.4\n1 0 obj<<>>endobj\nxref\n0 1\n"
                    b"trailer<<>>\nstartxref\n9\n%%EOF\n")
        self.assertEqual(Validate.validate_pdf(good), ("valid", None))
        noeof = os.path.join(self.dir, "noeof.pdf")
        with open(noeof, "wb") as f:
            f.write(b"%PDF-1.4\nstartxref\n9\n")
        self.assertEqual(Validate.validate_pdf(noeof)[0], "invalid")
        garbage = os.path.join(self.dir, "garbage.pdf")
        with open(garbage, "wb") as f:
            f.write(b"\x00" * 128)
        self.assertEqual(Validate.validate_pdf(garbage)[0], "invalid")

    def test_sevenzip_t(self):
        sz = core.discover_tool("sevenzip", None)
        src = os.path.join(self.dir, "src.bin")
        with open(src, "wb") as f:
            f.write(b"seven-zip-data" * 100)
        arch = os.path.join(self.dir, "t.7z")
        subprocess.run([sz, "a", arch, src], capture_output=True, check=True)
        self.assertEqual(Validate.validate_sevenzip(arch, sz), ("valid", None))
        with open(arch, "rb") as f:
            raw = f.read()
        bad = os.path.join(self.dir, "bad.7z")
        with open(bad, "wb") as f:
            f.write(raw[:len(raw) // 2])
        self.assertEqual(Validate.validate_sevenzip(bad, sz)[0], "invalid")

    def test_legacy_doc_and_gif_validator_routing(self):
        ole = os.path.join(self.dir, "legacy.doc")
        with open(ole, "wb") as f:
            f.write(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 32)
        with patch.object(
                Validate, "validate_sevenzip",
                return_value=("valid", None)) as sevenzip_check:
            self.assertEqual(
                Validate.validate_legacy_office(ole, "7z"),
                ("valid", None),
            )
        sevenzip_check.assert_called_once_with(ole, "7z")

        rtf = os.path.join(self.dir, "rtf-as-doc.doc")
        with open(rtf, "wb") as f:
            f.write(b"{\\rtf1 test}")
        status, detail = Validate.validate_legacy_office(rtf, "7z")
        self.assertEqual(status, "unsupported")
        self.assertIn("不是 OLE", detail)
        self.assertEqual(Validate._pick_validator("doc", "document"), "ole")
        self.assertEqual(
            Validate._pick_validator("gif", "image_gif"), "gif")
        self.assertEqual(
            Validate._pick_validator("gif", "other"), "gif")
        self.assertEqual(
            Validate._pick_validator("jfif", "photo_jpeg"), "media")
        self.assertEqual(
            Validate._pick_validator("jfif", "other"), "media")

    def test_gif_ffprobe_success_timeout_and_missing_tool(self):
        class Worker:
            @staticmethod
            def execute(_args):
                return b""

        with patch.object(
                Validate.meta,
                "ffprobe_full",
                return_value={
                    "streams": [
                        {"codec_type": "video", "codec_name": "gif"}
                    ]
                },
        ):
            self.assertEqual(
                Validate.validate_media(
                    "motion.gif", "image_gif", Worker(), "ffprobe"),
                ("valid", None),
            )
        with patch.object(
                Validate.meta,
                "ffprobe_full",
                side_effect=toolruntime.ToolProcessTimeout(
                    ["ffprobe"],
                    600,
                    pid=12345,
                    output=b"",
                    stderr=b"",
                    reaped=True,
                ),
        ):
            status, detail = Validate.validate_media(
                "motion.gif", "image_gif", Worker(), "ffprobe")
        self.assertEqual(status, "invalid")
        self.assertIn("ffprobe: 超时", detail)
        with self.assertRaises(core.PreflightError):
            Validate.validate_media(
                "motion.gif", "image_gif", Worker(), None)

    def test_header_only_wav_is_invalid(self):
        class Worker:
            @staticmethod
            def execute(_args):
                return b""

        with tempfile.TemporaryDirectory(
            prefix="audio_", dir=_RUNTIME_ROOT,
        ) as td:
            wav = os.path.join(td, "empty.wav")
            with open(wav, "wb") as stream:
                stream.write(b"RIFF" + b"\x00" * 40)
            with patch.object(
                    Validate.meta,
                    "ffprobe_full",
                    return_value={
                        "format": {},
                        "streams": [{"codec_type": "audio"}],
                    },
            ):
                status, detail = Validate.validate_media(
                    wav, "audio", Worker(), "ffprobe")
        self.assertEqual(status, "invalid")
        self.assertIn("没有可确认的音频样本", detail)

    def test_sevenzip_timeout_is_reported_not_raised(self):
        with patch.object(
                dbverify.toolruntime,
                "run_bounded_tool",
                side_effect=toolruntime.ToolProcessTimeout(
                    ["7z"],
                    3600,
                    pid=12346,
                    output=b"",
                    stderr=b"",
                    reaped=True,
                ),
        ):
            self.assertEqual(
                Validate.validate_sevenzip("slow.7z", "7z"),
                ("invalid", "7z t 超时"),
            )

    def test_shared_session_keeps_unknown_unsupported_without_tools(self):
        session = dbverify.FormatValidationSession({})
        try:
            spec = session.describe("unknown", "other")
            self.assertEqual(("none", "daisy-format"), (
                spec.validator, spec.tool_name))
            self.assertEqual(
                ("unsupported", None),
                session.validate("not-opened.unknown", "other", spec),
            )
        finally:
            session.close()

    def test_shared_session_maps_internal_results_and_tool_timeout(self):
        good = os.path.join(self.dir, "shared.pdf")
        with open(good, "wb") as stream:
            stream.write(b"%PDF-1.4\nstartxref\n0\n%%EOF\n")
        tools = {
            "sevenzip": {"path": "fixture-7z", "version": "24.fixture"},
        }
        session = dbverify.FormatValidationSession(tools)
        try:
            pdf_spec = session.describe("pdf", "document")
            self.assertEqual(
                ("valid", None),
                session.validate(good, "document", pdf_spec),
            )
            archive_spec = session.describe("7z", "archive")
            with patch.object(
                    dbverify, "validate_sevenzip",
                    return_value=("invalid", "7z t 超时")):
                self.assertEqual(
                    ("timeout", "7z t 超时"),
                    session.validate(
                        "fixture.7z", "archive", archive_spec),
                )
        finally:
            session.close()

    def test_exiftool_criteria(self):
        # 完好相机 JPG 的合规性警告不应被判为损坏
        ok_lines = [("Warning", "[minor] Odd offset for IFD0 tag 0x011a"),
                    ("Warning", "Missing required JPEG ExifIFD tag 0x9101"
                                " ComponentsConfiguration"),
                    ("Warning", "Missing required JPEG IFD0 tag 0x0213")]
        self.assertEqual(Validate.classify_et_findings(ok_lines), [])
        for bad in ("JPEG format error",
                    "Truncated 'mdat' data at offset 0x1f8",
                    "Error reading meta data",
                    "Processing JPEG-like data after unknown 998-byte header"):
            self.assertTrue(
                Validate.classify_et_findings([("Warning", bad)]), bad)
        self.assertTrue(
            Validate.classify_et_findings([("Error", "Unknown file type")]))
        # minor 前缀豁免（即便文本命中模式）
        self.assertEqual(Validate.classify_et_findings(
            [("Warning", "[minor] Truncated PreviewImage")]), [])

    def test_runtime_generated_truncated_png(self):
        good = os.path.join(self.dir, "generated_good.png")
        bad = os.path.join(self.dir, "generated_truncated.png")
        payload = core.build_tiny_png()
        with open(good, "wb") as f:
            f.write(payload)
        with open(bad, "wb") as f:
            f.write(payload[:-12])       # 动态移除 IEND 块，构造可重复截断

        exiftool = core.discover_tool("exiftool", None)
        worker = meta.ExifToolWorker(exiftool)
        try:
            self.assertEqual(
                Validate.validate_media(
                    good, "photo_working", worker, ffprobe=""),
                ("valid", None),
            )
            status, detail = Validate.validate_media(
                bad, "photo_working", worker, ffprobe="")
        finally:
            worker.close()

        self.assertEqual(status, "invalid")
        self.assertIn("Truncated PNG image", detail)


class _FormatFixture(unittest.TestCase):
    def setUp(self) -> None:
        os.makedirs(_RUNTIME_ROOT, exist_ok=True)
        self._td = tempfile.TemporaryDirectory(
            prefix="format_", dir=_RUNTIME_ROOT)
        self.base = self._td.name
        self.old_tree = os.path.join(self.base, "Tree")
        self.snaps = os.path.join(self.base, "Snapshots")
        os.makedirs(self.old_tree)
        os.makedirs(self.snaps)

    def tearDown(self) -> None:
        self._td.cleanup()

    def snap(self, tree: str, name: str, **kwargs) -> str:
        kwargs.setdefault("label", "T")
        return test_tree.build_snapshot(
            tree, self.snaps, name, **kwargs)


class TestValidateSnapshot(_FormatFixture):
    def test_end_to_end_mixed_tree(self):
        import zipfile as _zf
        with _zf.ZipFile(os.path.join(self.old_tree, "ok.zip"), "w",
                         _zf.ZIP_DEFLATED) as z:
            z.writestr("m.txt", b"zip-member" * 30)
        with open(os.path.join(self.old_tree, "ok.zip"), "rb") as f:
            raw = f.read()
        with open(os.path.join(self.old_tree, "bad.zip"), "wb") as f:
            f.write(raw[:-25])
        with open(os.path.join(self.old_tree, "doc.pdf"), "wb") as f:
            f.write(b"%PDF-1.4\nxref\nstartxref\n9\n%%EOF\n")
        png = core.build_tiny_png()
        tt.write(self.old_tree, "generated_good.png", png)
        tt.write(self.old_tree, "generated_truncated.png", png[:-12])
        tt.write(self.old_tree, "note.txt", b"plain")
        tt.write(self.old_tree, "gone.bin", b"will-vanish")
        snap = self.snap(self.old_tree, "val", hash_mode="none")
        os.remove(os.path.join(self.old_tree, "gone.bin"))
        rep = Validate.validate_snapshot(snap, {"T": self.old_tree},
                                         report_dir=self.base)
        by = {r["rel_path"]: r for r in rep["rows"]}
        self.assertEqual(by["ok.zip"]["status"], "valid")
        self.assertEqual(by["bad.zip"]["status"], "invalid")
        self.assertEqual(by["doc.pdf"]["status"], "valid")
        self.assertEqual(by["generated_good.png"]["status"], "valid")
        self.assertEqual(by["generated_truncated.png"]["status"], "invalid")
        self.assertIn("Truncated PNG image",
                      by["generated_truncated.png"]["detail"])
        self.assertEqual(by["note.txt"]["status"], "unsupported")
        self.assertEqual(by["gone.bin"]["status"], "missing")
        self.assertFalse(rep["ok"])
        self.assertEqual(
            rep["report_metadata"],
            core.report_metadata("格式校验"),
        )
        self.assertTrue(any(
            path.endswith("_Info.csv") for path in rep["files"]))
        for suffix in (".json", ".csv", ".md"):
            self.assertTrue(any(f.endswith(suffix) for f in rep["files"]),
                            suffix)
        self.assertRegex(                        # 报告名遵循当前命名体系；
            os.path.basename(rep["files"][0]),   # 问题状态只在报告内容中
            r"^T_Check_Format_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}"
            r"\.\d{6}_[0-9a-f]{8}\.json$")


if __name__ == "__main__":
    unittest.main(verbosity=2)

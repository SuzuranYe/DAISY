"""DAISY 核心、映射与归档单元测试。"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest.mock import patch


_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPT_DIR = os.path.dirname(_TEST_DIR)
_LIB_DIR = os.path.join(_SCRIPT_DIR, "Lib")
_MODULE_DIR = os.path.join(_SCRIPT_DIR, "Module")
sys.path[:0] = [_LIB_DIR, _MODULE_DIR, _SCRIPT_DIR]

import Script_DAISY_Lib_Storage_Core as core
import Script_DAISY_Lib_Storage_Windows as windows
import Script_DAISY_Lib_Storage_Smartctl as smartctl
import Script_DAISY_Lib_Storage_Service as service
import Script_DAISY_Lib_Storage_Archive as archive
import Script_DAISY_CLI as entry
import Script_DAISY_Module_Storage_Collect as collect_module


def fixture_record(
    *,
    number: int = 3,
    label: str = "Node",
    letter: str = "D:",
    detailed: bool = True,
) -> core.WindowsDiskRecord:
    volume = {
        "drive_letter": letter,
        "file_system_label": label,
        "file_system": "NTFS",
        "health_status": "Healthy",
        "size": 4_000_000,
        "size_remaining": 1_000_000,
        "used_bytes": 3_000_000,
        "used_percent": 75.0,
    }
    data = {
        "disk": {
            "number": number,
            "friendly_name": "Fixture SSD",
            "serial_number": "SERIAL-001",
            "unique_id": "fixture-id",
            "size": 4_100_000,
            "bus_type": "NVMe",
            "partition_style": "GPT",
            "health_status": "Healthy",
            "logical_sector_size": 512,
            "physical_sector_size": 4096,
        },
        "partitions": [
            {
                "disk_number": number,
                "partition_number": 1,
                "offset": 100_000,
                "size": 4_000_000,
                "end_offset_exclusive": 4_100_000,
                "type": "Basic",
                "operational_status": ["Online"],
                "volume": volume,
            }
        ],
        "layout_gaps": [
            {
                "kind": "leading_layout_gap",
                "offset": 0,
                "size": 100_000,
                "end_offset_exclusive": 100_000,
            }
        ],
        "collection": {
            "detail_level": "detailed" if detailed else "summary",
            "source_cmdlets": list(windows.SOURCE_CMDLETS_DETAILED),
        },
        "win32_disk_drive": None,
    }
    return core.WindowsDiskRecord(
        disk_number=number,
        data=data,
        warnings=(),
        detail_level="detailed" if detailed else "summary",
    )


def fixture_collection(
    *,
    exit_status: int = 0,
    warnings: tuple[str, ...] = (),
) -> core.CollectionResult:
    record = fixture_record()
    device = core.SmartDevice(
        name="/dev/sdd", device_type="nvme", protocol="NVMe", disk_number=3
    )
    target = core.DiskTarget(3, record, device)
    payload = {
        "smartctl": {
            "exit_status": exit_status,
            "output": ["SMART fixture output"],
        },
        "smart_status": {"passed": True},
        "serial_number": "SERIAL-001",
    }
    smart = core.SmartRead(
        payload=payload,
        raw_json=core.json_text(payload),
        stderr="",
        exit_status=exit_status,
        command=("smartctl.exe", "-x", "--json=ov", "-d", "nvme", "/dev/sdd"),
        smartctl_version="7.5",
    )
    return core.CollectionResult(
        target=target,
        windows=record,
        smart=smart,
        started_at_utc="2026-08-04T19:00:00Z",
        collected_at_utc="2026-08-04T19:00:01Z",
        collected_at_local="2026-08-05T03:00:01+08:00",
        warnings=warnings,
        report="DAISY fixture report\n",
    )


class TestCore(unittest.TestCase):
    def test_integrated_version_matches_daisy_release(self):
        import Script_DAISY_Lib_Snapshot_Core as daisy_core

        self.assertEqual(core.APP_VERSION, "1.6.8")
        self.assertEqual(core.APP_VERSION, daisy_core.SCANNER_VERSION)

    def test_disk_labels_and_archive_identity(self):
        record = fixture_record()
        self.assertEqual(record.drive_letters, ("D:",))
        self.assertEqual(record.volume_labels, ("Node",))
        self.assertEqual(record.explorer_names, ("D: Node",))
        self.assertEqual(core.archive_identity(record), "Node")
        stamp = datetime(2026, 8, 5, 3, 4, 5, tzinfo=timezone.utc)
        self.assertEqual(
            core.archive_base_name(record, stamp),
            "Node_PROFILE_2026-08-05_03-04-05",
        )

    def test_utc_iso_uses_the_same_aware_instant(self):
        local = datetime(
            2026,
            8,
            5,
            4,
            51,
            49,
            tzinfo=timezone(timedelta(hours=8)),
        )
        self.assertEqual(core.utc_iso(local), "2026-08-04T20:51:49Z")
        with self.assertRaises(ValueError):
            core.utc_iso(datetime(2026, 8, 5, 4, 51, 49))

    def test_safe_file_component_and_format(self):
        self.assertEqual(core.safe_file_component(' A<B>:C? '), "A_B_C")
        self.assertEqual(core.safe_file_component("CON"), "_CON")
        self.assertEqual(core.format_bytes(4_000_000_000_000), "4.00 TB")
        self.assertEqual(core.normalise_text("a\nb\nc\n"), "a\nb\nc\n")

    def test_smartctl_exit_bits(self):
        flags = core.decode_smartctl_exit_status(0x42)
        self.assertEqual(len(flags), 2)
        self.assertIn("设备无法打开", flags[0])
        self.assertIn("错误日志", flags[1])

    def test_collection_status_distinguishes_incomplete_results(self):
        self.assertEqual(
            core.classify_collection_status(0, has_warnings=False),
            "complete",
        )
        self.assertEqual(
            core.classify_collection_status(0x40, has_warnings=False),
            "complete_with_warnings",
        )
        self.assertEqual(
            core.classify_collection_status(0, has_warnings=True),
            "complete_with_warnings",
        )
        self.assertEqual(
            core.classify_collection_status(0x02, has_warnings=False),
            "incomplete",
        )


class TestWindowsInventoryLogic(unittest.TestCase):
    def test_volume_used_space_and_layout_gaps(self):
        volume = windows._normalise_volume(
            {
                "drive_letter": "m",
                "size": 1000,
                "size_remaining": 250,
                "operational_status": "OK",
            }
        )
        self.assertIsNotNone(volume)
        assert volume is not None
        self.assertEqual(volume["drive_letter"], "M:")
        self.assertEqual(volume["used_bytes"], 750)
        self.assertEqual(volume["used_percent"], 75.0)
        gaps = windows._layout_gaps(
            {"size": 1000},
            [
                {"offset": 100, "size": 300},
                {"offset": 500, "size": 400},
            ],
        )
        self.assertEqual([gap["size"] for gap in gaps], [100, 100, 100])
        self.assertIn("不等同于可分配空间", gaps[0]["note"])

    def test_identity_guard(self):
        original = fixture_record()
        windows.assert_same_disk(original, fixture_record())
        changed = fixture_record()
        changed.data["disk"]["unique_id"] = "another-device"
        with self.assertRaises(core.DaisySmartError):
            windows.assert_same_disk(original, changed)


class TestSmartctlCommands(unittest.TestCase):
    def test_windows_disk_number_mapping(self):
        self.assertEqual(smartctl.windows_disk_number("/dev/sda"), 0)
        self.assertEqual(smartctl.windows_disk_number("/dev/sdz"), 25)
        self.assertEqual(smartctl.windows_disk_number("/dev/sdaa"), 26)
        self.assertEqual(smartctl.windows_disk_number(r"\\.\PhysicalDrive10"), 10)

    def test_exact_read_only_templates(self):
        device = core.SmartDevice("/dev/sdk", "sat", disk_number=10)
        scan = smartctl.build_scan_command("smartctl.exe")
        read = smartctl.build_read_command("smartctl.exe", device)
        self.assertEqual(scan[1:], ["--scan-open", "--json=c"])
        self.assertEqual(
            read[1:], ["-x", "--json=ov", "-d", "sat", "/dev/sdk"]
        )
        with self.assertRaises(AssertionError):
            smartctl.assert_read_only_command(
                ["smartctl.exe", "-t", "long", "/dev/sdk"], purpose="read"
            )

    def test_smartctl_minimum_version_is_enforced(self):
        self.assertEqual(smartctl.require_supported_version("7.5"), "7.5")
        self.assertEqual(smartctl.require_supported_version("7.6 r1234"), "7.6 r1234")
        for value in ("7.4", "未知版本"):
            with self.assertRaises(core.DaisySmartError):
                smartctl.require_supported_version(value)


class TestServiceMapping(unittest.TestCase):
    def test_windows_and_smartctl_are_joined_by_disk_number(self):
        record = fixture_record(detailed=False)
        inventory = windows.WindowsInventory(
            records=(record,),
            warnings=(),
            powershell_executable="powershell.exe",
            powershell_version="5.1",
            collected_at_utc="t",
            detail_level="summary",
        )
        device = core.SmartDevice("/dev/sdd", "nvme", disk_number=3)
        scan = smartctl.SmartctlScan(
            devices=(device,),
            warnings=(),
            executable="smartctl.exe",
            version="7.5",
            command=("smartctl.exe", "--scan-open", "--json=c"),
        )
        with patch.object(windows, "read_inventory", return_value=inventory), patch.object(
            smartctl, "scan", return_value=scan
        ):
            result = service.scan_targets()
        self.assertEqual(len(result.targets), 1)
        self.assertIs(result.targets[0].windows, record)
        self.assertIs(result.targets[0].smart_device, device)

    def test_summary_report_formats_key_health_values(self):
        collection = fixture_collection()
        collection.windows.data["disk"]["is_read_only"] = False
        collection.windows.data["physical_disk"] = {"media_type": "HDD"}
        collection.windows.data["storage_reliability_counter"] = {
            "temperature_celsius": 30,
            "power_on_hours": 641,
            "wear_percent": 0,
            "read_errors_uncorrected": 0,
            "write_errors_uncorrected": None,
        }
        collection.smart.payload["ata_smart_attributes"] = {
            "table": [
                {
                    "id": 5,
                    "name": "Reallocated_Sector_Ct",
                    "value": 200,
                    "worst": 200,
                    "thresh": 140,
                    "when_failed": "",
                    "raw": {"value": 0, "string": "0"},
                },
                {
                    "id": 194,
                    "name": "Temperature_Celsius",
                    "value": 122,
                    "worst": 93,
                    "thresh": 0,
                    "when_failed": "",
                    "raw": {"value": 30, "string": "30"},
                },
                {
                    "id": 197,
                    "name": "Current_Pending_Sector",
                    "value": 200,
                    "worst": 200,
                    "thresh": 0,
                    "when_failed": "",
                    "raw": {"value": 1, "string": "1"},
                },
            ]
        }
        report = service.render_collection_report(
            collection.target,
            collection.windows,
            collection.smart,
            started_at_utc=collection.started_at_utc,
            collected_at_utc=collection.collected_at_utc,
            collected_at_local=collection.collected_at_local,
            warnings=collection.warnings,
        )
        self.assertTrue(report.startswith("DAISY 硬盘信息登记简化报告\n"))
        self.assertIn("Windows 只读属性：否", report)
        self.assertIn("报告生成工具：DAISY 硬盘信息登记", report)
        self.assertIn("作者：Suzuran Ye", report)
        self.assertIn("05 Reallocated_Sector_Ct｜原始值 0", report)
        self.assertIn("C5 Current_Pending_Sector｜原始值 1", report)
        self.assertIn("状态 注意（原始值非零，未触发阈值）", report)
        self.assertNotIn("Temperature_Celsius", report)
        self.assertNotIn("温度：", report)
        self.assertIn("磨损：0%（Windows 返回；HDD 不一定适用）", report)
        self.assertIn("未校正读／写错误：读 0｜写 未提供", report)


class TestArchive(unittest.TestCase):
    def test_create_runs_full_internal_verification_after_publish(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            original = archive.verify_archive
            with patch.object(
                    archive, "verify_archive", wraps=original) as verified:
                result = archive.create_archive(
                    fixture_collection(), temp_dir)
            verified.assert_called_once()
            self.assertEqual(
                os.path.abspath(os.fspath(verified.call_args.args[0])),
                os.path.abspath(result.path),
            )

    def test_create_verify_and_internal_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = archive.create_archive(fixture_collection(), temp_dir)
            self.assertTrue(os.path.basename(result.path).startswith("Node_PROFILE_"))
            self.assertTrue(result.path.endswith(f"_{result.fingerprint}.zip"))
            match = archive.ARCHIVE_FILENAME_RE.fullmatch(os.path.basename(result.path))
            self.assertIsNotNone(match)
            assert match is not None
            expected_names = archive.required_names(match.group("base"))
            verified = archive.verify_archive(result.path)
            self.assertEqual(verified.zip_sha256, result.zip_sha256)
            self.assertEqual(
                verified.manifest["application"]["version"], "1.6.8")
            self.assertEqual(
                verified.manifest["application"]["author"], "Suzuran Ye")
            self.assertEqual(
                verified.manifest["archive_schema_version"],
                3,
            )
            self.assertEqual(verified.manifest["archive_role"], core.ARCHIVE_ROLE)
            self.assertEqual(verified.manifest["archive"]["kind"], "PROFILE")
            self.assertEqual(
                verified.manifest["integrity"]["filename_layout_version"],
                3,
            )
            self.assertEqual(
                verified.manifest["collection"]["status"],
                "complete",
            )
            self.assertEqual(set(verified.internal_files), expected_names)
            self.assertEqual(len(expected_names), 3)
            self.assertTrue(all("/" not in name for name in expected_names))
            self.assertFalse(any("Report" in name for name in expected_names))
            self.assertFalse(any("Checksum" in name for name in expected_names))
            self.assertFalse(any(name.endswith("Smartctl.txt") for name in expected_names))
            with zipfile.ZipFile(result.path, "r") as package:
                for name in expected_names:
                    self.assertIn(name, package.namelist())
                for name in expected_names:
                    content = package.read(name)
                    self.assertFalse(content.startswith(b"\xef\xbb\xbf"), name)
                    self.assertNotIn(b"\n", content, name)
            for metadata in verified.manifest["payload_files"].values():
                self.assertEqual(set(metadata), {"bytes", "role"})

    def test_archive_creation_times_are_the_same_instant(self):
        created_local = datetime(
            2026,
            8,
            5,
            4,
            51,
            49,
            tzinfo=timezone(timedelta(hours=8)),
        )
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            core,
            "local_now",
            return_value=created_local,
        ):
            result = archive.create_archive(fixture_collection(), temp_dir)
        collection = result.manifest["collection"]
        self.assertEqual(
            collection["archive_created_at_utc"],
            "2026-08-04T20:51:49Z",
        )
        self.assertEqual(
            collection["archive_created_at_local"],
            "2026-08-05T04:51:49+08:00",
        )

    def test_old_smart_filename_and_inconsistent_times_are_rejected(self):
        self.assertIsNone(
            archive.ARCHIVE_FILENAME_RE.fullmatch(
                "Node_SMART_2026-08-05_03-04-05_12345678.zip"
            )
        )
        with self.assertRaises(core.DaisySmartError):
            archive._validate_manifest_time_pair(
                {
                    "archive_created_at_utc": "2026-08-04T20:51:49Z",
                    "archive_created_at_local": "2026-08-05T04:51:41+08:00",
                },
                "archive_created_at_utc",
                "archive_created_at_local",
            )

    def test_optional_summary_report_is_outside_zip(self):
        collection = fixture_collection()
        with tempfile.TemporaryDirectory() as temp_dir:
            result = archive.create_archive(
                collection,
                temp_dir,
                summary_txt=True,
            )
            self.assertIsNotNone(result.summary_report_path)
            assert result.summary_report_path is not None
            self.assertEqual(
                os.path.basename(result.summary_report_path),
                os.path.splitext(os.path.basename(result.path))[0] + "_Report.txt",
            )
            with open(result.summary_report_path, "rb") as handle:
                report = handle.read()
            self.assertFalse(report.startswith(b"\xef\xbb\xbf"))
            self.assertNotIn(b"\n", report)
            text = report.decode("utf-8")
            self.assertEqual(text, collection.report)
            self.assertNotIn("关联归档", text)
            self.assertNotIn(os.path.basename(result.path), text)
            self.assertNotIn(result.zip_sha256, text)
            with zipfile.ZipFile(result.path, "r") as package:
                self.assertNotIn(os.path.basename(result.summary_report_path), package.namelist())

    def test_optional_summary_collision_preserves_first_pair(self):
        collection = fixture_collection()
        forced = "b" * 64
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(core, "sha256_file", return_value=forced):
                first = archive.create_archive(
                    collection,
                    temp_dir,
                    summary_txt=True,
                )
            assert first.summary_report_path is not None
            with open(first.path, "rb") as handle:
                zip_digest = hashlib.sha256(handle.read()).hexdigest()
            with open(first.summary_report_path, "rb") as handle:
                report_digest = hashlib.sha256(handle.read()).hexdigest()
            with patch.object(core, "sha256_file", return_value=forced):
                with self.assertRaises(core.DaisySmartError):
                    archive.create_archive(
                        collection,
                        temp_dir,
                        summary_txt=True,
                    )
            with open(first.path, "rb") as handle:
                self.assertEqual(hashlib.sha256(handle.read()).hexdigest(), zip_digest)
            with open(first.summary_report_path, "rb") as handle:
                self.assertEqual(hashlib.sha256(handle.read()).hexdigest(), report_digest)

    def test_no_clobber_preserves_first_archive(self):
        collection = fixture_collection()
        with tempfile.TemporaryDirectory() as temp_dir:
            forced = "a" * 64
            with patch.object(core, "sha256_file", return_value=forced):
                first = archive.create_archive(collection, temp_dir)
            with open(first.path, "rb") as handle:
                original = hashlib.sha256(handle.read()).hexdigest()
            with patch.object(core, "sha256_file", return_value=forced):
                with self.assertRaises(core.DaisySmartError):
                    archive.create_archive(collection, temp_dir)
            with open(first.path, "rb") as handle:
                self.assertEqual(hashlib.sha256(handle.read()).hexdigest(), original)
            partials = [name for name in os.listdir(temp_dir) if name.endswith(".partial.zip")]
            self.assertEqual(len(partials), 1)

    def test_tamper_breaks_filename_fingerprint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = archive.create_archive(fixture_collection(), temp_dir)
            with open(result.path, "ab") as handle:
                handle.write(b"tamper")
            with self.assertRaises(core.DaisySmartError):
                archive.verify_archive(result.path)


class TestEntry(unittest.TestCase):
    def test_guide_lists_public_commands(self):
        text = entry.guide()
        for command in (
            "env-check", "storage-list", "storage-collect",
        ):
            self.assertIn(command, text)
        self.assertIn("只读", text)

    def test_collect_returns_one_for_diagnostic_archive(self):
        collection = fixture_collection(exit_status=0x02)
        scan = core.ScanResult(
            targets=(collection.target,),
            warnings=(),
            smartctl_executable="smartctl.exe",
            smartctl_version="7.5",
        )
        result = archive.ArchiveResult(
            path="diagnostic.zip",
            zip_sha256="a" * 64,
            fingerprint="AAAAAAAA",
            internal_files=("fixture.json",),
            manifest={"collection": {"status": "incomplete"}},
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(collect_module.service, "scan_targets", return_value=scan),
            patch.object(
                collect_module.service,
                "target_by_disk_number",
                return_value=collection.target,
            ),
            patch.object(
                collect_module.service,
                "collect_target",
                return_value=collection,
            ),
            patch.object(
                collect_module.archive,
                "create_archive",
                return_value=result,
            ),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            status = collect_module.main(["--disk-number", "3", "--json"])
        self.assertEqual(status, 1)
        self.assertFalse(json.loads(stdout.getvalue())["complete"])
        self.assertIn("不能视为完整的硬盘信息登记结果", stderr.getvalue())

    def test_fused_stg_module_dispatches_internal_list_mode(self):
        collection = fixture_collection()
        scan = core.ScanResult(
            targets=(collection.target,), warnings=(),
            smartctl_executable="smartctl.exe", smartctl_version="7.5",
        )
        stdout = io.StringIO()
        with (
            patch.object(
                collect_module.service, "scan_targets", return_value=scan),
            redirect_stdout(stdout),
        ):
            status = collect_module.main(["--list", "--json"])
        self.assertEqual(status, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["targets"][0]["disk_number"], 3)
        self.assertEqual(payload["application"]["author"], "Suzuran Ye")


if __name__ == "__main__":
    unittest.main(verbosity=2)

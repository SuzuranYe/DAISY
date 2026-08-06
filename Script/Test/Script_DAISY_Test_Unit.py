"""DAISY 单元与集成测试（unittest，仅使用 Python 标准库）。

运行：python -B .\\Script\\Test\\Script_DAISY_Test_Unit.py
语义说明：Spec\\Spec_DAISY_Technical.md；DDL 与精确运行行为以当前代码为准。
"""
from __future__ import annotations

import io
import json
import os
import re
import runpy
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPT = os.path.dirname(_TEST_DIR)
_REPO_ROOT = os.path.dirname(_SCRIPT)
_LIB = os.path.join(_SCRIPT, "Lib")
_MODULE = os.path.join(_SCRIPT, "Module")
sys.path[:0] = [_TEST_DIR, _SCRIPT, _LIB, _MODULE]

import Script_DAISY_Lib_DBS_01_Core as core
import Script_DAISY_GUI as gui
import Script_DAISY_MAIN as entry


class TestGuiArguments(unittest.TestCase):
    def test_gui_preferences_round_trip_as_utf8_lf(self):
        preferences = gui.default_gui_preferences()
        preferences.update({
            "window_size": [1600, 900],
            "font_family": "Segoe UI",
            "font_size_delta": 1,
            "confirm_close_when_idle": False,
            "last_task_key": "check_hash",
        })
        with tempfile.TemporaryDirectory() as temporary:
            path = os.path.join(temporary, "GUI_Settings.json")
            gui.save_gui_preferences(preferences, path)
            self.assertEqual(gui.load_gui_preferences(path), preferences)
            with open(path, "rb") as handle:
                raw = handle.read()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r\n", raw)
        self.assertTrue(raw.endswith(b"\n"))

    def test_invalid_gui_preferences_fall_back_safely(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = os.path.join(temporary, "GUI_Settings.json")
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                json.dump({
                    "window_size": [10, 20],
                    "font_family": "",
                    "font_size_delta": 99,
                    "confirm_close_when_idle": "no",
                    "last_task_key": "storage_list",
                }, handle)
            loaded = gui.load_gui_preferences(path)
        self.assertEqual(loaded, gui.default_gui_preferences())

    def test_page_preference_never_contains_form_values(self):
        app = object.__new__(gui.DaisyApp)
        app.gui_preferences = gui.default_gui_preferences()
        app.default_window_size = (1920, 1080)
        app.ui_font_family = "Microsoft YaHei UI"
        app.ui_font_size_delta = 0
        app.confirm_close_when_idle = True
        app.task = gui.TASK_BY_KEY["check_hash"]
        app.saved_values = {
            "check_hash": {
                "snapshot": r"E:\私人档案\snapshot.sqlite",
                "root_map": r"E:\私人档案",
            },
        }

        with patch.object(gui, "save_gui_preferences") as save:
            app._save_gui_preferences()

        payload = save.call_args.args[0]
        self.assertEqual(payload["last_task_key"], "check_hash")
        self.assertNotIn("saved_values", payload)
        self.assertNotIn("snapshot", payload)
        self.assertNotIn("root_map", payload)

    def test_form_titles_follow_one_concise_naming_structure(self):
        for task in gui.TASKS:
            for spec in task.fields:
                self.assertLessEqual(
                    len(spec.label), gui._FORM_FIELD_TITLE_MAX_CHARS,
                    f"{task.key}.{spec.key}: {spec.label}",
                )
                self.assertLessEqual(
                    len(spec.section), gui._FORM_FIELD_TITLE_MAX_CHARS,
                    f"{task.key}.{spec.key}: {spec.section}",
                )
                self.assertNotIn("*", spec.label)
        self.assertTrue(all(
            len(label) <= gui._FORM_FIELD_TITLE_MAX_CHARS
            for label in gui._TASK_TOOLBAR_LABELS.values()
        ))
        expected = {
            ("full_scan", "root_batch_mode"): "生成方式",
            ("full_scan", "resume"): "续传快照",
            ("full_scan", "collect_file_id"): "NTFS标识",
            ("check_format", "root_map"): "档案根目录",
            ("check_format", "force"): "指纹降级",
            ("check_hash", "force"): "指纹降级",
            ("diff", "old"): "基准快照",
            ("diff", "new"): "对比快照",
            ("storage_collect", "summary_txt"): "简化文本",
        }
        actual = {
            (task.key, spec.key): spec.label
            for task in gui.TASKS for spec in task.fields
        }
        for identity, label in expected.items():
            self.assertEqual(actual[identity], label)

    def test_env_check_exposes_only_environment_settings(self):
        fields = [spec.key for spec in gui.TASK_BY_KEY["env_check"].fields]
        self.assertEqual(
            gui.TASK_BY_KEY["env_check"].nav, "ENV-01  运行环境检测")
        self.assertEqual(
            gui.TASK_BY_KEY[gui._PROJECT_SELF_TEST_KEY].nav,
            "DBS-91  DAISY功能自检",
        )
        self.assertEqual(
            gui.TASK_BY_KEY[gui._PROJECT_SELF_TEST_KEY].fields, ())
        self.assertEqual(
            gui._NAV_COLOURS["env_check"],
            gui._NAV_COLOURS["full_scan"],
        )
        for task_key in gui._TASK_MENU_SECTIONS[1][1]:
            self.assertEqual(
                gui._NAV_COLOURS["full_scan"],
                gui._NAV_COLOURS[task_key],
            )
        self.assertEqual(
            fields,
            [
                "output_dir", "exiftool_path", "ffprobe_path", "sevenzip_path",
                "powershell_path", "smartctl_path",
            ],
        )
        args = gui.build_tool_args("env_check", {})
        for retired in ("--root", "--limit", "--et-sample", "--read-cap-gb"):
            self.assertNotIn(retired, args)

    def test_positive_snapshot_switches_map_to_negative_cli_flags(self):
        for task in gui.TASKS:
            for spec in task.fields:
                if spec.kind not in ("choice", "choice_flag"):
                    continue
                default_labels = [
                    label for label, value in spec.choices
                    if value == spec.default
                ]
                self.assertEqual(len(default_labels), 1, spec.key)
                self.assertTrue(
                    default_labels[0].endswith("（默认）"), spec.key)
                for label, value in spec.choices:
                    if value != spec.default:
                        self.assertNotIn("（默认）", label, spec.key)

        full_fields = {
            spec.key: spec for spec in gui.TASK_BY_KEY["full_scan"].fields
        }
        quick_fields = {
            spec.key: spec for spec in gui.TASK_BY_KEY["quick_scan"].fields
        }
        metadata_storage = full_fields["metadata_storage"]
        self.assertEqual(metadata_storage.default, "complete")
        self.assertEqual(
            metadata_storage.choices,
            (
                ("全量元数据：基础字段＋原始工具输出（默认）", "complete"),
                ("基础元数据：仅保留规范化常用字段", "normalized"),
            ),
        )
        self.assertIn("视频和音频还通过 ffprobe", metadata_storage.help)
        self.assertIn("GIF 在基础范围只使用 ExifTool", metadata_storage.help)
        self.assertEqual(metadata_storage.kind, "choice")
        for spec in (
                full_fields["collect_file_id"],
                quick_fields["collect_file_id"]):
            self.assertEqual(spec.kind, "choice_flag")
            self.assertEqual(spec.flag_value, False)
            self.assertEqual(
                tuple(value for _label, value in spec.choices),
                (True, False),
            )

        defaults = gui.build_tool_args(
            "full_scan", {"roots": r"E:\Archive"})
        self.assertEqual(
            defaults[defaults.index("--metadata-storage") + 1], "complete")
        self.assertNotIn("--no-file-id", defaults)
        self.assertEqual(
            defaults[defaults.index("--hash") + 1], "full",
            "Full 的 GUI 默认值必须登记完整 SHA-256")
        powershell = r"C:\Tools\pwsh.exe"
        hashed = gui.build_tool_args(
            "full_scan",
            {"roots": r"E:\Archive", "powershell_path": powershell},
        )
        self.assertEqual(
            hashed[hashed.index("--powershell-path") + 1], powershell)
        no_hash = gui.build_tool_args(
            "full_scan",
            {
                "roots": r"E:\Archive", "hash_mode": "none",
                "powershell_path": powershell,
            },
        )
        self.assertNotIn("--powershell-path", no_hash)

        reduced = gui.build_tool_args(
            "full_scan",
            {
                "roots": r"E:\Archive",
                "metadata_storage": "normalized",
                "collect_file_id": False,
            },
        )
        self.assertEqual(
            reduced[reduced.index("--metadata-storage") + 1], "normalized")
        self.assertIn("--no-file-id", reduced)

        quick_reduced = gui.build_tool_args(
            "quick_scan",
            {"roots": r"E:\Archive", "collect_file_id": False},
        )
        self.assertIn("--no-file-id", quick_reduced)

    def test_conditional_modes_do_not_emit_inactive_settings(self):
        resumed = gui.build_tool_args(
            "full_scan",
            {
                "start_mode": "resume",
                "resume": r"E:\Runs\A.partial.sqlite",
                "roots": r"E:\Archive",
                "hash_mode": "full",
                "metadata_storage": "normalized",
                "powershell_path": r"C:\Tools\pwsh.exe",
            },
        )
        self.assertIn("--resume", resumed)
        self.assertIn("--manual-resume", resumed)
        self.assertIn("--control-stdin", resumed)
        for inactive in (
                "--root", "--output-dir", "--hash",
                "--metadata-storage", "--verify-sample-percent",
                "--powershell-path"):
            self.assertNotIn(inactive, resumed)

        full_hash_check = gui.build_tool_args(
            "check_hash",
            {
                "snapshot": r"E:\Runs\A.sqlite",
                "check_scope": "full",
                "sample_percent": "23",
            },
        )
        self.assertIn("--full", full_hash_check)
        self.assertNotIn("--sample-percent", full_hash_check)
        self.assertNotIn(
            "--report", full_hash_check,
            "GUI 显示默认报告目录时仍须沿用 DBS-31 的自动命名规则",
        )
        custom_hash_report = gui.build_tool_args(
            "check_hash",
            {
                "snapshot": r"E:\Runs\A.sqlite",
                "root_map": r"E:\Archive",
                "report": r"E:\Reports\Hash.json",
            },
        )
        self.assertEqual(
            custom_hash_report[custom_hash_report.index("--report") + 1],
            r"E:\Reports\Hash.json",
        )

        sampled_format = gui.build_tool_args(
            "check_format",
            {
                "snapshot": r"E:\Runs\A.sqlite",
                "check_scope": "sample",
                "sample_percent": "12.5",
            },
        )
        self.assertIn("--sample-percent", sampled_format)
        self.assertIn("12.5", sampled_format)

    def test_every_supported_cli_setting_has_a_gui_mapping(self):
        expected = {
            "env_check": {
                "--output-dir", "--exiftool-path", "--ffprobe-path",
                "--sevenzip-path", "--powershell-path", "--smartctl-path",
            },
            "full_scan": {
                "--root", "--output-dir", "--hash", "--previous-snapshot",
                "--map-root", "--verify-sample-percent", "--metadata-storage",
                "--no-file-id", "--resume",
                "--exiftool-path",
                "--ffprobe-path", "--sevenzip-path", "--powershell-path",
                "--format-validation", "--format-sample-percent",
                "--timeout-action", "--retry-mode", "--show-current-file",
            },
            "quick_scan": {
                "--root", "--output-dir", "--no-file-id", "--resume"},
            "check_format": {
                "--snapshot", "--root", "--sample-percent", "--report-dir",
                "--exiftool-path", "--ffprobe-path", "--sevenzip-path",
                "--force",
            },
            "check_hash": {
                "--snapshot", "--root", "--sample-percent", "--full",
                "--powershell-path", "--force", "--report",
            },
            "diff": {
                "--old", "--new", "--output-dir", "--map-root", "--force",
            },
            "export_report": {"--output-dir"},
            "storage_list": {"--smartctl-path", "--powershell-path"},
            "storage_collect": {
                "--disk-number", "--output-dir", "--summary-txt",
                "--smartctl-path", "--powershell-path",
            },
        }
        for task_key, flags in expected.items():
            mapped = {
                spec.flag
                for spec in gui.TASK_BY_KEY[task_key].fields
                if spec.flag
            }
            self.assertEqual(mapped, flags, task_key)

        expected_output_dirs = {
            ("env_check", "output_dir"): gui._DEFAULT_REPORTS_DIR,
            ("full_scan", "output_dir"): gui._DEFAULT_SNAPSHOTS_DIR,
            ("quick_scan", "output_dir"): gui._DEFAULT_SNAPSHOTS_DIR,
            ("check_format", "report_dir"): gui._DEFAULT_REPORTS_DIR,
            ("check_hash", "report"): gui._DEFAULT_REPORTS_DIR,
            ("diff", "output_dir"): gui._DEFAULT_DIFFS_DIR,
            ("export_report", "output_dir"): gui._DEFAULT_REPORTS_DIR,
            ("storage_collect", "output_dir"): gui._DEFAULT_STORAGE_DIR,
        }
        for (task_key, field_key), expected_dir in (
                expected_output_dirs.items()):
            spec = next(
                field for field in gui.TASK_BY_KEY[task_key].fields
                if field.key == field_key
            )
            self.assertEqual(spec.default, expected_dir)
            self.assertTrue(os.path.isabs(spec.default))
            self.assertEqual(
                os.path.commonpath((gui._BASE, spec.default)), gui._BASE)

    def test_storage_tasks_build_exact_cli_arguments(self):
        summary_spec = next(
            spec for spec in gui.TASK_BY_KEY["storage_collect"].fields
            if spec.key == "summary_txt"
        )
        self.assertEqual(summary_spec.kind, "choice_flag")
        self.assertEqual(summary_spec.default, False)
        self.assertEqual(summary_spec.flag_value, True)
        self.assertEqual(
            summary_spec.choices,
            (
                ("不生成（默认）", False),
                ("生成 ZIP 外部 TXT", True),
            ),
        )

        with tempfile.TemporaryDirectory() as output_dir:
            collect = gui.build_tool_args(
                "storage_collect",
                {
                    "disk_number": "3",
                    "output_dir": output_dir,
                    "summary_txt": True,
                    "smartctl_path": r"C:\Tools\smartctl.exe",
                    "powershell_path": r"C:\Windows\powershell.exe",
                },
            )
        self.assertEqual(collect[0], "storage-collect")
        self.assertEqual(
            collect[collect.index("--disk-number") + 1], "3")
        self.assertIn("--summary-txt", collect)
        self.assertIn("--smartctl-path", collect)
        self.assertIn("--powershell-path", collect)

    def test_export_report_explains_snapshot_and_diff_outputs(self):
        source_type = next(
            spec for spec in gui.TASK_BY_KEY["export_report"].fields
            if spec.key == "source_type")
        self.assertIn("清单与诊断 CSV", source_type.choices[0][0])
        self.assertIn("摘要 Markdown 与变更 CSV", source_type.choices[1][0])
        for filename in (
                "Tree", "Summary", "Diff_summary.md",
                "Diff_details.csv", "Diff_subtrees.csv"):
            self.assertIn(filename, source_type.help)

    def test_storage_collect_requires_valid_disk_pool_numbers(self):
        self.assertIn(
            "请填写“物理硬盘池”。",
            gui.validate_values("storage_collect", {"disk_number": ""}),
        )
        for invalid in ("-1", "3.0", "disk3"):
            self.assertIn(
                "“物理硬盘池”包含无效编号，请重新检测并选择。",
                gui.validate_values(
                    "storage_collect", {"disk_number": invalid}),
            )
        self.assertNotIn(
            "“物理硬盘池”包含无效编号，请重新检测并选择。",
            gui.validate_values(
                "storage_collect", {"disk_number": "0\n3"}),
        )

    def test_storage_inventory_builds_full_pool_and_registrable_choices(self):
        targets = [
            {
                "disk_number": 3,
                "windows": {
                    "disk": {
                        "friendly_name": "Fixture SSD",
                        "size": 4_000_000_000,
                    },
                    "partitions": [{
                        "volume": {
                            "drive_letter": "D:",
                            "file_system_label": "Node",
                        },
                    }],
                },
                "smart_device": {"name": "/dev/sdd", "device_type": "nvme"},
            },
            {
                "disk_number": 4,
                "windows": {"disk": {"friendly_name": "No SMART"}},
                "smart_device": None,
            },
        ]
        choices = gui.storage_target_choices(targets)
        self.assertEqual(len(choices), 1)
        self.assertEqual(choices[0][1], "3")
        self.assertIn("PhysicalDrive3", choices[0][0])
        self.assertIn("D: Node", choices[0][0])
        options = gui.storage_disk_options(targets)
        self.assertEqual([option.disk_number for option in options], [3, 4])
        self.assertTrue(options[0].selectable)
        self.assertFalse(options[1].selectable)
        self.assertEqual(options[1].reason, "smartctl 未关联")

        app = object.__new__(gui.DaisyApp)
        app.storage_disk_choices = ()
        app.storage_disk_options = ()
        app.saved_values = {"storage_collect": {"disk_number": "9"}}
        app._apply_storage_inventory({"targets": targets})
        self.assertEqual(app.storage_disk_choices, choices)
        self.assertEqual(app.storage_disk_options, options)
        self.assertNotIn(
            "disk_number", app.saved_values["storage_collect"])
        disk_spec = next(
            spec for spec in gui.TASK_BY_KEY["storage_collect"].fields
            if spec.key == "disk_number")
        self.assertEqual(disk_spec.kind, "disk_pool")

    def test_storage_pool_splits_each_selected_disk_into_queue_job(self):
        jobs = gui.build_run_jobs(
            "storage_collect", {"disk_number": "3\n1\n3"})
        self.assertEqual(
            [job.label for job in jobs],
            ["PhysicalDrive3", "PhysicalDrive1"],
        )
        self.assertEqual(
            [job.values["disk_number"] for job in jobs], ["3", "1"])
        for job in jobs:
            args = gui.build_tool_args("storage_collect", job.values)
            self.assertEqual(args.count("--disk-number"), 1)

    def test_storage_detection_is_an_internal_step_of_registration(self):
        app = object.__new__(gui.DaisyApp)
        app.process = None
        app.worker_starting = False
        app.run_jobs = []
        app.is_administrator = True
        app.storage_disk_choices = (("PhysicalDrive3", "3"),)
        app.storage_disk_options = ()
        app.saved_values = {
            "storage_collect": {
                "disk_number": "3",
                "output_dir": r"C:\Result",
            },
        }
        calls = []
        app._save_current_values = lambda: calls.append("save")
        app._build_form = lambda: calls.append("build")
        app._begin_run_jobs = (
            lambda task_key, jobs: calls.append((task_key, jobs)))

        app._run_storage_inventory()

        self.assertEqual(app.storage_disk_choices, ())
        self.assertNotIn(
            "disk_number", app.saved_values["storage_collect"])
        self.assertEqual(calls[:2], ["save", "build"])
        task_key, jobs = calls[2]
        self.assertEqual(task_key, "storage_list")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].label, "检测物理硬盘")
        self.assertEqual(jobs[0].values, {})

        snapshot_args = gui.build_tool_args(
            "export_report",
            {"source_type": "snapshot", "source_path": "A.sqlite"},
        )
        diff_args = gui.build_tool_args(
            "export_report",
            {"source_type": "diff", "source_path": "D.sqlite"},
        )
        self.assertIn("--snapshot", snapshot_args)
        self.assertIn("--diff", diff_args)

        quick_relative = gui.build_tool_args(
            "quick_scan",
            {"roots": r"E:\Archive", "output_dir": r"Output\Custom"},
        )
        self.assertEqual(
            quick_relative[quick_relative.index("--output-dir") + 1],
            os.path.join(gui._BASE, "Output", "Custom"),
        )
        export_relative = gui.build_tool_args(
            "export_report",
            {
                "source_type": "snapshot",
                "source_path": "A.sqlite",
                "output_dir": r"Output\Custom",
            },
        )
        self.assertEqual(
            export_relative[export_relative.index("--output-dir") + 1],
            os.path.join(gui._BASE, "Output", "Custom"),
        )

    def test_project_root_public_files_are_controlled(self):
        with os.scandir(gui._BASE) as entries:
            root_files = {
                entry.name for entry in entries
                if entry.is_file(follow_symlinks=False)
            }
        self.assertEqual(
            root_files,
            {
                ".gitattributes",
                ".gitignore",
                "LICENSE",
                "README.md",
                "Start_DAISY_GUI.pyw",
            },
        )
        self.assertEqual(
            gui._MAIN,
            os.path.join(gui._SCRIPT_DIR, "Script_DAISY_MAIN.py"),
        )
        self.assertEqual(
            gui._LIB_DIR,
            os.path.join(gui._SCRIPT_DIR, "Lib"),
        )
        self.assertEqual(
            gui._TEST_DIR,
            os.path.join(gui._SCRIPT_DIR, "Test"),
        )
        self.assertTrue(os.path.isfile(gui._MAIN))
        self.assertFalse(os.path.isdir(os.path.join(gui._BASE, "Tests")))
        self.assertEqual(gui.project_self_test_missing_files(), [])
        self.assertEqual(
            gui.project_self_test_command("python"),
            [
                "python", "-B", "-m", "unittest", "discover",
                "-s", gui._TEST_DIR,
                "-p", "Script_DAISY_Test_*.py",
                "-v",
            ],
        )
        self.assertIn(
            r".\Script\Test",
            gui.project_self_test_preview(),
        )
        self.assertTrue(os.path.isfile(
            os.path.join(gui._BASE, "README.md")))
        with open(os.path.join(gui._BASE, "LICENSE"),
                  "r", encoding="utf-8") as f:
            license_text = f.read()
        self.assertTrue(license_text.startswith("MIT License\n"))
        self.assertIn("Copyright (c) 2026 Suzuran Ye", license_text)
        preview = gui.preview_command(
            "quick_scan", {"roots": r"E:\Archive"})
        self.assertIn(r".\Script\Script_DAISY_MAIN.py", preview)

    def test_cold_start_installer_only_installs_python(self):
        installer_path = os.path.join(
            gui._SCRIPT_DIR, "Script_DAISY_Install_Python.ps1")
        with open(installer_path, "r", encoding="utf-8") as f:
            installer = f.read()
        self.assertIn('"Python.Python.3.14"', installer)
        for package_id in (
                "OliverBetz.ExifTool", "Gyan.FFmpeg", "7zip.7zip"):
            self.assertNotIn(package_id, installer)
        prompt_index = installer.index(
            '$answer = Read-Host "Install or update Python 3.14? [y/N]"')
        install_index = installer.index("& $winget.Source install")
        self.assertLess(prompt_index, install_index)
        self.assertIn('if ($answer -notmatch "^[Yy]$")', installer)
        self.assertIn("--disable-interactivity", installer)
        self.assertFalse(os.path.exists(os.path.join(
            gui._BASE, "Install_DAISY_Dependencies.ps1")))
        with open(os.path.join(gui._BASE, "README.md"),
                  "r", encoding="utf-8") as f:
            readme = f.read()
        self.assertIn(
            r".\Script\Script_DAISY_Install_Python.ps1", readme)
        self.assertNotIn(r".\Install_DAISY_Dependencies.ps1", readme)

    def test_gui_dependency_commands_are_fixed_and_allowlisted(self):
        winget = r"C:\WindowsApps\winget.exe"
        expected = {
            "exiftool": "OliverBetz.ExifTool",
            "ffprobe": "Gyan.FFmpeg",
            "sevenzip": "7zip.7zip",
            "smartctl": "smartmontools.smartmontools",
        }
        for name, package_id in expected.items():
            command = gui.dependency_install_command(name, winget)
            self.assertEqual(command[0], winget)
            self.assertEqual(
                command[command.index("--id") + 1], package_id)
            for required in (
                    "--exact", "--source", "--accept-source-agreements",
                    "--accept-package-agreements", "--disable-interactivity"):
                self.assertIn(required, command)
        with self.assertRaises(ValueError):
            gui.dependency_install_command("arbitrary")

    def test_gui_dependency_install_targets_only_selected_allowlisted_tool(
            self):
        app = object.__new__(gui.DaisyApp)
        app.process = None
        app.run_jobs = []
        app.worker_starting = False
        app.root = object()
        started = []
        app._begin_run_jobs = lambda key, jobs: started.append((key, jobs))

        with patch.object(
                gui, "discover_winget",
                return_value=r"C:\WindowsApps\winget.exe"), \
                patch.object(
                    gui.messagebox, "askyesno", return_value=True):
            app._install_tool("ffprobe")

        self.assertEqual(started[0][0], gui._DEPENDENCY_INSTALL_KEY)
        jobs = started[0][1]
        self.assertEqual(
            [job.values["tool_name"] for job in jobs],
            ["ffprobe"],
        )
        self.assertTrue(all(
            job.values["winget_path"] == r"C:\WindowsApps\winget.exe"
            for job in jobs))

        started.clear()
        with patch.object(
                gui, "discover_winget",
                return_value=r"C:\WindowsApps\winget.exe"), \
                patch.object(
                    gui.messagebox, "askyesno", return_value=False):
            app._install_tool("exiftool")
        self.assertEqual(started, [])

    def test_environment_install_buttons_are_independent(self):
        class ButtonProbe:
            def __init__(self):
                self.options = {}

            def configure(self, **options):
                self.options.update(options)

        class SwitchProbe:
            def __init__(self):
                self.options = {}

            def set_mode(self, **options):
                self.options.update(options)

        class RootProbe:
            @staticmethod
            def after_idle(_callback):
                pass

        app = object.__new__(gui.DaisyApp)
        app.root = RootProbe()
        app.process = None
        app.worker_starting = False
        app.run_jobs = []
        app.install_tool_buttons = {
            name: ButtonProbe()
            for name in gui._INSTALLABLE_TOOL_PACKAGES
        }
        app.admin_mode_switch = SwitchProbe()
        app.is_administrator = False

        app._refresh_environment_actions()

        for name in gui._INSTALLABLE_TOOL_PACKAGES:
            button = app.install_tool_buttons[name]
            self.assertEqual(button.options["state"], "normal")
            self.assertEqual(
                button.options["text"],
                f"安装 {gui._TOOL_DISPLAY_NAMES[name]}",
            )
        self.assertEqual(
            app.admin_mode_switch.options["enabled"],
            os.name == "nt",
        )
        self.assertEqual(
            app.admin_mode_switch.options["value"], False)

        app.process = object()
        app._refresh_environment_actions()
        self.assertTrue(all(
            button.options["state"] == "disabled"
            for button in app.install_tool_buttons.values()
        ))
        self.assertEqual(
            app.admin_mode_switch.options["enabled"], False)

        app.process = None
        app.is_administrator = True
        app._refresh_environment_actions()
        self.assertEqual(app.admin_mode_switch.options["value"], True)
        self.assertEqual(app.admin_mode_switch.options["enabled"], False)

    def test_environment_summary_displays_local_versions(self):
        cache = {
            "exiftool": {
                "path": r"C:\Tools\exiftool.exe",
                "version": "13.59",
                "verified": True,
            },
            "powershell": {
                "path": r"C:\Windows\powershell.exe",
                "version": "5.1.26100.1",
                "verified": True,
            },
        }
        summary = gui.session_tool_cache_summary(
            "env_check", cache, path_exists=lambda _path: True)
        self.assertEqual(
            summary,
            "本机版本：ExifTool 13.59、PowerShell 5.1.26100.1",
        )

    def test_window_size_adapts_to_screen(self):
        self.assertEqual(
            gui.window_size_for_screen(2048, 1280), (1920, 1080))
        self.assertEqual(
            gui.window_size_for_screen(1920, 1080), (1840, 1020))
        self.assertEqual(
            gui.window_size_for_screen(1366, 768), (1286, 708))
        self.assertEqual(
            gui.window_size_for_screen(1024, 768), (944, 708))
        self.assertEqual(
            gui.window_size_for_screen(1280, 720), (1200, 660))
        small = gui.window_size_for_screen(800, 600)
        self.assertLessEqual(small[0], 800)
        self.assertLessEqual(small[1], 600)

    def test_window_geometry_fits_positive_and_negative_monitor_work_areas(self):
        secondary = gui.MonitorWorkArea(
            2, 1920, 0, 3200, 680, dpi=144)
        self.assertEqual(
            gui.fit_window_to_work_area(
                (1280, 720), (2250, 40), secondary),
            (1248, 640, 1936, 20),
        )
        left_monitor = gui.MonitorWorkArea(
            3, -1280, 0, 0, 1024, dpi=96)
        width, height, x, y = gui.fit_window_to_work_area(
            (1000, 700), (-1500, 900), left_monitor)
        self.assertEqual((width, height), (1000, 700))
        self.assertGreaterEqual(x, left_monitor.left)
        self.assertLessEqual(x + width, left_monitor.right)
        self.assertGreaterEqual(y, left_monitor.top)
        self.assertLessEqual(y + height, left_monitor.bottom)

    def test_monitor_change_refits_normal_window_to_target_work_area(self):
        class RootProbe:
            def __init__(self):
                self.width = 1280
                self.height = 720
                self.x = 2250
                self.y = 40
                self.geometries = []

            def state(self):
                return "normal"

            def winfo_width(self):
                return self.width

            def winfo_height(self):
                return self.height

            def winfo_x(self):
                return self.x

            def winfo_y(self):
                return self.y

            def geometry(self, value):
                self.geometries.append(value)

        app = object.__new__(gui.DaisyApp)
        app.root = RootProbe()
        app.mini_mode = False
        app._monitor_refresh_after_id = "pending"
        app._monitor_signature = (1, 0, 0, 1920, 1040, 96)
        app._monitor_applied_size = None
        app._preferred_normal_size = (1280, 720)
        app.normal_width_cap = 1200
        app.normal_min_size = (760, 640)
        sync_calls = []
        app._sync_task_toolbar_minimum_width = lambda: sync_calls.append(True)
        target = gui.MonitorWorkArea(
            2, 1920, 0, 3200, 680, dpi=144)

        with patch.object(
                gui, "_monitor_work_area_for_window", return_value=target):
            app._refresh_monitor_layout()

        self.assertIsNone(app._monitor_refresh_after_id)
        self.assertEqual(app._monitor_signature, target.signature)
        self.assertEqual(app._monitor_applied_size, (1248, 640))
        self.assertEqual(app.normal_min_size, (760, 640))
        self.assertEqual(app.normal_width_cap, 1200)
        self.assertEqual(sync_calls, [True])
        self.assertEqual(app.root.geometries, ["1248x640+1936+20"])

    def test_normal_minimum_width_follows_full_function_modules(self):
        class RootProbe:
            def __init__(self):
                self.minimum_sizes = []
                self.idle_updates = 0

            def update_idletasks(self):
                self.idle_updates += 1

            def minsize(self, width, height):
                self.minimum_sizes.append((width, height))

        class ToolbarProbe:
            def __init__(self, width):
                self.width = width

            def winfo_reqwidth(self):
                return self.width

        app = object.__new__(gui.DaisyApp)
        app.root = RootProbe()
        app.task_toolbar_panel = ToolbarProbe(936)
        app.normal_min_size = (760, 680)
        app.command_preview_expanded = False
        app.mini_mode = False
        app._sync_task_toolbar_minimum_width()
        self.assertEqual(app.normal_min_size, (936, 680))
        self.assertEqual(app.root.minimum_sizes[-1], (936, 680))

        app.task_toolbar_panel.width = 820
        app._sync_task_toolbar_minimum_width()
        self.assertEqual(app.normal_min_size, (936, 680))
        self.assertEqual(app.root.minimum_sizes[-1], (936, 680))

        app.task_toolbar_panel.width = 1400
        app.normal_width_cap = 1200
        app._sync_task_toolbar_minimum_width()
        self.assertEqual(app.normal_min_size, (1200, 680))
        self.assertEqual(app.root.minimum_sizes[-1], (1200, 680))

    def test_utility_buttons_wrap_without_reordering(self):
        widths = (118, 118, 118, 181)
        rows = gui.action_button_row_indexes(widths, 380)
        self.assertEqual(
            tuple(index for row in rows for index in row),
            tuple(range(len(widths))),
        )
        for row in rows:
            occupied = sum(widths[index] for index in row)
            occupied += 8 * max(0, len(row) - 1)
            self.assertLessEqual(occupied, 380)
        self.assertGreater(len(gui.action_button_row_indexes(widths, 300)), 1)

    def test_log_header_button_is_not_reparented_into_utility_grid(self):
        class ButtonProbe:
            def __init__(self, width=110):
                self.width = width
                self.grid_calls = []
                self.forgotten = 0

            def winfo_reqwidth(self):
                return self.width

            def grid_forget(self):
                self.forgotten += 1

            def grid(self, **options):
                self.grid_calls.append(options)

        class AreaProbe:
            @staticmethod
            def winfo_width():
                return 360

        app = object.__new__(gui.DaisyApp)
        output = ButtonProbe()
        cache = ButtonProbe()
        stop = ButtonProbe()
        run = ButtonProbe()
        clear_log = ButtonProbe()
        app.utility_buttons = (output, cache)
        app.execution_buttons = (stop, run)
        app.open_output_button = output
        app.clear_cache_button = cache
        app.clear_log_button = clear_log
        app.stop_button = stop
        app.run_button = run
        app.utility_action_area = AreaProbe()
        app.content = AreaProbe()

        app._layout_action_buttons()

        self.assertTrue(output.grid_calls)
        self.assertTrue(cache.grid_calls)
        self.assertTrue(stop.grid_calls)
        self.assertTrue(run.grid_calls)
        self.assertEqual(clear_log.grid_calls, [])
        self.assertEqual(clear_log.forgotten, 0)

    def test_full_hash_independent_sample_is_explained_and_top_menu(self):
        fields = {
            spec.key: spec for spec in gui.TASK_BY_KEY["full_scan"].fields
        }
        verify = fields["verify_percent"]
        self.assertTrue(verify.top_menu)
        self.assertEqual(verify.section, "哈希比例")
        for phrase in (
                "PowerShell Get-FileHash", "至少 100 个",
                "不是主哈希的覆盖比例"):
            self.assertIn(phrase, verify.help)
        args = gui.build_tool_args(
            "full_scan", {"roots": r"E:\Archive"})
        index = args.index("--verify-sample-percent")
        self.assertEqual(args[index + 1], "1.0")

    def test_all_hash_sampling_percentages_live_in_advanced_menu(self):
        sampling_fields = {
            (task.key, spec.key): spec
            for task in gui.TASKS
            for spec in task.fields
            if "哈希" in spec.label and (
                "抽样" in spec.label or "抽验" in spec.label)
        }
        self.assertEqual(
            set(sampling_fields),
            {("full_scan", "verify_percent"),
             ("check_hash", "sample_percent")},
        )
        self.assertTrue(all(
            spec.top_menu and spec.section == "哈希比例"
            for spec in sampling_fields.values()))

    def test_page_parameters_use_dropdowns_without_expand_or_check_controls(self):
        self.assertFalse(any(
            spec.kind in ("bool", "inverse_bool")
            for task in gui.TASKS for spec in task.fields))
        for task_key in ("check_format", "check_hash", "diff"):
            force = next(
                spec for spec in gui.TASK_BY_KEY[task_key].fields
                if spec.key == "force")
            self.assertEqual(force.kind, "choice_flag")
            self.assertEqual(force.default, False)
            self.assertEqual(force.flag_value, True)
            self.assertEqual(
                force.choices,
                (("不启用（默认）", False), ("启用", True)),
            )

    def test_hash_percentage_menu_edits_and_resets_original_fields(self):
        app = object.__new__(gui.DaisyApp)
        app.root = object()
        app.process = None
        app.worker_starting = False
        app.run_jobs = []
        app.task = gui.TASK_BY_KEY["env_check"]
        app.values = {}
        app.saved_values = {}
        app._refresh_hash_percentage_menu_labels = Mock()
        app._update_preview = Mock()
        app._set_status = Mock()

        self.assertEqual(
            app._hash_percentage_menu_label(
                "full_scan", "verify_percent"),
            "DBS-11 独立哈希抽验：1.0%",
        )
        with patch.object(
                gui.simpledialog, "askstring", return_value="2.5"):
            app._edit_hash_percentage("full_scan", "verify_percent")
        self.assertEqual(
            app.saved_values["full_scan"]["verify_percent"], "2.5")
        args = gui.build_tool_args(
            "full_scan", {"roots": r"E:\Archive", **app.saved_values["full_scan"]})
        index = args.index("--verify-sample-percent")
        self.assertEqual(args[index + 1], "2.5")

        with (
            patch.object(gui.simpledialog, "askstring", return_value="0"),
            patch.object(gui.messagebox, "showerror") as shown,
        ):
            app._edit_hash_percentage("check_hash", "sample_percent")
        shown.assert_called_once()
        self.assertNotIn("check_hash", app.saved_values)

        app._reset_hash_percentages()
        self.assertNotIn("verify_percent", app.saved_values["full_scan"])
        self.assertEqual(
            app._hash_percentage_menu_label(
                "full_scan", "verify_percent"),
            "DBS-11 独立哈希抽验：1.0%",
        )

    def test_tool_paths_live_in_top_menu_not_page_dropdowns(self):
        tool_fields = {
            spec.key: spec
            for task in gui.TASKS
            for spec in task.fields
            if spec.key in gui._TOOL_FIELD_BY_NAME.values()
        }
        self.assertEqual(
            set(tool_fields), set(gui._TOOL_FIELD_BY_NAME.values()))
        self.assertTrue(all(
            spec.top_menu and spec.section == "工具路径"
            for spec in tool_fields.values()
        ))

    def test_form_mousewheel_scrolls_and_stops_widget_defaults(self):
        class CanvasProbe:
            def __init__(self):
                self.calls = []

            def yview_scroll(self, units, mode):
                self.calls.append((units, mode))

        class Event:
            def __init__(self, *, delta=0, num=0):
                self.delta = delta
                self.num = num

        app = object.__new__(gui.DaisyApp)
        app.form_canvas = CanvasProbe()
        self.assertEqual(
            app._scroll_form(Event(delta=-60)), "break")
        self.assertEqual(app.form_canvas.calls, [(1, "units")])
        self.assertEqual(
            app._scroll_form(Event(num=4)), "break")
        self.assertEqual(app.form_canvas.calls[-1], (-1, "units"))

        class FittingCanvasProbe(CanvasProbe):
            def __init__(self):
                super().__init__()
                self.positions = []

            @staticmethod
            def bbox(_item):
                return (0, 0, 900, 320)

            @staticmethod
            def winfo_height():
                return 540

            def yview_moveto(self, fraction):
                self.positions.append(fraction)

        app.form_canvas = FittingCanvasProbe()
        self.assertEqual(app._scroll_form(Event(delta=120)), "break")
        self.assertEqual(app.form_canvas.calls, [])
        self.assertEqual(app.form_canvas.positions, [0.0])

    def test_technical_spec_declares_powershell_compatibility(self):
        spec_path = os.path.join(
            gui._BASE, "Spec", "Spec_DAISY_Technical.md")
        with open(spec_path, "r", encoding="utf-8") as f:
            spec = f.read()
        self.assertIn("Windows PowerShell 5.1", spec)
        self.assertIn("PowerShell 7.x", spec)
        self.assertIn("元数据 profile v7", spec)
        self.assertIn("视频、音频和 GIF", spec)
        self.assertIn("schema_version=3", spec)
        self.assertIn("archive_schema_version=3", spec)
        self.assertIn("不创建、读取或修改\n数据库", spec)
        self.assertIn("### 11.8 创建后自动核验准入", spec)
        self.assertIn("不读取旧结构", spec)
        self.assertIn("不提供按 mtime 静默跳过", spec)
        evolution_path = os.path.join(
            gui._BASE, "Spec", "Spec_DAISY_Version_Evolution.md")
        with open(evolution_path, "r", encoding="utf-8") as f:
            evolution = f.read()
        self.assertIn("Kit_AL v1.0.2", evolution)
        self.assertIn("DAISY v1.4.2", evolution)
        self.assertIn("DAISY v1.5.0", evolution)
        self.assertIn("DAISY v1.5.1", evolution)
        self.assertIn("STG-11 硬盘信息登记", evolution)
        retired_storage_spec = os.path.join(
            gui._BASE, "Spec", "Spec_DAISY_" + "Storage.md")
        self.assertFalse(os.path.exists(retired_storage_spec))
        lib_dir = os.path.join(gui._BASE, "Script", "Lib")
        self.assertEqual(
            {
                "Script_DAISY_Lib_DBS_01_Core.py",
                "Script_DAISY_Lib_DBS_02_Meta.py",
                "Script_DAISY_Lib_DBS_03_Hash.py",
                "Script_DAISY_Lib_DBS_04_Diff.py",
                "Script_DAISY_Lib_DBS_05_Reader.py",
                "Script_DAISY_Lib_DBS_06_Verify.py",
                "Script_DAISY_Lib_DBS_07_Parse.py",
                "Script_DAISY_Lib_DBS_08_State.py",
                "Script_DAISY_Lib_DBS_09_Run.py",
                "Script_DAISY_Lib_DBS_10_Issues.py",
                "Script_DAISY_Lib_STG_01_Core.py",
                "Script_DAISY_Lib_STG_02_Windows.py",
                "Script_DAISY_Lib_STG_03_Smartctl.py",
                "Script_DAISY_Lib_STG_04_Service.py",
                "Script_DAISY_Lib_STG_05_Archive.py",
            },
            {name for name in os.listdir(lib_dir) if name.endswith(".py")},
        )
        self.assertIn("独立队列总进度", evolution)
        self.assertIn("小窗视图", evolution)
        self.assertIn("设计过渡点", evolution)
        for dated_version in (
                "## 2026-07-21～22 — Kit_AL v1.0.3",
                "## 2026-07-26 — Kit_AL v1.1.0",
                "## 2026-07-29 — DAISY v1.3.1",
                "## 2026-07-29 形成、2026-07-30 公开 — DAISY v1.3.2"):
            self.assertIn(dated_version, evolution)
        for change_kind in ("新增：", "修复：", "删除："):
            self.assertIn(change_kind, evolution)

    def test_task_accent_colours_are_unified_green(self):
        env = (gui._GREEN_DARK, gui._GREEN_DEEP, gui._GREEN)
        database = (gui._GREEN_DARK, gui._GREEN_DEEP, gui._GREEN)
        self.assertEqual(gui.task_accent_colours("env_check"), env)
        for task_key in gui._TASK_MENU_SECTIONS[1][1]:
            self.assertEqual(gui.task_accent_colours(task_key), database)
        self.assertEqual(
            gui._TASK_MENU_SECTION_COLOURS["硬盘"][1:],
            (gui._RED, gui._RED_DEEP, gui._RED_SOFT),
        )

    def test_run_button_label_is_unified(self):
        self.assertEqual(gui._RUN_BUTTON_TEXT, "开始任务")

    def test_selected_top_task_style_uses_unified_deep_green(self):
        class StyleProbe:
            def __init__(self):
                self.configurations = {}
                self.mappings = {}
                self.layouts = {}

            @staticmethod
            def theme_names():
                return ("clam",)

            @staticmethod
            def theme_use(_name):
                pass

            def configure(self, name, **options):
                self.configurations[name] = options

            def map(self, name, **options):
                self.mappings[name] = options

            def layout(self, name, layout):
                self.layouts[name] = layout

            def element_create(self, name, *definition, **options):
                self.layouts[name] = (definition, options)

        style = StyleProbe()
        app = object.__new__(gui.DaisyApp)
        app.root = object()
        app.task = gui.TASKS[0]
        with (
            patch.object(gui.ttk, "Style", return_value=style),
            patch.object(
                gui, "_create_combobox_chevron", return_value=object()),
        ):
            app._configure_styles()

        for _section, (prefix, _accent, deep, soft) in (
                gui._TASK_MENU_SECTION_COLOURS.items()):
            name = f"{prefix}.TopTaskSelected.TButton"
            configured = style.configurations[name]
            self.assertEqual(
                configured["background"],
                gui._UNIFIED_ACTION_BACKGROUND,
            )
            self.assertEqual(
                configured["foreground"],
                gui._UNIFIED_ACTION_FOREGROUND,
            )
            self.assertEqual(configured["bordercolor"], gui._BORDER)
            self.assertEqual(configured["focusthickness"], 0)
            self.assertEqual(configured["font"], ("Microsoft YaHei UI", 9))
            self.assertTrue(all(
                colour == gui._UNIFIED_ACTION_BACKGROUND
                for _states, colour
                in style.mappings[name]["background"]
            ))
            self.assertNotIn("focus", str(style.layouts[name]).lower())
        normal = style.configurations["Env.TopTask.TButton"]
        self.assertEqual(normal["foreground"], gui._GREEN_DEEP)
        self.assertEqual(
            style.mappings["Env.TopTask.TButton"]["foreground"],
            [("disabled", gui._GREEN_DEEP)],
        )
        primary = style.configurations["Primary.TButton"]
        self.assertEqual(
            primary["background"], gui._UNIFIED_ACTION_BACKGROUND)
        self.assertEqual(
            primary["foreground"], gui._UNIFIED_ACTION_FOREGROUND)
        for name in ("Stop.TButton", "MiniStop.TButton"):
            stop = style.configurations[name]
            self.assertEqual(stop["background"], gui._AMBER_SOFT)
            self.assertEqual(stop["foreground"], gui._AMBER_DEEP)
            self.assertEqual(stop["borderwidth"], 0)
            self.assertEqual(stop["bordercolor"], gui._AMBER_SOFT)
            self.assertEqual(stop["relief"], "flat")
            self.assertEqual(
                style.mappings[name]["background"],
                [("active", gui._AMBER)],
            )

    def test_toolbar_selection_clears_button_focus(self):
        class RootProbe:
            def __init__(self):
                self.focus_calls = 0

            def focus_set(self):
                self.focus_calls += 1

        app = object.__new__(gui.DaisyApp)
        app.root = RootProbe()
        app._select_task = Mock()
        app._select_task_from_toolbar("diff")
        app._select_task.assert_called_once_with("diff")
        self.assertEqual(app.root.focus_calls, 1)

    def test_top_task_menus_use_theme_grouping(self):
        self.assertEqual(
            [section[0] for section in gui._TASK_MENU_SECTIONS],
            ["环境", "数据", "硬盘"],
        )
        self.assertNotIn("full_scan", gui._TASK_MENU_SECTIONS[0][1])
        self.assertNotIn("quick_scan", gui._TASK_MENU_SECTIONS[0][1])
        self.assertEqual(gui._TASK_MENU_SECTIONS[0][1], ("env_check",))
        self.assertEqual(gui._TASK_MENU_SECTIONS[1][1][:3],
                         ("full_scan", "quick_scan", "diff"))
        self.assertEqual(gui._TASK_MENU_SECTIONS[1][1][-1], "export_report")
        self.assertNotIn(
            gui._PROJECT_SELF_TEST_KEY, gui._TASK_MENU_ORDER)
        self.assertEqual(
            gui._TASK_MENU_SECTIONS[-1][1],
            ("storage_collect",),
        )
        self.assertNotIn("storage_list", gui._TASK_MENU_ORDER)
        self.assertNotIn("storage_" + "verify", gui.TASK_BY_KEY)
        self.assertEqual(
            tuple(
                task_key
                for _label, task_keys in gui._TASK_MENU_SECTIONS
                for task_key in task_keys
            ),
            gui._TASK_MENU_ORDER,
        )
        analysis_index = gui._TASK_MENU_ORDER.index("diff")
        self.assertEqual(
            gui._TASK_MENU_ORDER[analysis_index:analysis_index + 3],
            ("diff", "check_hash", "check_format"),
        )

    def test_standard_menu_groups_panel_and_advanced_entries(self):
        class VariableProbe:
            def __init__(self, value=None, **_options):
                self.value = value

            def get(self):
                return self.value

            def set(self, value):
                self.value = value

        class MenuProbe:
            def __init__(self, parent, **_options):
                self.parent = parent
                self.options = _options
                self.entries = []

            def _add(self, kind, options):
                self.entries.append({"kind": kind, **options})

            def add_command(self, **options):
                self._add("command", options)

            def add_cascade(self, **options):
                self._add("cascade", options)

            def add_checkbutton(self, **options):
                self._add("checkbutton", options)

            def add_radiobutton(self, **options):
                self._add("radiobutton", options)

            def add_separator(self):
                self._add("separator", {})

            def index(self, specifier):
                self.assert_end(specifier)
                return len(self.entries) - 1 if self.entries else None

            @staticmethod
            def assert_end(specifier):
                if specifier != "end":
                    raise AssertionError(specifier)

        class RootProbe:
            def configure(self, **options):
                self.options = options

        app = object.__new__(gui.DaisyApp)
        app.root = RootProbe()
        app.task = gui.TASKS[0]
        app.task_menu_entries = {}
        app.default_window_size = (1920, 1080)
        app.ui_font_family = "Microsoft YaHei UI"
        app.ui_font_size_delta = 0
        app.confirm_close_when_idle = True
        app._available_ui_font_families = lambda: (
            "Microsoft YaHei UI", "Segoe UI")
        with (
            patch.object(gui.tk, "Menu", MenuProbe),
            patch.object(gui.tk, "StringVar", VariableProbe),
            patch.object(gui.tk, "BooleanVar", VariableProbe),
            patch.object(gui.tk, "IntVar", VariableProbe),
        ):
            app._build_menu()
        top_labels = [
            entry["label"] for entry in app.app_menu.entries
            if entry["kind"] == "cascade"
        ]
        self.assertEqual(
            top_labels,
            [
                "文件", "面板", "高级", "设置", "视图", "帮助",
            ],
        )
        file_menu = app.app_menu.entries[0]["menu"]
        self.assertEqual(
            [entry["label"] for entry in file_menu.entries
             if entry["kind"] == "command"],
            ["打开项目目录", "打开结果目录", "退出 DAISY"],
        )
        self.assertEqual(
            [entry["label"] for entry in app.panel_menu.entries
             if entry["kind"] == "cascade"],
            ["环境", "数据", "硬盘"],
        )
        self.assertEqual(
            [entry["label"] for entry in app.advanced_menu.entries
             if entry["kind"] == "cascade"],
            ["工具路径", "哈希比例", "扫描行为"],
        )
        self.assertEqual(
            [entry["label"] for entry in app.advanced_menu.entries
             if entry["kind"] == "checkbutton"],
            [],
        )
        self.assertEqual(
            [entry["label"] for entry in app.advanced_menu.entries
             if entry["kind"] == "command"],
            ["显示命令预览", "DAISY功能自检"],
        )
        self.assertEqual(app.advanced_locked_menu_entries, [0, 1, 2, 6])
        self.assertEqual(
            app.app_menu.options["background"], gui._MENU_BACKGROUND)
        self.assertEqual(len("工具路径"), 4)
        self.assertEqual(
            [
                entry["label"]
                for entry in app.tool_path_menu.entries
                if entry["kind"] == "command"
            ],
            [
                f"{gui._TOOL_DISPLAY_NAMES[name]} 路径：自动发现"
                for name in gui._TOOL_PATH_MENU_ORDER
            ] + ["全部恢复自动发现"],
        )
        self.assertEqual(
            [entry["label"] for entry in app.hash_percentage_menu.entries
             if entry["kind"] == "command"],
            [
                "DBS-11 独立哈希抽验：1.0%",
                "DBS-31 内容哈希抽样：1.0%",
                "全部恢复默认比例",
            ],
        )
        self.assertEqual(
            [entry["label"] for entry in app.settings_menu.entries
             if entry["kind"] == "cascade"],
            ["默认窗口大小", "界面字体"],
        )
        self.assertEqual(
            [entry["label"] for entry in app.settings_menu.entries
             if entry["kind"] == "checkbutton"],
            ["空闲关闭时需要确认"],
        )
        self.assertEqual(
            [
                entry["label"]
                for entry in app.view_menu.entries
                if entry["kind"] == "command"
            ],
            [
                "折叠功能模块", "折叠任务设置",
                "展开运行进度", "展开运行日志", "进入小窗模式",
            ],
        )
        self.assertEqual(
            [
                entry["label"]
                for entry in app.view_menu.entries
                if entry["kind"] == "checkbutton"
            ],
            [],
        )
        help_menu = app.app_menu.entries[-1]["menu"]
        self.assertEqual(
            [entry["label"] for entry in help_menu.entries
             if entry["kind"] == "command"],
            ["联系作者", "关于 DAISY", "打开 GitHub 主页"],
        )
        self.assertEqual(
            [
                entry["label"]
                for entry in app.task_menus["环境"].entries
                if entry["kind"] == "radiobutton"
            ],
            [gui._TASK_TOOLBAR_LABELS[key]
             for key in gui._TASK_MENU_SECTIONS[0][1]],
        )
        self.assertEqual(
            sum(entry["kind"] == "separator"
                for entry in app.task_menus["数据"].entries),
            3,
        )
        self.assertEqual(
            app.task_menus["数据"].options["activebackground"],
            gui._UNIFIED_ACTION_BACKGROUND,
        )
        self.assertEqual(
            [
                entry["label"]
                for entry in app.task_menus["硬盘"].entries
                if entry["kind"] == "radiobutton"
            ],
            [gui._TASK_TOOLBAR_LABELS[key]
             for key in gui._TASK_MENU_SECTIONS[2][1]],
        )
        self.assertEqual(
            sum(entry["kind"] == "separator"
                for entry in app.task_menus["硬盘"].entries),
            0,
        )

    def test_view_menu_uses_expand_collapse_actions_except_preview(self):
        class MenuProbe:
            def __init__(self):
                self.labels = {}

            def entryconfigure(self, index, **options):
                self.labels[index] = options["label"]

        app = object.__new__(gui.DaisyApp)
        app.view_menu = MenuProbe()
        app.view_panel_menu_entries = {
            "task_toolbar": 0,
            "settings": 2,
            "progress": 3,
            "log": 4,
        }
        app.view_mini_mode_menu_index = 6
        app.task_toolbar_expanded = True
        app.settings_expanded = False
        app.progress_expanded = True
        app.log_expanded = False
        app.mini_mode = False
        app._refresh_view_menu_labels()
        self.assertEqual(
            app.view_menu.labels,
            {
                0: "折叠功能模块",
                2: "展开任务设置",
                3: "折叠运行进度",
                4: "展开运行日志",
                6: "进入小窗模式",
            },
        )
        app.mini_mode = True
        app._refresh_view_menu_labels()
        self.assertEqual(app.view_menu.labels[6], "返回完整界面")

    def test_idle_close_requires_confirmation(self):
        class RootProbe:
            def __init__(self):
                self.destroy_calls = 0

            def destroy(self):
                self.destroy_calls += 1

        app = object.__new__(gui.DaisyApp)
        app.root = RootProbe()
        app.process = None
        app.run_jobs = []
        app.worker_starting = False
        app.confirm_close_when_idle = True
        app._save_gui_preferences = Mock()

        with patch.object(
                gui.messagebox, "askyesno", side_effect=(False, True)) \
                as confirm:
            app._on_close()
            self.assertEqual(app.root.destroy_calls, 0)
            app._on_close()

        self.assertEqual(app.root.destroy_calls, 1)
        app._save_gui_preferences.assert_called_once_with()
        self.assertEqual(confirm.call_count, 2)
        self.assertTrue(all(
            call.args[0] == "确认退出" for call in confirm.call_args_list
        ))

    def test_idle_close_confirmation_can_be_disabled(self):
        class RootProbe:
            def __init__(self):
                self.destroy_calls = 0

            def destroy(self):
                self.destroy_calls += 1

        app = object.__new__(gui.DaisyApp)
        app.root = RootProbe()
        app.process = None
        app.run_jobs = []
        app.worker_starting = False
        app.confirm_close_when_idle = False
        app._save_gui_preferences = Mock()
        with patch.object(gui.messagebox, "askyesno") as confirm:
            app._on_close()
        confirm.assert_not_called()
        self.assertEqual(app.root.destroy_calls, 1)
        app._save_gui_preferences.assert_called_once_with()

    def test_administrator_restart_uses_current_python_and_canonical_gui(self):
        executable = os.path.abspath(r"C:\Python\pythonw.exe")
        program, parameters, directory = gui.administrator_restart_parts(
            executable=executable,
            argv=["ignored-launcher.pyw", "--fixture", "two words"],
            frozen=False,
        )
        self.assertEqual(program, executable)
        self.assertEqual(directory, gui._BASE)
        self.assertEqual(
            parameters,
            subprocess.list2cmdline([
                os.path.abspath(gui.__file__), "--fixture", "two words",
            ]),
        )
        frozen_program, frozen_parameters, _directory = (
            gui.administrator_restart_parts(
                executable=executable,
                argv=["DAISY.exe", "--fixture"],
                frozen=True,
            )
        )
        self.assertEqual(frozen_program, executable)
        self.assertEqual(
            frozen_parameters, subprocess.list2cmdline(["--fixture"]))

    def test_administrator_restart_closes_only_after_elevated_launch(self):
        class RootProbe:
            def __init__(self):
                self.destroy_calls = 0

            def destroy(self):
                self.destroy_calls += 1

            @staticmethod
            def after_idle(_callback):
                pass

        class SwitchProbe:
            def __init__(self):
                self.options = {}

            def set_mode(self, **options):
                self.options.update(options)

        app = object.__new__(gui.DaisyApp)
        app.root = RootProbe()
        app.process = None
        app.worker_starting = False
        app.run_jobs = []
        app.is_administrator = False
        app.install_tool_buttons = {}
        app.admin_mode_switch = SwitchProbe()
        app._save_gui_preferences = Mock()
        with (
            patch.object(gui.os, "name", "nt"),
            patch.object(gui.messagebox, "askyesno", return_value=True),
            patch.object(gui, "restart_as_windows_administrator") as restart,
        ):
            app._request_admin_mode(True)
        restart.assert_called_once_with()
        app._save_gui_preferences.assert_called_once_with()
        self.assertEqual(app.root.destroy_calls, 1)

        app.root.destroy_calls = 0
        app._save_gui_preferences.reset_mock()
        with (
            patch.object(gui.os, "name", "nt"),
            patch.object(gui.messagebox, "askyesno", return_value=True),
            patch.object(
                gui, "restart_as_windows_administrator",
                side_effect=OSError("UAC cancelled"),
            ),
            patch.object(gui.messagebox, "showerror") as shown,
        ):
            app._request_admin_mode(True)
        self.assertEqual(app.root.destroy_calls, 0)
        app._save_gui_preferences.assert_called_once_with()
        shown.assert_called_once()
        self.assertEqual(app.admin_mode_switch.options["value"], False)
        self.assertEqual(app.admin_mode_switch.options["enabled"], True)

        with patch.object(
                gui, "restart_as_windows_administrator") as restart:
            app._request_admin_mode(False)
        restart.assert_not_called()

    def test_active_close_requires_second_confirmation(self):
        class RootProbe:
            def __init__(self):
                self.destroy_calls = 0

            def destroy(self):
                self.destroy_calls += 1

        app = object.__new__(gui.DaisyApp)
        app.root = RootProbe()
        app.process = None
        app.run_jobs = [object()]
        app.worker_starting = False
        app.confirm_close_when_idle = False
        app.stop_requested = False
        app._set_stop_state = lambda _state: None
        app._save_gui_preferences = Mock()

        with patch.object(
                gui.messagebox, "askyesno", side_effect=(True, False)) \
                as declined:
            app._on_close()
        self.assertEqual(app.root.destroy_calls, 0)
        app._save_gui_preferences.assert_not_called()
        self.assertEqual(
            [call.args[0] for call in declined.call_args_list],
            ["确认退出", "再次确认退出"],
        )

        with patch.object(
                gui.messagebox, "askyesno", side_effect=(True, True)) \
                as confirmed:
            app._on_close()
        self.assertEqual(
            [call.args[0] for call in confirmed.call_args_list],
            ["确认退出", "再次确认退出"],
        )
        self.assertEqual(app.root.destroy_calls, 1)
        app._save_gui_preferences.assert_called_once_with()

    def test_top_task_navigation_entries_lock_together(self):
        class MenuProbe:
            def __init__(self):
                self.states = {}

            def entryconfigure(self, index, **options):
                self.states[index] = options["state"]

        class ButtonProbe:
            def __init__(self):
                self.states = []

            def configure(self, **options):
                if "state" in options:
                    self.states.append(options["state"])

        app = object.__new__(gui.DaisyApp)
        environment_menu = MenuProbe()
        database_menu = MenuProbe()
        app.task_menu_entries = {
            "env_check": (environment_menu, 0),
            "full_scan": (database_menu, 2),
        }
        environment_button = ButtonProbe()
        database_button = ButtonProbe()
        storage_button = ButtonProbe()
        app.task_toolbar_buttons = {
            "env_check": environment_button,
            "full_scan": database_button,
            "storage_list": storage_button,
        }
        app.advanced_menu = MenuProbe()
        app.advanced_locked_menu_entries = [0, 1]
        app._set_task_navigation_state("disabled")
        self.assertEqual(environment_menu.states, {0: "disabled"})
        self.assertEqual(database_menu.states, {2: "disabled"})
        self.assertEqual(environment_button.states, ["disabled"])
        self.assertEqual(database_button.states, ["disabled"])
        self.assertEqual(storage_button.states, ["disabled"])
        self.assertEqual(
            app.advanced_menu.states, {0: "disabled", 1: "disabled"})
        app._set_task_navigation_state("normal")
        self.assertEqual(environment_menu.states, {0: "normal"})
        self.assertEqual(database_menu.states, {2: "normal"})
        self.assertEqual(environment_button.states[-1], "normal")
        self.assertEqual(database_button.states[-1], "normal")
        self.assertEqual(storage_button.states[-1], "normal")
        self.assertEqual(
            app.advanced_menu.states, {0: "normal", 1: "normal"})

    def test_mini_mode_action_is_available_while_idle(self):
        class ButtonProbe:
            def __init__(self):
                self.options = {}

            def configure(self, **options):
                self.options.update(options)

        app = object.__new__(gui.DaisyApp)
        app.mini_mode = False
        app.process = None
        app.worker_starting = False
        app.run_jobs = []
        app.mini_mode_button = ButtonProbe()
        app._refresh_mini_action()
        self.assertEqual(
            app.mini_mode_button.options,
            {"text": "小窗运行", "state": "normal"},
        )

        calls = []
        app._enter_mini_mode = lambda: calls.append("enter")
        app._toggle_mini_mode()
        self.assertEqual(calls, ["enter"])

    def test_top_task_navigation_selection_uses_theme_highlight(self):
        class MenuProbe:
            def __init__(self):
                self.options = {}

            def entryconfigure(self, index, **options):
                self.options[index] = options

        class ButtonProbe:
            def __init__(self):
                self.options = {}

            def configure(self, **options):
                self.options.update(options)

        app = object.__new__(gui.DaisyApp)
        app.task = gui.TASK_BY_KEY["full_scan"]
        environment_menu = MenuProbe()
        database_menu = MenuProbe()
        app.task_menu_entries = {
            "env_check": (environment_menu, 0),
            "full_scan": (database_menu, 1),
        }
        environment_button = ButtonProbe()
        database_button = ButtonProbe()
        app.task_toolbar_buttons = {
            "env_check": environment_button,
            "full_scan": database_button,
            "storage_list": ButtonProbe(),
        }
        app.ui_font_family = "Microsoft YaHei UI"
        app.ui_font_size_delta = 0
        app._refresh_task_navigation_selection()
        self.assertEqual(
            environment_menu.options[0]["background"], gui._SURFACE)
        self.assertEqual(
            database_menu.options[1]["background"],
            gui._UNIFIED_ACTION_BACKGROUND,
        )
        self.assertEqual(
            database_menu.options[1]["foreground"],
            gui._UNIFIED_ACTION_FOREGROUND,
        )
        self.assertEqual(
            environment_button.options["style"], "Env.TopTask.TButton")
        self.assertEqual(
            database_button.options["style"],
            "Env.TopTaskSelected.TButton",
        )

    def test_top_task_toolbar_collapses_without_changing_selection(self):
        class BodyProbe:
            def __init__(self):
                self.manager = "pack"
                self.options = {}

            def winfo_manager(self):
                return self.manager

            def pack(self, **options):
                self.manager = "pack"
                self.options = options

            def pack_forget(self):
                self.manager = ""

        class ValueProbe:
            def set(self, value):
                self.value = value

        class ButtonProbe:
            def configure(self, **options):
                self.text = options["text"]

        class RootProbe:
            def after_idle(self, _callback):
                pass

        app = object.__new__(gui.DaisyApp)
        app.root = RootProbe()
        app.task_toolbar_expanded = True
        app.task_toolbar_horizontal_pad = 32
        app.task_toolbar_body = BodyProbe()
        app.task_toolbar_toggle_button = ButtonProbe()
        app.task_toolbar_visible_var = ValueProbe()
        app._set_task_toolbar_expanded(False)
        self.assertEqual(app.task_toolbar_body.manager, "")
        self.assertEqual(app.task_toolbar_toggle_button.text, "展开模块")
        self.assertFalse(app.task_toolbar_visible_var.value)
        app._set_task_toolbar_expanded(True)
        self.assertEqual(app.task_toolbar_body.manager, "pack")
        self.assertEqual(
            app.task_toolbar_body.options,
            {"fill": "x", "padx": 32, "pady": (0, 8)},
        )
        self.assertEqual(app.task_toolbar_toggle_button.text, "收起模块")
        self.assertTrue(app.task_toolbar_visible_var.value)

    def test_collapsed_settings_header_matches_panel_title_scale(self):
        class BodyProbe:
            def __init__(self):
                self.manager = "pack"

            def winfo_manager(self):
                return self.manager

            def pack(self, **_options):
                self.manager = "pack"

            def pack_forget(self):
                self.manager = ""

        class WidgetProbe:
            def __init__(self):
                self.options = {}

            def configure(self, **options):
                self.options.update(options)

        class RowProbe:
            def __init__(self):
                self.options = {}

            def pack_configure(self, **options):
                self.options.update(options)

        app = object.__new__(gui.DaisyApp)
        app.settings_body = BodyProbe()
        app.title_label = WidgetProbe()
        app.settings_title_row = RowProbe()
        app.settings_toggle_button = WidgetProbe()
        app.settings_expanded = True
        app.settings_title_expanded_font = (
            "Microsoft YaHei UI", 16, "bold")
        app.ui_font_family = "Microsoft YaHei UI"
        app.ui_font_size_delta = 0
        app.mini_mode = False
        app._refresh_view_menu_labels = lambda: None
        app._refresh_content_row_weights = lambda: None

        app._set_settings_expanded(False)
        self.assertEqual(
            app.title_label.options["font"],
            gui._COLLAPSED_PANEL_TITLE_FONT,
        )
        self.assertEqual(
            app.settings_title_row.options,
            {
                "padx": gui._PANEL_HEADER_PADX,
                "pady": gui._COLLAPSED_SETTINGS_HEADER_PADY,
            },
        )
        app._set_settings_expanded(True)
        self.assertEqual(
            app.title_label.options["font"],
            app.settings_title_expanded_font,
        )
        self.assertEqual(
            app.settings_title_row.options,
            {"padx": 22, "pady": (10, 6)},
        )

    def test_top_task_toolbar_keeps_fixed_theme_rows(self):
        class WidgetProbe:
            def __init__(self):
                self.placement = None
                self.grid_calls = 0
                self.forget_calls = 0
                self.options = {}

            def configure(self, **options):
                self.options.update(options)

            def grid_forget(self):
                self.placement = None
                self.forget_calls += 1

            def grid(self, **options):
                self.placement = (options["row"], options["column"])
                self.grid_calls += 1

        class BodyProbe:
            @staticmethod
            def winfo_width():
                return 1

        class RootProbe:
            @staticmethod
            def winfo_width():
                return 760

        app = object.__new__(gui.DaisyApp)
        app.root = RootProbe()
        app.task_toolbar_body = BodyProbe()
        all_keys = gui._TASK_MENU_ORDER
        app.task_toolbar_buttons = {
            key: WidgetProbe() for key in all_keys
        }
        app.task_toolbar_section_labels = {
            section_label: WidgetProbe()
            for section_label, _short_label, _task_keys
            in gui._TASK_TOOLBAR_ROWS
        }
        app._task_toolbar_layout_ready = False
        self.assertEqual(
            tuple(short_label for _section, short_label, _keys
                  in gui._TASK_TOOLBAR_ROWS),
            ("环境 ENV", "数据 DBS", "硬盘 STG"),
        )
        self.assertEqual(set(gui._TASK_TOOLBAR_LABELS), set(all_keys))
        self.assertTrue(all(
            len(label) == 6
            for label in gui._TASK_TOOLBAR_LABELS.values()
        ))
        self.assertEqual(gui._TASK_TOOLBAR_BUTTON_WIDTH, 12)
        self.assertEqual(gui._TASK_TOOLBAR_BUTTON_PADDING, (12, 4))
        self.assertEqual(gui._TASK_TOOLBAR_STYLE_PREFIX, "Env")
        self.assertEqual(gui._TASK_TOOLBAR_LABEL_COLOUR, gui._TEXT)
        self.assertEqual(gui._COLOUR_STRIP_HEIGHT, 4)
        with patch.object(
                gui.DaisyApp, "_sync_task_toolbar_minimum_width") as sync:
            for available in (1600, 1000, 760, 520):
                app._layout_task_toolbar(
                    types.SimpleNamespace(width=available))
                placements = {
                    key: button.placement
                    for key, button in app.task_toolbar_buttons.items()
                }
                self.assertNotIn(None, placements.values())
                self.assertEqual(
                    len(placements), len(set(placements.values())))
                for row_index, (section_label, _short_label, task_keys) in (
                        enumerate(gui._TASK_TOOLBAR_ROWS)):
                    self.assertEqual(
                        app.task_toolbar_section_labels[
                            section_label].placement,
                        (row_index, 0),
                    )
                    self.assertTrue(all(
                        placements[key][0] == row_index for key in task_keys
                    ))
            sync.assert_called_once_with()
        grid_calls = sum(
            button.grid_calls
            for button in app.task_toolbar_buttons.values()
        )
        forget_calls = sum(
            button.forget_calls
            for button in app.task_toolbar_buttons.values()
        )
        app._layout_task_toolbar(types.SimpleNamespace(width=520))
        self.assertEqual(
            sum(button.grid_calls
                for button in app.task_toolbar_buttons.values()),
            grid_calls,
        )
        self.assertEqual(
            sum(button.forget_calls
                for button in app.task_toolbar_buttons.values()),
            forget_calls,
        )

    def test_queue_progress_is_persistent_for_single_and_multiple_jobs(self):
        class WidgetProbe:
            def __init__(self):
                self.options = {}

            def configure(self, **options):
                self.options.update(options)

        app = object.__new__(gui.DaisyApp)
        app.queue_progress_bar = WidgetProbe()
        app.queue_detail_label = WidgetProbe()
        app.queue_percent_label = WidgetProbe()
        app.run_jobs = [types.SimpleNamespace(label="项目 A")]
        app._prepare_queue_progress()
        self.assertEqual(
            app.queue_detail_label.options["text"],
            "0/1 · 队列已准备",
        )
        app.run_job_index = 0
        app._update_queue_progress(0.5)
        self.assertEqual(app.queue_progress_bar.options["value"], 50.0)
        self.assertEqual(
            app.queue_detail_label.options["text"], "1/1 · 项目 A")
        self.assertEqual(app.queue_percent_label.options["text"], "50%")
        app.run_jobs = [object(), object(), object()]
        app._prepare_queue_progress()
        self.assertEqual(
            app.queue_detail_label.options["text"], "0/3 · 队列已准备")

    def _real_tk_app(self, preferences=None):
        gui._enable_dpi_awareness()
        try:
            root = gui.tk.Tk()
        except gui.tk.TclError as exc:
            self.skipTest(f"当前会话不能建立 Tk 窗口：{exc}")
        try:
            root.attributes("-alpha", 0.0)
        except gui.tk.TclError:
            root.withdraw()
        def destroy_root():
            try:
                pending = root.tk.call("after", "info")
                callback_ids = (
                    pending if isinstance(pending, (tuple, list))
                    else root.tk.splitlist(pending)
                )
                for callback_id in callback_ids:
                    while isinstance(callback_id, (tuple, list)):
                        if not callback_id:
                            break
                        callback_id = callback_id[0]
                    if not callback_id:
                        continue
                    try:
                        root.tk.call("after", "cancel", str(callback_id))
                    except (gui.tk.TclError, TypeError):
                        pass
                if root.winfo_exists():
                    root.destroy()
            except gui.tk.TclError:
                pass
        self.addCleanup(destroy_root)
        loaded_preferences = (
            gui.default_gui_preferences()
            if preferences is None else dict(preferences)
        )
        with patch.object(
                gui, "load_gui_preferences",
                return_value=loaded_preferences):
            app = gui.DaisyApp(root)
        root.geometry("1840x1020+0+0")
        root.update()
        return root, app

    @staticmethod
    def _tk_descendants(widget):
        descendants = []
        for child in widget.winfo_children():
            descendants.append(child)
            descendants.extend(TestGuiArguments._tk_descendants(child))
        return descendants

    def _assert_real_tk_page_geometry(self, root, app, context):
        """检查当前页面的边界、可点击控件尺寸与纵向可达性。"""
        root.update()
        root_left = root.winfo_rootx()
        root_top = root.winfo_rooty()
        root_right = root_left + root.winfo_width()
        root_bottom = root_top + root.winfo_height()
        self.assertGreater(root.winfo_width(), 700, context)
        self.assertGreater(root.winfo_height(), 600, context)

        visible_panels = [
            panel for panel in (
                app.task_card, app.progress_panel,
                app.log_panel, app.command_panel)
            if panel.winfo_ismapped()
        ]
        previous_bottom = None
        for panel in visible_panels:
            panel_left = panel.winfo_rootx()
            panel_top = panel.winfo_rooty()
            panel_right = panel_left + panel.winfo_width()
            panel_bottom = panel_top + panel.winfo_height()
            self.assertGreaterEqual(panel_left, root_left, context)
            self.assertGreaterEqual(panel_top, root_top, context)
            self.assertLessEqual(panel_right, root_right + 1, context)
            self.assertLessEqual(panel_bottom, root_bottom + 1, context)
            if previous_bottom is not None:
                self.assertGreaterEqual(panel_top, previous_bottom, context)
            previous_bottom = panel_bottom

        self.assertGreaterEqual(
            app.title_label.winfo_height(),
            app.title_label.winfo_reqheight(), context)
        self.assertGreaterEqual(
            app.desc_label.winfo_height(),
            app.desc_label.winfo_reqheight(), context)
        self.assertGreater(app.form_canvas.winfo_width(), 360, context)
        self.assertGreater(app.form_canvas.winfo_height(), 20, context)

        inner_left = app.form_inner.winfo_rootx()
        inner_right = inner_left + app.form_inner.winfo_width()
        clickable_types = (gui.tk.Button, gui.ttk.Button, gui.ttk.Combobox)
        for widget in self._tk_descendants(app.form_inner):
            if not widget.winfo_ismapped() or widget.winfo_width() <= 1:
                continue
            widget_left = widget.winfo_rootx()
            widget_right = widget_left + widget.winfo_width()
            self.assertGreaterEqual(widget_left, inner_left - 1, context)
            self.assertLessEqual(widget_right, inner_right + 1, context)
            if isinstance(widget, clickable_types):
                self.assertGreaterEqual(
                    widget.winfo_width() + 1,
                    widget.winfo_reqwidth(),
                    f"{context} · {widget}",
                )

        content_height = app._form_content_height()
        viewport_height = app.form_canvas.winfo_height()
        if content_height <= viewport_height:
            self.assertFalse(app.form_scroll.winfo_manager(), context)
            self.assertEqual(
                tuple(float(value) for value in app.form_canvas.yview()),
                (0.0, 1.0), context)
        else:
            self.assertEqual(app.form_scroll.winfo_manager(), "pack", context)
            app.form_canvas.yview_moveto(1.0)
            root.update_idletasks()
            visible_bottom = app.form_canvas.canvasy(viewport_height)
            self.assertGreaterEqual(
                visible_bottom + 2, content_height, context)
            app.form_canvas.yview_moveto(0.0)
            root.update_idletasks()
            self.assertEqual(float(app.form_canvas.yview()[0]), 0.0, context)

        command_bottom = (
            app.command_panel.winfo_rooty()
            + app.command_panel.winfo_height())
        self.assertLessEqual(command_bottom, root_bottom + 1, context)

    @unittest.skipUnless(os.name == "nt", "DAISY GUI 只支持 Windows")
    def test_real_tk_shell_can_be_constructed(self):
        root, app = self._real_tk_app()
        self.assertFalse(hasattr(app, "panel_splitter"))
        self.assertEqual(app.task_card.grid_info()["row"], 0)
        self.assertEqual(app.progress_panel.grid_info()["row"], 1)
        self.assertEqual(app.log_panel.grid_info()["row"], 2)
        self.assertEqual(app.command_panel.grid_info()["row"], 3)

        app._enter_mini_mode()
        root.update()
        self.assertFalse(app.task_card.winfo_manager())
        self.assertEqual(app.progress_panel.winfo_manager(), "grid")
        self.assertFalse(app.log_panel.winfo_manager())
        app._leave_mini_mode()
        root.update()
        for widget in (
                app.task_card, app.progress_panel,
                app.log_panel, app.command_panel):
            self.assertEqual(widget.winfo_manager(), "grid")
        self.assertEqual(root.winfo_width(), 1840)
        self.assertEqual(root.winfo_height(), 1020)

    @unittest.skipUnless(os.name == "nt", "DAISY GUI 只支持 Windows")
    def test_real_tk_reopens_last_page_without_form_values(self):
        preferences = gui.default_gui_preferences()
        preferences["last_task_key"] = "check_hash"
        root, app = self._real_tk_app(preferences)

        self.assertEqual(app.task.key, "check_hash")
        self.assertEqual(app.saved_values, {})
        self.assertEqual(app.values["snapshot"].get(), "")
        self.assertEqual(app.values["root_map"].get("1.0", "end-1c"), "")
        self.assertEqual(
            app.gui_preferences["last_task_key"], "check_hash")

    @unittest.skipUnless(os.name == "nt", "DAISY GUI 只支持 Windows")
    def test_real_tk_1080p_default_forms_fit_without_scrolling(self):
        root, app = self._real_tk_app()
        app._select_task("full_scan", save_current=False)
        root.update()
        description_heights = set()
        for task in gui.TASKS:
            app._select_task(task.key, save_current=False)
            root.update()
            bounds = app.form_canvas.bbox("all")
            content_height = (
                0 if bounds is None else int(bounds[3]) - int(bounds[1]))
            viewport_height = int(app.form_canvas.winfo_height())
            self.assertLessEqual(
                content_height, viewport_height,
                f"{task.key} 默认表单超出 1080P 可视区",
            )
            self.assertFalse(
                app.form_scroll.winfo_manager(),
                f"{task.key} 内容未溢出时不应显示滚动条",
            )
            for delta in (-120, -120, 120, 120):
                app._scroll_form(types.SimpleNamespace(delta=delta, num=0))
            root.update_idletasks()
            self.assertEqual(
                tuple(float(value) for value in app.form_canvas.yview()),
                (0.0, 1.0),
                f"{task.key} 内容未溢出时不应响应纵向滚动",
            )
            description_heights.add(app.desc_label.winfo_reqheight())
        self.assertEqual(
            len(description_heights), 1,
            "各页面副标题应保持统一单行高度",
        )

    @unittest.skipUnless(os.name == "nt", "DAISY GUI 只支持 Windows")
    def test_real_tk_independent_log_is_singleton_and_synchronized(self):
        root, app = self._real_tk_app()
        first = gui.TASKS[0].title + "\n"
        second = gui.TASKS[-1].title + "\n"
        app._append_log(first, "meta")
        app._open_log_window()
        try:
            app.log_window.attributes("-alpha", 0.0)
        except gui.tk.TclError:
            pass
        root.update()
        window = app.log_window
        self.assertGreaterEqual(window.winfo_height(), 700)
        app._open_log_window()
        self.assertIs(app.log_window, window)
        app._append_log(second, "success")
        expected = first + second
        self.assertEqual(app.log.get("1.0", "end-1c"), expected)
        self.assertEqual(
            app.log_window_text.get("1.0", "end-1c"), expected)
        app._clear_log()
        self.assertEqual(app.log.get("1.0", "end-1c"), "")
        self.assertEqual(app.log_window_text.get("1.0", "end-1c"), "")
        app._close_log_window()
        self.assertIsNone(app.log_window)
        self.assertIsNone(app.log_window_text)

    @unittest.skipUnless(os.name == "nt", "DAISY GUI 只支持 Windows")
    def test_real_tk_form_actions_and_storage_progress_are_consistent(self):
        root, app = self._real_tk_app()
        app._select_task("env_check", save_current=False)
        root.update()
        self.assertTrue(app.install_tool_buttons)
        install_positions = set()
        for button in app.install_tool_buttons.values():
            self.assertEqual(button.cget("style"), "FormAction.TButton")
            self.assertLess(button.winfo_reqwidth(), 220)
            self.assertGreaterEqual(
                button.winfo_width(), button.winfo_reqwidth())
            install_positions.add((
                int(button.grid_info()["row"]),
                int(button.grid_info()["column"]),
            ))
        self.assertEqual(
            install_positions, {(0, 0), (0, 1), (0, 2), (0, 3)})

        app._select_task("full_scan", save_current=False)
        root.update()
        roots = app.values["roots"]
        self.assertEqual(
            roots.add_button.cget("style"), "FormAction.TButton")
        self.assertGreaterEqual(roots.add_button.winfo_reqwidth(), 100)
        tooltip = roots.add_button._daisy_tooltip
        tooltip._show()
        root.update_idletasks()
        tooltip_label = tooltip._window.winfo_children()[0]
        self.assertEqual(tooltip_label.cget("text"), tooltip.text)
        work_area = gui._monitor_work_area_for_window(roots.add_button)
        self.assertLessEqual(
            tooltip._window.winfo_y() + tooltip._window.winfo_reqheight(),
            work_area.bottom,
        )
        tooltip._hide()

        form_tooltip_targets = [
            child for child in app.form_inner.winfo_children()
            if getattr(child, "_daisy_tooltip", None) is not None
        ]
        self.assertGreaterEqual(len(form_tooltip_targets), 2)

        combobox = next(
            child
            for cell in app.form_inner.winfo_children()
            for child in cell.winfo_children()
            if isinstance(child, gui.ttk.Combobox)
        )
        arrow_element = combobox.identify(
            combobox.winfo_width() - 11, combobox.winfo_height() // 2)
        self.assertEqual(combobox.cget("style"), "Daisy.TCombobox")
        arrow_layout = repr(app.style.layout("Daisy.TCombobox"))
        self.assertIn("Daisy.Combobox.chevron", arrow_layout)
        self.assertNotIn("Combobox.downarrow", arrow_layout)
        self.assertIn("Daisy.Combobox.chevron", arrow_element)
        normal_arrow = app.combobox_arrow_images[0]
        self.assertEqual((normal_arrow.width(), normal_arrow.height()), (22, 16))
        self.assertTrue(normal_arrow.transparency_get(0, 0))
        self.assertFalse(normal_arrow.transparency_get(10, 9))

        app._select_task("storage_collect", save_current=False)
        root.update()
        pool = app.values["disk_number"]
        self.assertEqual(
            pool.select_all_button.cget("style"), "FormAction.TButton")
        self.assertEqual(
            pool.clear_selection_button.cget("style"),
            "FormAction.TButton",
        )
        self.assertLess(pool.select_all_button.winfo_reqwidth(), 160)
        self.assertLess(pool.clear_selection_button.winfo_reqwidth(), 160)
        self.assertEqual(
            app.storage_detect_button.cget("style"),
            "FormAction.TButton",
        )

        app.process_task_key = "storage_list"
        app.run_jobs = [gui.RunJob("检测物理硬盘", {})]
        app.run_job_index = 0
        app._begin_progress()
        self.assertEqual(
            app.progress_stage_label.cget("text"),
            "检测物理硬盘 · 正在启动",
        )
        self.assertIn(
            "正在查询 Windows 存储接口与 smartctl",
            app.progress_detail_label.cget("text"),
        )

    @unittest.skipUnless(os.name == "nt", "DAISY GUI 只支持 Windows")
    def test_real_tk_titles_and_directory_actions_share_alignment(self):
        root, app = self._real_tk_app()
        def assert_page_alignment(context):
            field_right_edges = set()
            cell_left_edges = set()
            for task_key in gui._TASK_TOOLBAR_LABELS:
                app._select_task(task_key, save_current=False)
                root.update()
                task_values = gui._task_values(
                    app.task, app.saved_values.get(task_key, {}))
                active_specs = [
                    spec for spec in app.task.fields
                    if not spec.top_menu
                    and gui._field_active(spec, task_values)
                ]
                for spec in active_specs:
                    label = next(
                        child for child in app.form_inner.winfo_children()
                        if isinstance(child, gui.tk.Label)
                        and child.cget("text") == spec.label
                    )
                    self.assertNotIn("*", label.cget("text"), context)
                    self.assertGreaterEqual(
                        label.winfo_width(), label.winfo_reqwidth(), context)
                    field_right_edges.add(
                        label.winfo_rootx() + label.winfo_width())
                    row = int(label.grid_info()["row"])
                    cell = next(
                        child for child in app.form_inner.winfo_children()
                        if isinstance(child, gui.tk.Frame)
                        and int(child.grid_info().get("row", -1)) == row
                        and int(child.grid_info().get("column", -1)) == 1
                    )
                    cell_left_edges.add(cell.winfo_rootx())
            self.assertEqual(len(field_right_edges), 1, context)
            self.assertEqual(len(cell_left_edges), 1, context)

        combinations = 0
        for family in app._available_ui_font_families():
            for _size_label, size_delta in gui._UI_FONT_SIZE_OPTIONS:
                app._set_ui_font(
                    family=family, size_delta=size_delta, persist=False)
                assert_page_alignment(f"{family}／{size_delta:+d}")
                combinations += 1
        self.assertGreaterEqual(combinations, 6)

        app._set_ui_font(
            family=gui._UI_FONT_FAMILY, size_delta=0, persist=False)

        app._select_task("full_scan", save_current=False)
        root.update()
        list_add = app.values["roots"].add_button
        self.assertEqual(int(list_add.grid_info()["column"]), 1)
        self.assertEqual(str(list_add.grid_info()["sticky"]), "e")

        app._select_task("check_hash", save_current=False)
        root.update()
        mapping = app.values["root_map"]
        mapped_add = next(
            child for child in mapping.master.winfo_children()
            if isinstance(child, gui.ttk.Button)
            and child.cget("text") == "添加目录"
        )
        self.assertEqual(int(mapped_add.grid_info()["column"]), 1)
        self.assertEqual(str(mapped_add.grid_info()["sticky"]), "ne")

    @unittest.skipUnless(os.name == "nt", "DAISY GUI 只支持 Windows")
    def test_real_tk_scrollbar_only_appears_for_actual_overflow(self):
        root, app = self._real_tk_app()
        app._set_default_window_size((1366, 768), persist=False)
        app._set_ui_font(size_delta=2, persist=False)
        app._select_task("full_scan", save_current=False)
        root.update()
        self.assertGreater(
            app._form_content_height(), app.form_canvas.winfo_height())
        self.assertEqual(app.form_scroll.winfo_manager(), "pack")
        for _index in range(8):
            app._scroll_form(types.SimpleNamespace(delta=-120, num=0))
        root.update_idletasks()
        self.assertGreater(float(app.form_canvas.yview()[0]), 0.0)
        for _index in range(80):
            app._scroll_form(types.SimpleNamespace(delta=120, num=0))
        root.update_idletasks()
        self.assertEqual(float(app.form_canvas.yview()[0]), 0.0)

        app._select_task(gui._PROJECT_SELF_TEST_KEY, save_current=False)
        root.update()
        self.assertLessEqual(
            app._form_content_height(), app.form_canvas.winfo_height())
        self.assertFalse(app.form_scroll.winfo_manager())
        self.assertEqual(
            tuple(float(value) for value in app.form_canvas.yview()),
            (0.0, 1.0),
        )

    @unittest.skipUnless(os.name == "nt", "DAISY GUI 只支持 Windows")
    def test_real_tk_storage_detection_success_returns_to_selection(self):
        root, app = self._real_tk_app()
        app._select_task("storage_collect", save_current=False)
        app.storage_disk_options = (
            types.SimpleNamespace(selectable=True),
            types.SimpleNamespace(selectable=False),
        )
        app._set_settings_expanded(False)
        app._set_progress_expanded(True)
        app._set_log_expanded(True)
        app.process_task_key = "storage_list"
        app.run_jobs = [gui.RunJob("检测物理硬盘", {})]
        app.run_job_index = 0
        app.run_results = [0]
        app.stop_requested = False
        app.worker_starting = False
        app.close_after_stop = False
        with patch.object(gui.messagebox, "showinfo") as shown:
            app._finalize_run(0.2)
            root.update()
        shown.assert_called_once()
        self.assertTrue(app.settings_expanded)
        self.assertFalse(app.progress_expanded)
        self.assertFalse(app.log_expanded)
        self.assertIn("1 块可登记硬盘", app.status_label.cget("text"))

        app._start_next_job = lambda: None
        app._begin_run_jobs(
            "storage_collect", [gui.RunJob("PhysicalDrive3", {})])
        self.assertFalse(app.settings_expanded)
        self.assertTrue(app.progress_expanded)
        self.assertTrue(app.log_expanded)

    @unittest.skipUnless(os.name == "nt", "DAISY GUI 只支持 Windows")
    def test_real_tk_storage_detection_failure_keeps_diagnostics_open(self):
        root, app = self._real_tk_app()
        app._select_task("storage_collect", save_current=False)
        app._set_settings_expanded(False)
        app._set_progress_expanded(True)
        app._set_log_expanded(True)
        app.process_task_key = "storage_list"
        app.run_jobs = [gui.RunJob("检测物理硬盘", {})]
        app.run_job_index = 0
        app.run_results = [2]
        app.stop_requested = False
        app.worker_starting = False
        app.close_after_stop = False
        with (
            patch.object(gui.messagebox, "showinfo") as info,
            patch.object(gui.messagebox, "showwarning") as warning,
        ):
            app._finalize_run(0.2)
            root.update()
        info.assert_not_called()
        warning.assert_not_called()
        self.assertFalse(app.settings_expanded)
        self.assertTrue(app.progress_expanded)
        self.assertTrue(app.log_expanded)

    @unittest.skipUnless(os.name == "nt", "DAISY GUI 只支持 Windows")
    def test_real_tk_storage_detection_without_selectable_disk_warns(self):
        root, app = self._real_tk_app()
        app._select_task("storage_collect", save_current=False)
        app.storage_disk_options = (
            types.SimpleNamespace(selectable=False),
        )
        app._set_settings_expanded(False)
        app._set_progress_expanded(True)
        app._set_log_expanded(True)
        app.process_task_key = "storage_list"
        app.run_jobs = [gui.RunJob("检测物理硬盘", {})]
        app.run_job_index = 0
        app.run_results = [0]
        app.stop_requested = False
        app.worker_starting = False
        app.close_after_stop = False
        with patch.object(gui.messagebox, "showwarning") as shown:
            app._finalize_run(0.2)
            root.update()
        shown.assert_called_once()
        self.assertTrue(app.settings_expanded)
        self.assertFalse(app.progress_expanded)
        self.assertFalse(app.log_expanded)
        self.assertIn("没有找到可登记", app.status_label.cget("text"))

    @unittest.skipUnless(os.name == "nt", "DAISY GUI 只支持 Windows")
    def test_real_tk_every_field_tooltip_uses_text_cell_and_control(self):
        root, app = self._real_tk_app()
        expected_specs = [
            (task, spec)
            for task in gui.TASKS
            for spec in task.fields
            if spec.help and not spec.top_menu
        ]
        checked = 0
        for task, spec in expected_specs:
            app.saved_values[task.key] = {
                key: allowed[0]
                for key, allowed in spec.active_when
            }
            app._select_task(task.key, save_current=False)
            root.update()
            label_text = spec.label
            field_label = next(
                child for child in app.form_inner.winfo_children()
                if (isinstance(child, gui.tk.Label)
                    and child.cget("text") == label_text)
            )
            row = int(field_label.grid_info()["row"])
            cell = next(
                child for child in app.form_inner.winfo_children()
                if (isinstance(child, gui.tk.Frame)
                    and int(child.grid_info().get("row", -1)) == row
                    and int(child.grid_info().get("column", -1)) == 1)
            )
            targets = [field_label, cell, *self._tk_descendants(cell)]
            matching = [
                target for target in targets
                if getattr(
                    getattr(target, "_daisy_tooltip", None),
                    "text", None) == spec.help
            ]
            context = f"{task.key}.{spec.key}"
            self.assertNotIn("*", field_label.cget("text"), context)
            self.assertNotIn("ⓘ", field_label.cget("text"), context)
            self.assertIn(field_label, matching, context)
            self.assertIn(cell, matching, context)
            self.assertTrue(field_label.bind("<Enter>"), context)
            self.assertTrue(cell.bind("<Enter>"), context)
            self.assertGreaterEqual(len(matching), 3, context)

            tooltip = field_label._daisy_tooltip
            tooltip._show()
            root.update_idletasks()
            self.assertIsNotNone(tooltip._window, context)
            work_area = gui._monitor_work_area_for_window(field_label)
            self.assertGreaterEqual(
                tooltip._window.winfo_x(), work_area.left, context)
            self.assertLessEqual(
                tooltip._window.winfo_x()
                + tooltip._window.winfo_reqwidth(),
                work_area.right,
                context,
            )
            self.assertLessEqual(
                tooltip._window.winfo_y()
                + tooltip._window.winfo_reqheight(),
                work_area.bottom,
                context,
            )
            tooltip._hide()
            checked += 1
        self.assertEqual(checked, len(expected_specs))

    @unittest.skipUnless(os.name == "nt", "DAISY GUI 只支持 Windows")
    def test_real_tk_reselecting_same_choice_keeps_visible_text(self):
        root, app = self._real_tk_app()
        app._select_task("full_scan", save_current=False)
        root.update()

        def comboboxes(widget):
            found = []
            for child in widget.winfo_children():
                if isinstance(child, gui.ttk.Combobox):
                    found.append(child)
                found.extend(comboboxes(child))
            return found

        original = next(
            widget for widget in comboboxes(app.form_inner)
            if getattr(widget, "_daisy_field_key", None) == "start_mode"
        )
        initial_text = original.get()
        original.current(original.current())
        original.event_generate("<<ComboboxSelected>>")
        root.update()
        self.assertTrue(original.winfo_exists())
        self.assertEqual(original.get(), initial_text)

        original.current(1)
        original.event_generate("<<ComboboxSelected>>")
        root.update()
        changed = next(
            widget for widget in comboboxes(app.form_inner)
            if getattr(widget, "_daisy_field_key", None) == "start_mode"
        )
        self.assertFalse(original.winfo_exists())
        self.assertTrue(changed.get())
        self.assertEqual(
            app.saved_values["full_scan"]["start_mode"], "resume")

    @unittest.skipUnless(os.name == "nt", "DAISY GUI 只支持 Windows")
    def test_real_tk_running_layout_fills_remaining_height_with_log(self):
        root, app = self._real_tk_app()
        app._set_settings_expanded(False)
        app._set_progress_expanded(True)
        app._set_log_expanded(True)
        root.update()
        self.assertFalse(app.settings_expanded)
        self.assertTrue(app.progress_expanded)
        self.assertTrue(app.log_expanded)
        self.assertEqual(
            int(app.content.grid_rowconfigure(0)["weight"]), 0)
        self.assertEqual(
            int(app.content.grid_rowconfigure(2)["weight"]), 1)
        self.assertGreater(app.log_panel.winfo_height(), 120)
        command_bottom = (
            app.command_panel.winfo_y() + app.command_panel.winfo_height())
        self.assertLessEqual(
            abs(command_bottom - app.content.winfo_height()), 1)

    @unittest.skipUnless(os.name == "nt", "DAISY GUI 只支持 Windows")
    def test_real_tk_window_font_matrix_keeps_controls_usable(self):
        root, app = self._real_tk_app()
        families = app._available_ui_font_families()
        self.assertGreaterEqual(len(families), 2)
        checks = 0
        for family in families:
            for _label, size_delta in gui._UI_FONT_SIZE_OPTIONS:
                app._set_ui_font(
                    family=family, size_delta=size_delta, persist=False)
                for _window_label, window_size in gui._WINDOW_SIZE_OPTIONS:
                    app._set_default_window_size(
                        window_size, persist=False)
                    root.update()
                    for task_key in (
                            "env_check", "full_scan", "storage_collect"):
                        app._select_task(task_key, save_current=False)
                        root.update()
                        self.assertTrue(app.title_label.cget("text"))
                        self.assertTrue(app.desc_label.cget("text"))
                        self.assertGreater(app.form_canvas.winfo_width(), 400)
                        self.assertGreater(app.form_canvas.winfo_height(), 20)
                        root_right = root.winfo_rootx() + root.winfo_width()
                        for button in app.task_toolbar_buttons.values():
                            self.assertLessEqual(
                                button.winfo_rootx() + button.winfo_width(),
                                root_right + 1,
                            )
                        bounds = app.form_canvas.bbox("all")
                        content_height = (
                            0 if bounds is None
                            else int(bounds[3]) - int(bounds[1]))
                        if content_height > app.form_canvas.winfo_height():
                            app.form_canvas.yview_moveto(1.0)
                            root.update_idletasks()
                            self.assertGreater(
                                float(app.form_canvas.yview()[0]), 0.0)
                            app.form_canvas.yview_moveto(0.0)
                    checks += 1
        self.assertEqual(
            checks,
            len(families)
            * len(gui._UI_FONT_SIZE_OPTIONS)
            * len(gui._WINDOW_SIZE_OPTIONS),
        )
        self.assertTrue(hasattr(app, "settings_menu"))

    @unittest.skipUnless(os.name == "nt", "DAISY GUI 只支持 Windows")
    def test_real_tk_exhaustive_font_size_aspect_ratio_matrix(self):
        root, app = self._real_tk_app()
        families = app._available_ui_font_families()
        geometries = (
            (1840, 1020),
            (1440, 900),
            (1280, 720),
            (1280, 960),
            (1280, 1024),
            (1100, 850),
            (1024, 768),
        )
        task_keys = tuple(task.key for task in gui.TASKS)
        checks = 0
        for family in families:
            for _size_label, size_delta in gui._UI_FONT_SIZE_OPTIONS:
                app._set_ui_font(
                    family=family, size_delta=size_delta, persist=False)
                for width, height in geometries:
                    root.geometry(f"{width}x{height}+0+0")
                    root.update()
                    self.assertEqual(
                        (root.winfo_width(), root.winfo_height()),
                        (width, height),
                    )
                    for task_key in task_keys:
                        app._select_task(task_key, save_current=False)
                        context = (
                            f"{family} / +{size_delta} / "
                            f"{width}x{height} / {task_key}")
                        with self.subTest(context=context):
                            self._assert_real_tk_page_geometry(
                                root, app, context)
                        checks += 1
        self.assertEqual(
            checks,
            len(families) * len(gui._UI_FONT_SIZE_OPTIONS)
            * len(geometries) * len(task_keys),
        )

    @unittest.skipUnless(os.name == "nt", "DAISY GUI 只支持 Windows")
    def test_real_tk_relative_scaling_aspect_ratio_matrix(self):
        root, app = self._real_tk_app()
        base_scaling = float(root.tk.call("tk", "scaling"))
        scaling_factors = (1.0, 1.25, 1.5)
        geometries = (
            (1440, 900),
            (1280, 720),
            (1280, 960),
            (1100, 850),
        )
        task_keys = tuple(task.key for task in gui.TASKS)
        checks = 0
        try:
            for scaling_factor in scaling_factors:
                root.tk.call(
                    "tk", "scaling", base_scaling * scaling_factor)
                for _size_label, size_delta in gui._UI_FONT_SIZE_OPTIONS:
                    app._set_ui_font(
                        size_delta=size_delta, persist=False)
                    for width, height in geometries:
                        root.geometry(f"{width}x{height}+0+0")
                        root.update()
                        self.assertEqual(
                            (root.winfo_width(), root.winfo_height()),
                            (width, height),
                        )
                        for task_key in task_keys:
                            app._select_task(task_key, save_current=False)
                            context = (
                                f"scale {scaling_factor:.2f} / "
                                f"+{size_delta} / {width}x{height} / "
                                f"{task_key}")
                            with self.subTest(context=context):
                                self._assert_real_tk_page_geometry(
                                    root, app, context)
                            checks += 1
        finally:
            root.tk.call("tk", "scaling", base_scaling)
        self.assertEqual(
            checks,
            len(scaling_factors) * len(gui._UI_FONT_SIZE_OPTIONS)
            * len(geometries) * len(task_keys),
        )

    @unittest.skipUnless(os.name == "nt", "DAISY GUI 只支持 Windows")
    def test_real_tk_every_combobox_arrow_and_selection_path(self):
        root, app = self._real_tk_app()
        choice_kinds = {"choice", "choice_flag", "disk_choice"}
        expected_specs = [
            (task, spec)
            for task in gui.TASKS
            for spec in task.fields
            if spec.kind in choice_kinds and not spec.top_menu
        ]
        checked = 0
        for task, spec in expected_specs:
            saved = {
                key: allowed[0]
                for key, allowed in spec.active_when
            }
            app.saved_values[task.key] = saved
            app._select_task(task.key, save_current=False)
            root.update()
            combobox = next(
                widget
                for widget in self._tk_descendants(app.form_inner)
                if (isinstance(widget, gui.ttk.Combobox)
                    and getattr(widget, "_daisy_field_key", None)
                    == spec.key)
            )
            context = f"{task.key}.{spec.key}"
            arrow_hits = [
                x for x in range(
                    max(0, combobox.winfo_width() - 24),
                    combobox.winfo_width())
                if "Daisy.Combobox.chevron" in combobox.identify(
                    x, combobox.winfo_height() // 2)
            ]
            self.assertGreaterEqual(len(arrow_hits), 20, context)
            click_x = arrow_hits[len(arrow_hits) // 2]
            initial_text = combobox.get()
            combobox.event_generate(
                "<ButtonPress-1>", x=click_x,
                y=combobox.winfo_height() // 2)
            combobox.event_generate(
                "<ButtonRelease-1>", x=click_x,
                y=combobox.winfo_height() // 2)
            root.update()
            try:
                root.tk.call("ttk::combobox::Unpost", str(combobox))
            except gui.tk.TclError:
                pass
            self.assertEqual(combobox.get(), initial_text, context)

            current_index = combobox.current()
            combobox.current(current_index)
            combobox.event_generate("<<ComboboxSelected>>")
            root.update()
            self.assertTrue(combobox.winfo_exists(), context)
            self.assertEqual(combobox.get(), initial_text, context)

            values = tuple(combobox.cget("values"))
            if len(values) > 1:
                alternate_index = (current_index + 1) % len(values)
                combobox.current(alternate_index)
                combobox.event_generate("<<ComboboxSelected>>")
                root.update()
                rebuilt = next(
                    widget
                    for widget in self._tk_descendants(app.form_inner)
                    if (isinstance(widget, gui.ttk.Combobox)
                        and getattr(widget, "_daisy_field_key", None)
                        == spec.key)
                )
                self.assertTrue(rebuilt.get(), context)
                self.assertEqual(rebuilt.current(), alternate_index, context)
            checked += 1
        self.assertEqual(checked, len(expected_specs))

    @unittest.skipUnless(os.name == "nt", "DAISY GUI 只支持 Windows")
    def test_tcl_runtime_bootstrap_survives_missing_or_bad_environment(self):
        code = (
            "import os,sys;"
            "sys.path.insert(0,os.path.join(os.getcwd(),'Script'));"
            "import Script_DAISY_GUI as gui;"
            "root=gui.tk.Tk();root.withdraw();"
            "print(root.tk.call('info','patchlevel'));"
            "print(root.tk.call('package','require','Tk'));"
            "root.update_idletasks();root.destroy()"
        )
        for mode in ("missing", "invalid"):
            environment = os.environ.copy()
            if mode == "missing":
                environment.pop("TCL_LIBRARY", None)
                environment.pop("TK_LIBRARY", None)
            else:
                environment["TCL_LIBRARY"] = r"Z:\missing\tcl"
                environment["TK_LIBRARY"] = r"Z:\missing\tk"
            completed = subprocess.run(
                [sys.executable, "-B", "-c", code],
                cwd=gui._BASE,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
            )
            self.assertEqual(
                completed.returncode, 0,
                f"{mode}: {completed.stderr}")
            versions = completed.stdout.splitlines()
            self.assertEqual(len(versions), 2, mode)
            self.assertTrue(all(
                version.startswith("8.6") for version in versions), mode)

    @unittest.skipUnless(os.name == "nt", "DAISY GUI 只支持 Windows")
    def test_both_user_gui_entries_construct_and_close_cleanly(self):
        original_tk = gui.tk.Tk
        original_app = gui.DaisyApp
        roots = []

        def auto_closing_root():
            root = original_tk()
            root.withdraw()
            roots.append(root)
            return root

        def auto_closing_app(root):
            app = original_app(root)
            root.after(120, app._destroy_root)
            return app

        launcher = os.path.join(gui._BASE, "Start_DAISY_GUI.pyw")
        with (
            patch.object(gui.tk, "Tk", side_effect=auto_closing_root),
            patch.object(gui, "DaisyApp", side_effect=auto_closing_app),
            patch.object(
                gui, "load_gui_preferences",
                return_value=gui.default_gui_preferences()),
        ):
            with self.assertRaises(SystemExit) as launcher_exit:
                runpy.run_path(launcher, run_name="__main__")
            self.assertEqual(launcher_exit.exception.code, 0)

            with patch.object(
                    sys, "argv", ["Script_DAISY_MAIN.py", "gui"]):
                self.assertEqual(entry.main(), 0)

        self.assertEqual(len(roots), 2)
        for root in roots:
            with self.assertRaises(gui.tk.TclError):
                root.winfo_exists()

    def test_starting_jobs_expands_progress_and_log(self):
        class WidgetProbe:
            def __init__(self):
                self.options = {}

            def configure(self, **options):
                self.options.update(options)

        class AdminProbe:
            def set_mode(self, **_options):
                pass

        app = object.__new__(gui.DaisyApp)
        app.storage_disk_choices = ()
        app.storage_disk_options = ()
        app.saved_values = {}
        app.process_task_key = None
        app.stop_requested = False
        app.run_jobs = []
        app.run_button = WidgetProbe()
        app.install_tool_buttons = {}
        app.admin_mode_switch = AdminProbe()
        app.is_administrator = False
        calls = []
        app._set_stop_state = lambda state: calls.append(("stop", state))
        app._set_settings_expanded = (
            lambda value: calls.append(("settings", value)))
        app._set_progress_expanded = (
            lambda value: calls.append(("progress", value)))
        app._set_log_expanded = lambda value: calls.append(("log", value))
        app._prepare_queue_progress = lambda: calls.append(("queue", True))
        app._refresh_mini_action = lambda: None
        app._set_task_navigation_state = lambda _state: None
        app._refresh_environment_actions = lambda: None
        app._set_status = lambda _text: None
        app._start_next_job = lambda: calls.append(("start", True))
        app._begin_run_jobs(
            "full_scan", [gui.RunJob("档案", {"roots": r"E:\档案"})])
        self.assertIn(("settings", False), calls)
        self.assertIn(("progress", True), calls)
        self.assertIn(("log", True), calls)
        self.assertLess(
            calls.index(("settings", False)), calls.index(("start", True)))
        self.assertLess(
            calls.index(("log", True)), calls.index(("start", True)))

    def test_status_badge_uses_task_or_semantic_colour(self):
        self.assertEqual(
            gui.status_badge_background("full_scan"), gui._GREEN_DARK)
        self.assertEqual(
            gui.status_badge_background("diff"), gui._GREEN_DARK)
        self.assertEqual(
            gui.status_badge_background("diff", gui._DANGER),
            gui._DANGER,
        )

    def test_result_directory_offer_requires_completed_business_task(self):
        for returncodes in ([0], [1], [0, 1]):
            self.assertTrue(gui.should_offer_result_directory(
                returncodes, stopped=False, maintenance=False))
        for returncodes, stopped, maintenance in (
                ([], False, False),
                ([None], False, False),
                ([2], False, False),
                ([0], True, False),
                ([0], False, True)):
            self.assertFalse(gui.should_offer_result_directory(
                returncodes, stopped=stopped, maintenance=maintenance))

    def test_completed_task_can_open_existing_result_directory(self):
        app = object.__new__(gui.DaisyApp)
        app.root = object()
        path = r"C:\Result"
        with (
            patch.object(gui.os.path, "isdir", return_value=True),
            patch.object(gui.messagebox, "askyesno", return_value=True) as ask,
            patch.object(gui.os, "startfile") as start,
        ):
            app._offer_open_result_directory(path)
        ask.assert_called_once()
        start.assert_called_once_with(path)

        with (
            patch.object(gui.os.path, "isdir", return_value=False),
            patch.object(gui.messagebox, "askyesno") as missing_ask,
        ):
            app._offer_open_result_directory(path)
        missing_ask.assert_not_called()

    def test_task_titles_match_menu_names(self):
        for task in gui.TASKS:
            menu_name = task.nav.split(maxsplit=1)[1]
            self.assertEqual(task.title, menu_name)
        for task_key in gui._TASK_MENU_ORDER:
            self.assertEqual(
                gui.task_display_title(task_key),
                gui._TASK_TOOLBAR_LABELS[task_key],
            )
            self.assertEqual(len(gui.task_display_title(task_key)), 6)
        self.assertEqual(
            gui.task_display_title(gui._PROJECT_SELF_TEST_KEY),
            "DAISY功能自检",
        )

    def test_final_task_numbering_and_menu_order(self):
        self.assertEqual(
            [gui.TASK_BY_KEY[key].nav for key in gui._TASK_MENU_ORDER],
            [
                "ENV-01  运行环境检测",
                "DBS-11  完整档案扫描",
                "DBS-12  快速档案扫描",
                "DBS-21  快照变更分析",
                "DBS-31  内容哈希核验",
                "DBS-32  文件结构核验",
                "DBS-41  结果报告导出",
                "STG-11  硬盘信息登记",
            ],
        )
        expected = (
            ("env-check", "env_check", "ENV-01  运行环境检测",
             "Script_DAISY_Module_ENV_01_Env_Check"),
            ("scan", "full_scan", "DBS-11  完整档案扫描",
             "Script_DAISY_Module_DBS_10_Scan"),
            ("scan", "quick_scan", "DBS-12  快速档案扫描",
             "Script_DAISY_Module_DBS_10_Scan"),
            ("diff", "diff", "DBS-21  快照变更分析",
             "Script_DAISY_Module_DBS_21_Diff"),
            ("check-hash", "check_hash", "DBS-31  内容哈希核验",
             "Script_DAISY_Module_DBS_31_Check_Hash"),
            ("check-format", "check_format", "DBS-32  文件结构核验",
             "Script_DAISY_Module_DBS_32_Check_Format"),
            ("export-report", "export_report", "DBS-41  结果报告导出",
             "Script_DAISY_Module_DBS_41_Export_Report"),
            ("storage-collect", "storage_collect", "STG-11  硬盘信息登记",
             "Script_DAISY_Module_STG_11_Collect"),
        )
        for command, task_key, nav, module in expected:
            task = gui.TASK_BY_KEY[task_key]
            self.assertEqual(task.command, command)
            self.assertEqual(task.nav, nav)
            self.assertEqual(entry.COMMANDS[command][0], module)
            module_path = os.path.join(_MODULE, module + ".py")
            self.assertTrue(os.path.isfile(module_path))
            with open(module_path, encoding="utf-8") as handle:
                self.assertIn(" ".join(nav.split()), handle.read())
        scan_module = "Script_DAISY_Module_DBS_10_Scan"
        self.assertEqual(entry.COMMANDS["scan"][0], scan_module)
        self.assertTrue(os.path.isfile(os.path.join(
            _MODULE, scan_module + ".py")))
        verify_module = "Script_DAISY_Module_DBS_30_Verify"
        self.assertEqual(entry.COMMANDS["verify"][0], verify_module)
        self.assertTrue(os.path.isfile(os.path.join(
            _MODULE, verify_module + ".py")))
        self.assertEqual(len(gui._TASK_MENU_ORDER), 8)
        self.assertEqual(
            sorted(
                name for name in os.listdir(_MODULE)
                if name.startswith("Script_DAISY_Module_")
                and name.endswith(".py")
            ),
            sorted([
                *(module + ".py" for module in {
                    *(item[-1] for item in expected),
                    "Script_DAISY_Module_DBS_11_Full_Scan",
                    "Script_DAISY_Module_DBS_12_Quick_Scan",
                    verify_module,
                }),
            ]),
        )
        self.assertEqual(
            gui.TASK_BY_KEY[gui._PROJECT_SELF_TEST_KEY].nav,
            "DBS-91  DAISY功能自检",
        )
        self.assertNotIn(gui._PROJECT_SELF_TEST_KEY, gui._TASK_MENU_ORDER)
        self.assertEqual(
            entry.COMMANDS["storage-list"][0],
            "Script_DAISY_Module_STG_11_Collect",
        )
        self.assertNotIn("storage-" + "verify", entry.COMMANDS)
        self.assertNotIn("storage_" + "verify", gui.TASK_BY_KEY)
        self.assertNotIn("migrate-naming", entry.COMMANDS)

    def test_stg_gui_module_and_internal_detection_require_admin(self):
        self.assertEqual(
            gui._STG_ADMIN_TASKS,
            {"storage_list", "storage_collect"},
        )
        for task_key in gui._STG_ADMIN_TASKS:
            self.assertIn(
                "管理员权限", gui.TASK_BY_KEY[task_key].description)
        self.assertEqual(gui._TASK_MENU_SECTIONS[-1][1], ("storage_collect",))
        for task_key in gui._STG_ADMIN_TASKS:
            app = object.__new__(gui.DaisyApp)
            app.root = object()
            app.task = gui.TASK_BY_KEY[task_key]
            app.is_administrator = False
            with patch.object(
                    gui.messagebox, "askyesno", return_value=False) as ask:
                self.assertFalse(app._confirmation({}))
            self.assertIn("顶部管理员模式开关", ask.call_args.args[1])

    def test_validation_tasks_require_current_root(self):
        for task_key in ("check_hash", "check_format"):
            root_field = next(
                spec for spec in gui.TASK_BY_KEY[task_key].fields
                if spec.key == "root_map")
            self.assertTrue(root_field.required)
            issues = gui.validate_values(
                task_key, {"snapshot": __file__, "root_map": ""})
            self.assertIn("请填写“档案根目录”。", issues)

    def test_validation_task_args_include_current_root(self):
        with tempfile.TemporaryDirectory() as current_root:
            expected_root = os.path.abspath(current_root)
            for task_key in ("check_hash", "check_format"):
                args = gui.build_tool_args(
                    task_key,
                    {"snapshot": __file__, "root_map": current_root},
                )
                root_index = args.index("--root")
                self.assertEqual(args[root_index + 1], expected_root)

                mapped_args = gui.build_tool_args(
                    task_key,
                    {
                        "snapshot": __file__,
                        "root_map": f"archive={current_root}",
                    },
                )
                mapped_root_index = mapped_args.index("--root")
                self.assertEqual(
                    mapped_args[mapped_root_index + 1],
                    f"archive={expected_root}",
                )

    def test_project_identity_is_visible_and_canonical(self):
        self.assertEqual(core.PROJECT_NAME, "DAISY")
        self.assertEqual(core.SCANNER_VERSION, "1.5.1")
        self.assertEqual(core.SCHEMA_VERSION, 3)
        self.assertEqual(core.READABLE_SCHEMA_VERSIONS, frozenset({3}))
        self.assertEqual(core.MIN_READER_VERSION, "1.4.1")
        self.assertEqual(
            core.PROJECT_FULL_NAME,
            "Database for Archive Integrity by Suzuran Ye",
        )
        self.assertEqual(core.PROJECT_AUTHOR, "Suzuran Ye")
        self.assertEqual(
            gui._PROJECT_CONTACT,
            "151104858+SuzuranYe@users.noreply.github.com",
        )
        self.assertEqual(
            gui._PROJECT_GITHUB_URL,
            "https://github.com/SuzuranYe/DAISY",
        )
        title = gui.project_window_title()
        for token in (
                core.PROJECT_NAME, core.PROJECT_FULL_NAME,
                core.PROJECT_AUTHOR, core.SCANNER_VERSION):
            self.assertIn(token, title)
        self.assertIn(f"Author: {core.PROJECT_AUTHOR}", title)
        about = gui.about_message()
        for token in (
                "环境：", "数据：", "硬盘：",
                f"DBS SQLite schema：{core.SCHEMA_VERSION}",
                f"DBS 元数据 profile：{gui.metadata.PROFILE_VERSION}",
                f"STG 归档 schema：{gui.storage_core.ARCHIVE_SCHEMA_VERSION}",
                f"DBS 完整快照最低读取器：v{core.MIN_READER_VERSION}",
                "未完成 partial：仅允许相同生成器版本",
                "DBS 与 STG 数据模型彼此独立"):
            self.assertIn(token, about)
        self.assertIn(gui._PROJECT_CONTACT, about)
        self.assertIn(gui._PROJECT_CONTACT, gui.contact_message())

    def test_gui_stream_parser_handles_chunked_events(self):
        prefix = "@@DAISY_GUI@@"
        pending, events = gui.parse_gui_stream(
            "", "普通日志\n" + prefix
            + '{"event":"progress_start","stage_idx":2,"stage_total":6}\n半')
        self.assertEqual(pending, "半")
        self.assertEqual(events[0], ("output", "普通日志\n"))
        self.assertEqual(events[1][0], "gui_event")
        self.assertEqual(events[1][1]["stage_idx"], 2)
        pending, events = gui.parse_gui_stream(
            pending, "行日志\n", final=True)
        self.assertEqual(pending, "")
        self.assertEqual(events, [("output", "半行日志\n")])

    def test_progress_helpers(self):
        self.assertEqual(gui.progress_fraction(1, 4), 25.0)
        self.assertEqual(gui.progress_fraction(9, 4), 100.0)
        self.assertIsNone(gui.progress_fraction(1, 0))
        self.assertEqual(gui.queue_progress_fraction(0, 3), 0.0)
        self.assertAlmostEqual(
            gui.queue_progress_fraction(0, 3, 0.5), 100 / 6)
        self.assertAlmostEqual(
            gui.queue_progress_fraction(1, 3, 1), 200 / 3)
        self.assertEqual(gui.queue_progress_fraction(5, 3, 1), 100.0)
        self.assertEqual(gui.queue_progress_fraction("bad", 3), 0.0)
        detail, fraction = gui.progress_detail({
            "done": 2, "total": 4, "bytes_done": 1024,
            "bytes_total": 4096, "elapsed": 5, "eta": 10,
            "errors": 1,
        })
        self.assertEqual(fraction, 25.0)
        for token in ("2/4", "ETA", "错误 1"):
            self.assertIn(token, detail)

    def test_progress_target_uses_full_current_root_for_single_and_queue(self):
        first = r"E:\Archive Project\Original Files"
        second = r"F:\Second Root\Media"
        single = gui.RunJob("Archive", {"roots": f"Archive={first}"})
        combined = gui.RunJob(
            "合并 2 个目录",
            {"roots": f"Archive={first}\nMedia={second}"},
        )
        self.assertEqual(
            gui.run_job_target_text("full_scan", single), first)
        combined_text = gui.run_job_target_text("quick_scan", combined)
        self.assertEqual(combined_text, f"{first}；{second}")
        self.assertNotIn("…", combined_text)
        self.assertEqual(
            gui.run_job_target_text(
                "storage_collect",
                gui.RunJob("硬盘信息登记", {"disk_number": "3"}),
            ),
            "PhysicalDrive3",
        )

    def test_session_tool_cache_precedence_and_quick_isolation(self):
        cache = {
            "exiftool": {"path": r"C:\Tools\exiftool.exe", "verified": True},
            "ffprobe": {"path": r"C:\Cached\ffprobe.exe", "verified": True},
            "powershell": {"path": r"C:\Windows\pwsh.exe", "verified": True},
            "sevenzip": {"path": r"C:\Unverified\7z.exe", "verified": False},
        }
        values = {"ffprobe_path": r"D:\Manual\ffprobe.exe"}
        effective, sources = gui.merge_session_tool_paths(
            "full_scan", values, cache,
            path_exists=lambda path: not path.startswith(r"C:\Missing"))
        self.assertEqual(effective["exiftool_path"],
                         r"C:\Tools\exiftool.exe")
        self.assertEqual(effective["ffprobe_path"],
                         r"D:\Manual\ffprobe.exe")
        self.assertEqual(sources["exiftool"], "session_cache")
        self.assertEqual(sources["ffprobe"], "manual")
        self.assertEqual(sources["sevenzip"], "auto_discovery")
        self.assertEqual(effective["powershell_path"],
                         r"C:\Windows\pwsh.exe")
        self.assertEqual(sources["powershell"], "session_cache")
        menu_effective, menu_sources = gui.merge_session_tool_paths(
            "full_scan", values, cache,
            manual_paths={
                "ffprobe": r"E:\TopMenu\ffprobe.exe",
                "sevenzip": r"E:\TopMenu\7z.exe",
            },
            path_exists=lambda _path: True,
        )
        self.assertEqual(
            menu_effective["ffprobe_path"],
            r"E:\TopMenu\ffprobe.exe",
        )
        self.assertEqual(
            menu_effective["sevenzip_path"], r"E:\TopMenu\7z.exe")
        self.assertEqual(menu_sources["ffprobe"], "manual_menu")
        self.assertEqual(menu_sources["sevenzip"], "manual_menu")
        quick, quick_sources = gui.merge_session_tool_paths(
            "quick_scan", {"roots": "X"}, cache,
            path_exists=lambda _path: True)
        self.assertEqual(quick, {"roots": "X"})
        self.assertEqual(quick_sources, {})
        self.assertEqual(len(cache), 4)  # Quick 不读取也不清空窗口缓存
        checked, check_sources = gui.merge_session_tool_paths(
            "check_hash", {}, cache, path_exists=lambda _path: True)
        self.assertEqual(checked["powershell_path"],
                         r"C:\Windows\pwsh.exe")
        self.assertEqual(check_sources["powershell"], "session_cache")
        self.assertEqual(
            gui.session_tool_cache_summary(
                "full_scan", cache, path_exists=lambda _path: True),
            "已缓存：ExifTool、ffprobe、PowerShell",
        )
        self.assertEqual(
            gui.session_tool_cache_summary(
                "quick_scan", cache, path_exists=lambda _path: True),
            "",
        )
        self.assertEqual(
            gui.session_tool_cache_summary(
                "full_scan", {}, path_exists=lambda _path: True),
            "",
        )

    def test_session_tool_cache_clear_is_complete_and_idempotent(self):
        cache = {
            "exiftool": {"path": r"C:\Tools\exiftool.exe", "verified": True},
            "sevenzip": {"path": r"C:\Tools\7z.exe", "verified": False},
        }
        self.assertEqual(gui.clear_session_tool_cache(cache), 2)
        self.assertEqual(cache, {})
        self.assertEqual(gui.clear_session_tool_cache(cache), 0)

    def test_project_cache_cleanup_is_allowlisted_and_reports_paths(self):
        with tempfile.TemporaryDirectory() as td:
            cache_dirs = (
                os.path.join(td, "__pycache__"),
                os.path.join(td, "Script", ".pytest_cache"),
                os.path.join(td, "Script", "Lib", ".mypy_cache"),
                os.path.join(td, "Script", "Test", ".ruff_cache"),
            )
            for path in cache_dirs:
                os.makedirs(path)
                with open(
                        os.path.join(path, "marker.bin"), "wb") as stream:
                    stream.write(b"cache")
            standalone = os.path.join(td, "Script", "orphan.pyc")
            with open(standalone, "wb") as stream:
                stream.write(b"compiled")
            ordinary = os.path.join(td, "Script", "keep.py")
            with open(ordinary, "w", encoding="utf-8", newline="\n") as stream:
                stream.write("print('keep')\n")
            excluded = os.path.join(td, "Output", "__pycache__")
            os.makedirs(excluded)
            with open(
                    os.path.join(excluded, "keep.pyc"), "wb") as stream:
                stream.write(b"output")

            result = gui.clean_project_caches(td)

            self.assertEqual(len(result.directories), 4)
            self.assertEqual(result.files, (
                os.path.join("Script", "orphan.pyc"),))
            self.assertEqual(result.errors, ())
            for path in cache_dirs:
                self.assertFalse(os.path.exists(path))
            self.assertFalse(os.path.exists(standalone))
            self.assertTrue(os.path.isfile(ordinary))
            self.assertTrue(os.path.isdir(excluded))

    def test_gui_cache_button_restores_cold_start_session_state(self):
        app = object.__new__(gui.DaisyApp)
        app.root = object()
        app.process = None
        app.worker_starting = False
        app.run_jobs = []
        app.detected_tools = {
            "exiftool": {
                "path": r"C:\Tools\exiftool.exe", "verified": True,
            },
        }
        app.manual_tool_paths = {
            "ffprobe": r"C:\Tools\ffprobe.exe",
        }
        app.saved_values = {"full_scan": {"roots": r"E:\Archive"}}
        app.storage_disk_choices = (("PhysicalDrive3", "3"),)
        app.storage_disk_options = (object(),)
        app.environment_missing_names = ("sevenzip",)
        app.missing_installable_tools = ("sevenzip",)
        app.mini_mode = False
        calls = []
        app._set_task_toolbar_expanded = (
            lambda value: calls.append(("toolbar", value)))
        app._set_settings_expanded = (
            lambda value: calls.append(("settings", value)))
        app._set_progress_expanded = (
            lambda value: calls.append(("progress", value)))
        app._set_log_expanded = (
            lambda value: calls.append(("log", value)))
        app._set_command_preview_expanded = (
            lambda value: calls.append(("preview", value)))
        app._clear_log = lambda: calls.append(("clear_log", True))
        app._refresh_tool_path_menu_labels = (
            lambda: calls.append(("tool_menu", True)))
        app._select_task = (
            lambda key, save_current=False:
            calls.append(("task", key, save_current)))
        app._set_status = (
            lambda text, colour=None: calls.append(("status", text)))
        cleanup = gui.ProjectCacheCleanup(
            directories=(os.path.join("Script", "__pycache__"),),
            files=(os.path.join("Script", "orphan.pyc"),),
            errors=(),
        )

        with (
            patch.object(gui, "clean_project_caches", return_value=cleanup),
            patch.object(gui.messagebox, "askyesno", return_value=True),
            patch.object(gui.messagebox, "showinfo") as shown,
        ):
            app._clear_tool_cache()

        self.assertEqual(app.detected_tools, {})
        self.assertEqual(app.manual_tool_paths, {})
        self.assertEqual(app.saved_values, {})
        self.assertEqual(app.storage_disk_choices, ())
        self.assertEqual(app.storage_disk_options, ())
        self.assertEqual(app.environment_missing_names, ())
        self.assertEqual(app.missing_installable_tools, ())
        self.assertIn(("toolbar", True), calls)
        self.assertIn(("settings", True), calls)
        self.assertIn(("progress", False), calls)
        self.assertIn(("log", False), calls)
        self.assertIn(("preview", False), calls)
        self.assertIn(("clear_log", True), calls)
        self.assertIn(("task", "env_check", False), calls)
        self.assertIn(("status", "就绪"), calls)
        shown.assert_called_once()
        self.assertIn("已清理 4 项可重建缓存", shown.call_args.args[1])

    def test_multi_root_default_separates_and_preserves_order(self):
        root_specs = [r"Alpha=E:\Archive A", r"Beta=F:\Archive B"]
        values = {"roots": "\n".join(root_specs)}
        jobs = gui.build_run_jobs("quick_scan", values)
        self.assertEqual([job.label for job in jobs], ["Alpha", "Beta"])
        self.assertEqual(
            [job.values["roots"] for job in jobs], root_specs)
        for job, root_spec in zip(jobs, root_specs):
            args = gui.build_tool_args("quick_scan", job.values)
            self.assertEqual(args.count("--root"), 1)
            self.assertIn(root_spec, args)
        self.assertEqual(len(gui.preview_commands("quick_scan", values)), 2)
        self.assertTrue(
            gui.preview_command("quick_scan", values).startswith("队列 2 项"))

    def test_multi_root_combined_keeps_one_job(self):
        root_specs = [r"Alpha=E:\Archive A", r"Beta=F:\Archive B"]
        values = {
            "roots": "\n".join(root_specs),
            "root_batch_mode": gui._ROOT_BATCH_COMBINED,
        }
        jobs = gui.build_run_jobs("full_scan", values)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].label, "合并 2 个目录")
        args = gui.build_tool_args("full_scan", jobs[0].values)
        self.assertEqual(args.count("--root"), 2)
        for root_spec in root_specs:
            self.assertIn(root_spec, args)

    def test_start_confirmation_lists_full_scan_roots_and_duration(self):
        roots = r"Alpha=E:\Archive A" + "\n" + r"Beta=F:\Archive B"
        separate = gui.root_confirmation_text(
            "full_scan", {"roots": roots})
        self.assertIn(
            "将对以下文件夹分别运行完整档案扫描并分别生成数据库：",
            separate,
        )
        self.assertIn(os.path.abspath(r"E:\Archive A"), separate)
        self.assertIn(os.path.abspath(r"F:\Archive B"), separate)

        combined = gui.root_confirmation_text(
            "full_scan",
            {
                "roots": roots,
                "root_batch_mode": gui._ROOT_BATCH_COMBINED,
            },
        )
        self.assertIn("共同生成一个数据库", combined)
        self.assertEqual(
            gui.root_confirmation_text(
                "full_scan", {"start_mode": "resume", "roots": roots}),
            "",
        )

        app = object.__new__(gui.DaisyApp)
        app.root = object()
        app.task = gui.TASK_BY_KEY["full_scan"]
        app.is_administrator = False
        values = gui._task_values(app.task, {"roots": roots})
        with patch.object(
                gui.messagebox, "askyesno", return_value=False) as ask:
            self.assertFalse(app._confirmation(values, 2))
        prompt = ask.call_args.args[1]
        self.assertIn(separate, prompt)
        self.assertIn("可能持续几小时到几天", prompt)

    def test_multi_root_validation_enforces_limit_and_uniqueness(self):
        with tempfile.TemporaryDirectory() as td:
            paths = []
            for index in range(10):
                path = os.path.join(td, f"Root_{index}")
                os.makedirs(path)
                paths.append(path)
            self.assertEqual(
                gui.validate_values(
                    "quick_scan", {"roots": "\n".join(paths[:9])}),
                [],
            )
            limit_issues = gui.validate_values(
                "quick_scan", {"roots": "\n".join(paths)})
            self.assertTrue(any("最多只能添加 9 个" in issue
                                for issue in limit_issues))
            duplicate_issues = gui.validate_values(
                "quick_scan", {"roots": f"{paths[0]}\n{paths[0]}"})
            self.assertTrue(any("根目录重复" in issue
                                for issue in duplicate_issues))
            label_issues = gui.validate_values(
                "quick_scan",
                {
                    "roots": f"Same={paths[0]}\nSame={paths[1]}",
                    "root_batch_mode": gui._ROOT_BATCH_COMBINED,
                },
            )
            self.assertTrue(any("根标签不能重复" in issue
                                for issue in label_issues))
            malformed = gui.validate_values(
                "quick_scan", {"roots": "Broken="})
            self.assertTrue(any("应为 label=路径" in issue
                                for issue in malformed))

    def test_full_resume_is_never_split_into_a_directory_queue(self):
        jobs = gui.build_run_jobs(
            "full_scan",
            {
                "start_mode": "resume",
                "resume": r"E:\Runs\A.partial.sqlite",
                "roots": r"A=E:\Archive" + "\n" + r"B=F:\Archive",
            },
        )
        self.assertEqual(len(jobs), 1)
        self.assertNotIn(
            "--root", gui.build_tool_args("full_scan", jobs[0].values))


class TestStructuredProgress(unittest.TestCase):
    def test_progress_emits_machine_readable_events_in_gui_mode(self):
        output = io.StringIO()
        with patch.dict(os.environ, {"DAISY_GUI_PROGRESS": "1"}), \
                patch("sys.stdout", output):
            progress = core.Progress(2, 6, "枚举", quiet=True)
            progress.update(1, 2, bytes_done=3, bytes_total=6)
            progress.finish("2 项")
        records = []
        for line in output.getvalue().splitlines():
            self.assertTrue(line.startswith(core.GUI_EVENT_PREFIX))
            records.append(json.loads(line[len(core.GUI_EVENT_PREFIX):]))
        self.assertEqual(
            [record["event"] for record in records],
            ["progress_start", "progress_update", "progress_finish"])
        self.assertEqual(records[1]["stage_idx"], 2)
        self.assertEqual(records[1]["bytes_total"], 6)


class TestPathKey(unittest.TestCase):
    def test_nfc_nfd_equivalence(self):
        nfc = "café\\a.txt"          # é 预组合
        nfd = "café\\a.txt"         # e + 组合重音
        self.assertEqual(core.make_path_key(nfc), core.make_path_key(nfd))

    def test_casefold_eszett(self):
        self.assertEqual(core.make_path_key("straße.txt"),
                         core.make_path_key("STRASSE.txt"))

    def test_separator_and_case(self):
        self.assertEqual(core.make_path_key("A\\B\\C.CR3"), "a/b/c.cr3")

    def test_chinese_passthrough(self):
        self.assertEqual(core.make_path_key("素材\\春.CR3"), "素材/春.cr3")


class TestTimestamps(unittest.TestCase):
    def test_epoch_zero(self):
        self.assertEqual(core.ns_to_utc_iso(0), "1970-01-01T00:00:00.0000000Z")

    def test_100ns_truncation(self):
        # 123 ns 截断到 100ns 粒度 → 0000001
        self.assertEqual(core.ns_to_utc_iso(1_000_000_123),
                         "1970-01-01T00:00:01.0000001Z")

    def test_end_of_day(self):
        ns = 86_400 * 10**9 - 100
        self.assertEqual(core.ns_to_utc_iso(ns), "1970-01-01T23:59:59.9999999Z")

    def test_local_offset_is_int_minutes(self):
        off = core.local_utc_offset_min()
        self.assertIsInstance(off, int)
        self.assertTrue(-14 * 60 <= off <= 14 * 60)


class TestFileId(unittest.TestCase):
    def test_hex_lowercase_no_prefix(self):
        self.assertEqual(core.id_hex(0xC04A578C4A577E5A), "c04a578c4a577e5a")

    def test_zero_is_none(self):
        self.assertIsNone(core.id_hex(0))


class TestMediaKind(unittest.TestCase):
    CASES = {
        "cr3": "photo_raw", "CR2": "photo_raw", "dng": "photo_raw",
        "jpg": "photo_jpeg", "JPEG": "photo_jpeg", "jfif": "photo_jpeg",
        "gif": "image_gif", "GIF": "image_gif",
        "psd": "photo_working", "psb": "photo_working", "tif": "photo_working",
        "tiff": "photo_working", "png": "photo_working",
        "mp4": "video_mp4", "MOV": "video_mp4", "lrf": "video_mp4",
        "crm": "video_crm", "aac": "audio",
        "zip": "archive", "7z": "archive", "rar": "archive",
        "tar": "archive", "gz": "archive", "bz2": "archive", "xz": "archive",
        "pdf": "document", "doc": "document", "docx": "document",
        "xlsx": "document", "pptx": "document",
        "xls": "other", "ppt": "other",
        "txt": "other", "md": "other", "csv": "other", "srt": "other", "": "other",
    }

    def test_mapping(self):
        for ext, kind in self.CASES.items():
            self.assertEqual(core.media_kind_for(ext), kind, f"ext={ext!r}")


class TestDdl(unittest.TestCase):
    # 文档逐字符比对守卫已退役：DDL 权威即代码（Spec 只写语义），
    # 本类保留可执行性与约束层守卫。
    def test_executes_and_enforces_constraints(self):
        con = sqlite3.connect(":memory:")
        self.addCleanup(con.close)
        con.executescript(core.SNAPSHOT_DDL)
        tables = {
            row[0] for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")
        }
        self.assertNotIn("block_hashes", tables)
        self.assertIn("video_gps_points", tables)
        self.assertIn("metadata_diagnostics", tables)
        with self.assertRaises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO snapshot_info"
                " (id,snapshot_uuid,schema_version,path_key_rule,scan_status,"
                " hash_coverage,started_at_utc,local_utc_offset_min,hostname,"
                " os_version,scanner_version,config_json)"
                " VALUES (1,'u',1,1,'running','partial','t',0,'h','w','1','{}')")
        con.execute("INSERT INTO roots (root_id,root_path,root_label) VALUES (1,'D:/A','a')")
        con.execute("INSERT INTO dirs (dir_id,root_id,rel_path,path_key,enum_status,"
                    "observed_at_utc) VALUES (1,1,'','','ok','t')")
        ins = ("INSERT INTO entries (entry_id,root_id,dir_id,rel_path,path_key,name,"
               "extension,media_kind,size_bytes,modified_at_utc,attributes,"
               "observed_at_utc) VALUES ")
        con.execute(ins + "(1,1,1,'café.txt','café.txt','x','txt','other',1,'t',0,'t')")
        # path_key 碰撞行可入库（rel_path 不同）
        con.execute(ins + "(2,1,1,'café.txt','café.txt','x','txt','other',1,'t',0,'t')")
        con.execute(
            "INSERT INTO video_gps_points"
            " (entry_id,point_index,timestamp_seconds,gps_latitude,"
            " gps_longitude,gps_altitude,source,raw_value)"
            " VALUES (1,0,NULL,27.25,111.75,NULL,'ffprobe:test',"
            " '+27.25+111.75/')")
        with self.assertRaises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO video_gps_points"
                " (entry_id,point_index,gps_latitude,gps_longitude,"
                " source,raw_value)"
                " VALUES (1,1,90.1,111.75,'ffprobe:test','bad')")
        with self.assertRaises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO video_gps_points"
                " (entry_id,point_index,timestamp_seconds,gps_latitude,"
                " gps_longitude,source,raw_value)"
                " VALUES (1,2,-0.1,27.25,111.75,'ffprobe:test','bad')")
        with self.assertRaises(sqlite3.IntegrityError):   # 同 rel_path 拦截
            con.execute(ins + "(3,1,1,'café.txt','café.txt','x','txt','other',1,'t',0,'t')")

        with self.assertRaises(sqlite3.IntegrityError):   # valid 无 hash_hex 拦截
            con.execute("INSERT INTO hashes (entry_id,origin,size_bytes,bytes_read,"
                        "status,tool,tool_version) VALUES (1,'computed',1,1,'valid','t','1')")
        with self.assertRaises(sqlite3.IntegrityError):   # reused 溯源不全拦截
            con.execute("INSERT INTO hashes (entry_id,hash_hex,origin,"
                        "source_snapshot_uuid,size_bytes,status,tool,tool_version)"
                        " VALUES (1,'ab','reused','u0',1,'valid','t','1')")

    def test_v141_reader_schema_boundary(self):
        self.assertEqual(core.require_readable_schema_version(3), 3)
        for unsupported in (0, 1, 2, 4, 99):
            with self.assertRaises(core.PreflightError):
                core.require_readable_schema_version(unsupported)


class TestRootSpec(unittest.TestCase):
    def test_label_path_syntax(self):
        label, path = core.parse_root_spec("photos=D:\\Media\\Photos")
        self.assertEqual(label, "photos")
        self.assertTrue(path.endswith("Photos"))

    def test_default_label_is_folder_name(self):
        with tempfile.TemporaryDirectory() as td:
            sub = os.path.join(td, "Archive2024")
            os.makedirs(sub)
            label, path = core.parse_root_spec(sub)
            self.assertEqual(label, "Archive2024")

    def test_drive_root_rejected(self):
        with self.assertRaises(core.PreflightError):
            core.validate_root(os.path.abspath(os.sep))

    def test_single_snapshot_root_accepts_direct_folder(self):
        with tempfile.TemporaryDirectory() as td:
            mapping = core.resolve_current_root_specs(["Archive"], [td])
            self.assertEqual(mapping, {"Archive": os.path.abspath(td)})

    def test_multiple_snapshot_roots_require_complete_label_mapping(self):
        with tempfile.TemporaryDirectory() as td:
            left = os.path.join(td, "left")
            right = os.path.join(td, "right")
            os.makedirs(left)
            os.makedirs(right)
            mapping = core.resolve_current_root_specs(
                ["A", "B"], [f"A={left}", f"B={right}"])
            self.assertEqual(mapping["A"], os.path.abspath(left))
            self.assertEqual(mapping["B"], os.path.abspath(right))
            with self.assertRaisesRegex(
                    core.PreflightError, "只适用于单根快照"):
                core.resolve_current_root_specs(["A", "B"], [left, right])
            with self.assertRaisesRegex(
                    core.PreflightError, "尚未指定"):
                core.resolve_current_root_specs(["A", "B"], [f"A={left}"])
            with self.assertRaisesRegex(
                    core.PreflightError, "不存在"):
                core.resolve_current_root_specs(["A"], [f"X={left}"])


class TestVersionParsing(unittest.TestCase):
    def test_exiftool(self):
        self.assertEqual(core.parse_version_tuple("13.59"), (13, 59))

    def test_ffprobe_banner(self):
        line = "ffprobe version 8.1.1-full_build-www.gyan.dev Copyright (c)"
        self.assertEqual(core.parse_ffprobe_version(line), "8.1.1")

    def test_sevenzip_banner(self):
        line = "7-Zip 26.01 (x64) : Copyright (c) 1999-2026 Igor Pavlov : 2026-04-27"
        self.assertEqual(core.parse_sevenzip_version(line), "26.01")


class _SnapshotFixture(unittest.TestCase):
    """夹具：临时档案树＋partial 快照库。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.arch = os.path.join(self._td.name, "Archive测试")
        os.makedirs(os.path.join(self.arch, "sub 目录"))
        self._write("a.CR3", b"raw-data-1")
        self._write("b.txt", b"hello")
        self._write(os.path.join("sub 目录", "c.MP4"), b"mp4-bytes")
        self.out_dir = os.path.join(self._td.name, "Snapshots")
        os.makedirs(self.out_dir)
        self.partial = os.path.join(self.out_dir, "Scan_test.partial.sqlite")
        self.con = core.create_partial_snapshot(
            self.partial, [("Archive测试", self.arch)], config={"phase": "test"})

    def tearDown(self):
        try:
            self.con.close()
        except sqlite3.ProgrammingError:
            pass
        self._td.cleanup()

    def _write(self, rel, data: bytes):
        p = os.path.join(self.arch, rel)
        with open(p, "wb") as f:
            f.write(data)


class TestEnumeration(_SnapshotFixture):
    def test_full_tree_registered(self):
        stats = core.enumerate_and_reconcile(self.con)
        rows = {r[0]: r for r in self.con.execute(
            "SELECT rel_path, media_kind, size_bytes, volume_serial, file_index_hex"
            " FROM entries")}
        self.assertEqual(set(rows), {"a.CR3", "b.txt", "sub 目录\\c.MP4"})
        self.assertEqual(rows["a.CR3"][1], "photo_raw")
        self.assertEqual(rows["b.txt"][1], "other")
        self.assertEqual(rows["sub 目录\\c.MP4"][1], "video_mp4")
        self.assertEqual(rows["b.txt"][2], 5)
        self.assertIsNotNone(rows["a.CR3"][3])          # NTFS file id 已采集
        self.assertIsNotNone(rows["a.CR3"][4])
        dirs = {r[0]: r[1] for r in self.con.execute(
            "SELECT rel_path, enum_status FROM dirs")}
        self.assertEqual(dirs, {"": "ok", "sub 目录": "ok"})   # 根目录恒有一行
        self.assertEqual(stats["files"], 3)
        root_status = self.con.execute("SELECT enum_status FROM roots").fetchone()[0]
        self.assertEqual(root_status, "ok")

    def test_reconcile_preserves_and_resets(self):
        core.enumerate_and_reconcile(self.con)
        keep_id, = self.con.execute(
            "SELECT entry_id FROM entries WHERE rel_path='b.txt'").fetchone()
        self.con.execute("UPDATE entries SET meta_status='skipped',"
                         " hash_status='skipped'")
        self.con.commit()
        # 变更树：删 a.CR3、新增 d.zip、改写 c.MP4（size 变）、b.txt 不动
        os.remove(os.path.join(self.arch, "a.CR3"))
        self._write("d.zip", b"zip!")
        self._write(os.path.join("sub 目录", "c.MP4"), b"mp4-bytes-changed")
        core.enumerate_and_reconcile(self.con)
        rows = {r[0]: r for r in self.con.execute(
            "SELECT rel_path, entry_id, meta_status, hash_status FROM entries")}
        self.assertEqual(set(rows), {"b.txt", "d.zip", "sub 目录\\c.MP4"})
        self.assertEqual(rows["b.txt"][1], keep_id)              # 未变者保 id
        self.assertEqual(rows["b.txt"][2], "skipped")            # 未变者保状态
        self.assertEqual(rows["sub 目录\\c.MP4"][2], "pending")   # 变者重置
        self.assertEqual(rows["d.zip"][2], "pending")            # 新增为 pending

    def test_rescan_flags_unstable(self):
        core.enumerate_and_reconcile(self.con)
        self.con.execute("UPDATE entries SET meta_status='skipped',"
                         " hash_status='skipped'")
        self.con.commit()
        self._write("b.txt", b"hello-changed")
        changed = core.rescan_check(self.con)
        self.assertEqual(changed, 1)
        st, = self.con.execute(
            "SELECT meta_status FROM entries WHERE rel_path='b.txt'").fetchone()
        self.assertEqual(st, "unstable")


class TestFinalize(_SnapshotFixture):
    def test_finalize_produces_immutable_snapshot(self):
        core.enumerate_and_reconcile(self.con)
        self.con.execute("UPDATE entries SET meta_status='skipped',"
                         " hash_status='skipped'")
        self.con.commit()
        final = core.finalize_snapshot(self.con, self.partial, hash_coverage="none")
        self.assertTrue(os.path.exists(final))
        self.assertFalse(os.path.exists(self.partial))
        self.assertEqual(
            core.filename_sha256_high32(final), core.sha256_file(final)[:8].upper())
        self.assertTrue(core.filename_sha256_high32_matches(final))
        self.assertEqual(os.listdir(self.out_dir), [os.path.basename(final)])
        con2 = sqlite3.connect(final)
        status = con2.execute(
            "SELECT scan_status,database_integrity,has_file_issues,"
            " has_unstable_entries,has_enumeration_gaps,hash_coverage"
            " FROM snapshot_info").fetchone()
        self.assertEqual(status, ("complete", "ok", 0, 0, 0, "none"))
        manifest = json.loads(con2.execute(
            "SELECT manifest_json FROM snapshot_manifest").fetchone()[0])
        self.assertEqual(manifest["schema_version"], 3)
        self.assertEqual(manifest["data_contract"], "daisy-snapshot-v3")
        self.assertEqual(manifest["min_reader_version"], "1.4.1")
        self.assertEqual(manifest["status"], {
            "database_integrity": "ok",
            "scan_status": "complete",
            "has_file_issues": False,
            "has_unstable_entries": False,
            "has_enumeration_gaps": False,
        })
        config = json.loads(con2.execute(
            "SELECT config_json FROM snapshot_info").fetchone()[0])
        self.assertEqual(config["data_contract"], "daisy-snapshot-v3")
        self.assertEqual(config["min_reader_version"], "1.4.1")
        self.assertEqual(manifest["integrity"]["retained_bits"], 32)
        self.assertFalse(manifest["integrity"]["full_digest_retained"])
        self.assertNotIn("snapshot_sha256", manifest)
        self.assertEqual(con2.execute(
            "SELECT event FROM run_events ORDER BY event_seq DESC LIMIT 1"
        ).fetchone()[0], "snapshot_sealed")
        con2.close()

    def test_file_issue_only_adds_report_and_keeps_database_healthy(self):
        core.enumerate_and_reconcile(self.con)
        self.con.execute("UPDATE entries SET meta_status='skipped',"
                         " hash_status='skipped'")
        self.con.execute("UPDATE entries SET meta_status='error'"
                         " WHERE rel_path='a.CR3'")
        self.con.commit()
        final = core.finalize_snapshot(
            self.con, self.partial, hash_coverage="none")
        issue = core.artifact_issue_report_path(final)
        self.assertTrue(os.path.isfile(final))
        self.assertTrue(os.path.isfile(issue))
        con = sqlite3.connect(final)
        state = con.execute(
            "SELECT database_integrity,scan_status,has_file_issues,"
            " has_unstable_entries,has_enumeration_gaps FROM snapshot_info"
        ).fetchone()
        counts = json.loads(con.execute(
            "SELECT counts_json FROM snapshot_info").fetchone()[0])
        con.close()
        self.assertEqual(state, ("ok", "complete", 1, 0, 0))
        self.assertNotIn("abnormal", counts)
        self.assertEqual(counts["has_file_issues"], True)
        with open(issue, encoding="utf-8") as handle:
            report = handle.read()
        self.assertIn("数据库完整性正常", report)
        self.assertIn("源文件或扫描证据问题", report)

    def test_unrecognized_format_stays_in_database_but_not_issues_md(self):
        core.enumerate_and_reconcile(self.con)
        self.con.execute("UPDATE entries SET meta_status='skipped',"
                         " hash_status='skipped'")
        entry_id, = self.con.execute(
            "SELECT entry_id FROM entries WHERE rel_path='b.txt'").fetchone()
        self.con.execute(
            "UPDATE entries SET meta_status='error' WHERE entry_id=?",
            (entry_id,),
        )
        observed = core.now_utc_iso()
        self.con.execute(
            "INSERT INTO metadata_diagnostics"
            " (entry_id,provider,severity,diagnostic_code,field_name,message,"
            " raw_value,observed_at_utc) VALUES (?,?,?,?,?,?,?,?)",
            (entry_id, "exiftool", "error", "exiftool_reported_error",
             "ExifTool:Error", "Unknown file type", None, observed),
        )
        self.con.execute(
            "INSERT INTO errors"
            " (entry_id,stage,error_code,message,occurred_at_utc)"
            " VALUES (?,?,?,?,?)",
            (entry_id, "metadata", "exiftool_reported_error",
             "Unknown file type", observed),
        )
        self.con.commit()

        final = core.finalize_snapshot(
            self.con, self.partial, hash_coverage="none")
        issue = core.artifact_issue_report_path(final)
        self.assertFalse(os.path.exists(issue))
        con = sqlite3.connect(final)
        self.assertEqual(
            con.execute("SELECT COUNT(*) FROM errors").fetchone()[0], 1)
        self.assertEqual(
            con.execute(
                "SELECT COUNT(*) FROM metadata_diagnostics").fetchone()[0],
            1,
        )
        self.assertEqual(
            con.execute(
                "SELECT has_file_issues FROM snapshot_info").fetchone()[0],
            1,
        )
        con.close()

    def test_unrecognized_format_issue_filter_is_deliberately_narrow(self):
        self.assertFalse(core.issue_record_is_visible(
            " EXIFTOOL_REPORTED_ERROR ", " Unknown   file type。 "))
        self.assertFalse(core.issue_record_is_visible(
            "exiftool_reported_error", "Unsupported file type."))
        self.assertTrue(core.issue_record_is_visible(
            "exiftool_reported_error", "File format error"))
        self.assertTrue(core.issue_record_is_visible(
            "metadata_read_failed", "Unknown file type"))

    def test_finalize_rejects_pending(self):
        core.enumerate_and_reconcile(self.con)   # 状态仍为 pending
        with self.assertRaises(core.PreflightError):
            core.finalize_snapshot(self.con, self.partial, hash_coverage="none")


import Script_DAISY_Lib_DBS_02_Meta as meta                              # noqa: E402


class TestTagIndex(unittest.TestCase):
    SAMPLE = {
        "SourceFile": "x",
        "ExifIFD:Main:DateTimeOriginal": {"desc": "d", "id": 1,
                                          "val": "2025:06:24 18:07:52"},
        "ExifIFD:Doc1:DateTimeOriginal": "1999:01:01 00:00:00",   # 应劣后于 Main
        "IFD0:Model": "Canon EOS R5",
        "Canon:Main:CanonModelID": "EOS R5 Mark II",
        "Photoshop:Main:LayerUnicodeNames": ["Base", "Grade"],
        "PDF:Author": "Suzuran Ye",
    }

    def test_main_preferred_over_doc(self):
        idx = meta.build_tag_index(self.SAMPLE)
        self.assertEqual(meta.tval(idx, "ExifIFD:DateTimeOriginal"),
                         "2025:06:24 18:07:52")

    def test_plain_and_dict_values(self):
        idx = meta.build_tag_index(self.SAMPLE)
        self.assertEqual(meta.tval(idx, "IFD0:Model"), "Canon EOS R5")
        self.assertEqual(meta.tval(idx, "Canon:CanonModelID"), "EOS R5 Mark II")

    def test_chain_first_hit(self):
        idx = meta.build_tag_index(self.SAMPLE)
        self.assertEqual(
            meta.tchain(idx, ["IFD0:Model", "Canon:CanonModelID"]), "Canon EOS R5")
        self.assertEqual(
            meta.tchain(idx, ["IFD0:Nope", "Canon:CanonModelID"]),
            "EOS R5 Mark II")

    def test_any_group_lookup_for_documents(self):
        idx = meta.build_tag_index(self.SAMPLE)
        self.assertEqual(meta.tany(idx, "Author"), "Suzuran Ye")

    def test_list_value_json(self):
        idx = meta.build_tag_index(self.SAMPLE)
        import json as _j
        self.assertEqual(_j.loads(meta.as_json_text(
            meta.tval(idx, "Photoshop:LayerUnicodeNames"))), ["Base", "Grade"])


class TestValueParsers(unittest.TestCase):
    def test_first_float(self):
        self.assertEqual(meta.first_float("105.0 mm"), 105.0)
        self.assertEqual(meta.first_float("4"), 4.0)
        self.assertAlmostEqual(meta.first_float("-2/3 EV"), -2 / 3)
        self.assertAlmostEqual(meta.first_float("+4/3"), 4 / 3)
        self.assertIsNone(meta.first_float("1/0"))
        self.assertIsNone(meta.first_float("n/a"))

    def test_first_int(self):
        self.assertEqual(meta.first_int("16"), 16)
        self.assertEqual(meta.first_int("48000 Hz"), 48000)
        self.assertIsNone(meta.first_int(None))

    def test_positive_int_pair(self):
        self.assertEqual(meta.first_positive_int_pair("8192 6144"),
                         (8192, 6144))
        self.assertIsNone(meta.first_positive_int_pair("0 0"))

    def test_gps_dms_to_decimal(self):
        v = meta.gps_decimal("27 deg 16' 43.09\" N")
        self.assertAlmostEqual(v, 27 + 16 / 60 + 43.09 / 3600, places=6)
        v = meta.gps_decimal("111 deg 44' 36.91\" W")
        self.assertAlmostEqual(v, -(111 + 44 / 60 + 36.91 / 3600), places=6)
        self.assertIsNone(meta.gps_decimal(None))

    def test_iso6709_video_location(self):
        self.assertEqual(
            meta.parse_iso6709_location("+27.278636+111.743586/"),
            (27.278636, 111.743586, None))
        self.assertEqual(
            meta.parse_iso6709_location("-27.5-111.25+123.75/"),
            (-27.5, -111.25, 123.75))
        for invalid in (
                "+91.0+111.0/", "+27.0+181.0/",
                "27.0+111.0/", "+27.0+111.0", None):
            self.assertIsNone(meta.parse_iso6709_location(invalid), invalid)

    def test_video_gps_rows_support_multiple_points(self):
        rows = meta.video_gps_rows({
            "format": {
                "tags": {
                    "LOCATION": [
                        "+27.1000+111.2000/",
                        "+27.3000+111.4000+12.5/",
                    ],
                },
            },
        })
        self.assertEqual([r["point_index"] for r in rows], [0, 1])
        self.assertEqual([r["timestamp_seconds"] for r in rows], [None, None])
        self.assertEqual(rows[0]["source"],
                         "ffprobe:format.tags.LOCATION")
        self.assertEqual(
            (rows[1]["gps_latitude"], rows[1]["gps_longitude"],
             rows[1]["gps_altitude"]),
            (27.3, 111.4, 12.5))
        self.assertEqual(rows[1]["raw_value"],
                         "+27.3000+111.4000+12.5/")

    def test_offset_minutes(self):
        self.assertEqual(meta.offset_minutes("+08:00"), 480)
        self.assertEqual(meta.offset_minutes("-05:30"), -330)
        self.assertIsNone(meta.offset_minutes("Z没有"))

    def test_capture_time_utc(self):
        self.assertEqual(
            meta.capture_utc("2025:06:24 18:07:52", 480),
            "2025-06-24T10:07:52Z")
        self.assertEqual(
            meta.capture_utc("2025:02:21 12:20:39.34+08:00", 480),
            "2025-02-21T04:20:39.34Z")
        self.assertIsNone(meta.capture_utc("2025:06:24 18:07:52", None))

    def test_explicit_iso_utc_preserves_fraction(self):
        self.assertEqual(
            meta.normalize_explicit_utc("2025-02-09T13:19:59.000000Z"),
            "2025-02-09T13:19:59.000000Z")
        self.assertEqual(
            meta.normalize_explicit_utc("2025-02-09T22:19:58.56+09:00"),
            "2025-02-09T13:19:58.56Z")

    def test_trailing_offset_in_value(self):
        self.assertEqual(meta.offset_minutes_from_value(
            "2025:08:16 08:32:50+08:00"), 480)
        self.assertIsNone(meta.offset_minutes_from_value("2025:08:16 08:32:50"))


class TestPayload(unittest.TestCase):
    def test_canonical_stable_and_hash(self):
        a = meta.make_payload({"b": 1, "a": {"y": 2, "x": 1}})
        b = meta.make_payload({"a": {"x": 1, "y": 2}, "b": 1})
        self.assertEqual(a.sha256, b.sha256)
        self.assertEqual(a.uncompressed_bytes, b.uncompressed_bytes)
        import zlib as _z
        self.assertEqual(_z.decompress(a.zlib_blob), _z.decompress(b.zlib_blob))


class TestExifToolWorker(unittest.TestCase):
    def test_restart_and_close_release_pipes(self):
        worker = meta.ExifToolWorker(core.discover_tool("exiftool", None))
        first = worker._proc
        try:
            worker.restart()
            self.assertTrue(first.stdin.closed)
            self.assertTrue(first.stdout.closed)
            second = worker._proc
        finally:
            worker.close()
        self.assertIsNone(worker._proc)
        self.assertTrue(second.stdin.closed)
        self.assertTrue(second.stdout.closed)


class TestExifToolTimeoutPolicy(unittest.TestCase):
    def test_timeout_scales_by_ceil_nine_gib_steps(self):
        step = 9 * 1024 ** 3
        self.assertEqual(meta.exiftool_timeout_for_size(0), 90)
        self.assertEqual(meta.exiftool_timeout_for_size(step - 1), 90)
        self.assertEqual(meta.exiftool_timeout_for_size(step), 90)
        self.assertEqual(meta.exiftool_timeout_for_size(step + 1), 180)
        self.assertEqual(meta.exiftool_timeout_for_size(2 * step), 180)
        self.assertEqual(meta.exiftool_timeout_for_size(2 * step + 1), 270)
        self.assertEqual(meta.exiftool_timeout_for_size(41_792_298_800), 450)
        self.assertEqual(meta.exiftool_timeout_for_size(62_431_687_520), 630)

    def test_policy_is_manifest_serializable_and_validated(self):
        policy = meta.exiftool_timeout_policy()
        self.assertEqual(json.loads(json.dumps(policy)), policy)
        with self.assertRaises(ValueError):
            meta.exiftool_timeout_for_size(1, {"minimum_seconds": 90})


class TestArchiveBackend(unittest.TestCase):
    def test_zip_summary(self):
        import zipfile as _zf
        with tempfile.TemporaryDirectory() as td:
            zp = os.path.join(td, "t.zip")
            with _zf.ZipFile(zp, "w", _zf.ZIP_DEFLATED) as z:
                z.writestr("a.txt", "hello" * 100)
                z.writestr("b/c.bin", b"\x00" * 1000)
            s = meta.zip_summary(zp)
            self.assertEqual(s["member_count"], 2)
            self.assertEqual(s["uncompressed_bytes"], 500 + 1000)
            self.assertGreater(s["compressed_bytes"], 0)
            self.assertEqual(s["has_encrypted"], 0)

    def test_sevenzip_slt_parser(self):
        text = ("7-Zip 26.01 (x64)\n\n--\nPath = t.7z\n\n----------\n"
                "Path = a.txt\nSize = 500\nPacked Size = 20\nEncrypted = -\n\n"
                "Path = b\\c.bin\nSize = 1000\nPacked Size = 30\nEncrypted = +\n\n")
        s = meta.parse_7z_slt(text)
        self.assertEqual(s["member_count"], 2)
        self.assertEqual(s["uncompressed_bytes"], 1500)
        self.assertEqual(s["compressed_bytes"], 50)
        self.assertEqual(s["has_encrypted"], 1)


class TestArchiveMembers(unittest.TestCase):
    def test_zip_members_full_detail(self):
        import zipfile as _zf
        import zlib as _z
        with tempfile.TemporaryDirectory() as td:
            zp = os.path.join(td, "t.zip")
            data_a = b"hello-alpha" * 50
            data_b = b"\x00" * 2000
            with _zf.ZipFile(zp, "w") as z:
                zi = _zf.ZipInfo("stored.bin", (2024, 5, 6, 7, 8, 10))
                z.writestr(zi, data_a, compress_type=_zf.ZIP_STORED)
                z.writestr("目录/deflated.bin", data_b,
                           compress_type=_zf.ZIP_DEFLATED)
            s = meta.zip_summary(zp)
            m = s["members"]
            self.assertEqual([x["member_index"] for x in m], [0, 1])
            self.assertEqual(m[0]["member_path"], "stored.bin")
            self.assertEqual(m[0]["method"], "store")
            self.assertEqual(m[0]["crc32_hex"], f"{_z.crc32(data_a):08x}")
            self.assertEqual(m[0]["size_bytes"], len(data_a))
            self.assertEqual(m[0]["packed_bytes"], len(data_a))
            self.assertEqual(m[0]["modified_raw"], "2024-05-06 07:08:10")
            self.assertEqual(m[1]["member_path"], "目录/deflated.bin")
            self.assertEqual(m[1]["method"], "deflate")
            self.assertLess(m[1]["packed_bytes"], 2000)
            for x in m:
                self.assertIsInstance(x["flag_bits"], int)
                self.assertIsInstance(x["header_offset"], int)
                self.assertIsInstance(x["create_version"], int)
                self.assertIsInstance(x["extract_version"], int)
                self.assertTrue(x["host_os"])
                self.assertEqual(x["encrypted"], 0)
                self.assertEqual(x["is_dir"], 0)
            self.assertGreater(m[1]["header_offset"], m[0]["header_offset"])
            self.assertEqual(s["member_count"], 2)     # 聚合摘要不受影响

    def test_7z_slt_members(self):
        text = ("7-Zip 26.01 (x64)\n\n--\nPath = t.7z\n\n----------\n"
                "Path = a.txt\nSize = 500\nPacked Size = 20\n"
                "Modified = 2024-05-06 07:08:10\nAttributes = A\n"
                "CRC = 1A2B3C4D\nEncrypted = -\nMethod = LZMA2:19\n\n"
                "Path = b\\sub\nSize = 0\nPacked Size = 0\n"
                "Modified = 2024-05-06 07:08:11\nAttributes = D\n"
                "CRC = \nEncrypted = +\nMethod = LZMA2:19 7zAES\n\n")
        s = meta.parse_7z_slt(text)
        m = s["members"]
        self.assertEqual(len(m), 2)
        self.assertEqual(m[0]["member_path"], "a.txt")
        self.assertEqual(m[0]["crc32_hex"], "1a2b3c4d")
        self.assertEqual(m[0]["method"], "LZMA2:19")
        self.assertEqual(m[0]["encrypted"], 0)
        self.assertEqual(m[0]["is_dir"], 0)
        self.assertEqual(m[0]["modified_raw"], "2024-05-06 07:08:10")
        self.assertEqual(m[1]["is_dir"], 1)            # Attributes = D
        self.assertEqual(m[1]["encrypted"], 1)
        self.assertIsNone(m[1]["crc32_hex"])
        self.assertEqual(m[1]["method"], "LZMA2:19 7zAES")

    def test_members_registered_via_metadata_stage(self):
        import zipfile as _zf
        import zlib as _z
        with tempfile.TemporaryDirectory() as td:
            arch = os.path.join(td, "Arch")
            os.makedirs(arch)
            data = b"member-payload" * 10
            with _zf.ZipFile(os.path.join(arch, "arch.zip"), "w",
                             _zf.ZIP_DEFLATED) as z:
                z.writestr("in ner/文件.txt", data)
            partial = os.path.join(td, "Scan_t.partial.sqlite")
            con = core.create_partial_snapshot(partial, [("A", arch)],
                                               config={"phase": "test"})
            core.enumerate_and_reconcile(con)
            tools = {k: {"path": core.discover_tool(k, None), "version": "t"}
                     for k in ("exiftool", "ffprobe", "sevenzip")}
            meta.process_metadata_stage(
                con, tools, retain_original_metadata=False)
            st, = con.execute("SELECT meta_status FROM entries"
                              " WHERE rel_path='arch.zip'").fetchone()
            self.assertEqual(st, "done")
            rows = con.execute(
                "SELECT m.member_index, m.member_path, m.crc32_hex, m.method,"
                " m.size_bytes FROM archive_members m JOIN entries e"
                " ON e.entry_id = m.entry_id"
                " WHERE e.rel_path='arch.zip'").fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][1], "in ner/文件.txt")
            self.assertEqual(rows[0][2], f"{_z.crc32(data):08x}")
            self.assertEqual(rows[0][3], "deflate")
            self.assertEqual(rows[0][4], len(data))
            con.close()


class TestRawBackendCoverage(unittest.TestCase):
    class _ExifWorker:
        extracted = []

        def __init__(self, _path):
            type(self).extracted = []

        def extract(self, file_path, photo_profile=False, timeout=None):
            type(self).extracted.append((file_path, photo_profile, timeout))
            return {
                "SourceFile": file_path,
                "File:Main:FileType": {
                    "desc": "File Type",
                    "val": os.path.splitext(file_path)[1].lstrip(".").upper(),
                },
            }

        def close(self):
            pass

    def _snapshot(self, td):
        arch = os.path.join(td, "Arch")
        os.makedirs(arch)
        tt.write(arch, "opaque.bin", b"opaque")
        tt.write(arch, "motion.gif", b"GIF89a")
        tt.write(arch, "legacy.doc", b"not-a-real-doc")
        tt.write(arch, "photo.jfif", b"not-a-real-jfif")
        tt.write(arch, "still.cr3", b"not-a-real-cr3")
        import zipfile as _zf
        with _zf.ZipFile(os.path.join(arch, "pack.zip"), "w") as z:
            z.writestr("member.txt", b"member")
        partial = os.path.join(td, "Scan_t.partial.sqlite")
        con = core.create_partial_snapshot(
            partial, [("A", arch)], config={"phase": "test"})
        core.enumerate_and_reconcile(con)
        return con

    @staticmethod
    def _gif_ffprobe(_path, file_path, timeout=None):
        if not file_path.lower().endswith(".gif"):
            raise AssertionError("非视频、非音频且非 GIF 不应调用 ffprobe")
        return {
            "format": {"format_name": "gif", "duration": "0.2"},
            "streams": [{"codec_type": "video", "codec_name": "gif"}],
        }

    def test_raw_enabled_captures_all_exiftool_and_only_gif_ffprobe(self):
        with tempfile.TemporaryDirectory() as td:
            con = self._snapshot(td)
            tools = {
                "exiftool": {"path": "unused", "version": "13.test"},
                "ffprobe": {"path": "unused", "version": "8.test"},
                "sevenzip": {"path": "unused", "version": "24.test"},
            }
            with patch.object(
                    meta, "ExifToolWorker", self._ExifWorker), \
                    patch.object(
                        meta, "ffprobe_full",
                        side_effect=self._gif_ffprobe) as ffprobe_mock:
                stats = meta.process_metadata_stage(con, tools)
            self.assertEqual(meta.PROFILE_VERSION, 7)
            self.assertEqual(stats["done"], 6)
            self.assertEqual(stats["ffprobe_payloads"], 1)
            self.assertEqual(stats["ffprobe_optional_unreadable"], 0)
            self.assertEqual(stats["ffprobe_optional_timeouts"], 0)
            self.assertEqual(ffprobe_mock.call_count, 1)
            self.assertTrue(
                ffprobe_mock.call_args.args[1].lower().endswith(".gif"))
            rows = con.execute(
                "SELECT e.rel_path,e.media_kind,e.meta_status,p.provider,"
                " p.profile_version FROM entries e JOIN raw_payloads p"
                " ON p.entry_id=e.entry_id"
                " ORDER BY e.rel_path,p.provider").fetchall()
            self.assertEqual(
                rows,
                [("legacy.doc", "document", "done", "exiftool",
                  meta.PROFILE_VERSION),
                 ("motion.gif", "image_gif", "done", "exiftool",
                  meta.PROFILE_VERSION),
                 ("motion.gif", "image_gif", "done", "ffprobe",
                  meta.PROFILE_VERSION),
                 ("opaque.bin", "other", "done", "exiftool",
                  meta.PROFILE_VERSION),
                 ("pack.zip", "archive", "done", "exiftool",
                  meta.PROFILE_VERSION),
                 ("photo.jfif", "photo_jpeg", "done", "exiftool",
                  meta.PROFILE_VERSION),
                 ("still.cr3", "photo_raw", "done", "exiftool",
                  meta.PROFILE_VERSION)])
            self.assertEqual(
                [os.path.basename(x[0])
                 for x in self._ExifWorker.extracted],
                ["legacy.doc", "motion.gif", "opaque.bin", "pack.zip",
                 "photo.jfif", "still.cr3"])
            self.assertTrue(all(
                x[2] == 90 for x in self._ExifWorker.extracted))
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM archive_members").fetchone(),
                (1,))
            con.close()

    def test_normalized_mode_keeps_known_other_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            con = self._snapshot(td)
            tools = {
                "exiftool": {"path": "unused", "version": "13.test"},
                "ffprobe": {"path": "unused", "version": "8.test"},
                "sevenzip": {"path": "unused", "version": "24.test"},
            }
            with patch.object(
                    meta, "ExifToolWorker", self._ExifWorker), \
                    patch.object(
                        meta, "ffprobe_full",
                        side_effect=AssertionError(
                            "基础元数据不应为 GIF 调用可选 ffprobe")):
                stats = meta.process_metadata_stage(
                    con, tools, retain_original_metadata=False)
            self.assertEqual(stats["done"], 5)
            self.assertEqual(
                con.execute(
                    "SELECT meta_status FROM entries"
                    " WHERE rel_path='opaque.bin'").fetchone(),
                ("not_applicable",))
            self.assertEqual(
                con.execute(
                    "SELECT media_kind,meta_status FROM entries"
                    " WHERE rel_path='motion.gif'").fetchone(),
                ("image_gif", "done"))
            self.assertEqual(
                con.execute(
                    "SELECT media_kind,meta_status FROM entries"
                    " WHERE rel_path='legacy.doc'").fetchone(),
                ("document", "done"))
            self.assertEqual(
                con.execute(
                    "SELECT media_kind,meta_status FROM entries"
                    " WHERE rel_path='photo.jfif'").fetchone(),
                ("photo_jpeg", "done"))
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM photo_metadata").fetchone(),
                (3,))
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM document_metadata").fetchone(),
                (1,))
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM raw_payloads").fetchone(),
                (0,))
            self.assertEqual(
                [os.path.basename(x[0])
                 for x in self._ExifWorker.extracted],
                ["legacy.doc", "motion.gif", "photo.jfif", "still.cr3"])
            self.assertEqual(stats["ffprobe_payloads"], 0)
            con.close()


class TestPhotoMapping(unittest.TestCase):
    def test_photo_row_from_captured_json(self):
        d = {
            "ExifIFD:Main:DateTimeOriginal": "2025:06:24 18:07:52",
            "ExifIFD:Main:OffsetTimeOriginal": "+08:00",
            "IFD0:Main:Make": "Canon", "IFD0:Main:Model": "Canon EOS R7",
            "ExifIFD:Main:SerialNumber": "012345",
            "ExifIFD:Main:LensModel": "RF100-500mm F4.5-7.1 L IS USM",
            "ExifIFD:Main:ExifImageWidth": 6960,
            "ExifIFD:Main:ExifImageHeight": 4447,
            "ExifIFD:Main:ISO": 100, "ExifIFD:Main:FNumber": 4.0,
            "ExifIFD:Main:ExposureTime": "1/500",
            "ExifIFD:Main:FocalLength": "105.0 mm",
            "ExifIFD:Main:ColorSpace": "sRGB",
            "ICC_Profile:Main:ProfileDescription": "Adobe RGB (1998)",
            "IFD0:Main:Software": "Adobe Photoshop 26.7 (Windows)",
            "Composite:Main:GPSLatitude": "27 deg 16' 43.09\" N",
            "Composite:Main:GPSLongitude": "111 deg 44' 36.91\" E",
        }
        row = meta.photo_row(meta.build_tag_index(d))
        self.assertEqual(row["capture_time_raw"], "2025:06:24 18:07:52")
        self.assertEqual(row["capture_tz_offset_min"], 480)
        self.assertEqual(row["capture_time_utc"], "2025-06-24T10:07:52Z")
        self.assertEqual(row["camera_model"], "Canon EOS R7")
        self.assertEqual(row["width"], 6960)
        self.assertEqual(row["f_number"], 4.0)
        self.assertEqual(row["focal_length_mm"], 105.0)
        self.assertEqual(row["icc_profile"], "Adobe RGB (1998)")
        self.assertEqual(row["software"], "Adobe Photoshop 26.7 (Windows)")
        self.assertAlmostEqual(row["gps_latitude"], 27.27863611, places=6)
        self.assertAlmostEqual(row["gps_longitude"], 111.7435861, places=5)

    def test_numeric_exposure_and_canon_as_shot_white_balance(self):
        d = {
            "ExifIFD:Main:Copy1:ExposureCompensation": {
                "val": "-2/3", "num": -0.6666666667},
            "Canon:Main:ExposureCompensation": {
                "val": "-2/3", "num": -0.666666666666667},
            "ExifIFD:Main:Copy1:WhiteBalance": {
                "val": "Manual", "num": 1},
            "Canon:Main:WhiteBalance": {"val": "Daylight", "num": 1},
            "Canon:Main:ColorTemperature": {"val": 4000},
            "Track4:Doc2:ColorTempAsShot": {"val": 5214},
        }
        idx = meta.build_tag_index(d)
        self.assertAlmostEqual(
            meta.tnum(idx, "ExifIFD:ExposureCompensation"),
            -0.6666666667)
        row = meta.photo_row(idx, "cr3")
        self.assertAlmostEqual(row["exposure_compensation"],
                               -0.6666666667)
        self.assertEqual(row["white_balance"], "Daylight")
        self.assertEqual(row["color_temperature"], 5214)

    def test_dng_effective_fields_timezone_and_placeholder_gps(self):
        d = {
            "ExifIFD:Main:DateTimeOriginal": "2025:02:01 16:28:03",
            "XMP-xmp:Main:Copy1:CreateDate": "2025:02:01 16:28:03+09:00",
            "SubIFD:Main:DefaultCropSize": "3840 2160",
            "SubIFD:Main:ImageWidth": 4000,
            "SubIFD:Main:ImageHeight": 2250,
            "SubIFD:Main:BitsPerSample": 16,
            "Composite:Main:GPSLatitude": "0 deg 0' 0.00\" N",
            "Composite:Main:GPSLongitude": "0 deg 0' 0.00\" E",
            "Composite:Main:GPSAltitude": "0 m Above Sea Level",
        }
        row = meta.photo_row(meta.build_tag_index(d), ".DNG")
        self.assertEqual((row["width"], row["height"]), (3840, 2160))
        self.assertEqual(row["bit_depth"], 16)
        self.assertEqual(row["capture_tz_offset_min"], 540)
        self.assertEqual(row["capture_time_utc"], "2025-02-01T07:28:03Z")
        self.assertEqual(
            (row["gps_latitude"], row["gps_longitude"], row["gps_altitude"]),
            (None, None, None))

    def test_xmp_timezone_requires_matching_wall_time(self):
        d = {
            "ExifIFD:Main:DateTimeOriginal": "2025:02:01 16:28:03",
            "XMP-xmp:Main:CreateDate": "2025:02:01 16:29:03+09:00",
        }
        row = meta.photo_row(meta.build_tag_index(d), "dng")
        self.assertIsNone(row["capture_tz_offset_min"])
        self.assertIsNone(row["capture_time_utc"])

    def test_subseconds_generic_temperature_and_invalid_values(self):
        d = {
            "Composite:Main:SubSecDateTimeOriginal":
                "2025:02:21 12:20:39.34+08:00",
            "IFD0:Main:Make": "Canon",
            "ExifIFD:Main:LensSerialNumber": "0000000000",
            "ExifIFD:Main:FNumber": 0,
            "ExifIFD:Main:FocalLength": {"val": "0 mm", "num": 0},
            "ExifIFD:Main:FocalLengthIn35mmFormat": {
                "val": "50 mm", "num": 50},
            "ExifIFD:Main:ExposureCompensation": {"val": "-2/3"},
            "Canon:Main:ColorTemperature": 4000,
            "Composite:Main:GPSLatitude": {"val": "0 deg 0' 0.00\" N",
                                            "num": 0},
            "Composite:Main:GPSLongitude": {"val": "0 deg 0' 0.00\" E",
                                             "num": 0},
            "Composite:Main:GPSAltitude": {"val": "3 m", "num": 3},
        }
        diagnostics = []
        row = meta.photo_row(meta.build_tag_index(d), "cr3", diagnostics)
        self.assertEqual(row["capture_time_raw"],
                         "2025:02:21 12:20:39.34+08:00")
        self.assertEqual(row["capture_time_utc"],
                         "2025-02-21T04:20:39.34Z")
        self.assertIsNone(row["exposure_compensation"])
        self.assertIsNone(row["color_temperature"])
        self.assertEqual(
            (row["f_number"], row["focal_length_mm"],
             row["focal_length_35mm"], row["lens_serial"]),
            (None, None, None, None))
        self.assertEqual(
            (row["gps_latitude"], row["gps_longitude"],
             row["gps_altitude"]), (None, None, None))
        self.assertEqual(
            {item["diagnostic_code"] for item in diagnostics},
            {"invalid_all_zero_lens_serial", "invalid_nonpositive_f_number",
             "invalid_nonpositive_focal_length",
             "invalid_zero_gps_placeholder"})


class TestVideoMapping(unittest.TestCase):
    def test_canon_precise_local_ffprobe_utc_and_scalar_author(self):
        doc = {
            "Composite:Main:SubSecDateTimeOriginal":
                "2025:02:09 22:19:58.56+09:00",
            "ExifIFD:Main:OffsetTimeOriginal": "+09:00",
            "IFD0:Main:Make": "Canon",
            "IFD0:Main:Artist": "SuzuranYe",
        }
        ff = {"format": {"format_name": "mov,mp4", "tags": {
            "creation_time": "2025-02-09T13:19:59.000000Z"}},
            "streams": []}
        row = meta.video_row(meta.build_tag_index(doc), ff)
        self.assertEqual(row["capture_time_raw"],
                         "2025:02:09 22:19:58.56+09:00")
        self.assertEqual(row["capture_tz_offset_min"], 540)
        self.assertEqual(row["capture_time_utc"],
                         "2025-02-09T13:19:58.56Z")
        self.assertNotIn("utc=ffprobe:format.tags.creation_time",
                         row["capture_time_source"])
        self.assertEqual(row["author"], "SuzuranYe")

    def test_ffprobe_utc_is_fallback_without_explicit_local_offset(self):
        doc = {
            "Composite:Main:SubSecDateTimeOriginal":
                "2025:02:09 22:19:58.56",
        }
        ff = {"format": {"tags": {
            "creation_time": "2025-02-09T13:19:59.000000Z"}}}
        row = meta.video_row(meta.build_tag_index(doc), ff)
        self.assertEqual(row["capture_time_raw"],
                         "2025:02:09 22:19:58.56")
        self.assertIsNone(row["capture_tz_offset_min"])
        self.assertEqual(row["capture_time_utc"],
                         "2025-02-09T13:19:59.000000Z")
        self.assertIn("utc=ffprobe:format.tags.creation_time",
                      row["capture_time_source"])

    def test_dji_category_aac_fallback_and_diagnostics(self):
        doc = {
            "Microsoft:Main:Category":
                "pb_file:dvtm_Air3s.proto;model_name:FC9113;pb_version:2;",
            "ItemList:Main:Encoder": "DJI Air3s",
            "AAC:Main:SampleRate": 48000,
            "AAC:Main:ProfileType": "Main",
            "File:Main:FileType": "AAC",
            "File:Main:MIMEType": "audio/aac",
            "Track3:Main:Warning": "embedded data available",
        }
        idx = meta.build_tag_index(doc)
        row = meta.video_row(idx, None)
        self.assertEqual(
            (row["camera_make"], row["camera_model"], row["encoder"]),
            ("DJI", "FC9113", "DJI Air3s"))
        audio = meta.audio_stream_rows_from_exif(idx)
        self.assertEqual((audio[0]["codec_name"], audio[0]["sample_rate"]),
                         ("aac", 48000))
        self.assertIsNone(audio[0]["profile"])
        reported = meta.reported_diagnostics(doc)
        self.assertEqual(
            (reported[0]["severity"], reported[0]["field_name"]),
            ("warning", "Track3:Main:Warning"))
        self.assertEqual(
            meta.av_validation_diagnostics(
                "video_mp4", 1241, {"format": {}, "streams": []})[0]
            ["diagnostic_code"], "media_no_streams")
        wav = {"format": {"duration": "0.0"}, "streams": [{
            "codec_type": "audio", "duration": None}]}
        self.assertEqual(
            meta.av_validation_diagnostics("audio", 44, wav)[0]
            ["diagnostic_code"], "audio_no_samples")


class TestVideoGpsStage(unittest.TestCase):
    class _ExifWorker:
        def __init__(self, _path):
            pass

        def extract(self, file_path, photo_profile=False, timeout=None):
            return {"SourceFile": file_path}

        def close(self):
            pass

    def test_video_point_written_raw_retained_and_retry_replaced(self):
        import zlib as _z
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        td = temp_dir.name
        con = None
        try:
            arch = os.path.join(td, "Arch")
            os.makedirs(arch)
            for name in ("clip.mp4", "song.mp3"):
                with open(os.path.join(arch, name), "wb") as f:
                    f.write(b"fixture")
            partial = os.path.join(td, "Scan_t.partial.sqlite")
            con = core.create_partial_snapshot(
                partial, [("A", arch)], config={"phase": "test"})
            core.enumerate_and_reconcile(con)
            tools = {
                "exiftool": {"path": "unused", "version": "13.test"},
                "ffprobe": {"path": "unused", "version": "8.test"},
                "sevenzip": {"path": "unused", "version": "24.test"},
            }
            ff = {
                "format": {
                    "format_name": "mov,mp4",
                    "tags": {"location": "+27.1250+111.8750/"},
                },
                "streams": [],
            }
            with patch.object(meta, "ExifToolWorker", self._ExifWorker), \
                    patch.object(meta, "ffprobe_full", return_value=ff):
                stats = meta.process_metadata_stage(con, tools)
            self.assertEqual(stats["error"], 2)
            self.assertEqual(stats["diagnostic_error"], 2)
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM metadata_diagnostics"
                " WHERE diagnostic_code='media_no_streams'").fetchone(), (2,))
            rows = con.execute(
                "SELECT e.rel_path,g.point_index,g.timestamp_seconds,"
                " g.gps_latitude,g.gps_longitude,g.gps_altitude,"
                " g.source,g.raw_value"
                " FROM video_gps_points g JOIN entries e"
                " ON e.entry_id=g.entry_id").fetchall()
            self.assertEqual(
                rows,
                [("clip.mp4", 0, None, 27.125, 111.875, None,
                  "ffprobe:format.tags.location",
                  "+27.1250+111.8750/")])

            payload, profile = con.execute(
                "SELECT p.payload_zlib,p.profile_version"
                " FROM raw_payloads p JOIN entries e"
                " ON e.entry_id=p.entry_id"
                " WHERE e.rel_path='clip.mp4' AND p.provider='ffprobe'"
            ).fetchone()
            self.assertEqual(profile, meta.PROFILE_VERSION)
            raw = json.loads(_z.decompress(payload).decode("utf-8"))
            self.assertEqual(raw["format"]["tags"]["location"],
                             "+27.1250+111.8750/")

            con.execute(
                "UPDATE entries SET meta_status='pending'"
                " WHERE rel_path='clip.mp4'")
            con.commit()
            ff["format"]["tags"]["location"] = "-27.5-111.25+8.0/"
            with patch.object(meta, "ExifToolWorker", self._ExifWorker), \
                    patch.object(meta, "ffprobe_full", return_value=ff):
                meta.process_metadata_stage(con, tools)
            retried = con.execute(
                "SELECT gps_latitude,gps_longitude,gps_altitude,raw_value"
                " FROM video_gps_points").fetchall()
            self.assertEqual(
                retried, [(-27.5, -111.25, 8.0, "-27.5-111.25+8.0/")])
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM raw_payloads p JOIN entries e"
                " ON e.entry_id=p.entry_id"
                " WHERE e.rel_path='clip.mp4'").fetchone()[0], 2)
        finally:
            if con is not None:
                con.close()


import importlib                                               # noqa: E402
import time                                                    # noqa: E402

import Script_DAISY_Lib_DBS_03_Hash as dbh                               # noqa: E402
import Script_DAISY_Module_ENV_01_Env_Check as envcheck                  # noqa: E402

SHA_ABC = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"

FullScan = importlib.import_module("Script_DAISY_Module_DBS_11_Full_Scan")


class TestEnvironmentInventory(unittest.TestCase):
    def test_inventory_lists_all_available_versions_and_every_missing_tool(
            self):
        def discover(name, _explicit):
            if name == "ffprobe":
                raise core.PreflightError("未找到 ffprobe")
            return rf"C:\Tools\{name}.exe"

        def resolved(name, path, *, explicit, version=None):
            return {
                "path": path,
                "version": version or {
                    "exiftool": "13.59",
                    "sevenzip": "26.02",
                }[name],
                "resolution": "manual" if explicit else "auto_discovery",
                "verified": True,
            }

        with patch.object(core, "discover_tool", side_effect=discover), \
                patch.object(
                    core, "resolved_tool_info", side_effect=resolved), \
                patch.object(
                    dbh, "discover_powershell",
                    return_value=(r"C:\Windows\powershell.exe", "5.1")), \
                patch.object(
                    envcheck.smartctl, "find_smartctl",
                    return_value=r"C:\Tools\smartctl.exe"), \
                patch.object(
                    envcheck.smartctl, "version", return_value="7.5"):
            tools, issues = envcheck.inspect_local_tools({
                "exiftool": None,
                "ffprobe": None,
                "sevenzip": None,
                "powershell": None,
                "smartctl": None,
            })

        self.assertEqual(
            set(tools),
            {"exiftool", "sevenzip", "powershell", "smartctl"})
        self.assertEqual(tools["exiftool"]["version"], "13.59")
        self.assertEqual(tools["powershell"]["version"], "5.1")
        self.assertEqual(tools["smartctl"]["version"], "7.5")
        self.assertEqual(
            issues,
            [{
                "name": "ffprobe",
                "display": "ffprobe",
                "installable": True,
                "reason": "未找到 ffprobe",
            }],
        )

    def test_optional_raw_capability_uses_unified_registry(self):
        capability = envcheck.envcap.RuntimeCapability(
            envcheck.envcap.RAW_CAPABILITY_ID,
            envcheck.envcap.RAW_CAPABILITY_TITLE,
            "unavailable",
            reason="synthetic missing rawpy",
            provider="rawpy/LibRaw",
            isolated=True,
        )
        with patch.object(
                envcheck.envcap,
                "probe_runtime_capabilities",
                return_value={
                    envcheck.envcap.RAW_CAPABILITY_ID: capability,
                }):
            result = envcheck.inspect_runtime_capabilities()
        self.assertEqual(
            result[envcheck.envcap.RAW_CAPABILITY_ID]["state"],
            "unavailable",
        )
        self.assertIn(
            "synthetic missing rawpy",
            result[envcheck.envcap.RAW_CAPABILITY_ID]["reason"],
        )

    def test_gui_inventory_event_keeps_only_allowlisted_install_targets(self):
        app = object.__new__(gui.DaisyApp)
        app.detected_tools = {}
        app.environment_missing_names = ()
        app.missing_installable_tools = ()
        app._cache_detected_tools = lambda payload: (
            app.detected_tools.update(payload["tools"]))
        app._refresh_tool_cache_labels = lambda: None

        app._apply_environment_inventory({
            "tools": {
                "exiftool": {
                    "path": r"C:\Tools\exiftool.exe",
                    "version": "13.59",
                    "verified": True,
                },
            },
            "missing": [
                {"name": "ffprobe", "installable": True},
                {"name": "powershell", "installable": False},
                {"name": "arbitrary", "installable": True},
            ],
        })

        self.assertEqual(
            app.environment_missing_names, ("ffprobe", "powershell"))
        self.assertEqual(app.missing_installable_tools, ("ffprobe",))
        self.assertIn("exiftool", app.detected_tools)


class TestResumeProfileGuard(unittest.TestCase):
    def test_current_profile_partial_opens(self):
        with tempfile.TemporaryDirectory() as td:
            arch = os.path.join(td, "Arch")
            os.makedirs(arch)
            partial = os.path.join(td, "current.partial.sqlite")
            con = core.create_partial_snapshot(
                partial, [("A", arch)],
                config={"profile_version": meta.PROFILE_VERSION})
            con.close()
            core.release_scan_lock(partial)
            resumed = None
            try:
                resumed, roots = FullScan.open_resume(partial)
                self.assertEqual(roots, [("A", arch)])
            finally:
                if resumed is not None:
                    resumed.close()
                core.release_scan_lock(partial)

    def test_same_scanner_old_profile_is_rejected_without_stale_lock(self):
        with tempfile.TemporaryDirectory() as td:
            arch = os.path.join(td, "Arch")
            os.makedirs(arch)
            partial = os.path.join(td, "old-profile.partial.sqlite")
            con = core.create_partial_snapshot(
                partial, [("A", arch)], config={"profile_version": 1})
            con.close()
            core.release_scan_lock(partial)
            with self.assertRaisesRegex(
                    core.PreflightError, "禁止跨版本或 profile 续传"):
                FullScan.open_resume(partial)
            self.assertFalse(os.path.exists(partial + ".lock"))

    def test_current_profile_without_gps_table_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            arch = os.path.join(td, "Arch")
            os.makedirs(arch)
            partial = os.path.join(td, "missing-table.partial.sqlite")
            con = core.create_partial_snapshot(
                partial, [("A", arch)],
                config={"profile_version": meta.PROFILE_VERSION})
            con.execute("DROP TABLE video_gps_points")
            con.commit()
            con.close()
            core.release_scan_lock(partial)
            with self.assertRaisesRegex(
                    core.PreflightError, "禁止跨版本或 profile 续传"):
                FullScan.open_resume(partial)
            self.assertFalse(os.path.exists(partial + ".lock"))


class TestHashOneFile(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = self._td.name

    def tearDown(self):
        self._td.cleanup()

    def _write(self, name, data: bytes) -> str:
        p = os.path.join(self.dir, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def test_valid_known_vector(self):
        p = self._write("abc.bin", b"abc")
        r = dbh.hash_one_file(p, expected_size=3)
        self.assertEqual(r["status"], "valid")
        self.assertEqual(r["hash_hex"], SHA_ABC)
        self.assertEqual(r["bytes_read"], 3)
        self.assertEqual((r["pre_size"], r["post_size"]), (3, 3))
        self.assertEqual(r["pre_mtime_utc"], r["post_mtime_utc"])
        self.assertEqual(r["chunk_bytes"], core.HASH_CHUNK_BYTES)

    def test_size_mismatch_is_unstable(self):
        p = self._write("d.bin", b"abcd")
        r = dbh.hash_one_file(p, expected_size=3)
        self.assertEqual(r["status"], "unstable")

    def test_change_during_read_is_unstable(self):
        p = self._write("big.bin", b"x" * 12)
        state = {"hit": False}

        def poke(_bytes_done):
            if not state["hit"]:
                state["hit"] = True
                with open(p, "ab") as f:
                    f.write(b"MORE")

        r = dbh.hash_one_file(p, expected_size=12, chunk_bytes=4, on_chunk=poke)
        self.assertEqual(r["status"], "unstable")

    def test_missing_file_failed(self):
        r = dbh.hash_one_file(os.path.join(self.dir, "nope.bin"), expected_size=1)
        self.assertEqual(r["status"], "failed")
        self.assertIsNone(r["hash_hex"])
        self.assertTrue(r["failure_reason"])


class TestStallWatchdog(unittest.TestCase):
    def test_fires_once_per_episode_and_rearms(self):
        fired = []
        wd = dbh.StallWatchdog(0.15, lambda label, secs: fired.append(label),
                               poll_s=0.03)
        try:
            wd.beat("f1")
            time.sleep(0.5)
            self.assertEqual(fired, ["f1"])          # 一次停滞只报一次
            wd.beat("f2")                            # 恢复进展后重新武装
            time.sleep(0.5)
            self.assertEqual(fired, ["f1", "f2"])
        finally:
            wd.stop()


class TestHashStage(_SnapshotFixture):
    def test_full_mode_hashes_all(self):
        core.enumerate_and_reconcile(self.con)
        stats = dbh.process_hash_stage(self.con, "full")
        self.assertEqual(stats["done"], 3)
        rows = self.con.execute(
            "SELECT e.rel_path, e.size_bytes, e.hash_status, h.hash_hex,"
            " h.origin, h.status, h.bytes_read FROM entries e"
            " JOIN hashes h ON h.entry_id = e.entry_id").fetchall()
        self.assertEqual(len(rows), 3)
        for rel, size, hstat, hex_, origin, status, br in rows:
            self.assertEqual(hstat, "done", rel)
            self.assertEqual(status, "valid", rel)
            self.assertEqual(origin, "computed", rel)
            self.assertEqual(br, size, rel)
            self.assertEqual(hex_, core.sha256_file(os.path.join(self.arch, rel)))

    def test_placeholder_skipped_and_missing_failed(self):
        core.enumerate_and_reconcile(self.con)
        self.con.execute("UPDATE entries SET is_placeholder=1 WHERE rel_path='b.txt'")
        self.con.commit()
        os.remove(os.path.join(self.arch, "a.CR3"))
        dbh.process_hash_stage(self.con, "full")
        st = dict(self.con.execute("SELECT rel_path, hash_status FROM entries"))
        self.assertEqual(st["b.txt"], "skipped")
        self.assertEqual(st["a.CR3"], "error")
        self.assertEqual(st["sub 目录\\c.MP4"], "done")
        hrow = self.con.execute(
            "SELECT h.status, h.hash_hex FROM hashes h JOIN entries e"
            " ON e.entry_id=h.entry_id WHERE e.rel_path='a.CR3'").fetchone()
        self.assertEqual(hrow[0], "failed")
        self.assertIsNone(hrow[1])
        n_err = self.con.execute(
            "SELECT COUNT(*) FROM errors WHERE stage='hash'").fetchone()[0]
        self.assertGreaterEqual(n_err, 1)
        n_ph = self.con.execute(
            "SELECT COUNT(*) FROM hashes h JOIN entries e ON e.entry_id=h.entry_id"
            " WHERE e.rel_path='b.txt'").fetchone()[0]
        self.assertEqual(n_ph, 0)                    # 占位文件不产生 hashes 行

    def test_interrupt_resume_full_bytes_accounting(self):
        core.enumerate_and_reconcile(self.con)
        with self.assertRaises(KeyboardInterrupt):
            dbh.process_hash_stage(self.con, "full", max_files=1)
        done1 = self.con.execute("SELECT COUNT(*) FROM entries"
                                 " WHERE hash_status='done'").fetchone()[0]
        self.assertEqual(done1, 1)
        # 遗留 processing 也应被续传拾起
        self.con.execute("UPDATE entries SET hash_status='processing'"
                         " WHERE hash_status='pending' AND rel_path='b.txt'")
        self.con.commit()
        dbh.process_hash_stage(self.con, "full")
        bad = self.con.execute(
            "SELECT COUNT(*) FROM entries e LEFT JOIN hashes h"
            " ON h.entry_id=e.entry_id AND h.status='valid'"
            " WHERE e.hash_status<>'done' OR h.bytes_read IS NULL"
            " OR h.bytes_read <> e.size_bytes").fetchone()[0]
        self.assertEqual(bad, 0)                     # bytes_read 全量核对
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM hashes").fetchone()[0], 3)


class _IncrementalFixture(unittest.TestCase):
    """夹具：五文件树；A（全量）→ B/C（增量）多级快照。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.arch = os.path.join(self._td.name, "Arch增量")
        os.makedirs(self.arch)
        for name, data in [("keep.bin", b"keep-data-123"),
                           ("change.bin", b"original"),
                           ("touch.bin", b"touch-data"),
                           ("swap.bin", b"swap-data"),
                           ("badprev.bin", b"bad-prev")]:
            self._write(name, data)
        self.out = os.path.join(self._td.name, "Snap")
        os.makedirs(self.out)

    def tearDown(self):
        self._td.cleanup()

    def _write(self, rel, data: bytes):
        with open(os.path.join(self.arch, rel), "wb") as f:
            f.write(data)

    def _build(self, name, previous_path=None, hash_failure=False,
               file_issue=False, unstable=False,
               enumeration_gap=False) -> str:
        partial = os.path.join(self.out, f"Scan_{name}.partial.sqlite")
        con = core.create_partial_snapshot(partial, [("Arch", self.arch)],
                                           config={"phase": "test"})
        core.enumerate_and_reconcile(con)
        prev = dbh.load_previous(previous_path) if previous_path else None
        dbh.process_hash_stage(con, "incremental" if prev else "full",
                               previous=prev)
        if hash_failure:
            con.execute("UPDATE hashes SET status='failed', hash_hex=NULL,"
                        " bytes_read=NULL WHERE entry_id=(SELECT entry_id"
                        " FROM entries WHERE rel_path='badprev.bin')")
            con.execute("UPDATE entries SET hash_status='error'"
                        " WHERE rel_path='badprev.bin'")
        con.execute("UPDATE entries SET meta_status='skipped'"
                    " WHERE meta_status='pending'")
        if file_issue:
            con.execute("UPDATE entries SET meta_status='error'"
                        " WHERE rel_path='badprev.bin'")
        if unstable:
            con.execute("UPDATE entries SET meta_status='unstable',"
                        " hash_status='unstable' WHERE rel_path='badprev.bin'")
        if enumeration_gap:
            con.execute("UPDATE dirs SET enum_status='access_denied',"
                        " error_message='test gap' WHERE rel_path='' ")
            con.execute("UPDATE roots SET enum_status='failed'")
        con.commit()
        return core.finalize_snapshot(
            con, partial, "incremental" if prev else "full")

    def _hash_rows(self, db):
        con = sqlite3.connect(db)
        rows = {r[0]: r[1:] for r in con.execute(
            "SELECT e.rel_path, h.origin, h.source_snapshot_uuid,"
            " h.source_computed_at_utc, h.reuse_basis, h.bytes_read,"
            " h.hash_hex, h.finished_at_utc, e.hash_status"
            " FROM entries e JOIN hashes h ON h.entry_id=e.entry_id")}
        uuid_, = con.execute("SELECT snapshot_uuid FROM snapshot_info").fetchone()
        con.close()
        return uuid_, rows

    def _copy_and_update(self, source, stem, sql) -> str:
        plain = os.path.join(self.out, stem + ".sqlite")
        shutil.copyfile(source, plain)
        con = sqlite3.connect(plain)
        con.execute(sql)
        con.commit()
        con.close()
        token = core.sha256_file(plain)[:8].upper()
        final = os.path.join(self.out, f"{stem}_{token}.sqlite")
        os.replace(plain, final)
        return final


class TestIncrementalReuse(_IncrementalFixture):
    def test_reuse_recompute_and_provenance(self):
        final_a = self._build("A")
        uuid_a, rows_a = self._hash_rows(final_a)
        # 树变更：改内容（size 变）；仅 mtime 变（显式 +5s，避开时钟量化）
        self._write("change.bin", b"changed-and-longer")
        p_touch = os.path.join(self.arch, "touch.bin")
        st = os.stat(p_touch)
        os.utime(p_touch, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        partial = os.path.join(self.out, "Scan_B.partial.sqlite")
        con = core.create_partial_snapshot(partial, [("Arch", self.arch)],
                                           config={"phase": "test"})
        core.enumerate_and_reconcile(con)
        # 模拟文件被替换：file id 不一致必须拒绝复用
        con.execute("UPDATE entries SET file_index_hex='deadbeef'"
                    " WHERE rel_path='swap.bin'")
        con.commit()
        prev = dbh.load_previous(final_a)
        self.assertFalse(prev.has_file_issues)
        self.assertEqual(prev.uuid, uuid_a)
        stats = dbh.process_hash_stage(con, "incremental", previous=prev)
        rows = {r[0]: r[1:] for r in con.execute(
            "SELECT e.rel_path, h.origin, h.source_snapshot_uuid,"
            " h.source_computed_at_utc, h.reuse_basis, h.bytes_read, h.hash_hex"
            " FROM entries e JOIN hashes h ON h.entry_id=e.entry_id")}
        origin, su, st_, basis, br, hex_ = rows["keep.bin"]
        self.assertEqual(origin, "reused")
        self.assertEqual(su, uuid_a)
        self.assertEqual(st_, rows_a["keep.bin"][6])   # A 侧计算事件时间
        self.assertEqual(basis, "size+mtime+fileid")
        self.assertIsNone(br)
        self.assertEqual(hex_, rows_a["keep.bin"][5])
        for rel in ("change.bin", "touch.bin", "swap.bin"):
            self.assertEqual(rows[rel][0], "computed", rel)
        self.assertEqual(rows["badprev.bin"][0], "reused")
        self.assertEqual(stats["reused"], 2)
        self.assertEqual(stats["done"], 5)
        con.close()

    def test_file_issues_do_not_block_valid_hash_reuse(self):
        source = self._build("FileIssue", file_issue=True)
        prev = dbh.load_previous(source)
        self.assertTrue(prev.has_file_issues)
        self.assertIsNotNone(prev.lookup("Arch", core.make_path_key("keep.bin")))

    def test_hash_failure_unstable_and_enumeration_gap_are_blocked(self):
        for name, kwargs in (
                ("HashFailure", {"hash_failure": True}),
                ("Unstable", {"unstable": True}),
                ("EnumerationGap", {"enumeration_gap": True})):
            source = self._build(name, **kwargs)
            with self.subTest(name=name), self.assertRaises(core.PreflightError):
                dbh.load_previous(source)

    def test_multilevel_source_points_to_origin(self):
        final_a = self._build("A")
        uuid_a, rows_a = self._hash_rows(final_a)
        final_b = self._build("B", previous_path=final_a)
        uuid_b, _ = self._hash_rows(final_b)
        self.assertNotEqual(uuid_a, uuid_b)
        final_c = self._build("C", previous_path=final_b)
        _, rows_c = self._hash_rows(final_c)
        origin, su, st_, _basis = rows_c["keep.bin"][:4]
        self.assertEqual(origin, "reused")
        self.assertEqual(su, uuid_a)                   # 指向最初计算，而非 B
        self.assertEqual(st_, rows_a["keep.bin"][6])

    def test_admission_rejects_tampered_snapshot_bytes(self):
        final_a = self._build("A")
        with open(final_a, "ab") as f:
            f.write(b"tamper")
        with self.assertRaises(core.PreflightError):
            dbh.load_previous(final_a)

    def test_admission_rejects_incomplete_noncurrent_and_bad_integrity(self):
        source = self._build("Admission")
        incomplete = self._copy_and_update(
            source, "Incomplete",
            "UPDATE snapshot_info SET scan_status='running'")
        noncurrent = self._copy_and_update(
            source, "Schema2",
            "UPDATE snapshot_info SET schema_version=2")
        not_verified = self._copy_and_update(
            source, "IntegrityPending",
            "UPDATE snapshot_info SET database_integrity='pending'")
        for path in (incomplete, noncurrent, not_verified):
            with self.subTest(path=path), self.assertRaises(core.PreflightError):
                dbh.load_previous(path)

        corrupt_plain = os.path.join(self.out, "Corrupt.sqlite")
        shutil.copyfile(source, corrupt_plain)
        with open(corrupt_plain, "r+b") as handle:
            handle.seek(0)
            handle.write(b"not-a-sqlite-database")
        token = core.sha256_file(corrupt_plain)[:8].upper()
        corrupt = os.path.join(self.out, f"Corrupt_{token}.sqlite")
        os.replace(corrupt_plain, corrupt)
        with self.assertRaises(core.PreflightError):
            dbh.load_previous(corrupt)


class TestSampling(unittest.TestCase):
    def test_stratified_min_and_deterministic(self):
        rows = [(i, i * 1000) for i in range(1, 1001)]
        picked = dbh.pick_sample(rows, percent=1.0, min_count=100, seed="s1")
        self.assertEqual(len(picked), 100)
        self.assertEqual(picked, dbh.pick_sample(rows, 1.0, 100, seed="s1"))
        self.assertNotEqual(picked, dbh.pick_sample(rows, 1.0, 100, seed="s2"))
        sizes = sorted(s for _, s in rows)
        quarts = [sizes[249], sizes[499], sizes[749]]
        strata = [0, 0, 0, 0]
        for _, s in picked:
            strata[sum(s > t for t in quarts)] += 1
        self.assertTrue(all(c > 0 for c in strata), strata)   # 大小分层覆盖
        small = [(1, 10), (2, 20)]
        self.assertEqual(sorted(dbh.pick_sample(small, 1.0, 100, seed="x")),
                         small)                               # 不足 min 全取


class TestPowershellDiscovery(unittest.TestCase):
    def test_discover(self):
        path, version = dbh.discover_powershell()
        self.assertTrue(os.path.isfile(path))
        self.assertTrue(version)

    def test_candidates_include_standard_windows_location_without_path(self):
        environment = {
            "SystemRoot": r"D:\Windows",
            "ProgramW6432": r"D:\Program Files",
            "ProgramFiles": r"D:\Program Files",
            "ProgramFiles(x86)": r"D:\Program Files (x86)",
            "LOCALAPPDATA": r"D:\Users\Test\AppData\Local",
        }
        with patch.dict(os.environ, environment, clear=True):
            with patch.object(dbh.shutil, "which", return_value=None):
                candidates = dbh._powershell_candidates()
        self.assertIn(
            r"D:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
            candidates,
        )
        self.assertIn(
            r"D:\Program Files\PowerShell\7\pwsh.exe", candidates)
        self.assertEqual(
            candidates.count(r"D:\Program Files\PowerShell\7\pwsh.exe"), 1)

    def test_auto_discovery_skips_broken_candidate(self):
        bad = r"C:\Broken\powershell.exe"
        good = r"C:\PowerShell\7\pwsh.exe"
        success = subprocess.CompletedProcess(
            [], 0, stdout="7.5.2\n", stderr="")
        with patch.object(
                dbh, "_powershell_candidates", return_value=[bad, good]):
            with patch.object(dbh.os.path, "isfile", return_value=True):
                with patch.object(
                        dbh.subprocess, "run",
                        side_effect=[OSError("blocked"), success]):
                    path, version = dbh.discover_powershell()
        self.assertEqual(path, good)
        self.assertEqual(version, "7.5.2")

    def test_explicit_path_is_authoritative(self):
        explicit = r"D:\Portable\pwsh.exe"
        success = subprocess.CompletedProcess(
            [], 0, stdout="7.4.7\n", stderr="")
        with patch.object(dbh.os.path, "isfile", return_value=True):
            with patch.object(dbh.subprocess, "run", return_value=success):
                with patch.object(dbh, "_powershell_candidates") as automatic:
                    path, version = dbh.discover_powershell(explicit)
        automatic.assert_not_called()
        self.assertEqual(path, explicit)
        self.assertEqual(version, "7.4.7")

    def test_unusable_candidates_have_distinct_error(self):
        candidate = r"C:\Broken\powershell.exe"
        failed = subprocess.CompletedProcess(
            [], 3, stdout="", stderr="Get-FileHash unavailable")
        with patch.object(
                dbh, "_powershell_candidates", return_value=[candidate]):
            with patch.object(dbh.os.path, "isfile", return_value=True):
                with patch.object(dbh.subprocess, "run", return_value=failed):
                    with self.assertRaisesRegex(
                            core.PreflightError, "候选.*均无法"):
                        dbh.discover_powershell()

    def test_missing_error_mentions_manual_override(self):
        with patch.object(dbh, "_powershell_candidates", return_value=[]):
            with self.assertRaisesRegex(
                    core.PreflightError, "--powershell-path"):
                dbh.discover_powershell()


class TestEnvironmentCheckPowershell(unittest.TestCase):
    def test_report_contains_verified_powershell_smoke(self):
        tools = {
            name: {
                "path": rf"C:\Tools\{name}.exe",
                "version": "99",
                "resolution": "auto_discovery",
                "verified": True,
            }
            for name in ("exiftool", "ffprobe", "sevenzip")
        }
        with tempfile.TemporaryDirectory() as td:
            argv = ["env-check", "--output-dir", td]
            runtime_capabilities = {
                envcheck.envcap.RAW_CAPABILITY_ID: {
                    "id": envcheck.envcap.RAW_CAPABILITY_ID,
                    "title": envcheck.envcap.RAW_CAPABILITY_TITLE,
                    "state": "unavailable",
                    "available": False,
                    "version": None,
                    "reason": "synthetic optional dependency missing",
                    "provider": "rawpy/LibRaw",
                    "isolated": True,
                    "details": {"worker_reaped": True},
                },
            }
            with patch.object(sys, "argv", argv), patch.object(
                    envcheck,
                    "inspect_runtime_capabilities",
                    return_value=runtime_capabilities):
                with patch.object(
                        envcheck, "inspect_local_tools",
                        return_value=(tools, [])):
                    with patch.object(
                            envcheck.core, "run_preflight",
                            return_value=tools):
                        smart_scan = types.SimpleNamespace(
                            devices=(object(),),
                            executable=r"C:\Tools\smartctl.exe",
                            version="7.5",
                        )
                        storage_inventory = types.SimpleNamespace(
                            records=(object(),),
                        )
                        with patch.object(
                                envcheck.smartctl, "scan",
                                return_value=smart_scan), patch.object(
                                envcheck.storage_windows, "read_inventory",
                                return_value=storage_inventory):
                            with patch.object(
                                    envcheck.dbh, "discover_powershell",
                                    return_value=(r"C:\Windows\powershell.exe",
                                                  "5.1.0")):
                                with patch.object(
                                        envcheck.dbh, "get_filehash_batch",
                                        return_value=[SHA_ABC]):
                                    with patch.object(sys, "stdout", io.StringIO()):
                                        with patch.object(
                                                sys, "stderr", io.StringIO()):
                                            self.assertEqual(envcheck.main(), 0)
            reports = [
                os.path.join(td, name) for name in os.listdir(td)
                if name.startswith("Env_Check_") and name.endswith(".json")
            ]
            self.assertEqual(len(reports), 1)
            with open(reports[0], encoding="utf-8") as f:
                report = json.load(f)
        self.assertEqual(report["tools"]["powershell"]["version"], "5.1.0")
        self.assertEqual(
            report["checks"]["powershell_get_filehash"], "passed")
        self.assertEqual(
            report["checks"]["smartctl_readonly_scan"], "passed")
        self.assertEqual(
            report["checks"]["windows_storage_inventory"], "passed")
        self.assertEqual(
            report["checks"]["rawpy_libraw"], "unavailable")
        self.assertEqual(
            report["runtime_capabilities"][
                envcheck.envcap.RAW_CAPABILITY_ID]["reason"],
            "synthetic optional dependency missing",
        )


class TestIndependentVerify(_SnapshotFixture):
    def test_verify_pass_then_injected_bad_hash_detected(self):
        core.enumerate_and_reconcile(self.con)
        dbh.process_hash_stage(self.con, "full")
        v = dbh.independent_verify(self.con, percent=100.0, min_count=1)
        self.assertEqual((v["sampled"], v["matched"], v["mismatched"]),
                         (3, 3, 0))
        # 注入坏哈希：必须被检出并标 unstable
        self.con.execute("UPDATE hashes SET hash_hex='" + "0" * 64 + "'"
                         " WHERE entry_id=(SELECT entry_id FROM entries"
                         " WHERE rel_path='b.txt')")
        self.con.commit()
        v2 = dbh.independent_verify(self.con, percent=100.0, min_count=1)
        self.assertEqual(v2["mismatched"], 1)
        st, = self.con.execute("SELECT hash_status FROM entries"
                               " WHERE rel_path='b.txt'").fetchone()
        self.assertEqual(st, "unstable")
        n = self.con.execute("SELECT COUNT(*) FROM errors WHERE stage='hash'"
                             " AND error_code='verify_mismatch'").fetchone()[0]
        self.assertEqual(n, 1)




import hashlib                                                 # noqa: E402
import shutil                                                  # noqa: E402
import subprocess                                              # noqa: E402

import Script_DAISY_Lib_DBS_04_Diff as dbdiff                            # noqa: E402
import Script_DAISY_Test_Tree as tt                                           # noqa: E402


class TestDiffDdl(unittest.TestCase):
    def test_diff_ddl_executes(self):
        # 文档比对守卫退役（DDL 权威在代码）；保留可执行性守卫
        con = sqlite3.connect(":memory:")
        con.executescript(dbdiff.DIFF_DDL)
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertLessEqual({"diff_info", "diff_entries", "diff_dirs",
                              "diff_hash_groups", "diff_subtrees"}, tables)
        info_columns = {
            row[1] for row in con.execute("PRAGMA table_info(diff_info)")
        }
        self.assertLessEqual(
            {"schema_version", "old_schema_version", "new_schema_version"},
            info_columns,
        )
        con.close()


class _DiffFixture(unittest.TestCase):
    """夹具：旧树→快照；复制树（copy2 保 mtime）→变换→快照；Compare。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.base = self._td.name
        self.old_tree = os.path.join(self.base, "TreeOld")
        self.new_tree = os.path.join(self.base, "TreeNew")
        self.snaps = os.path.join(self.base, "Snap")
        os.makedirs(self.old_tree)
        os.makedirs(self.snaps)

    def tearDown(self):
        self._td.cleanup()

    def clone(self):
        shutil.copytree(self.old_tree, self.new_tree)

    def snap(self, tree, name, **kw):
        kw.setdefault("label", "T")
        return tt.build_snapshot(tree, self.snaps, name, **kw)

    def diff(self, old_snap, new_snap, **kw):
        out = os.path.join(self.base, "out.diff.sqlite")
        if os.path.exists(out):
            os.remove(out)
        dbdiff.compare(old_snap, new_snap, out, **kw)
        con = sqlite3.connect(out)
        con.row_factory = sqlite3.Row
        d = {t: [dict(r) for r in con.execute(f"SELECT * FROM {t}")]
             for t in ("diff_entries", "diff_dirs", "diff_hash_groups",
                       "diff_subtrees")}
        d["info"] = dict(con.execute("SELECT * FROM diff_info").fetchone())
        con.close()
        return d

    @staticmethod
    def row(d, rel):
        hits = [r for r in d["diff_entries"]
                if rel in (r["old_rel_path"], r["new_rel_path"])]
        assert len(hits) == 1, f"{rel}: {hits}"
        return hits[0]


class TestDiffAdmission(_DiffFixture):
    def _copy_with_schema(self, snapshot, schema_version, stem):
        plain = os.path.join(self.snaps, stem + ".sqlite")
        shutil.copyfile(snapshot, plain)
        con = sqlite3.connect(plain)
        con.execute(
            "UPDATE snapshot_info SET schema_version=?, scanner_version=?",
            (schema_version, core.SCANNER_VERSION),
        )
        manifest_text, = con.execute(
            "SELECT manifest_json FROM snapshot_manifest").fetchone()
        manifest = json.loads(manifest_text)
        manifest["schema_version"] = schema_version
        con.execute(
            "UPDATE snapshot_manifest SET manifest_json=?",
            (json.dumps(manifest, ensure_ascii=False),),
        )
        con.commit()
        con.close()
        suffix = core.sha256_file(plain)[:8].upper()
        final = os.path.join(self.snaps, f"{stem}_{suffix}.sqlite")
        os.replace(plain, final)
        return final

    def test_admission_rules(self):
        tt.write(self.old_tree, "a.bin", b"data")
        s1 = self.snap(self.old_tree, "A")
        out = os.path.join(self.base, "d.sqlite")
        # partial 命名拒绝
        fake = os.path.join(self.snaps, "Scan_x.partial.sqlite")
        shutil.copyfile(s1, fake)
        with self.assertRaises(core.PreflightError):
            dbdiff.compare(fake, s1, out)
        # 文件名高32bit指纹缺失：默认拒绝，--force 放行且 forced=1
        s2 = self.snap(self.old_tree, "B")
        missing = re.sub(r"_[0-9A-F]{8}\.sqlite$", ".sqlite", s2)
        os.rename(s2, missing)
        with self.assertRaises(core.PreflightError):
            dbdiff.compare(s1, missing, out)
        dbdiff.compare(s1, missing, out, force=True)
        con = sqlite3.connect(out)
        self.assertEqual(con.execute(
            "SELECT forced FROM diff_info").fetchone()[0], 1)
        con.close()
        # 文件名高32bit指纹与实际字节不符：即使 --force 也拒绝（硬性项）
        s3 = self.snap(self.old_tree, "C")
        with open(s3, "ab") as f:
            f.write(b"tamper")
        with self.assertRaises(core.PreflightError):
            dbdiff.compare(s1, s3, os.path.join(self.base, "d2.sqlite"),
                           force=True)

    def test_only_schema_3_is_readable(self):
        tt.write(self.old_tree, "a.bin", b"same")
        current = self.snap(self.old_tree, "current")
        for schema in (1, 2, 4, 99):
            other = self._copy_with_schema(
                current, schema, f"schema-{schema}")
            with self.subTest(schema=schema), self.assertRaises(
                    core.PreflightError):
                dbdiff.compare(
                    current, other,
                    os.path.join(self.base, f"schema-{schema}.diff.sqlite"))


class TestDiffGolden(_DiffFixture):
    def test_t01_t14_unchanged_independent(self):
        tt.write(self.old_tree, "a.bin", b"alpha-data")
        self.clone()
        d = self.diff(self.snap(self.old_tree, "old"),
                      self.snap(self.new_tree, "new"))
        r = self.row(d, "a.bin")
        self.assertEqual((r["status"], r["evidence"]),
                         ("unchanged", "independent_computation"))
        self.assertIsNone(r["metadata_changed"])
        self.assertEqual((d["info"]["old_hash_coverage"],
                          d["info"]["new_hash_coverage"]), ("full", "full"))

    def test_single_root_auto_pair_different_labels(self):
        # 单根自动配对：label 不同、内容相同 → 全 unchanged
        import json as _json
        tt.write(self.old_tree, "a.bin", b"same-data")
        self.clone()
        d = self.diff(self.snap(self.old_tree, "old", label="库-M"),
                      self.snap(self.new_tree, "new", label="库-A"))
        r = self.row(d, "a.bin")
        self.assertEqual((r["status"], r["evidence"]),
                         ("unchanged", "independent_computation"))
        m = _json.loads(d["info"]["root_mapping_json"])
        self.assertEqual(m["auto_paired"], ["库-M", "库-A"])
        self.assertEqual(m["pairs"], [["库-M", "库-A"]])
        self.assertEqual({x["status"] for x in d["diff_dirs"]}, {"unchanged"})

    def test_t02_size_change_stat_only(self):
        tt.write(self.old_tree, "a.bin", b"short")
        self.clone()
        tt.write(self.new_tree, "a.bin", b"longer-content")
        d = self.diff(self.snap(self.old_tree, "old"),
                      self.snap(self.new_tree, "new"))
        r = self.row(d, "a.bin")
        self.assertEqual((r["status"], r["evidence"]),
                         ("content_changed", "stat_only"))

    def test_t03_same_size_change_hash_evidence(self):
        tt.write(self.old_tree, "a.bin", b"AAAA")
        self.clone()
        tt.write(self.new_tree, "a.bin", b"BBBB")
        d = self.diff(self.snap(self.old_tree, "old"),
                      self.snap(self.new_tree, "new"))
        r = self.row(d, "a.bin")
        self.assertEqual((r["status"], r["evidence"]),
                         ("content_changed", "independent_computation"))
        self.assertNotEqual(r["old_hash_hex"], r["new_hash_hex"])

    def test_t04_touch_stat_changed_content_same(self):
        tt.write(self.old_tree, "a.bin", b"same-content")
        self.clone()
        p = os.path.join(self.new_tree, "a.bin")
        st = os.stat(p)          # 显式 +5s：避开系统时钟量化粒度的同刻度陷阱
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        d = self.diff(self.snap(self.old_tree, "old"),
                      self.snap(self.new_tree, "new"))
        r = self.row(d, "a.bin")
        self.assertEqual((r["status"], r["evidence"]),
                         ("stat_changed_content_same", "independent_computation"))

    def test_t05_move_rename_group_paired(self):
        tt.write(self.old_tree, os.path.join("d1", "f old.bin"), b"move-me")
        self.clone()
        os.makedirs(os.path.join(self.new_tree, "d2"))
        os.rename(os.path.join(self.new_tree, "d1", "f old.bin"),
                  os.path.join(self.new_tree, "d2", "g new.bin"))
        d = self.diff(self.snap(self.old_tree, "old"),
                      self.snap(self.new_tree, "new"))
        r = self.row(d, "d2\\g new.bin")
        self.assertEqual((r["status"], r["evidence"], r["reason"]),
                         ("moved_or_renamed", "independent_computation",
                          "group_paired"))
        self.assertEqual(r["old_rel_path"], "d1\\f old.bin")
        self.assertIsNotNone(r["group_id"])
        g, = d["diff_hash_groups"]
        self.assertEqual((g["old_count"], g["new_count"]), (1, 1))

    def test_t06_copied_group_2_to_3(self):
        tt.write(self.old_tree, "a.bin", b"dup-content")
        tt.write(self.old_tree, "b.bin", b"dup-content")
        self.clone()
        tt.write(self.new_tree, "c.bin", b"dup-content")
        d = self.diff(self.snap(self.old_tree, "old"),
                      self.snap(self.new_tree, "new"))
        r = self.row(d, "c.bin")
        self.assertEqual((r["status"], r["evidence"]),
                         ("copied", "independent_computation"))
        g, = d["diff_hash_groups"]
        self.assertEqual((g["old_count"], g["new_count"]), (2, 3))

    def test_t07_t08_deleted_added(self):
        tt.write(self.old_tree, "keep.bin", b"keep")
        tt.write(self.old_tree, "gone.bin", b"gone-data")
        self.clone()
        os.remove(os.path.join(self.new_tree, "gone.bin"))
        tt.write(self.new_tree, "fresh.bin", b"fresh-unique")
        d = self.diff(self.snap(self.old_tree, "old"),
                      self.snap(self.new_tree, "new"))
        self.assertEqual((self.row(d, "gone.bin")["status"],
                          self.row(d, "gone.bin")["evidence"]),
                         ("deleted", "insufficient"))
        self.assertEqual((self.row(d, "fresh.bin")["status"],
                          self.row(d, "fresh.bin")["evidence"]),
                         ("added", "insufficient"))     # M=0 不误判 copied

    def test_t09_case_rename_same_entry(self):
        tt.write(self.old_tree, "Name.TXT", b"case-data")
        self.clone()
        os.rename(os.path.join(self.new_tree, "Name.TXT"),
                  os.path.join(self.new_tree, "name.txt"))
        d = self.diff(self.snap(self.old_tree, "old"),
                      self.snap(self.new_tree, "new"))
        r = self.row(d, "Name.TXT")
        self.assertEqual(r["status"], "unchanged")
        self.assertEqual(r["reason"], "case_or_form_rename")
        self.assertEqual(r["new_rel_path"], "name.txt")

    def test_t10_nfc_nfd_same_entry(self):
        nfd = "café.bin"
        nfc = "café.bin"
        tt.write(self.old_tree, nfd, b"unicode-data")
        self.clone()
        os.rename(os.path.join(self.new_tree, nfd),
                  os.path.join(self.new_tree, nfc))
        d = self.diff(self.snap(self.old_tree, "old"),
                      self.snap(self.new_tree, "new"))
        r = self.row(d, nfc)
        self.assertEqual(r["status"], "unchanged")
        self.assertEqual(r["reason"], "case_or_form_rename")

    def test_t11_enum_failed_subtree_no_deleted(self):
        tt.write(self.old_tree, "root.bin", b"root-data")
        tt.write(self.old_tree, os.path.join("sub", "f1.bin"), b"f1")
        tt.write(self.old_tree, os.path.join("sub", "f2.bin"), b"f2")
        self.clone()
        s1 = self.snap(self.old_tree, "old")
        denied = os.path.normcase(
            core.to_extended_path(os.path.join(self.new_tree, "sub")))
        real_scandir = os.scandir

        def deny_target(path):
            current = os.path.normcase(os.path.abspath(os.fspath(path)))
            if current == denied:
                raise PermissionError(13, "测试模拟拒绝访问", os.fspath(path))
            return real_scandir(path)

        with patch.object(core.os, "scandir", side_effect=deny_target):
            s2 = self.snap(self.new_tree, "new")
        d = self.diff(s1, s2)
        for rel in ("sub\\f1.bin", "sub\\f2.bin"):
            r = self.row(d, rel)
            self.assertEqual(r["status"], "unknown", rel)
            self.assertIn("enum_failed_new", r["reason"], rel)
        self.assertEqual(self.row(d, "root.bin")["status"], "unchanged")
        self.assertFalse([r for r in d["diff_entries"]
                          if r["status"] == "deleted"])   # 绝无 deleted
        st = [s for s in d["diff_subtrees"] if s["side"] == "new"]
        self.assertEqual(len(st), 1)
        self.assertEqual(st[0]["affected_estimate"], 2)

    def test_t11b_root_failed_no_mass_deleted(self):
        tt.write(self.old_tree, "a.bin", b"a")
        tt.write(self.old_tree, os.path.join("s", "b.bin"), b"b")
        self.clone()
        s1 = self.snap(self.old_tree, "old")
        s2 = self.snap(self.new_tree, "new",
                       pre_enum=lambda con, tree: shutil.rmtree(tree))
        d = self.diff(s1, s2)
        self.assertEqual(
            {r["status"] for r in d["diff_entries"]}, {"unknown"})
        self.assertFalse([r for r in d["diff_entries"]
                          if r["status"] == "deleted"])
        roots_failed = [s for s in d["diff_subtrees"]
                        if s["side"] == "new" and s["rel_path"] == ""]
        self.assertEqual(len(roots_failed), 1)      # root 级失败醒目落点

    def test_t12_hash_none(self):
        tt.write(self.old_tree, "same.bin", b"12345")
        tt.write(self.old_tree, "diff.bin", b"aaaa")
        self.clone()
        tt.write(self.new_tree, "diff.bin", b"bbbbbb")
        d = self.diff(self.snap(self.old_tree, "old", hash_mode="none"),
                      self.snap(self.new_tree, "new", hash_mode="none"))
        self.assertEqual((self.row(d, "same.bin")["status"],
                          self.row(d, "same.bin")["evidence"]),
                         ("hash_missing", "insufficient"))
        self.assertEqual((self.row(d, "diff.bin")["status"],
                          self.row(d, "diff.bin")["evidence"]),
                         ("content_changed", "stat_only"))
        self.assertEqual((d["info"]["old_hash_coverage"],
                          d["info"]["new_hash_coverage"]), ("none", "none"))

    def test_t13_reuse_chain_propagated(self):
        tt.write(self.old_tree, "a.bin", b"chain-data")
        sa = self.snap(self.old_tree, "A")
        sb = self.snap(self.old_tree, "B", hash_mode="incremental",
                       previous_path=sa)
        sc = self.snap(self.old_tree, "C", hash_mode="incremental",
                       previous_path=sb)
        d = self.diff(sa, sc)
        r = self.row(d, "a.bin")
        self.assertEqual((r["status"], r["evidence"]),
                         ("unchanged", "propagated_single_computation"))

    def test_t15_hardlink_not_copied(self):
        tt.write(self.old_tree, "a.bin", b"hardlink-content")
        self.clone()
        os.link(os.path.join(self.new_tree, "a.bin"),
                os.path.join(self.new_tree, "b.bin"))
        d = self.diff(self.snap(self.old_tree, "old"),
                      self.snap(self.new_tree, "new"))
        r = self.row(d, "b.bin")
        self.assertEqual(r["status"], "added")          # 不判 copied
        self.assertIn("hardlink", r["reason"])
        g, = d["diff_hash_groups"]
        self.assertEqual((g["old_count"], g["new_count"]), (1, 2))
        self.assertEqual(g["new_hardlink_sets"], 1)
        self.assertEqual(self.row(d, "a.bin")["status"], "unchanged")

    def test_t16_t16b_unstable(self):
        tt.write(self.old_tree, "stable.bin", b"S" * 64)
        tt.write(self.old_tree, "victim.bin", b"V" * 64)
        self.clone()
        tt.write(self.new_tree, "import.bin", b"I" * 64)

        def hook(con, tree):        # 枚举后同尺寸改写 → 复扫标 unstable
            bump = 1_600_000_000 * 10 ** 9      # 显式 mtime，确定性触发复扫差异
            tt.write(tree, "victim.bin", b"W" * 64, mtime_ns=bump)
            tt.write(tree, "import.bin", b"J" * 64, mtime_ns=bump)

        d = self.diff(self.snap(self.old_tree, "old"),
                      self.snap(self.new_tree, "new", post_enum=hook))
        r = self.row(d, "victim.bin")
        self.assertEqual((r["status"], r["evidence"]),
                         ("unstable", "insufficient"))
        r2 = self.row(d, "import.bin")
        self.assertEqual(r2["status"], "added")         # 不得从新增清单消失
        self.assertEqual(r2["reason"], "unstable_content")
        self.assertEqual(self.row(d, "stable.bin")["status"], "unchanged")

    def test_t17_placeholder_hash_missing(self):
        tt.write(self.old_tree, "cloud.bin", b"cloud-data")
        self.clone()
        hook = lambda con, tree: con.execute(
            "UPDATE entries SET is_placeholder=1 WHERE rel_path='cloud.bin'")
        s1 = self.snap(self.old_tree, "old", post_enum=hook)
        s2 = self.snap(self.new_tree, "new", post_enum=hook)
        for s in (s1, s2):                       # 属性核查：从未被打开哈希
            con = sqlite3.connect(s)
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM hashes").fetchone()[0], 0)
            con.close()
        r = self.row(self.diff(s1, s2), "cloud.bin")
        self.assertEqual((r["status"], r["evidence"]),
                         ("hash_missing", "insufficient"))

    def test_t18_file_to_dir(self):
        tt.write(self.old_tree, "x", b"was-a-file")
        tt.write(self.old_tree, "s.bin", b"sibling")
        self.clone()
        os.remove(os.path.join(self.new_tree, "x"))
        tt.write(self.new_tree, os.path.join("x", "y.txt"), b"inside")
        d = self.diff(self.snap(self.old_tree, "old"),
                      self.snap(self.new_tree, "new"))
        self.assertEqual(self.row(d, "x")["status"], "deleted")
        dir_rows = [r for r in d["diff_dirs"] if r["path_key"] == "x"]
        self.assertEqual(len(dir_rows), 1)
        self.assertEqual(dir_rows[0]["status"], "added")

    def test_t19_path_key_collision_group_unknown(self):
        nfd = "café.bin"
        nfc = "café.bin"
        tt.write(self.old_tree, nfd, b"one")
        tt.write(self.old_tree, nfc, b"two")
        self.clone()
        d = self.diff(self.snap(self.old_tree, "old"),
                      self.snap(self.new_tree, "new"))
        rows = [r for r in d["diff_entries"]
                if r["reason"] == "path_key_collision"]
        self.assertEqual(len(rows), 4)                  # 两侧各两行逐行列出
        self.assertEqual({r["status"] for r in rows}, {"unknown"})
        rels = {r["old_rel_path"] for r in rows} | {r["new_rel_path"]
                                                    for r in rows}
        self.assertIn(nfd, rels)
        self.assertIn(nfc, rels)

    def test_t20_backup_workflow_all_unchanged(self):
        main = os.path.join(self.base, "Main2024")
        os.makedirs(main)
        tt.write(main, "f1.bin", b"content-1")
        tt.write(main, os.path.join("sub", "f2.bin"), b"content-2")
        backup = os.path.join(self.base, "Backup2024")   # 顶层名不同
        shutil.copytree(main, backup)                    # copy2 保 mtime，创建时间自变
        d = self.diff(self.snap(main, "main", label="Archive2024"),
                      self.snap(backup, "backup", label="Archive2024"))
        self.assertEqual({r["status"] for r in d["diff_entries"]},
                         {"unchanged"})
        self.assertEqual({r["status"] for r in d["diff_dirs"]},
                         {"unchanged"})                  # 内容与结构双维度一致

    def test_fault_locked_file_failed_and_continue(self):
        import msvcrt
        tt.write(self.old_tree, "ok.bin", b"fine")
        tt.write(self.old_tree, "locked.bin", b"locked-bytes")
        self.clone()
        s1 = self.snap(self.old_tree, "old")
        partial = os.path.join(self.snaps, "Scan_new.partial.sqlite")
        con = core.create_partial_snapshot(partial, [("T", self.new_tree)],
                                           config={"phase": "test"})
        core.enumerate_and_reconcile(con)
        lf = open(os.path.join(self.new_tree, "locked.bin"), "r+b")
        try:
            msvcrt.locking(lf.fileno(), msvcrt.LK_NBLCK, 12)   # 独占字节区
            stats = dbh.process_hash_stage(con, "full")        # 不崩溃
        finally:
            msvcrt.locking(lf.fileno(), msvcrt.LK_UNLCK, 12)
            lf.close()
        self.assertEqual(stats["error"], 1)
        self.assertEqual(stats["done"], 1)
        st = dict(con.execute("SELECT rel_path, hash_status FROM entries"))
        self.assertEqual(st["locked.bin"], "error")
        self.assertEqual(st["ok.bin"], "done")
        n_err = con.execute("SELECT COUNT(*) FROM errors"
                            " WHERE stage='hash'").fetchone()[0]
        self.assertEqual(n_err, 1)
        con.execute("UPDATE entries SET meta_status='skipped',"
                    " hash_status='skipped' WHERE hash_status='error'")
        con.execute("UPDATE entries SET meta_status='skipped'"
                    " WHERE meta_status='pending'")
        con.commit()
        s2 = core.finalize_snapshot(con, partial, "full")      # 与正式 Full 一致
        d = self.diff(s1, s2)
        self.assertEqual(self.row(d, "locked.bin")["status"], "hash_missing")

    def test_fault_long_path_over_260(self):
        deep_rel = "\\".join(["d" + "x" * 30] * 10)            # 逐级超长
        top = os.path.join(self.old_tree, deep_rel.split("\\")[0])
        target = os.path.join(self.old_tree, deep_rel)
        os.makedirs(core.to_extended_path(target))
        try:
            with open(core.to_extended_path(
                    os.path.join(target, "deep.bin")), "wb") as f:
                f.write(b"deep-data")
            s1 = self.snap(self.old_tree, "old")
            con = sqlite3.connect(s1)
            row = con.execute(
                "SELECT e.rel_path, e.hash_status, h.hash_hex FROM entries e"
                " JOIN hashes h ON h.entry_id=e.entry_id").fetchone()
            con.close()
            self.assertTrue(row[0].endswith("deep.bin"))
            self.assertGreater(len(os.path.join(self.old_tree, row[0])), 260)
            self.assertEqual(row[1], "done")
            self.assertEqual(row[2],
                             hashlib.sha256(b"deep-data").hexdigest())
        finally:
            shutil.rmtree(core.to_extended_path(top))   # 长路径须在 tearDown 前清

    def test_metadata_extraction_changed_and_null(self):
        tt.write(self.old_tree, "m.bin", b"meta-data")
        self.clone()

        def inject(sha):
            def f(con):
                eid, = con.execute("SELECT entry_id FROM entries"
                                   " WHERE rel_path='m.bin'").fetchone()
                con.execute(
                    "INSERT INTO raw_payloads (entry_id, provider,"
                    " payload_zlib, payload_sha256, uncompressed_bytes,"
                    " provider_version, profile_version, parsed_at_utc)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (eid, "exiftool", b"x", sha, 1, "13.59", 1, "t"))
            return f

        s1 = self.snap(self.old_tree, "old", pre_finalize=inject("aa" * 32))
        s2 = self.snap(self.new_tree, "new", pre_finalize=inject("bb" * 32))
        r = self.row(self.diff(s1, s2), "m.bin")
        self.assertEqual(r["status"], "metadata_extraction_changed")
        self.assertEqual(r["metadata_changed"], 1)
        # 一侧无 payload → 未评估（NULL），状态按属性落 unchanged
        s3 = self.snap(self.new_tree, "new2")
        r2 = self.row(self.diff(s1, s3), "m.bin")
        self.assertEqual(r2["status"], "unchanged")
        self.assertIsNone(r2["metadata_changed"])

    def test_file_access_date_is_ignored_but_other_payload_changes_are_not(self):
        tt.write(self.old_tree, "m.bin", b"meta-data")
        self.clone()

        def inject(access_date, model):
            document = {
                "SourceFile": "m.bin",
                "System:Main:FileAccessDate": {
                    "id": 0,
                    "val": access_date,
                },
                "EXIF:Main:Model": {
                    "id": 272,
                    "val": model,
                },
            }
            payload = meta.make_payload(document)

            def apply(con):
                eid, = con.execute(
                    "SELECT entry_id FROM entries WHERE rel_path='m.bin'"
                ).fetchone()
                con.execute(
                    "INSERT INTO raw_payloads (entry_id, provider,"
                    " payload_zlib, payload_sha256, uncompressed_bytes,"
                    " provider_version, profile_version, parsed_at_utc)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (eid, "exiftool", payload.zlib_blob, payload.sha256,
                     payload.uncompressed_bytes, "13.59", 1, "t"))
            return apply

        s1 = self.snap(
            self.old_tree, "old",
            pre_finalize=inject("2026:01:01 00:00:00+00:00", "Same"))
        s2 = self.snap(
            self.new_tree, "new",
            pre_finalize=inject("2026:01:02 00:00:00+00:00", "Same"))
        old_con = sqlite3.connect(s1)
        new_con = sqlite3.connect(s2)
        try:
            old_sha, = old_con.execute(
                "SELECT payload_sha256 FROM raw_payloads").fetchone()
            new_sha, = new_con.execute(
                "SELECT payload_sha256 FROM raw_payloads").fetchone()
        finally:
            old_con.close()
            new_con.close()
        self.assertNotEqual(old_sha, new_sha)
        access_only = self.row(self.diff(s1, s2), "m.bin")
        self.assertEqual(access_only["status"], "unchanged")
        self.assertEqual(access_only["metadata_changed"], 0)

        s3 = self.snap(
            self.new_tree, "new2",
            pre_finalize=inject("2026:01:03 00:00:00+00:00", "Changed"))
        actual_change = self.row(self.diff(s1, s3), "m.bin")
        self.assertEqual(
            actual_change["status"], "metadata_extraction_changed")
        self.assertEqual(actual_change["metadata_changed"], 1)

    def test_exiftool_root_path_fields_are_ignored(self):
        tt.write(self.old_tree, "m.bin", b"meta-data")
        self.clone()

        def inject(source_file, directory):
            document = {
                "SourceFile": source_file,
                "System:Main:Directory": {
                    "id": 0,
                    "val": directory,
                },
                "EXIF:Main:Model": {
                    "id": 272,
                    "val": "Same",
                },
            }
            payload = meta.make_payload(document)

            def apply(con):
                eid, = con.execute(
                    "SELECT entry_id FROM entries WHERE rel_path='m.bin'"
                ).fetchone()
                con.execute(
                    "INSERT INTO raw_payloads (entry_id, provider,"
                    " payload_zlib, payload_sha256, uncompressed_bytes,"
                    " provider_version, profile_version, parsed_at_utc)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (eid, "exiftool", payload.zlib_blob, payload.sha256,
                     payload.uncompressed_bytes, "13.59", 1, "t"))
            return apply

        s1 = self.snap(
            self.old_tree, "old",
            pre_finalize=inject(
                r"E:\OldRoot\m.bin", r"E:\OldRoot"))
        s2 = self.snap(
            self.new_tree, "new",
            pre_finalize=inject(
                r"F:\MovedRoot\m.bin", r"F:\MovedRoot"))
        row = self.row(self.diff(s1, s2), "m.bin")
        self.assertEqual(row["status"], "unchanged")
        self.assertEqual(row["metadata_changed"], 0)


class TestFilenameSha256High32(unittest.TestCase):
    def test_extract_match_and_tamper_detection(self):
        with tempfile.TemporaryDirectory() as td:
            partial = os.path.join(td, "A.partial.sqlite")
            with open(partial, "wb") as f:
                f.write(b"fake-db")
            digest = core.sha256_file(partial)
            final = os.path.join(
                td, (f"A_Full_2026-01-01_00-00-00.000000_1234abcd_"
                     f"{digest[:8].upper()}.sqlite"))
            os.rename(partial, final)
            self.assertEqual(core.filename_sha256_high32(final), digest[:8].upper())
            self.assertTrue(core.filename_sha256_high32_matches(final))
            with open(final, "ab") as f:
                f.write(b"tamper")
            self.assertFalse(core.filename_sha256_high32_matches(final))

    def test_missing_or_malformed_suffix_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            lone = os.path.join(td, "x.sqlite")
            with open(lone, "wb") as f:
                f.write(b"d")
            self.assertIsNone(core.filename_sha256_high32(lone))
            self.assertIsNone(core.filename_sha256_high32_matches(lone))
            self.assertIsNone(core.filename_sha256_high32(
                os.path.join(td, "x_1234567.sqlite")))
            self.assertEqual(core.filename_sha256_high32(
                os.path.join(td, "x_12345678.sqlite")), "12345678")
            self.assertIsNone(core.filename_sha256_high32(
                os.path.join(td, "x_1234abcd_abcdef12.sqlite")))
            self.assertIsNone(core.filename_sha256_high32(
                os.path.join(td, "x_SHA8-12345678.sqlite")))


class TestSnapshotNaming(unittest.TestCase):
    def test_final_format_stops_at_seconds(self):
        n = core.snapshot_name(["Archive2024"], "Full")
        self.assertRegex(
            n, r"^Archive2024_Full_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}"
               r"$")

    def test_multi_root_join_and_sanitize(self):
        n = core.snapshot_name(["A:B", "C|D"], "Quick")
        self.assertTrue(n.startswith("A_B+C_D_Quick_"), n)

    def test_same_second_final_stem_stable_working_names_unique(self):
        with patch.object(core.time, "strftime",
                          return_value="2026-08-03_12-34-56"):
            stems = {core.snapshot_name(["X"], "Quick") for _ in range(8)}
        self.assertEqual(stems, {"X_Quick_2026-08-03_12-34-56"})
        stem = next(iter(stems))
        working = {core.snapshot_working_name(stem) for _ in range(8)}
        self.assertEqual(len(working), 8)
        for name in working:
            self.assertRegex(
                name,
                r"^X_Quick_2026-08-03_12-34-56\.\d{6}_[0-9a-f]{8}$")

    def test_deviation_only_profile_tokens(self):
        self.assertEqual(core.snapshot_profile_tokens("full"), [])
        self.assertEqual(
            core.snapshot_profile_tokens(
                "full", "none", raw_payload=False, file_id=False),
            ["No-Hash", "Basic-Metadata", "No-FID"])
        self.assertEqual(
            core.snapshot_profile_tokens("full", "incremental"),
            ["Hash-Inc"])
        self.assertEqual(core.snapshot_profile_tokens("quick"), [])
        self.assertEqual(
            core.snapshot_profile_tokens("quick", file_id=False),
            ["No-FID"])
        name = core.snapshot_name(
            ["A"], "Full", ["No-Hash", "Basic-Metadata", "No-FID"])
        self.assertTrue(
            name.startswith("A_Full_No-Hash_Basic-Metadata_No-FID_"))


class TestQuickScan(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.arch = os.path.join(self._td.name, "Arch快速")
        os.makedirs(os.path.join(self.arch, "sub 目录"))
        for rel, data in [("a.CR3", b"raw-like"), ("b.txt", b"hello"),
                          (os.path.join("sub 目录", "c.MP4"), b"mp4-like")]:
            with open(os.path.join(self.arch, rel), "wb") as f:
                f.write(data)
        self.out = os.path.join(self._td.name, "Snap")
        os.makedirs(self.out)

    def tearDown(self):
        self._td.cleanup()

    def _run_quick(self):
        script = os.path.join(
            _MODULE, "Script_DAISY_Module_DBS_12_Quick_Scan.py")
        r = subprocess.run([sys.executable, "-B", script,
                            "--root", f"Q={self.arch}",
                            "--output-dir", self.out, "--quiet"],
                           capture_output=True, timeout=120)
        stderr = r.stderr.decode("utf-8", "replace")
        self.assertEqual(r.returncode, 0, stderr)
        self.assertNotIn("本次运行含异常", stderr)
        snaps = [f for f in os.listdir(self.out) if f.endswith(".sqlite")]
        self.assertEqual(len(snaps), 1)
        self.assertRegex(
            snaps[0],
            r"^Q_Quick_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}"
            r"_[0-9A-F]{8}\.sqlite$",
            "封存命名须精确到秒并包含 SHA-256 高32bit大写指纹")
        return os.path.join(self.out, snaps[0])

    def test_quick_scan_end_to_end(self):
        final = self._run_quick()
        # 成功封存只留下单一 SQLite；运行事件和清单已内嵌。
        self.assertEqual(os.listdir(self.out), [os.path.basename(final)])
        self.assertEqual(
            core.filename_sha256_high32(final), core.sha256_file(final)[:8].upper())
        con = sqlite3.connect(f"file:{final}?mode=ro", uri=True)
        status, cov, et_ver, config = con.execute(
            "SELECT scan_status, hash_coverage, exiftool_version,"
            " config_json FROM snapshot_info").fetchone()
        self.assertEqual((status, cov), ("complete", "none"))
        self.assertIsNone(et_ver)                # 全程未接触外部工具
        self.assertIn("quick", config)
        rows = {r[0]: r for r in con.execute(
            "SELECT rel_path, media_kind, size_bytes, modified_at_utc,"
            " created_at_utc, meta_status, hash_status, volume_serial,"
            " file_index_hex FROM entries")}
        self.assertEqual(set(rows), {"a.CR3", "b.txt", "sub 目录\\c.MP4"})
        self.assertEqual(rows["a.CR3"][1], "photo_raw")
        self.assertEqual(rows["b.txt"][2], 5)
        for rel, r in rows.items():
            self.assertTrue(r[3].endswith("Z"), rel)         # UTC 时间戳
            self.assertEqual(r[6], "skipped", rel)           # 哈希一律跳过
            self.assertIsNotNone(r[7], rel)                  # file_id 照常采集
        self.assertEqual(rows["a.CR3"][5], "skipped")        # 媒体：元数据跳过
        self.assertEqual(rows["b.txt"][5], "not_applicable")  # other 照常
        for tbl in ("hashes", "photo_metadata", "video_gps_points",
                    "raw_payloads"):
            self.assertEqual(con.execute(
                f"SELECT COUNT(*) FROM {tbl}").fetchone()[0], 0, tbl)
        manifest = json.loads(con.execute(
            "SELECT manifest_json FROM snapshot_manifest").fetchone()[0])
        self.assertEqual(manifest["effective_profile"]["scan_kind"], "quick")
        self.assertEqual(
            manifest["integrity"]["token_format"],
            "<first-8-hex-uppercase>")
        self.assertEqual(
            manifest["integrity"]["bit_selection"], "most_significant_32")
        self.assertEqual(manifest["integrity"]["hex_case"], "upper")
        self.assertFalse(manifest["integrity"]["full_digest_retained"])
        self.assertEqual(manifest["filename_layout_version"], 2)
        self.assertNotRegex(manifest["snapshot_stem"],
                            r"\.\d{6}_[0-9a-f]{8}$")
        events = con.execute(
            "SELECT event FROM run_events ORDER BY event_seq").fetchall()
        self.assertGreater(len(events), 1)
        self.assertEqual(events[-1][0], "snapshot_sealed")
        con.close()

    def test_quick_snapshot_diff_compatible(self):
        quick = self._run_quick()        # 文件名高32bit指纹准入通过
        full = tt.build_snapshot(self.arch, self.out, "full", label="Q")
        out = os.path.join(self._td.name, "d.diff.sqlite")
        dbdiff.compare(quick, full, out)         # 快扫快照可直接参与 Diff
        con = sqlite3.connect(out)
        st = {r[0] for r in con.execute("SELECT status FROM diff_entries")}
        self.assertEqual(st, {"hash_missing"})   # 单侧无哈希→如实 hash_missing
        cov = con.execute("SELECT old_hash_coverage, new_hash_coverage"
                          " FROM diff_info").fetchone()
        self.assertEqual(cov, ("none", "full"))
        con.close()

    def test_diff_cli_output_named_with_labels(self):
        s1 = tt.build_snapshot(self.arch, self.out, "d1", label="测试库")
        s2 = tt.build_snapshot(self.arch, self.out, "d2", label="测试库")
        diffs = os.path.join(self._td.name, "Diffs")
        script = os.path.join(
            _MODULE, "Script_DAISY_Module_DBS_21_Diff.py")
        r = subprocess.run([sys.executable, "-B", script,
                            "--old", s1, "--new", s2,
                            "--output-dir", diffs], capture_output=True,
                           timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        names = sorted(os.listdir(diffs))
        dbs = [n for n in names if n.endswith(".sqlite")]
        self.assertEqual(len(dbs), 1)
        self.assertRegex(
            dbs[0],
            r"^测试库_Diff_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}"
            r"_[0-9A-F]{8}\.sqlite$")
        self.assertEqual(names, dbs)
        final = os.path.join(diffs, dbs[0])
        self.assertEqual(
            core.filename_sha256_high32(final), core.sha256_file(final)[:8].upper())

    def test_forced_diff_adds_same_folder_issue_report(self):
        s1 = tt.build_snapshot(self.arch, self.out, "f1", label="测试库")
        s2 = tt.build_snapshot(self.arch, self.out, "f2", label="测试库")
        token = core.filename_sha256_high32(s2)
        legacy = s2[:-len(f"_{token}.sqlite")] + ".sqlite"
        os.rename(s2, legacy)
        diffs = os.path.join(self._td.name, "ForcedDiffs")
        script = os.path.join(
            _MODULE, "Script_DAISY_Module_DBS_21_Diff.py")
        result = subprocess.run(
            [sys.executable, "-B", script, "--old", s1, "--new", legacy,
             "--output-dir", diffs, "--force"],
            capture_output=True, timeout=120,
        )
        self.assertEqual(
            result.returncode, 0,
            result.stderr.decode("utf-8", "replace"))
        names = sorted(os.listdir(diffs))
        db, = [name for name in names if name.endswith(".sqlite")]
        issue, = [name for name in names if name.endswith("_Issues.md")]
        self.assertEqual(
            issue, os.path.splitext(db)[0] + "_Issues.md")
        with open(os.path.join(diffs, issue), encoding="utf-8") as handle:
            self.assertIn("降级准入", handle.read())


if __name__ == "__main__":
    unittest.main(verbosity=2)

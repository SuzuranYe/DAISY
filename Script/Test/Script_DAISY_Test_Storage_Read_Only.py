"""DAISY 只读边界与源代码回归测试。"""
from __future__ import annotations

import ast
import os
import sys
import unittest


_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPT_DIR = os.path.dirname(_TEST_DIR)
_LIB_DIR = os.path.join(_SCRIPT_DIR, "Lib")
sys.path.insert(0, _LIB_DIR)

import Script_DAISY_Lib_Storage_Core as core
import Script_DAISY_Lib_Storage_Windows as windows
import Script_DAISY_Lib_Storage_Smartctl as smartctl


class TestReadOnlyBoundary(unittest.TestCase):
    def test_powershell_inventory_has_no_mutating_cmdlet(self):
        for detailed in (False, True):
            script = windows._powershell_script(3, detailed=detailed)
            windows.assert_read_only_script(script)
            for command in windows.FORBIDDEN_STORAGE_COMMANDS:
                self.assertNotIn(command + " ", script)

    def test_smartctl_templates_are_fixed(self):
        device = core.SmartDevice("/dev/sdk", "sat", disk_number=10)
        commands = (
            smartctl.build_scan_command("smartctl.exe"),
            smartctl.build_read_command("smartctl.exe", device),
        )
        flattened = {argument for command in commands for argument in command[1:]}
        self.assertTrue(smartctl.MUTATING_OR_ACTIVE_OPTIONS.isdisjoint(flattened))
        self.assertEqual(commands[0][1:], ["--scan-open", "--json=c"])
        self.assertEqual(commands[1][1], "-x")

    def test_subprocess_calls_never_enable_shell(self):
        source_files = []
        for root, _directories, files in os.walk(_SCRIPT_DIR):
            if os.path.basename(root) == "Test":
                continue
            for name in files:
                if name.endswith(".py"):
                    source_files.append(os.path.join(root, name))
        for path in source_files:
            with open(path, "r", encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), filename=path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for keyword in node.keywords:
                    if keyword.arg != "shell":
                        continue
                    self.assertFalse(
                        isinstance(keyword.value, ast.Constant) and keyword.value.value is True,
                        path,
                    )


if __name__ == "__main__":
    unittest.main(verbosity=2)

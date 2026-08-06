r"""Script_DAISY_Module_DBS_41_Export_Report：DBS-41 旧报告导出兼容入口。

解析、CSV 与 XLSX 实现位于 Script_DAISY_Lib_DBS_07_Parse；本文件只保留
既有 CLI、退出码和供旧调用方使用的函数名。
"""
from __future__ import annotations

import argparse
import os
import sys

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_LIB_DIR = os.path.join(os.path.dirname(_MODULE_DIR), "Lib")
sys.path.insert(0, _LIB_DIR)
import Script_DAISY_Lib_DBS_01_Core as core
import Script_DAISY_Lib_DBS_07_Parse as dbparse


export_snapshot = dbparse.export_snapshot
export_diff = dbparse.export_diff

# v1.5.1 测试和第三方调用可能引用这两个既有内部符号；行为保持阶段继续导出。
_XLSX_MAX_CELL_CHARS = dbparse._XLSX_MAX_CELL_CHARS
_excel_row = dbparse._excel_row


def main() -> int:
    core.force_utf8_io()
    parser = argparse.ArgumentParser(
        description="DBS-41 结果报告导出（只读输入）")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--snapshot", help="封存快照 .sqlite")
    group.add_argument("--diff", help="Diff 数据库 .sqlite")
    parser.add_argument("--output-dir", default="Output/Reports")
    args = parser.parse_args()
    try:
        if args.snapshot:
            result = export_snapshot(args.snapshot, args.output_dir)
        else:
            result = export_diff(args.diff, args.output_dir)
    except core.PreflightError as exc:
        print(f"导出失败：{exc}", file=sys.stderr)
        return 2
    print(f"导出目录:{result['folder']}")
    for filename in result["files"]:
        print(f"  {filename}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

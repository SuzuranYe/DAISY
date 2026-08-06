r"""Script_DAISY_Module_DBS_31_Check_Hash：DBS-31 内容哈希核验兼容入口。

stat／哈希巡检和报告写出位于 Script_DAISY_Lib_DBS_06_Verify；本文件只保留
既有 CLI、退出码和供旧调用方使用的 patrol 函数名。

用法：
  python .\Script\Script_DAISY_MAIN.py check-hash --snapshot .\Output\Snapshots\Scan_x.sqlite ^
      --root "Archive2024=E:\Archive2024" [--sample-percent 1] [--full]
"""
from __future__ import annotations

import argparse
import os
import sys

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_LIB_DIR = os.path.join(os.path.dirname(_MODULE_DIR), "Lib")
sys.path.insert(0, _LIB_DIR)
import Script_DAISY_Lib_DBS_01_Core as core
import Script_DAISY_Lib_DBS_06_Verify as dbverify


# v1.5.1 Python 调用和测试可能引用这些既有符号。
dbh = dbverify.dbh
patrol = dbverify.patrol_hash


def main() -> int:
    core.force_utf8_io()
    parser = argparse.ArgumentParser(
        description="DBS-31 内容哈希核验：快照 vs 当前磁盘（只读）")
    parser.add_argument(
        "--snapshot", required=True, help="封存快照 .sqlite 路径")
    parser.add_argument(
        "--root", action="append", required=True,
        help="当前根目录；单根可直接给路径，多根须逐项 label=当前路径")
    parser.add_argument("--sample-percent", type=float, default=1.0)
    parser.add_argument("--full", action="store_true", help="全量哈希核对")
    parser.add_argument("--powershell-path")
    parser.add_argument(
        "--force", action="store_true",
        help="文件名高32bit指纹缺失时仍继续（不符仍拒绝）")
    parser.add_argument(
        "--report", help="报告 JSON 输出路径（默认 Output/Reports）")
    args = parser.parse_args()

    try:
        progress = core.Progress(1, 1, "内容哈希核验")
        report = patrol(
            args.snapshot,
            sample_percent=args.sample_percent,
            full=args.full,
            powershell=args.powershell_path,
            force=args.force,
            root_specs=args.root,
            on_progress=lambda index, total: progress.update(
                index, total=total),
        )
    except core.PreflightError as exc:
        print(f"巡检失败：{exc}", file=sys.stderr)
        return 2

    progress.finish(
        f"stat {report['stat_checked']:,} 条 / "
        f"哈希 {report['hash_checked']:,} 条")
    report_path, issue_report = dbverify.write_hash_report(
        report, args.report)

    print(f"快照：{report['snapshot']}（coverage={report['hash_coverage']}）")
    print(
        f"stat 核对：{report['stat_checked']:,} 条 | "
        f"缺失 {len(report['stat_missing'])} | "
        f"变化 {len(report['stat_changed'])}")
    print(
        f"哈希核对（{report['mode']}）：{report['hash_checked']:,}/"
        f"{report['hash_eligible']:,} 条 | "
        f"不一致 {len(report['hash_mismatched'])} | "
        f"工具错误 {len(report['hash_tool_error'])}")
    print(f"报告：{report_path}")
    if issue_report:
        print(f"问题报告：{issue_report}")
    if report["ok"]:
        print("结论：当前磁盘与基准快照一致（在本次核对口径内）")
        return 0
    print(
        "结论：发现差异——建议尽快做完整性复核（--hash full 全量重扫＋Diff）",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())

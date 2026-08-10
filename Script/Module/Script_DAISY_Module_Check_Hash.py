r"""Script_DAISY_Module_Check_Hash：哈希核验兼容入口。

文件状态／哈希核验和报告写出位于 Script_DAISY_Lib_Snapshot_Verify；本文件只保留
既有 CLI、退出码和供旧调用方使用的 patrol 函数名。

用法：
  python .\Script\Script_DAISY_CLI.py check-hash `
    --snapshot .\Output\Snapshots\Scan_x.sqlite `
    --root "Archive2024=E:\Archive2024" [--sample-percent 1] [--full]
"""
from __future__ import annotations

import argparse
import os
import sys

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_LIB_DIR = os.path.join(os.path.dirname(_MODULE_DIR), "Lib")
sys.path.insert(0, _LIB_DIR)
import Script_DAISY_Lib_Snapshot_Core as core
import Script_DAISY_Lib_Snapshot_Verify as dbverify


# v1.5.1 Python 调用和测试可能引用这些既有符号。
dbh = dbverify.dbh
patrol = dbverify.patrol_hash


def main() -> int:
    core.force_utf8_io()
    parser = argparse.ArgumentParser(
        description="兼容哈希核验入口：只读比较封存快照与当前档案")
    parser.add_argument(
        "--snapshot", required=True, help="封存快照路径 (.sqlite)")
    parser.add_argument(
        "--root", action="append", required=True,
        help="当前档案根目录；单根可直接给路径，多根逐项使用「根目录名=路径」")
    parser.add_argument(
        "--sample-percent", type=float, default=1.0,
        help="哈希核验抽样比例；默认 1%%")
    parser.add_argument("--full", action="store_true", help="全量核验 SHA-256")
    parser.add_argument("--powershell-path", help="PowerShell 可执行文件路径")
    parser.add_argument(
        "--force", action="store_true",
        help="允许缺少文件名指纹；指纹不一致仍拒绝")
    parser.add_argument(
        "--report", help="报告 JSON 输出路径（默认 Output/Reports）")
    args = parser.parse_args()

    try:
        progress = core.Progress(1, 1, "哈希核验")
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
        print(f"核验失败：{exc}", file=sys.stderr)
        return 2

    progress.finish(
        f"文件状态 {report['stat_checked']:,} 项／"
        f"哈希 {report['hash_checked']:,} 项")
    report_path, issue_report = dbverify.write_hash_report(
        report, args.report)

    coverage = {
        "full": "完整", "partial": "部分", "none": "无",
    }.get(str(report["hash_coverage"]), str(report["hash_coverage"]))
    mode = "全量" if report["mode"] == "full" else "抽样"
    print(f"快照：{report['snapshot']}（哈希覆盖：{coverage}）")
    print(
        f"文件状态：{report['stat_checked']:,} 项｜"
        f"缺失 {len(report['stat_missing'])}｜"
        f"变化 {len(report['stat_changed'])}")
    print(
        f"哈希核对（{mode}）：{report['hash_checked']:,}／"
        f"{report['hash_eligible']:,} 项｜"
        f"不一致 {len(report['hash_mismatched'])}｜"
        f"工具故障 {len(report['hash_tool_error'])}")
    print(f"报告：{report_path}")
    if issue_report:
        print(f"问题报告：{issue_report}")
    if report["ok"]:
        print("结论：在本次核对口径内未发现差异。")
        return 0
    print(
        "结论：发现差异。请查看问题报告；如需重新建库，可建立新的完整快照，"
        "再与基准快照进行对比。",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())

r"""Script_DAISY_Module_DBS_32_Check_Format：DBS-32 格式核验兼容入口。

格式判据、工具调用和报告写出位于 Script_DAISY_Lib_DBS_06_Verify；本文件只保留
既有 CLI、退出码和供旧调用方使用的函数名。

用法：
  python .\Script\Script_DAISY_MAIN.py check-format --snapshot .\Output\Snapshots\Scan_x.sqlite ^
      --root "Archive2024=E:\Archive2024" [--sample-percent 100]
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


# v1.5.1 Python 调用、测试和替换探针可能引用这些既有符号。
dbh = dbverify.dbh
meta = dbverify.meta
subprocess = dbverify.subprocess
_OLE_MAGIC = dbverify._OLE_MAGIC
_OOXML_EXTS = dbverify._OOXML_EXTS
_MEDIA_KINDS = dbverify._MEDIA_KINDS
_FFPROBE_KINDS = dbverify._FFPROBE_KINDS
_CORRUPT_RE = dbverify._CORRUPT_RE

validate_zip = dbverify.validate_zip
validate_pdf = dbverify.validate_pdf
validate_sevenzip = dbverify.validate_sevenzip
parse_et_text = dbverify.parse_et_text
classify_et_findings = dbverify.classify_et_findings
validate_media = dbverify.validate_media
_pick_validator = dbverify.pick_format_validator
validate_snapshot = dbverify.validate_format_snapshot


def validate_legacy_office(
    path: str,
    sevenzip: str,
) -> tuple[str, str | None]:
    """保留旧入口对 validate_sevenzip 替换点的运行时绑定。"""
    return dbverify.validate_legacy_office(
        path,
        sevenzip,
        sevenzip_validator=validate_sevenzip,
    )


def main() -> int:
    core.force_utf8_io()
    parser = argparse.ArgumentParser(
        description="DBS-32 文件结构核验（只读，独立报告）")
    parser.add_argument("--snapshot", required=True)
    parser.add_argument(
        "--root", action="append", required=True,
        help="当前根目录；单根可直接给路径，多根须逐项 label=当前路径")
    parser.add_argument("--sample-percent", type=float, default=100.0)
    parser.add_argument("--report-dir")
    parser.add_argument("--exiftool-path")
    parser.add_argument("--ffprobe-path")
    parser.add_argument("--sevenzip-path")
    parser.add_argument(
        "--force", action="store_true",
        help="文件名高32bit指纹缺失时仍继续（不符仍拒绝）")
    args = parser.parse_args()
    try:
        progress = core.Progress(1, 1, "格式校验")
        report = validate_snapshot(
            args.snapshot,
            sample_percent=args.sample_percent,
            report_dir=args.report_dir,
            exiftool=args.exiftool_path,
            ffprobe=args.ffprobe_path,
            sevenzip=args.sevenzip_path,
            force=args.force,
            root_specs=args.root,
            on_progress=lambda index, total: progress.update(
                index, total=total),
        )
    except core.PreflightError as exc:
        print(f"校验失败：{exc}", file=sys.stderr)
        return 2
    progress.finish(f"核对 {report['checked']:,} 条")
    print(
        f"核对 {report['checked']:,} 条 | "
        + "，".join(
            f"{key}={value:,}"
            for key, value in sorted(report["counts"].items())
        )
        + f" | 用时 {report['elapsed_s']}s"
    )
    for path in report["files"]:
        print(f"报告：{path}")
    if report["ok"]:
        print("结论：全部通过（在本次口径内）")
        return 0
    print(
        "结论：发现 invalid/missing——详见报告；结合哈希层交叉解读",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())

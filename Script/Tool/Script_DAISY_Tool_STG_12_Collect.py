"""Script_DAISY_Tool_STG_12_Collect：采集单块硬盘并生成指纹 ZIP。"""
from __future__ import annotations

import argparse
import json
import os
import sys

_TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPT_DIR = os.path.dirname(_TOOL_DIR)
_BASE = os.path.dirname(_SCRIPT_DIR)
_LIB_DIR = os.path.join(_SCRIPT_DIR, "Lib")
sys.path.insert(0, _LIB_DIR)

import Script_DAISY_Lib_STG_01_Core as core
import Script_DAISY_Lib_STG_04_Service as service
import Script_DAISY_Lib_STG_05_Archive as archive


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="只读采集单块硬盘并生成 DAISY ZIP")
    parser.add_argument("--disk-number", type=int, required=True)
    parser.add_argument(
        "--output-dir", default=os.path.join(_BASE, "Output", "Storage"))
    parser.add_argument("--smartctl-path")
    parser.add_argument("--powershell-path")
    parser.add_argument(
        "--summary-txt",
        action="store_true",
        help="在 ZIP 外同时输出简化 TXT 报告",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    try:
        scan_progress = core.Progress(
            1, 3, "确认物理硬盘身份", quiet=args.as_json)
        scan = service.scan_targets(
            smartctl_path=args.smartctl_path,
            powershell_path=args.powershell_path,
        )
        target = service.target_by_disk_number(scan, args.disk_number)
        scan_progress.finish(target.physical_label)
        collect_progress = core.Progress(
            2, 3, "只读采集硬盘信息", quiet=args.as_json)
        collection = service.collect_target(
            target,
            smartctl_path=scan.smartctl_executable or args.smartctl_path,
            powershell_path=args.powershell_path,
            smartctl_version=scan.smartctl_version,
        )
        collect_progress.finish(collection.collection_status)
        archive_progress = core.Progress(
            3, 3, "生成存储档案", quiet=args.as_json)
        result = archive.create_archive(
            collection,
            args.output_dir,
            summary_txt=args.summary_txt,
        )
        archive_progress.finish(os.path.basename(result.path))
    except core.DaisySmartError as exc:
        print(f"采集失败：{exc}", file=sys.stderr)
        return 2
    payload = {
        "archive": result.path,
        "collection_status": collection.collection_status,
        "complete": collection.is_complete,
        "zip_sha256": result.zip_sha256,
        "fingerprint": result.fingerprint,
        "internal_files": list(result.internal_files),
        "summary_report": result.summary_report_path,
    }
    core.emit_gui_event(
        "storage_archive_created",
        path=result.path,
        collection_status=collection.collection_status,
        summary_report=result.summary_report_path,
    )
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"归档：{result.path}")
        print(f"采集状态：{collection.collection_status}")
        print(f"ZIP SHA-256：{result.zip_sha256}")
        print(f"文件名指纹：{result.fingerprint}")
        if result.summary_report_path:
            print(f"简化 TXT：{result.summary_report_path}")
    if not collection.is_complete:
        print(
            "警告：smartctl 存在访问或命令层错误；ZIP 已保留为诊断归档，"
            "不能视为完整硬盘登记。",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

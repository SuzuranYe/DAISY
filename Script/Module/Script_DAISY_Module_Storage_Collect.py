"""Script_DAISY_Module_Storage_Collect：硬盘信息登记。

同一脚本提供页内列盘准备与单块硬盘只读采集，并生成指纹 ZIP。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPT_DIR = os.path.dirname(_MODULE_DIR)
_BASE = os.path.dirname(_SCRIPT_DIR)
_LIB_DIR = os.path.join(_SCRIPT_DIR, "Lib")
sys.path.insert(0, _LIB_DIR)

import Script_DAISY_Lib_Storage_Core as core
import Script_DAISY_Lib_Storage_Service as service
import Script_DAISY_Lib_Storage_Archive as archive


_LIST_MODE_ARGUMENT = "--list"
_COLLECTION_STATUS_LABELS = {
    "complete": "完整",
    "complete_with_warnings": "完整，但有提示",
    "incomplete": "不完整",
}


def _list_disks(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="检测硬盘：只读列出硬盘，并标明可用的 SMART 读取目标")
    parser.add_argument("--smartctl-path", help="smartctl 可执行文件路径")
    parser.add_argument("--powershell-path", help="PowerShell 可执行文件路径")
    parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="以 JSON 输出检测结果",
    )
    args = parser.parse_args(argv)
    try:
        progress = core.Progress(
            1, 1, "检测硬盘", quiet=args.as_json)
        scan = service.scan_targets(
            smartctl_path=args.smartctl_path,
            powershell_path=args.powershell_path,
        )
        progress.finish(f"检测到 {len(scan.targets)} 块硬盘")
    except core.DaisySmartError as exc:
        print(f"检测硬盘失败：{exc}", file=sys.stderr)
        return 2
    if args.as_json:
        print(json.dumps(scan.to_dict(), ensure_ascii=False, indent=2))
        return 0
    core.emit_gui_event(
        "storage_inventory",
        targets=[target.to_dict() for target in scan.targets],
        warnings=list(scan.warnings),
    )
    print("硬盘          盘符          资源管理器名称                    型号                         SMART 读取目标")
    print("-" * 112)
    for target in scan.targets:
        record = target.windows
        letters = ",".join(record.drive_letters) if record else "—"
        names = "；".join(record.explorer_names) if record else "—"
        model = record.model if record else "—"
        smart_state = (
            f"{target.smart_device.name} -d {target.smart_device.device_type}"
            if target.smart_device else "无可用目标"
        )
        print(
            f"{target.physical_label:<13} {letters:<13} "
            f"{names[:30]:<32} {model[:28]:<30} {smart_state}"
        )
    if scan.warnings:
        print("\n提示：", file=sys.stderr)
        for warning in scan.warnings:
            print(f"  - {warning}", file=sys.stderr)
    return 0


def _collect_disk(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="硬盘信息登记：只读采集单块硬盘并生成硬盘档案 ZIP")
    parser.add_argument(
        "--disk-number", type=int, required=True,
        help="Windows 磁盘编号，例如 3 表示 PhysicalDrive3",
    )
    parser.add_argument(
        "--output-dir", default=os.path.join(_BASE, "Output", "Storage"),
        help="硬盘档案输出目录；默认 Output/Storage",
    )
    parser.add_argument("--smartctl-path", help="smartctl 可执行文件路径")
    parser.add_argument("--powershell-path", help="PowerShell 可执行文件路径")
    parser.add_argument(
        "--summary-txt",
        action="store_true",
        help="在 ZIP 外同时生成简化报告 (TXT)",
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="以 JSON 输出归档结果",
    )
    args = parser.parse_args(argv)
    try:
        scan_progress = core.Progress(
            1, 3, "确认硬盘身份", quiet=args.as_json)
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
        collect_progress.finish(_COLLECTION_STATUS_LABELS.get(
            collection.collection_status, "状态未知"))
        archive_progress = core.Progress(
            3, 3, "生成硬盘档案", quiet=args.as_json)
        result = archive.create_archive(
            collection,
            args.output_dir,
            summary_txt=args.summary_txt,
        )
        archive_progress.finish(os.path.basename(result.path))
    except core.DaisySmartError as exc:
        print(f"硬盘信息登记失败：{exc}", file=sys.stderr)
        return 2
    payload = {
        "report_metadata": core.report_metadata("硬盘信息登记"),
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
        print(f"硬盘档案：{result.path}")
        status_label = _COLLECTION_STATUS_LABELS.get(
            collection.collection_status, "未知")
        print(f"采集状态：{status_label}")
        print(f"ZIP SHA-256：{result.zip_sha256}")
        print(f"文件名指纹：{result.fingerprint}")
        if result.summary_report_path:
            print(f"简化报告：{result.summary_report_path}")
    if not collection.is_complete:
        print(
            "警告：本次采集出现访问或命令错误；ZIP 已保留为诊断归档，"
            "不能视为完整的硬盘信息登记结果。",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == [_LIST_MODE_ARGUMENT]:
        return _list_disks(arguments[1:])
    return _collect_disk(arguments)


if __name__ == "__main__":
    raise SystemExit(main())

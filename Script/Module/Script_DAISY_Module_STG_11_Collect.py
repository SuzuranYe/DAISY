"""Script_DAISY_Module_STG_11_Collect：STG-11 硬盘信息登记。

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

import Script_DAISY_Lib_STG_01_Core as core
import Script_DAISY_Lib_STG_04_Service as service
import Script_DAISY_Lib_STG_05_Archive as archive


_LIST_MODE_ARGUMENT = "--list"


def _list_disks(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="STG-11 硬盘信息登记的内部列盘步骤")
    parser.add_argument("--smartctl-path")
    parser.add_argument("--powershell-path")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    try:
        progress = core.Progress(
            1, 1, "读取物理硬盘清单", quiet=args.as_json)
        scan = service.scan_targets(
            smartctl_path=args.smartctl_path,
            powershell_path=args.powershell_path,
        )
        progress.finish(f"发现 {len(scan.targets)} 个目标")
    except core.DaisySmartError as exc:
        print(f"扫描失败：{exc}", file=sys.stderr)
        return 2
    if args.as_json:
        print(json.dumps(scan.to_dict(), ensure_ascii=False, indent=2))
        return 0
    core.emit_gui_event(
        "storage_inventory",
        targets=[target.to_dict() for target in scan.targets],
        warnings=list(scan.warnings),
    )
    print("物理盘        盘符          资源管理器名称                    型号                         SMART")
    print("-" * 112)
    for target in scan.targets:
        record = target.windows
        letters = ",".join(record.drive_letters) if record else "—"
        names = "；".join(record.explorer_names) if record else "—"
        model = record.model if record else "—"
        smart_state = (
            f"{target.smart_device.name} -d {target.smart_device.device_type}"
            if target.smart_device else "不可用"
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
        description="STG-11 硬盘信息登记：只读采集单块硬盘并生成 DAISY ZIP")
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
        "report_metadata": core.report_metadata("STG-11 硬盘信息登记"),
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


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == [_LIST_MODE_ARGUMENT]:
        return _list_disks(arguments[1:])
    return _collect_disk(arguments)


if __name__ == "__main__":
    raise SystemExit(main())

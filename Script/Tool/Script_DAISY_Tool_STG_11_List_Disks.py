"""Script_DAISY_Tool_STG_11_List_Disks：列出物理盘及 smartctl 关联。"""
from __future__ import annotations

import argparse
import json
import os
import sys

_TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
_LIB_DIR = os.path.join(os.path.dirname(_TOOL_DIR), "Lib")
sys.path.insert(0, _LIB_DIR)

import Script_DAISY_Lib_STG_01_Core as core
import Script_DAISY_Lib_STG_04_Service as service


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="列出可登记的物理硬盘")
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


if __name__ == "__main__":
    raise SystemExit(main())

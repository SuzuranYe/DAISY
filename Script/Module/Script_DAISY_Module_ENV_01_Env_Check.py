"""Script_DAISY_Module_ENV_01_Env_Check：ENV-01 运行环境检测。

只检查 DAISY 所需工具、版本、冒烟样本、只读断言与 SHA-256 自检；不读取
用户档案，也不执行性能测试。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_LIB_DIR = os.path.join(os.path.dirname(_MODULE_DIR), "Lib")
sys.path.insert(0, _LIB_DIR)
import Script_DAISY_Lib_01_Core as core
import Script_DAISY_Lib_03_Hash as dbh
import Script_DAISY_Lib_STG_01_Core as storage_core
import Script_DAISY_Lib_STG_02_Windows as storage_windows
import Script_DAISY_Lib_STG_03_Smartctl as smartctl


_TOOL_DISPLAY_NAMES = {
    "exiftool": "ExifTool",
    "ffprobe": "ffprobe",
    "sevenzip": "7-Zip",
    "powershell": "PowerShell",
    "smartctl": "smartctl",
}
_GUI_INSTALLABLE_TOOLS = frozenset(
    ("exiftool", "ffprobe", "sevenzip", "smartctl"))


def inspect_local_tools(
    explicit: dict[str, str | None],
) -> tuple[dict[str, dict], list[dict[str, object]]]:
    """独立检查每项工具，使 GUI 能一次列全本机版本与缺失项。"""
    tools: dict[str, dict] = {}
    issues: list[dict[str, object]] = []
    for name in ("exiftool", "ffprobe", "sevenzip"):
        try:
            path = core.discover_tool(name, explicit.get(name))
            tools[name] = core.resolved_tool_info(
                name, path, explicit=bool(explicit.get(name)))
        except core.PreflightError as exc:
            issues.append({
                "name": name,
                "display": _TOOL_DISPLAY_NAMES[name],
                "installable": name in _GUI_INSTALLABLE_TOOLS,
                "reason": str(exc),
            })
    try:
        ps_path, ps_version = dbh.discover_powershell(
            explicit.get("powershell"))
        tools["powershell"] = core.resolved_tool_info(
            "powershell", ps_path,
            explicit=bool(explicit.get("powershell")),
            version=ps_version,
        )
    except core.PreflightError as exc:
        issues.append({
            "name": "powershell",
            "display": _TOOL_DISPLAY_NAMES["powershell"],
            "installable": False,
            "reason": str(exc),
        })
    try:
        smartctl_path = smartctl.find_smartctl(explicit.get("smartctl"))
        smartctl_version = smartctl.require_supported_version(
            smartctl.version(smartctl_path))
        tools["smartctl"] = core.resolved_tool_info(
            "smartctl", str(smartctl_path),
            explicit=bool(explicit.get("smartctl")),
            version=smartctl_version,
        )
    except storage_core.DaisySmartError as exc:
        issues.append({
            "name": "smartctl",
            "display": _TOOL_DISPLAY_NAMES["smartctl"],
            "installable": True,
            "reason": str(exc),
        })
    return tools, issues


def main() -> int:
    core.force_utf8_io()
    ap = argparse.ArgumentParser(description="ENV-01 运行环境检测")
    ap.add_argument("--output-dir", default="Output/Reports")
    ap.add_argument("--exiftool-path")
    ap.add_argument("--ffprobe-path")
    ap.add_argument("--sevenzip-path")
    ap.add_argument("--powershell-path")
    ap.add_argument("--smartctl-path")
    args = ap.parse_args()

    print("== DAISY ENV-01 运行环境检测 ==")
    prog = core.Progress(1, 1, "运行环境检测")
    explicit = {
        "exiftool": args.exiftool_path,
        "ffprobe": args.ffprobe_path,
        "sevenzip": args.sevenzip_path,
        "powershell": args.powershell_path,
        "smartctl": args.smartctl_path,
    }
    inventory, issues = inspect_local_tools(explicit)
    core.emit_gui_event(
        "environment_inventory", tools=inventory, missing=issues)
    if inventory:
        core.emit_gui_event("tools_detected", tools=inventory)
    print("本机工具版本：")
    for name in (
            "exiftool", "ffprobe", "sevenzip", "powershell", "smartctl"):
        info = inventory.get(name)
        if info:
            print(
                f"  {_TOOL_DISPLAY_NAMES[name]:<10} "
                f"{info['version']:<12} {info['path']}")
    if issues:
        print("缺失或不可用：", file=sys.stderr)
        for issue in issues:
            print(
                f"  {issue['display']}：{issue['reason']}",
                file=sys.stderr,
            )
        return 2

    try:
        tools = core.run_preflight(
            {"exiftool": args.exiftool_path, "ffprobe": args.ffprobe_path,
             "sevenzip": args.sevenzip_path}, output_dir=None)
        ps_path, ps_version = dbh.discover_powershell(args.powershell_path)
        tools["powershell"] = core.resolved_tool_info(
            "powershell", ps_path, explicit=bool(args.powershell_path),
            version=ps_version)
        smart_scan = smartctl.scan(args.smartctl_path)
        if not smart_scan.devices:
            raise core.PreflightError(
                "smartctl 只读扫描未发现物理硬盘；请检查权限、驱动或转接盒。")
        tools["smartctl"] = core.resolved_tool_info(
            "smartctl", smart_scan.executable,
            explicit=bool(args.smartctl_path),
            version=smart_scan.version,
        )
        storage_inventory = storage_windows.read_inventory(
            detailed=False,
            powershell=args.powershell_path,
            timeout=60,
        )
        if not storage_inventory.records:
            raise core.PreflightError("Windows 存储查询未发现物理硬盘。")
        with tempfile.TemporaryDirectory() as td:
            sample = os.path.join(td, "powershell_hash_smoke.bin")
            with open(sample, "wb") as f:
                f.write(b"abc")
            expected = core.sha256_file(sample)
            actual = dbh.get_filehash_batch(
                [sample], powershell=ps_path)[0]
            if actual != expected:
                raise core.PreflightError(
                    "PowerShell Get-FileHash 冒烟测试失败")
        core.emit_gui_event(
            "tools_detected", tools=tools)
    except (core.PreflightError, storage_core.DaisySmartError) as exc:
        print(f"环境不就绪：\n{exc}", file=sys.stderr)
        return 2
    print(
        "  SHA-256 NIST 向量 / 五工具冒烟＋存储只读查询断言：通过")

    report = {**core.report_metadata("ENV-01 运行环境检测"),
              "generated_at_utc": core.now_utc_iso(),
              "scanner_version": core.SCANNER_VERSION, "tools": tools,
              "checks": {"sha256_nist": "passed",
                         "tool_smoke_readonly": "passed",
                         "powershell_get_filehash": "passed",
                         "smartctl_readonly_scan": "passed",
                         "windows_storage_inventory": "passed"}}
    os.makedirs(args.output_dir, exist_ok=True)
    out = os.path.join(
        args.output_dir,
        f"Env_Check_{time.strftime('%Y-%m-%d_%H-%M-%S')}.json")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    prog.finish("五工具、版本、冒烟、存储只读查询与 SHA-256 自检通过")
    print(f"\n报告：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

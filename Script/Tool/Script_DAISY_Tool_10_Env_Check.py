"""Script_DAISY_Tool_10_Env_Check：正式运行环境检查。

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

_TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
_LIB_DIR = os.path.join(os.path.dirname(_TOOL_DIR), "Lib")
sys.path.insert(0, _LIB_DIR)
import Script_DAISY_Lib_01_Core as core
import Script_DAISY_Lib_03_Hash as dbh


_TOOL_DISPLAY_NAMES = {
    "exiftool": "ExifTool",
    "ffprobe": "ffprobe",
    "sevenzip": "7-Zip",
    "powershell": "PowerShell",
}
_GUI_INSTALLABLE_TOOLS = frozenset(("exiftool", "ffprobe", "sevenzip"))


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
    return tools, issues


def main() -> int:
    core.force_utf8_io()
    ap = argparse.ArgumentParser(description="DAISY 运行环境检查")
    ap.add_argument("--output-dir", default="Output/Reports")
    ap.add_argument("--exiftool-path")
    ap.add_argument("--ffprobe-path")
    ap.add_argument("--sevenzip-path")
    ap.add_argument("--powershell-path")
    args = ap.parse_args()

    print("== DAISY 运行环境检查 ==")
    prog = core.Progress(1, 1, "环境检查")
    explicit = {
        "exiftool": args.exiftool_path,
        "ffprobe": args.ffprobe_path,
        "sevenzip": args.sevenzip_path,
        "powershell": args.powershell_path,
    }
    inventory, issues = inspect_local_tools(explicit)
    core.emit_gui_event(
        "environment_inventory", tools=inventory, missing=issues)
    if inventory:
        core.emit_gui_event("tools_detected", tools=inventory)
    print("本机工具版本：")
    for name in ("exiftool", "ffprobe", "sevenzip", "powershell"):
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
    except core.PreflightError as exc:
        print(f"环境不就绪：\n{exc}", file=sys.stderr)
        return 2
    print("  SHA-256 NIST 向量 / 四工具冒烟＋只读断言：通过")

    report = {"generated_at_utc": core.now_utc_iso(),
              "scanner_version": core.SCANNER_VERSION, "tools": tools,
              "checks": {"sha256_nist": "passed",
                         "tool_smoke_readonly": "passed",
                         "powershell_get_filehash": "passed"}}
    os.makedirs(args.output_dir, exist_ok=True)
    out = os.path.join(
        args.output_dir,
        f"Env_Check_{time.strftime('%Y-%m-%d_%H-%M-%S')}.json")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    prog.finish("四工具、版本、冒烟、只读断言与 SHA-256 自检通过")
    print(f"\n报告：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

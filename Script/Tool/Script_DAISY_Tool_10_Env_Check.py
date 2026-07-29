"""Script_DAISY_Tool_10_Env_Check：正式运行环境检查。

只检查 DAISY 所需工具、版本、冒烟样本、只读断言与 SHA-256 自检；不读取
用户档案，也不执行性能测试。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

_TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
_LIB_DIR = os.path.join(os.path.dirname(_TOOL_DIR), "Lib")
sys.path.insert(0, _LIB_DIR)
import Script_DAISY_Lib_01_Core as core


def main() -> int:
    core.force_utf8_io()
    ap = argparse.ArgumentParser(description="DAISY 运行环境检查")
    ap.add_argument("--output-dir", default="Output/Reports")
    ap.add_argument("--exiftool-path")
    ap.add_argument("--ffprobe-path")
    ap.add_argument("--sevenzip-path")
    args = ap.parse_args()

    print("== DAISY 运行环境检查 ==")
    prog = core.Progress(1, 1, "环境检查")
    try:
        tools = core.run_preflight(
            {"exiftool": args.exiftool_path, "ffprobe": args.ffprobe_path,
             "sevenzip": args.sevenzip_path}, output_dir=None)
    except core.PreflightError as exc:
        print(f"环境不就绪：\n{exc}", file=sys.stderr)
        return 2
    for name, info in tools.items():
        print(f"  {name:<9} {info['version']:<8} {info['path']}")
    print("  SHA-256 NIST 向量 / 三工具冒烟＋只读断言：通过")

    report = {"generated_at_utc": core.now_utc_iso(),
              "scanner_version": core.SCANNER_VERSION, "tools": tools,
              "checks": {"sha256_nist": "passed",
                         "tool_smoke_readonly": "passed"}}
    os.makedirs(args.output_dir, exist_ok=True)
    out = os.path.join(
        args.output_dir,
        f"Env_Check_{time.strftime('%Y-%m-%d_%H-%M-%S')}.json")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    prog.finish("三工具、版本、冒烟、只读断言与 SHA-256 自检通过")
    print(f"\n报告：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

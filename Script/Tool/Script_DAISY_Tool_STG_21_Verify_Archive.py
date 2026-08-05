"""Script_DAISY_Tool_STG_21_Verify_Archive：核验存储 ZIP 结构与 CRC。"""
from __future__ import annotations

import argparse
import json
import os
import sys

_TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
_LIB_DIR = os.path.join(os.path.dirname(_TOOL_DIR), "Lib")
sys.path.insert(0, _LIB_DIR)

import Script_DAISY_Lib_STG_01_Core as core
import Script_DAISY_Lib_STG_05_Archive as archive


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="核验 DAISY ZIP 归档")
    parser.add_argument("archive")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    try:
        progress = core.Progress(
            1, 1, "核验存储档案", quiet=args.as_json)
        result = archive.verify_archive(args.archive)
        progress.finish(f"{len(result.internal_files)} 个成员通过")
    except core.DaisySmartError as exc:
        print(f"核验失败：{exc}", file=sys.stderr)
        return 2
    collection_metadata = result.manifest.get("collection", {})
    collection_status = (
        collection_metadata.get("status")
        if isinstance(collection_metadata, dict) else None
    )
    payload = {
        "status": "passed",
        "collection_status": collection_status,
        "archive": result.path,
        "zip_sha256": result.zip_sha256,
        "fingerprint": result.fingerprint,
        "internal_files": list(result.internal_files),
        "archive_schema_version": result.manifest.get("archive_schema_version"),
    }
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("核验通过。")
        print(f"采集状态：{collection_status}")
        print(f"归档：{result.path}")
        print(f"ZIP SHA-256：{result.zip_sha256}")
        print(f"内部文件：{len(result.internal_files)} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

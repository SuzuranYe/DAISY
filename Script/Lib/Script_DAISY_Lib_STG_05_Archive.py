"""生成和核验单文件、不可覆盖的 DAISY ZIP 归档。"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import Script_DAISY_Lib_STG_01_Core as core


MEMBER_SUFFIXES = {
    "manifest": "Manifest.json",
    "smartctl_json": "Smartctl.json",
    "storage": "Storage.json",
}
MEMBER_ROLES = {
    "smartctl_json": "smartctl_raw_json",
    "storage": "windows_storage_json",
}
ARCHIVE_FILENAME_RE = re.compile(
    rf"^(?P<base>.+_{re.escape(core.ARCHIVE_KIND)}_"
    r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})_"
    r"(?P<fingerprint>[0-9A-Fa-f]{8})\.zip$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ArchiveResult:
    path: str
    zip_sha256: str
    fingerprint: str
    internal_files: tuple[str, ...]
    manifest: dict[str, Any]
    summary_report_path: str | None = None


@dataclass(frozen=True)
class VerificationResult:
    path: str
    zip_sha256: str
    fingerprint: str
    internal_files: tuple[str, ...]
    manifest: dict[str, Any]


def member_names(base: str) -> dict[str, str]:
    """返回当前 schema 的平铺成员名；base 不含 ZIP 指纹。"""
    if not base or Path(base).name != base or "/" in base or "\\" in base:
        raise core.DaisySmartError(f"归档成员名前缀无效：{base!r}")
    return {
        key: f"{base}_{suffix}"
        for key, suffix in MEMBER_SUFFIXES.items()
    }


def required_names(base: str) -> frozenset[str]:
    return frozenset(member_names(base).values())


def _payload_files(
    collection: core.CollectionResult,
    names: dict[str, str],
) -> dict[str, bytes]:
    return {
        names["smartctl_json"]: core.utf8_lf_bytes(collection.smart.raw_json),
        names["storage"]: core.utf8_lf_bytes(
            core.json_text(collection.windows.to_dict())
        ),
    }


def _manifest(
    collection: core.CollectionResult,
    payload_files: dict[str, bytes],
    names: dict[str, str],
    base: str,
    archive_created_at: datetime,
) -> dict[str, Any]:
    record = collection.windows
    files = {
        name: {
            "bytes": len(content),
            "role": MEMBER_ROLES[key],
        }
        for key, name in names.items()
        if key in MEMBER_ROLES
        for content in (payload_files[name],)
    }
    return {
        "archive_schema_version": core.ARCHIVE_SCHEMA_VERSION,
        "application": {
            "name": core.APP_NAME,
            "title": core.APP_TITLE,
            "version": core.APP_VERSION,
            "author": core.APP_AUTHOR,
        },
        "archive_role": core.ARCHIVE_ROLE,
        "archive": {
            "kind": core.ARCHIVE_KIND,
            "member_layout": "flat",
            "member_filename_stem": base,
        },
        "collection": {
            "status": collection.collection_status,
            "started_at_utc": collection.started_at_utc,
            "completed_at_utc": collection.collected_at_utc,
            "completed_at_local": collection.collected_at_local,
            "archive_created_at_utc": core.utc_iso(archive_created_at),
            "archive_created_at_local": archive_created_at.isoformat(
                timespec="seconds"
            ),
            "read_only_boundary": {
                "windows": "query-only storage cmdlets and CIM classes",
                "smartctl": "--scan-open and -x only; no tests or setting changes",
                "may_wake_sleeping_disk": True,
            },
        },
        "host": core.host_metadata(),
        "device": {
            "disk_number": record.disk_number,
            "physical_label": f"PhysicalDrive{record.disk_number}",
            "explorer_names": list(record.explorer_names),
            "volume_labels": list(record.volume_labels),
            "drive_letters": list(record.drive_letters),
            "model": record.model,
            "serial_number": record.serial,
            "unique_id": record.unique_id,
            "size": record.size,
            "bus_type": record.bus_type,
            "partition_style": record.partition_style,
        },
        "smartctl": {
            "device": (
                collection.target.smart_device.to_dict()
                if collection.target.smart_device else None
            ),
            **collection.smart.metadata_dict(),
        },
        "windows_collection": record.data.get("collection", {}),
        "warnings": list(collection.warnings),
        "payload_files": files,
        "integrity": {
            "zip_member_crc_checked_at_creation": True,
            "internal_sha256_stored": False,
            "zip_filename_fingerprint_algorithm": "SHA-256",
            "zip_filename_fingerprint_bits": 32,
            "zip_filename_fingerprint_rule": (
                "uppercase first 8 hexadecimal characters of the final ZIP SHA-256"
            ),
            "full_zip_digest_stored_inside_archive": False,
            "filename_layout_version": core.FILENAME_LAYOUT_VERSION,
            "filename_pattern": (
                "<ExplorerVolumeLabel-or-fallback>_PROFILE_"
                "YYYY-MM-DD_HH-MM-SS_XXXXXXXX.zip"
            ),
            "member_filename_pattern": (
                "<archive-stem-without-fingerprint>_<type>.<extension>"
            ),
        },
        "privacy_notice": (
            "This archive can contain device serial numbers, volume labels, mount paths, "
            "computer name and hardware identifiers. Review before sharing."
        ),
    }


def _validate_member_name(name: str) -> None:
    path = PurePosixPath(name)
    if (
        not name
        or path.is_absolute()
        or len(path.parts) != 1
        or ".." in path.parts
        or "\\" in name
    ):
        raise core.DaisySmartError(f"ZIP 内部路径不安全：{name!r}")


def _manifest_datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise core.DaisySmartError(f"Manifest 时间字段无效：{field}。")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise core.DaisySmartError(
            f"Manifest 时间字段无法解析：{field}。"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise core.DaisySmartError(f"Manifest 时间字段缺少时区：{field}。")
    return parsed


def _validate_manifest_time_pair(
    collection: dict[str, Any],
    utc_field: str,
    local_field: str,
) -> None:
    utc_value = _manifest_datetime(collection.get(utc_field), utc_field)
    local_value = _manifest_datetime(collection.get(local_field), local_field)
    if utc_value.astimezone(timezone.utc) != local_value.astimezone(timezone.utc):
        raise core.DaisySmartError(
            f"Manifest 的 {utc_field} 与 {local_field} 不是同一时刻。"
        )


def _write_working_zip(path: Path, files: dict[str, bytes]) -> None:
    try:
        with zipfile.ZipFile(
            path,
            mode="x",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            allowZip64=True,
        ) as archive:
            for name, content in sorted(files.items()):
                _validate_member_name(name)
                archive.writestr(name, content)
    except FileExistsError as exc:
        raise core.DaisySmartError(f"归档工作文件已存在，未覆盖：{path}") from exc
    except (OSError, zipfile.BadZipFile) as exc:
        raise core.DaisySmartError(f"ZIP 创建失败；工作文件如已生成会保留：{path}：{exc}") from exc


def _publish_no_clobber(working: Path, final: Path, *, label: str) -> None:
    if final.exists():
        raise core.DaisySmartError(
            f"{label}发布冲突：目标已存在且不会覆盖：{final}\n"
            f"本次工作文件保留于：{working}"
        )
    try:
        if os.name == "nt":
            os.rename(working, final)
        else:
            os.link(working, final)
            os.unlink(working)
    except OSError as exc:
        raise core.DaisySmartError(
            f"{label}发布失败，目标保持不动：{final}：{exc}\n"
            f"本次工作文件保留于：{working}"
        ) from exc


def _write_exclusive(path: Path, content: bytes, *, label: str) -> None:
    try:
        with path.open("xb") as handle:
            handle.write(content)
    except FileExistsError as exc:
        raise core.DaisySmartError(f"{label}工作文件已存在，未覆盖：{path}") from exc
    except OSError as exc:
        raise core.DaisySmartError(f"{label}工作文件创建失败：{path}：{exc}") from exc


def create_archive(
    collection: core.CollectionResult,
    output_dir: str | os.PathLike[str],
    *,
    summary_txt: bool = False,
) -> ArchiveResult:
    directory = Path(output_dir).expanduser().resolve()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise core.DaisySmartError(f"无法创建归档目录：{directory}：{exc}") from exc
    if not directory.is_dir():
        raise core.DaisySmartError(f"归档目标不是目录：{directory}")

    try:
        collection_local_time = datetime.fromisoformat(
            collection.collected_at_local
        )
    except ValueError:
        collection_local_time = core.local_now()
    base = core.archive_base_name(collection.windows, collection_local_time)
    unique = f"{time.time_ns() // 1000 % 1_000_000:06d}_{uuid.uuid4().hex[:8]}"
    working = directory / f".{base}.{unique}.partial.zip"

    names = member_names(base)
    payload_files = _payload_files(collection, names)
    archive_created_at = core.local_now()
    manifest = _manifest(
        collection,
        payload_files,
        names,
        base,
        archive_created_at,
    )
    manifest_bytes = core.utf8_lf_bytes(core.json_text(manifest))
    archive_files = {
        **payload_files,
        names["manifest"]: manifest_bytes,
    }
    _write_working_zip(working, archive_files)

    try:
        with zipfile.ZipFile(working, "r") as archive:
            bad_member = archive.testzip()
            names = archive.namelist()
    except (OSError, zipfile.BadZipFile) as exc:
        raise core.DaisySmartError(f"ZIP 写后复核失败，工作文件保留于 {working}：{exc}") from exc
    if bad_member:
        raise core.DaisySmartError(
            f"ZIP 写后 CRC 复核失败：{bad_member}；工作文件保留于 {working}"
        )
    if set(names) != set(archive_files) or len(names) != len(set(names)):
        raise core.DaisySmartError(f"ZIP 写后文件清单不一致；工作文件保留于 {working}")

    zip_sha256 = core.sha256_file(working)
    fingerprint = zip_sha256[:8].upper()
    final = directory / f"{base}_{fingerprint}.zip"
    summary_final: Path | None = None
    summary_working: Path | None = None
    if summary_txt:
        summary_final = directory / f"{final.stem}_Report.txt"
        if summary_final.exists():
            raise core.DaisySmartError(
                f"简化报告目标已存在且不会覆盖：{summary_final}\n"
                f"本次 ZIP 工作文件保留于：{working}"
            )
        summary_working = directory / f".{final.stem}.{unique}.Report.partial.txt"
        report_bytes = core.utf8_lf_bytes(collection.report)
        _write_exclusive(summary_working, report_bytes, label="简化报告")

    _publish_no_clobber(working, final, label="ZIP 归档")
    try:
        verified = verify_archive(final)
    except core.DaisySmartError as exc:
        raise core.DaisySmartError(
            f"ZIP 已发布，但创建后完整自检失败：{final}：{exc}"
        ) from exc
    if summary_final is not None and summary_working is not None:
        try:
            _publish_no_clobber(summary_working, summary_final, label="简化报告")
        except core.DaisySmartError as exc:
            raise core.DaisySmartError(
                f"ZIP 已成功发布至：{final}\n"
                f"但简化报告发布失败：{exc}"
            ) from exc
    return ArchiveResult(
        path=str(final),
        zip_sha256=zip_sha256,
        fingerprint=fingerprint,
        internal_files=verified.internal_files,
        manifest=verified.manifest,
        summary_report_path=str(summary_final) if summary_final else None,
    )

def verify_archive(path: str | os.PathLike[str]) -> VerificationResult:
    archive_path = Path(path).expanduser().resolve()
    if not archive_path.is_file():
        raise core.DaisySmartError(f"归档不存在：{archive_path}")
    filename_match = ARCHIVE_FILENAME_RE.fullmatch(archive_path.name)
    if not filename_match:
        raise core.DaisySmartError(
            "ZIP 文件名不符合 <名称>_PROFILE_日期_时间_8位指纹.zip。"
        )
    base = filename_match.group("base")
    expected_names = required_names(base)
    expected_by_role = member_names(base)
    zip_sha256 = core.sha256_file(archive_path)
    fingerprint = filename_match.group("fingerprint").upper()
    if zip_sha256[:8].upper() != fingerprint:
        raise core.DaisySmartError(
            f"ZIP 文件名指纹不匹配：文件名 {fingerprint}，实际 {zip_sha256[:8].upper()}。"
        )

    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise core.DaisySmartError("ZIP 包含重复内部路径。")
            for name in names:
                _validate_member_name(name)
            if set(names) != expected_names:
                missing = sorted(expected_names - set(names))
                extra = sorted(set(names) - expected_names)
                detail: list[str] = []
                if missing:
                    detail.append("缺少：" + "、".join(missing))
                if extra:
                    detail.append("多余：" + "、".join(extra))
                raise core.DaisySmartError(
                    f"ZIP 文件清单不符合 schema {core.ARCHIVE_SCHEMA_VERSION}："
                    + "；".join(detail)
                )
            bad_member = archive.testzip()
            if bad_member:
                raise core.DaisySmartError(f"ZIP CRC 复核失败：{bad_member}")
            try:
                manifest = json.loads(
                    archive.read(expected_by_role["manifest"]).decode("utf-8")
                )
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise core.DaisySmartError(f"归档清单无法解析：{exc}") from exc
            member_sizes = {
                name: archive.getinfo(name).file_size
                for name in expected_names
            }
    except zipfile.BadZipFile as exc:
        raise core.DaisySmartError(f"不是有效的 ZIP：{archive_path}：{exc}") from exc
    except OSError as exc:
        raise core.DaisySmartError(f"无法读取 ZIP：{archive_path}：{exc}") from exc

    if not isinstance(manifest, dict):
        raise core.DaisySmartError("Manifest.json 根节点不是对象。")
    if manifest.get("archive_schema_version") != core.ARCHIVE_SCHEMA_VERSION:
        raise core.DaisySmartError(
            "不支持的归档 schema："
            f"{manifest.get('archive_schema_version')!r}。"
        )
    if manifest.get("archive_role") != core.ARCHIVE_ROLE:
        raise core.DaisySmartError("Manifest 的归档角色不是单硬盘只读档案。")
    archive_metadata = manifest.get("archive")
    if not isinstance(archive_metadata, dict):
        raise core.DaisySmartError("Manifest 缺少 archive。")
    if archive_metadata.get("kind") != core.ARCHIVE_KIND:
        raise core.DaisySmartError("Manifest 的归档类型不是 PROFILE。")
    if archive_metadata.get("member_layout") != "flat":
        raise core.DaisySmartError("Manifest 的成员布局不是 flat。")
    if archive_metadata.get("member_filename_stem") != base:
        raise core.DaisySmartError("Manifest 的成员名前缀与 ZIP 文件名不一致。")
    collection_metadata = manifest.get("collection")
    if not isinstance(collection_metadata, dict):
        raise core.DaisySmartError("Manifest.json 缺少 collection。")
    collection_status = collection_metadata.get("status")
    if collection_status not in core.COLLECTION_STATUSES:
        raise core.DaisySmartError(
            f"Manifest.json 采集状态无效：{collection_status!r}。"
        )
    _validate_manifest_time_pair(
        collection_metadata,
        "completed_at_utc",
        "completed_at_local",
    )
    _validate_manifest_time_pair(
        collection_metadata,
        "archive_created_at_utc",
        "archive_created_at_local",
    )
    declared = manifest.get("payload_files")
    if not isinstance(declared, dict):
        raise core.DaisySmartError("Manifest.json 缺少 payload_files。")
    expected_payload_names = expected_names - {expected_by_role["manifest"]}
    if set(declared) != expected_payload_names:
        raise core.DaisySmartError("Manifest.json 的 payload_files 与 ZIP 不一致。")
    for key, name in expected_by_role.items():
        if key == "manifest":
            continue
        metadata = declared.get(name)
        if not isinstance(metadata, dict) or set(metadata) != {"bytes", "role"}:
            raise core.DaisySmartError(f"Manifest.json 文件声明无效：{name}")
        if core.int_or_none(metadata.get("bytes")) != member_sizes[name]:
            raise core.DaisySmartError(f"Manifest.json 文件大小声明不匹配：{name}")
        if metadata.get("role") != MEMBER_ROLES[key]:
            raise core.DaisySmartError(f"Manifest.json 文件角色声明无效：{name}")
    return VerificationResult(
        path=str(archive_path),
        zip_sha256=zip_sha256,
        fingerprint=fingerprint,
        internal_files=tuple(sorted(names)),
        manifest=manifest,
    )

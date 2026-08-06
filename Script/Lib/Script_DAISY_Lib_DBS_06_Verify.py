"""DAISY DBS 核验功能的共用只读输入模型。

本模块只合并 DBS-31／32 已有的快照准入、root 映射和连接生命周期；
不改变哈希抽样、格式判据、报告字段、退出码或输出文件命名。
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import sqlite3
from typing import Iterable

import Script_DAISY_Lib_DBS_01_Core as core
import Script_DAISY_Lib_DBS_05_Reader as dbreader


@dataclass
class VerificationSnapshot:
    """一次核验所使用的封存快照、当前 root 映射和只读连接。"""

    path: str
    connection: sqlite3.Connection
    descriptor: dbreader.DatabaseDescriptor
    snapshot_uuid: str
    hash_coverage: str
    root_labels: tuple[str, ...]
    current_roots: dict[int, str]
    labels_by_root_id: dict[int, str]

    @property
    def filename(self) -> str:
        return os.path.basename(self.path)

    def physical_path(self, root_id: int, relative_path: str) -> str:
        return os.path.join(self.current_roots[root_id], relative_path)

    def logical_path(self, root_id: int, relative_path: str) -> str:
        return self.labels_by_root_id[root_id] + "\\" + relative_path

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> VerificationSnapshot:
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()


def _validate_snapshot_filename(path: str, force: bool) -> str:
    normalized = os.path.abspath(path)
    if not os.path.isfile(normalized):
        raise core.PreflightError(f"快照不存在：{normalized}")
    recorded = core.filename_sha256_high32(normalized)
    if recorded is not None:
        if recorded != core.sha256_file(normalized)[:8].upper():
            raise core.PreflightError("快照文件名高32bit指纹不符")
    elif not force:
        raise core.PreflightError(
            f"快照文件名缺少高32bit指纹（--force 可越过）：{normalized}")
    return normalized


def _root_specs(
    root_map: dict[str, str] | None,
    root_specs: list[str] | None,
) -> list[str]:
    if root_specs is not None:
        return list(root_specs)
    return [
        f"{label}={path}"
        for label, path in (root_map or {}).items()
    ]


def open_verification_snapshot(
    snapshot_path: str,
    *,
    root_map: dict[str, str] | None = None,
    root_specs: list[str] | None = None,
    force: bool = False,
    required_capabilities: Iterable[str] = ("files",),
) -> VerificationSnapshot:
    """按既有 DBS-31／32 规则打开快照并解析当前 root 映射。"""
    normalized = _validate_snapshot_filename(snapshot_path, force)
    connection, descriptor = dbreader.open_database(
        normalized, expected_type="snapshot")
    try:
        dbreader.require_capabilities(
            descriptor, *tuple(required_capabilities))
        row = connection.execute(
            "SELECT snapshot_uuid,hash_coverage"
            " FROM snapshot_info WHERE id=1").fetchone()
        if row is None:
            raise core.PreflightError("快照数据库缺少 snapshot_info id=1")
        snapshot_uuid, hash_coverage = row
        root_rows = list(connection.execute(
            "SELECT root_id,root_label,root_path"
            " FROM roots ORDER BY root_id"))
        labels = [str(root[1]) for root in root_rows]
        current_by_label = core.resolve_current_root_specs(
            labels, _root_specs(root_map, root_specs))
        current_roots: dict[int, str] = {}
        labels_by_root_id: dict[int, str] = {}
        for root_id, label, _recorded_path in root_rows:
            current = current_by_label[label]
            if not os.path.isdir(current):
                raise core.PreflightError(
                    f"root「{label}」当前路径不存在：{current}"
                    f"（用 --root \"{label}=当前路径\" 指定）")
            current_roots[int(root_id)] = current
            labels_by_root_id[int(root_id)] = str(label)
        return VerificationSnapshot(
            path=normalized,
            connection=connection,
            descriptor=descriptor,
            snapshot_uuid=str(snapshot_uuid),
            hash_coverage=str(hash_coverage),
            root_labels=tuple(labels),
            current_roots=current_roots,
            labels_by_root_id=labels_by_root_id,
        )
    except Exception:
        connection.close()
        raise

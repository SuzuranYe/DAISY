"""DAISY schema 4 运行状态、session、attempt、lease 与续传事务。

本模块实现 Spec_DAISY_V1_6_0_Data_Contract.md。schema 4 是冻结 schema 3
业务表的超集；这里不迁移或写入任何 schema 3 封存库或旧 partial。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import sqlite3
import sys
from typing import Callable, Iterable, Mapping
import uuid

import Script_DAISY_Lib_DBS_01_Core as core


SCHEMA_VERSION = 4
DATA_CONTRACT = "daisy-snapshot-v4"
MIN_READER_VERSION = "1.6.0"
RESUME_CONTRACT = "daisy-resume-v1"
PROJECTION_CONTRACT = "daisy-snapshot-projection-v1"
FILENAME_LAYOUT_VERSION = 3
LEASE_HEARTBEAT_SECONDS = 5
LEASE_TTL_SECONDS = 30

RUN_STATES = (
    "running",
    "pause_requested",
    "paused",
    "stopped",
    "sealing",
    "sealed_unpublished",
    "published",
    "failed_recoverable",
    "failed_terminal",
)
SESSION_STATUSES = (
    "active", "paused", "saved", "stopped", "completed", "failed",
    "abandoned",
)
RESUME_HINTS = ("none", "suggest", "manual_only")
STAGES = (
    "enumerate",
    "hash",
    "metadata",
    "format",
    "rescan",
    "verify_hash",
    "verify_format",
    "seal",
    "publish",
)
STAGE_STATES = (
    "pending",
    "running",
    "pause_requested",
    "paused",
    "completed",
    "skipped",
    "failed_recoverable",
    "failed_terminal",
)
ATTEMPT_STAGES = (
    "hash", "metadata", "format", "verify_hash", "verify_format",
)
ATTEMPT_STATUSES = (
    "running",
    "succeeded",
    "invalid",
    "error",
    "timeout",
    "unstable",
    "unsupported",
    "skipped_policy",
    "cancelled",
    "abandoned",
)
ATTEMPT_DECISIONS = (
    "none", "continue_waiting", "skip_and_record", "stop_and_resume",
)
DECISION_SOURCES = (
    "none", "user", "default", "advanced_policy", "shutdown",
)
FORMAT_STATUSES = (
    "pending",
    "processing",
    "valid",
    "invalid",
    "unsupported",
    "timeout",
    "error",
    "unstable",
    "skipped_policy",
)

ALLOWED_TRANSITIONS = {
    "running": frozenset((
        "pause_requested", "stopped", "sealing", "failed_recoverable",
        "failed_terminal",
    )),
    "pause_requested": frozenset((
        "running", "paused", "stopped", "failed_recoverable",
        "failed_terminal",
    )),
    "paused": frozenset((
        "running", "stopped", "failed_recoverable", "failed_terminal",
    )),
    "stopped": frozenset(("running", "failed_terminal")),
    "sealing": frozenset((
        "sealed_unpublished", "failed_recoverable", "failed_terminal",
    )),
    "sealed_unpublished": frozenset(("published", "failed_recoverable")),
    "failed_recoverable": frozenset((
        "running", "stopped", "failed_terminal",
    )),
    "published": frozenset(),
    "failed_terminal": frozenset(),
}


SNAPSHOT_DDL_V4_EXTENSION = r"""

CREATE TABLE run_sessions (
    session_id             TEXT PRIMARY KEY CHECK (length(session_id) = 32),
    session_number         INTEGER NOT NULL UNIQUE CHECK (session_number > 0),
    parent_session_id      TEXT REFERENCES run_sessions(session_id),
    session_kind           TEXT NOT NULL
                           CHECK (session_kind IN ('initial','resume')),
    session_status         TEXT NOT NULL
                           CHECK (session_status IN
                                  ('active','paused','saved','stopped',
                                   'completed','failed','abandoned')),
    started_at_utc         TEXT NOT NULL,
    updated_at_utc         TEXT NOT NULL,
    ended_at_utc           TEXT,
    hostname               TEXT NOT NULL,
    pid                    INTEGER NOT NULL CHECK (pid > 0),
    process_start_token    TEXT,
    lease_id               TEXT NOT NULL CHECK (length(lease_id) = 32),
    lease_acquired_at_utc  TEXT NOT NULL,
    lease_heartbeat_at_utc TEXT NOT NULL,
    lease_expires_at_utc   TEXT NOT NULL,
    scanner_version        TEXT NOT NULL,
    resume_contract        TEXT NOT NULL,
    config_json            TEXT NOT NULL,
    tools_json             TEXT NOT NULL,
    end_reason             TEXT
);

CREATE TABLE snapshot_runtime (
    id                      INTEGER PRIMARY KEY CHECK (id = 1),
    snapshot_uuid           TEXT NOT NULL REFERENCES snapshot_info(snapshot_uuid),
    schema_version          INTEGER NOT NULL CHECK (schema_version = 4),
    data_contract           TEXT NOT NULL CHECK (data_contract = 'daisy-snapshot-v4'),
    min_reader_version      TEXT NOT NULL CHECK (min_reader_version = '1.6.0'),
    resume_contract         TEXT NOT NULL CHECK (resume_contract = 'daisy-resume-v1'),
    projection_contract     TEXT NOT NULL
                            CHECK (projection_contract =
                                   'daisy-snapshot-projection-v1'),
    filename_layout_version INTEGER NOT NULL CHECK (filename_layout_version = 3),
    run_state               TEXT NOT NULL
                            CHECK (run_state IN
                                   ('running','pause_requested','paused',
                                    'stopped','sealing','sealed_unpublished',
                                    'published','failed_recoverable',
                                    'failed_terminal')),
    state_revision          INTEGER NOT NULL CHECK (state_revision > 0),
    resume_hint             TEXT NOT NULL
                            CHECK (resume_hint IN
                                   ('none','suggest','manual_only')),
    active_session_id       TEXT NOT NULL REFERENCES run_sessions(session_id),
    current_stage           TEXT
                            CHECK (current_stage IS NULL OR current_stage IN
                                   ('enumerate','hash','metadata','format',
                                    'rescan','verify_hash','verify_format',
                                    'seal','publish')),
    created_at_utc          TEXT NOT NULL,
    updated_at_utc          TEXT NOT NULL,
    last_checkpoint_at_utc  TEXT NOT NULL,
    output_dir              TEXT NOT NULL,
    partial_path            TEXT NOT NULL,
    publish_stem_path       TEXT NOT NULL,
    event_log_path          TEXT NOT NULL,
    published_path_pattern  TEXT,
    last_error_code         TEXT,
    last_error_message      TEXT
);

CREATE TABLE stage_checkpoints (
    stage                  TEXT PRIMARY KEY
                           CHECK (stage IN
                                  ('enumerate','hash','metadata','format',
                                   'rescan','verify_hash','verify_format',
                                   'seal','publish')),
    stage_order            INTEGER NOT NULL UNIQUE CHECK (stage_order > 0),
    state                  TEXT NOT NULL
                           CHECK (state IN
                                  ('pending','running','pause_requested',
                                   'paused','completed','skipped',
                                   'failed_recoverable','failed_terminal')),
    session_id             TEXT REFERENCES run_sessions(session_id),
    items_done             INTEGER NOT NULL DEFAULT 0 CHECK (items_done >= 0),
    items_total            INTEGER CHECK (items_total IS NULL OR items_total >= 0),
    bytes_done             INTEGER NOT NULL DEFAULT 0 CHECK (bytes_done >= 0),
    bytes_total            INTEGER CHECK (bytes_total IS NULL OR bytes_total >= 0),
    error_count            INTEGER NOT NULL DEFAULT 0 CHECK (error_count >= 0),
    current_entry_id       INTEGER REFERENCES entries(entry_id),
    started_at_utc         TEXT,
    updated_at_utc         TEXT NOT NULL,
    finished_at_utc        TEXT,
    checkpoint_json        TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE run_state_events (
    event_id          INTEGER PRIMARY KEY,
    session_id        TEXT NOT NULL REFERENCES run_sessions(session_id),
    session_event_seq INTEGER NOT NULL CHECK (session_event_seq > 0),
    occurred_at_utc   TEXT NOT NULL,
    event             TEXT NOT NULL CHECK (event <> ''),
    from_state        TEXT,
    to_state          TEXT NOT NULL,
    state_revision    INTEGER NOT NULL CHECK (state_revision > 0),
    payload_json      TEXT NOT NULL,
    UNIQUE (session_id, session_event_seq)
);

CREATE TABLE entry_attempts (
    attempt_id             INTEGER PRIMARY KEY,
    entry_id               INTEGER NOT NULL REFERENCES entries(entry_id),
    session_id             TEXT NOT NULL REFERENCES run_sessions(session_id),
    stage                  TEXT NOT NULL
                           CHECK (stage IN
                                  ('hash','metadata','format',
                                   'verify_hash','verify_format')),
    attempt_number         INTEGER NOT NULL CHECK (attempt_number > 0),
    status                 TEXT NOT NULL
                           CHECK (status IN
                                  ('running','succeeded','invalid','error',
                                   'timeout','unstable','unsupported',
                                   'skipped_policy','cancelled','abandoned')),
    tool_name              TEXT,
    tool_version           TEXT,
    started_at_utc         TEXT NOT NULL,
    last_progress_at_utc   TEXT NOT NULL,
    ended_at_utc           TEXT,
    source_size_bytes      INTEGER CHECK (source_size_bytes IS NULL OR
                                          source_size_bytes >= 0),
    source_modified_at_utc TEXT,
    bytes_read             INTEGER NOT NULL DEFAULT 0 CHECK (bytes_read >= 0),
    final_offset           INTEGER NOT NULL DEFAULT 0 CHECK (final_offset >= 0),
    stall_count            INTEGER NOT NULL DEFAULT 0 CHECK (stall_count >= 0),
    max_stall_seconds      REAL NOT NULL DEFAULT 0 CHECK (max_stall_seconds >= 0),
    decision               TEXT NOT NULL DEFAULT 'none'
                           CHECK (decision IN
                                  ('none','continue_waiting','skip_and_record',
                                   'stop_and_resume')),
    decision_source        TEXT NOT NULL DEFAULT 'none'
                           CHECK (decision_source IN
                                  ('none','user','default','advanced_policy',
                                   'shutdown')),
    end_reason             TEXT,
    error_code             TEXT,
    error_message          TEXT,
    result_json            TEXT NOT NULL DEFAULT '{}',
    UNIQUE (entry_id, stage, attempt_number)
);

CREATE INDEX idx_entry_attempts_session
    ON entry_attempts(session_id, stage, status);

CREATE UNIQUE INDEX idx_entry_attempts_running
    ON entry_attempts(entry_id, stage) WHERE status = 'running';

CREATE TABLE read_performance (
    performance_id         INTEGER PRIMARY KEY,
    attempt_id             INTEGER NOT NULL UNIQUE
                           REFERENCES entry_attempts(attempt_id),
    entry_id               INTEGER NOT NULL REFERENCES entries(entry_id),
    session_id             TEXT NOT NULL REFERENCES run_sessions(session_id),
    stage                  TEXT NOT NULL,
    origin                 TEXT NOT NULL
                           CHECK (origin IN
                                  ('computed','reused','independent','tool')),
    size_bytes             INTEGER NOT NULL CHECK (size_bytes >= 0),
    bytes_read             INTEGER NOT NULL CHECK (bytes_read >= 0),
    elapsed_seconds        REAL NOT NULL CHECK (elapsed_seconds >= 0),
    active_read_seconds    REAL NOT NULL CHECK (active_read_seconds >= 0),
    stall_count            INTEGER NOT NULL CHECK (stall_count >= 0),
    longest_stall_seconds  REAL NOT NULL CHECK (longest_stall_seconds >= 0),
    first_stall_offset     INTEGER CHECK (first_stall_offset IS NULL OR
                                          first_stall_offset >= 0),
    last_stall_offset      INTEGER CHECK (last_stall_offset IS NULL OR
                                          last_stall_offset >= 0),
    final_offset           INTEGER NOT NULL CHECK (final_offset >= 0),
    ended_reason           TEXT NOT NULL,
    candidate_confidence   TEXT NOT NULL DEFAULT 'none'
                           CHECK (candidate_confidence IN ('none','low','high')),
    candidate_reason       TEXT,
    recorded_at_utc        TEXT NOT NULL
);

CREATE INDEX idx_read_performance_group
    ON read_performance(stage, origin, size_bytes);

CREATE TABLE format_checks (
    entry_id        INTEGER PRIMARY KEY REFERENCES entries(entry_id),
    attempt_id      INTEGER UNIQUE REFERENCES entry_attempts(attempt_id),
    status          TEXT NOT NULL
                    CHECK (status IN
                           ('pending','processing','valid','invalid',
                            'unsupported','timeout','error','unstable',
                            'skipped_policy')),
    coverage        TEXT NOT NULL CHECK (coverage IN ('sample','full')),
    validator       TEXT NOT NULL CHECK (validator <> ''),
    tool_name       TEXT,
    tool_version    TEXT,
    stat_match      INTEGER CHECK (stat_match IS NULL OR stat_match IN (0,1)),
    detail          TEXT,
    checked_at_utc  TEXT,
    result_revision INTEGER NOT NULL DEFAULT 1 CHECK (result_revision > 0)
);
"""

SNAPSHOT_DDL_V4 = core.SNAPSHOT_DDL + SNAPSHOT_DDL_V4_EXTENSION


@dataclass(frozen=True)
class RuntimeSnapshot:
    snapshot_uuid: str
    run_state: str
    state_revision: int
    resume_hint: str
    active_session_id: str
    current_stage: str | None
    output_dir: str
    partial_path: str
    publish_stem_path: str
    event_log_path: str
    published_path_pattern: str | None
    updated_at_utc: str


@dataclass(frozen=True)
class RecoveryResult:
    attempts_abandoned: int
    hash_entries_reset: int
    metadata_entries_reset: int
    format_entries_reset: int
    stages_reset: int
    runtime: RuntimeSnapshot


@dataclass(frozen=True)
class EventJournalRead:
    records: tuple[dict[str, object], ...]
    truncated_tail: bool


@dataclass(frozen=True)
class LeaseRecord:
    lease_id: str
    session_id: str
    host: str
    pid: int
    process_start_token: str | None
    acquired_at_utc: str
    heartbeat_at_utc: str
    expires_at_utc: str

    def as_dict(self) -> dict[str, object]:
        return {
            "lease_id": self.lease_id,
            "session_id": self.session_id,
            "host": self.host,
            "pid": self.pid,
            "process_start_token": self.process_start_token,
            "acquired_at_utc": self.acquired_at_utc,
            "heartbeat_at_utc": self.heartbeat_at_utc,
            "expires_at_utc": self.expires_at_utc,
        }


@dataclass(frozen=True)
class PublicationResult:
    final_path: str
    sha256: str
    partial_removed: bool
    lease_released: bool
    warnings: tuple[str, ...]
    issue_report_path: str | None = None
    artifact_paths: tuple[str, ...] = ()


_UNSET = object()


def _new_id() -> str:
    return uuid.uuid4().hex


def _require_compact_uuid(value: str, label: str) -> str:
    text = str(value)
    if len(text) != 32 or any(
            character not in "0123456789abcdef" for character in text):
        raise core.PreflightError(
            f"{label} 必须是 32 位小写十六进制 UUID")
    return text


def _now_text(value: str | None = None) -> str:
    return str(value or core.now_utc_iso())


def _tool_version_value(
    tools: dict[str, object],
    name: str,
) -> str | None:
    value = tools.get(name)
    if isinstance(value, dict):
        value = value.get("version")
    if value is None:
        return None
    return str(value)


def _parse_utc(value: str) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if "." in text:
        head, tail = text.split(".", 1)
        fraction, sign, offset = tail.partition("+")
        if not sign:
            fraction, sign, offset = tail.partition("-")
        fraction = (fraction + "000000")[:6]
        text = head + "." + fraction
        if sign:
            text += sign + offset
    result = datetime.fromisoformat(text)
    if result.tzinfo is None:
        raise ValueError(f"UTC 时间缺少时区：{value!r}")
    return result.astimezone(timezone.utc)


def _add_seconds(value: str, seconds: int) -> str:
    moment = _parse_utc(value) + timedelta(seconds=seconds)
    return moment.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _normalized(path: str) -> str:
    return os.path.abspath(os.fspath(path))


def _validate_output_identity(
    output_dir: str,
    partial_path: str,
    publish_stem_path: str,
    event_log_path: str,
) -> tuple[str, str, str, str]:
    output = _normalized(output_dir)
    partial = _normalized(partial_path)
    publish = _normalized(publish_stem_path)
    event_log = _normalized(event_log_path)
    if not partial.casefold().endswith(".partial.sqlite"):
        raise core.PreflightError(
            f"schema 4 partial 路径后缀无效：{partial}")
    for label, path in (
        ("partial", partial),
        ("publish stem", publish),
        ("event log", event_log),
    ):
        if os.path.normcase(os.path.dirname(path)) != os.path.normcase(output):
            raise core.PreflightError(
                f"{label} 不在冻结输出目录内：{path}；输出目录：{output}")
    identities = {
        os.path.normcase(partial),
        os.path.normcase(publish),
        os.path.normcase(event_log),
    }
    if len(identities) != 3:
        raise core.PreflightError(
            "partial、publish stem 与 event log 必须是三个不同路径")
    return output, partial, publish, event_log


def require_v4_connection(con: sqlite3.Connection) -> None:
    row = con.execute(
        "SELECT schema_version FROM snapshot_info WHERE id=1").fetchone()
    if row is None or int(row[0]) != SCHEMA_VERSION:
        actual = None if row is None else row[0]
        raise core.PreflightError(
            f"需要 schema 4 partial，实际 schema_version={actual}")
    required = {
        "run_sessions",
        "snapshot_runtime",
        "stage_checkpoints",
        "run_state_events",
        "entry_attempts",
        "read_performance",
        "format_checks",
    }
    tables = {
        str(row[0]) for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")
    }
    missing = sorted(required - tables)
    if missing:
        raise core.PreflightError(
            "schema 4 partial 缺少运行表：" + "、".join(missing))


def initialize_v4_connection(
    con: sqlite3.Connection,
    roots: list[tuple[str, str]],
    config: dict[str, object],
    *,
    output_dir: str,
    partial_path: str,
    publish_stem_path: str,
    event_log_path: str | None = None,
    tool_versions: dict[str, object] | None = None,
    scanner_version: str = MIN_READER_VERSION,
    snapshot_uuid: str | None = None,
    session_id: str | None = None,
    lease_id: str | None = None,
    hostname: str | None = None,
    pid: int | None = None,
    process_start_token: str | None = None,
    now_utc: str | None = None,
) -> RuntimeSnapshot:
    """在新连接中创建 schema 4；调用方负责路径独占与 root 预检。"""
    if not roots:
        raise core.PreflightError("数据库结构版本 4 至少需要一个根目录")
    now = _now_text(now_utc)
    snapshot_id = _require_compact_uuid(
        snapshot_uuid or _new_id(), "snapshot_uuid")
    active_session = _require_compact_uuid(
        session_id or _new_id(), "session_id")
    active_lease = _require_compact_uuid(
        lease_id or _new_id(), "lease_id")
    event_path = event_log_path or (
        partial_path[:-len(".partial.sqlite")] + ".events.jsonl")
    output, partial, publish, event_log = _validate_output_identity(
        output_dir, partial_path, publish_stem_path, event_path)
    if len({label for label, _path in roots}) != len(roots):
        raise core.PreflightError("数据库结构版本 4 的根目录名重复")
    normalized_roots = [
        (str(label), _normalized(path)) for label, path in roots]
    if len({os.path.normcase(path) for _label, path in normalized_roots}) \
            != len(normalized_roots):
        raise core.PreflightError("数据库结构版本 4 的根目录路径重复")

    effective_config = dict(config)
    effective_config.update({
        "data_contract": DATA_CONTRACT,
        "min_reader_version": MIN_READER_VERSION,
        "resume_contract": RESUME_CONTRACT,
        "projection_contract": PROJECTION_CONTRACT,
        "filename_layout_version": FILENAME_LAYOUT_VERSION,
    })
    tools = dict(tool_versions or {})
    host = str(hostname or socket.gethostname())
    process_id = int(os.getpid() if pid is None else pid)
    if process_id <= 0:
        raise core.PreflightError("session PID 必须大于 0")
    expires = _add_seconds(now, LEASE_TTL_SECONDS)

    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SNAPSHOT_DDL_V4)
    with con:
        con.execute(
            "INSERT INTO snapshot_info"
            " (id,snapshot_uuid,schema_version,path_key_rule,scan_status,"
            " database_integrity,hash_coverage,started_at_utc,"
            " local_utc_offset_min,hostname,os_version,scanner_version,"
            " exiftool_version,ffprobe_version,sevenzip_version,"
            " hash_algorithm,hash_chunk_bytes,config_json)"
            " VALUES"
            " (1,?,?,?,'running','pending','none',?,?,?,?,?,?,?,?,"
            " 'sha256',?,?)",
            (
                snapshot_id,
                SCHEMA_VERSION,
                core.PATH_KEY_RULE,
                now,
                core.local_utc_offset_min(),
                host,
                platform.platform(),
                scanner_version,
                _tool_version_value(tools, "exiftool"),
                _tool_version_value(tools, "ffprobe"),
                _tool_version_value(tools, "sevenzip"),
                core.HASH_CHUNK_BYTES,
                json.dumps(effective_config, ensure_ascii=False),
            ),
        )
        for root_id, (label, path) in enumerate(normalized_roots, 1):
            con.execute(
                "INSERT INTO roots"
                " (root_id,root_path,root_label,enum_status)"
                " VALUES (?,?,?,'pending')",
                (root_id, path, label),
            )
        con.execute(
            "INSERT INTO run_sessions"
            " (session_id,session_number,parent_session_id,session_kind,"
            " session_status,started_at_utc,updated_at_utc,hostname,pid,"
            " process_start_token,lease_id,lease_acquired_at_utc,"
            " lease_heartbeat_at_utc,lease_expires_at_utc,scanner_version,"
            " resume_contract,config_json,tools_json)"
            " VALUES (?,1,NULL,'initial','active',?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                active_session,
                now,
                now,
                host,
                process_id,
                process_start_token,
                active_lease,
                now,
                now,
                expires,
                scanner_version,
                RESUME_CONTRACT,
                json.dumps(effective_config, ensure_ascii=False),
                json.dumps(tools, ensure_ascii=False),
            ),
        )
        con.execute(
            "INSERT INTO snapshot_runtime"
            " (id,snapshot_uuid,schema_version,data_contract,"
            " min_reader_version,resume_contract,projection_contract,"
            " filename_layout_version,run_state,state_revision,resume_hint,"
            " active_session_id,current_stage,created_at_utc,updated_at_utc,"
            " last_checkpoint_at_utc,output_dir,partial_path,"
            " publish_stem_path,event_log_path)"
            " VALUES"
            " (1,?,?,?,?,?,?,?,'running',1,'none',?,'enumerate',?,?,?,?,?,?,?)",
            (
                snapshot_id,
                SCHEMA_VERSION,
                DATA_CONTRACT,
                MIN_READER_VERSION,
                RESUME_CONTRACT,
                PROJECTION_CONTRACT,
                FILENAME_LAYOUT_VERSION,
                active_session,
                now,
                now,
                now,
                output,
                partial,
                publish,
                event_log,
            ),
        )
        for stage_order, stage in enumerate(STAGES, 1):
            state = "running" if stage == "enumerate" else "pending"
            con.execute(
                "INSERT INTO stage_checkpoints"
                " (stage,stage_order,state,session_id,started_at_utc,"
                " updated_at_utc) VALUES (?,?,?,?,?,?)",
                (
                    stage,
                    stage_order,
                    state,
                    active_session if state == "running" else None,
                    now if state == "running" else None,
                    now,
                ),
            )
        con.execute(
            "INSERT INTO run_state_events"
            " (session_id,session_event_seq,occurred_at_utc,event,"
            " from_state,to_state,state_revision,payload_json)"
            " VALUES (?,1,?,'run_initialized',NULL,'running',1,?)",
            (
                active_session,
                now,
                json.dumps({
                    "schema_version": SCHEMA_VERSION,
                    "data_contract": DATA_CONTRACT,
                    "resume_contract": RESUME_CONTRACT,
                }, ensure_ascii=False),
            ),
        )
    return load_runtime(con)


def load_runtime(con: sqlite3.Connection) -> RuntimeSnapshot:
    require_v4_connection(con)
    row = con.execute(
        "SELECT snapshot_uuid,run_state,state_revision,resume_hint,"
        " active_session_id,current_stage,output_dir,partial_path,"
        " publish_stem_path,event_log_path,published_path_pattern,updated_at_utc"
        " FROM snapshot_runtime WHERE id=1"
    ).fetchone()
    if row is None:
        raise core.PreflightError("schema 4 缺少 snapshot_runtime id=1")
    return RuntimeSnapshot(
        snapshot_uuid=str(row[0]),
        run_state=str(row[1]),
        state_revision=int(row[2]),
        resume_hint=str(row[3]),
        active_session_id=str(row[4]),
        current_stage=(str(row[5]) if row[5] is not None else None),
        output_dir=str(row[6]),
        partial_path=str(row[7]),
        publish_stem_path=str(row[8]),
        event_log_path=str(row[9]),
        published_path_pattern=(
            str(row[10]) if row[10] is not None else None),
        updated_at_utc=str(row[11]),
    )


def _append_state_event(
    con: sqlite3.Connection,
    session_id: str,
    occurred_at_utc: str,
    event: str,
    from_state: str | None,
    to_state: str,
    state_revision: int,
    payload: dict[str, object] | None = None,
) -> None:
    row = con.execute(
        "SELECT COALESCE(MAX(session_event_seq),0)"
        " FROM run_state_events WHERE session_id=?",
        (session_id,),
    ).fetchone()
    sequence = int(row[0]) + 1
    con.execute(
        "INSERT INTO run_state_events"
        " (session_id,session_event_seq,occurred_at_utc,event,from_state,"
        " to_state,state_revision,payload_json) VALUES (?,?,?,?,?,?,?,?)",
        (
            session_id,
            sequence,
            occurred_at_utc,
            event,
            from_state,
            to_state,
            state_revision,
            json.dumps(payload or {}, ensure_ascii=False),
        ),
    )


def _coarse_snapshot_state(
    run_state: str,
    *,
    integrity_failed: bool = False,
) -> tuple[str, str]:
    if run_state in ("sealed_unpublished", "published"):
        return "complete", "ok"
    if run_state in (
        "paused", "stopped", "failed_recoverable", "failed_terminal",
    ):
        return (
            "interrupted",
            "failed" if run_state == "failed_terminal" and integrity_failed
            else "pending",
        )
    return "running", "pending"


def _default_session_status(target_state: str) -> tuple[str, bool]:
    if target_state == "paused":
        return "paused", False
    if target_state == "stopped":
        return "stopped", True
    if target_state == "published":
        return "completed", True
    if target_state in ("failed_recoverable", "failed_terminal"):
        return "failed", True
    return "active", False


def transition_run_state(
    con: sqlite3.Connection,
    target_state: str,
    *,
    event: str,
    session_id: str | None = None,
    expected_revision: int | None = None,
    resume_hint: str | None = None,
    current_stage: str | None | object = _UNSET,
    published_path_pattern: str | None | object = _UNSET,
    session_status: str | None = None,
    end_session: bool | None = None,
    end_reason: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    integrity_failed: bool = False,
    payload: dict[str, object] | None = None,
    now_utc: str | None = None,
    _allow_resume: bool = False,
) -> RuntimeSnapshot:
    """以 state + revision CAS 原子转换运行状态并写 session/event。"""
    if target_state not in RUN_STATES:
        raise ValueError(f"未知运行状态：{target_state}")
    if not event:
        raise ValueError("状态转换 event 不能为空")
    runtime = load_runtime(con)
    source_state = runtime.run_state
    if target_state not in ALLOWED_TRANSITIONS[source_state]:
        raise core.PreflightError(
            f"非法运行状态转换：{source_state} -> {target_state}")
    if source_state in ("stopped", "failed_recoverable") \
            and target_state == "running" and not _allow_resume:
        raise core.PreflightError(
            f"{source_state} 必须创建新的 resume session 才能继续")
    active_session = session_id or runtime.active_session_id
    if active_session != runtime.active_session_id:
        raise core.PreflightError(
            "状态转换 session 与 snapshot_runtime.active_session_id 不一致")
    revision = (
        runtime.state_revision
        if expected_revision is None else int(expected_revision)
    )
    if revision != runtime.state_revision:
        raise core.PreflightError(
            f"状态 revision 已变化：期望 {revision}，"
            f"实际 {runtime.state_revision}")
    hint = runtime.resume_hint if resume_hint is None else resume_hint
    if hint not in RESUME_HINTS:
        raise ValueError(f"未知 resume_hint：{hint}")
    stage = runtime.current_stage if current_stage is _UNSET else current_stage
    if stage is not None and stage not in STAGES:
        raise ValueError(f"未知 current_stage：{stage}")
    publication_pattern = (
        runtime.published_path_pattern
        if published_path_pattern is _UNSET else published_path_pattern
    )
    if publication_pattern is not None:
        publication_pattern = _normalized(str(publication_pattern))
    if target_state == "published":
        expected_pattern = (
            runtime.publish_stem_path
            + "_<SHA256-high32-uppercase>.sqlite"
        )
        if publication_pattern != expected_pattern:
            raise core.PreflightError(
                "published 状态必须记录冻结发布路径模式："
                + expected_pattern)
    status_default, end_default = _default_session_status(target_state)
    status = session_status or status_default
    should_end = end_default if end_session is None else bool(end_session)
    if status not in SESSION_STATUSES:
        raise ValueError(f"未知 session_status：{status}")
    now = _now_text(now_utc)
    next_revision = runtime.state_revision + 1
    scan_status, database_integrity = _coarse_snapshot_state(
        target_state, integrity_failed=integrity_failed)
    finished_at = (
        now if target_state in ("sealed_unpublished", "published") else None
    )

    with con:
        changed = con.execute(
            "UPDATE snapshot_runtime SET run_state=?,state_revision=?,"
            " resume_hint=?,current_stage=?,updated_at_utc=?,"
            " last_checkpoint_at_utc=?,published_path_pattern=?,"
            " last_error_code=?,last_error_message=?"
            " WHERE id=1 AND run_state=? AND state_revision=?"
            " AND active_session_id=?",
            (
                target_state,
                next_revision,
                hint,
                stage,
                now,
                now,
                publication_pattern,
                error_code,
                error_message,
                source_state,
                runtime.state_revision,
                active_session,
            ),
        ).rowcount
        if changed != 1:
            raise core.PreflightError(
                "运行状态已被其他控制动作修改，当前转换未提交")
        con.execute(
            "UPDATE snapshot_info SET scan_status=?,database_integrity=?,"
            " finished_at_utc=? WHERE id=1",
            (scan_status, database_integrity, finished_at),
        )
        session_changed = con.execute(
            "UPDATE run_sessions SET session_status=?,updated_at_utc=?,"
            " ended_at_utc=?,end_reason=? WHERE session_id=?",
            (
                status,
                now,
                now if should_end else None,
                end_reason,
                active_session,
            ),
        ).rowcount
        if session_changed != 1:
            raise core.PreflightError(
                f"状态转换找不到 active session：{active_session}")
        _append_state_event(
            con,
            active_session,
            now,
            event,
            source_state,
            target_state,
            next_revision,
            payload,
        )
    return load_runtime(con)


def request_pause(
    con: sqlite3.Connection,
    *,
    now_utc: str | None = None,
    for_exit: bool = False,
) -> RuntimeSnapshot:
    return transition_run_state(
        con,
        "pause_requested",
        event="pause_requested",
        payload={"for_exit": bool(for_exit)},
        now_utc=now_utc,
    )


def mark_paused(
    con: sqlite3.Connection,
    *,
    now_utc: str | None = None,
    for_exit: bool = False,
) -> RuntimeSnapshot:
    return transition_run_state(
        con,
        "paused",
        event="progress_saved" if for_exit else "paused",
        resume_hint="suggest" if for_exit else "none",
        session_status="saved" if for_exit else "paused",
        end_session=for_exit,
        end_reason="save_exit" if for_exit else None,
        payload={"for_exit": bool(for_exit)},
        now_utc=now_utc,
    )


def continue_running(
    con: sqlite3.Connection,
    *,
    now_utc: str | None = None,
) -> RuntimeSnapshot:
    runtime = load_runtime(con)
    row = con.execute(
        "SELECT ended_at_utc FROM run_sessions WHERE session_id=?",
        (runtime.active_session_id,),
    ).fetchone()
    if row is None or row[0] is not None:
        raise core.PreflightError(
            "已结束的 session 不能同会话继续；必须创建 resume session")
    return transition_run_state(
        con,
        "running",
        event="continued",
        resume_hint="none",
        session_status="active",
        now_utc=now_utc,
    )


def save_paused_for_exit(
    con: sqlite3.Connection,
    *,
    now_utc: str | None = None,
) -> RuntimeSnapshot:
    """把同会话 paused 安全结束为可建议续传的保存点。"""
    runtime = load_runtime(con)
    if runtime.run_state != "paused":
        raise core.PreflightError(
            f"状态 {runtime.run_state} 不能保存并退出")
    session = con.execute(
        "SELECT session_status,ended_at_utc FROM run_sessions"
        " WHERE session_id=?",
        (runtime.active_session_id,),
    ).fetchone()
    if session is None or session[0] != "paused" or session[1] is not None:
        raise core.PreflightError(
            "只有尚未结束的 paused session 可以保存并退出")
    now = _now_text(now_utc)
    next_revision = runtime.state_revision + 1
    with con:
        changed = con.execute(
            "UPDATE snapshot_runtime SET state_revision=?,"
            " resume_hint='suggest',updated_at_utc=?,"
            " last_checkpoint_at_utc=? WHERE id=1 AND run_state='paused'"
            " AND state_revision=? AND active_session_id=?",
            (
                next_revision,
                now,
                now,
                runtime.state_revision,
                runtime.active_session_id,
            ),
        ).rowcount
        if changed != 1:
            raise core.PreflightError(
                "paused 保存并退出时状态已变化，未提交")
        session_changed = con.execute(
            "UPDATE run_sessions SET session_status='saved',"
            " updated_at_utc=?,ended_at_utc=?,end_reason='save_exit'"
            " WHERE session_id=? AND session_status='paused'"
            " AND ended_at_utc IS NULL",
            (now, now, runtime.active_session_id),
        ).rowcount
        if session_changed != 1:
            raise core.PreflightError(
                "paused 保存并退出时 session 已变化，未提交")
        _append_state_event(
            con,
            runtime.active_session_id,
            now,
            "paused_saved_for_exit",
            "paused",
            "paused",
            next_revision,
            {"resume_hint": "suggest"},
        )
    return load_runtime(con)


def stop_run(
    con: sqlite3.Connection,
    *,
    reason: str = "user_stop",
    now_utc: str | None = None,
) -> RuntimeSnapshot:
    return transition_run_state(
        con,
        "stopped",
        event="stopped",
        resume_hint="manual_only",
        session_status="stopped",
        end_session=True,
        end_reason=reason,
        now_utc=now_utc,
    )


def begin_sealing(
    con: sqlite3.Connection,
    *,
    now_utc: str | None = None,
) -> RuntimeSnapshot:
    return transition_run_state(
        con,
        "sealing",
        event="sealing_started",
        current_stage="seal",
        now_utc=now_utc,
    )


def mark_sealed_unpublished(
    con: sqlite3.Connection,
    *,
    now_utc: str | None = None,
) -> RuntimeSnapshot:
    return transition_run_state(
        con,
        "sealed_unpublished",
        event="sealed_unpublished",
        current_stage="publish",
        now_utc=now_utc,
    )


def mark_published(
    con: sqlite3.Connection,
    *,
    now_utc: str | None = None,
) -> RuntimeSnapshot:
    runtime = load_runtime(con)
    return transition_run_state(
        con,
        "published",
        event="published",
        resume_hint="none",
        current_stage=None,
        published_path_pattern=(
            runtime.publish_stem_path
            + "_<SHA256-high32-uppercase>.sqlite"
        ),
        session_status="completed",
        end_session=True,
        end_reason="published",
        now_utc=now_utc,
    )


def fail_run(
    con: sqlite3.Connection,
    *,
    recoverable: bool,
    error_code: str,
    error_message: str,
    integrity_failed: bool = False,
    payload: dict[str, object] | None = None,
    now_utc: str | None = None,
) -> RuntimeSnapshot:
    target = "failed_recoverable" if recoverable else "failed_terminal"
    return transition_run_state(
        con,
        target,
        event="run_failed",
        resume_hint="suggest" if recoverable else "none",
        session_status="failed",
        end_session=True,
        end_reason=error_code,
        error_code=error_code,
        error_message=error_message,
        integrity_failed=integrity_failed,
        payload=(
            {"error_code": error_code, "error_message": error_message}
            if payload is None else payload
        ),
        now_utc=now_utc,
    )


def start_resume_session(
    con: sqlite3.Connection,
    *,
    config: dict[str, object],
    tools: dict[str, object],
    manual: bool = False,
    scanner_version: str = MIN_READER_VERSION,
    session_id: str | None = None,
    lease_id: str | None = None,
    hostname: str | None = None,
    pid: int | None = None,
    process_start_token: str | None = None,
    now_utc: str | None = None,
) -> RuntimeSnapshot:
    """为 paused/stopped/failed_recoverable 创建新的续传 session。"""
    runtime = load_runtime(con)
    if runtime.run_state not in (
        "paused", "stopped", "failed_recoverable",
    ):
        raise core.PreflightError(
            f"当前状态 {runtime.run_state} 不能创建 resume session")
    if runtime.run_state == "stopped" and not manual:
        raise core.PreflightError("stopped partial 只允许用户明确手动续传")
    old_session = con.execute(
        "SELECT ended_at_utc FROM run_sessions WHERE session_id=?",
        (runtime.active_session_id,),
    ).fetchone()
    if old_session is None or old_session[0] is None:
        raise core.PreflightError(
            "旧 session 尚未安全结束，不能创建并行 resume session")

    now = _now_text(now_utc)
    new_session = _require_compact_uuid(
        session_id or _new_id(), "session_id")
    new_lease = _require_compact_uuid(lease_id or _new_id(), "lease_id")
    host = str(hostname or socket.gethostname())
    process_id = int(os.getpid() if pid is None else pid)
    if process_id <= 0:
        raise core.PreflightError("session PID 必须大于 0")
    expires = _add_seconds(now, LEASE_TTL_SECONDS)
    row = con.execute(
        "SELECT COALESCE(MAX(session_number),0) FROM run_sessions"
    ).fetchone()
    session_number = int(row[0]) + 1
    next_revision = runtime.state_revision + 1

    effective_config = dict(config)
    effective_config.update({
        "data_contract": DATA_CONTRACT,
        "min_reader_version": MIN_READER_VERSION,
        "resume_contract": RESUME_CONTRACT,
        "projection_contract": PROJECTION_CONTRACT,
        "filename_layout_version": FILENAME_LAYOUT_VERSION,
    })
    with con:
        con.execute(
            "INSERT INTO run_sessions"
            " (session_id,session_number,parent_session_id,session_kind,"
            " session_status,started_at_utc,updated_at_utc,hostname,pid,"
            " process_start_token,lease_id,lease_acquired_at_utc,"
            " lease_heartbeat_at_utc,lease_expires_at_utc,scanner_version,"
            " resume_contract,config_json,tools_json)"
            " VALUES (?,?,?,'resume','active',?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                new_session,
                session_number,
                runtime.active_session_id,
                now,
                now,
                host,
                process_id,
                process_start_token,
                new_lease,
                now,
                now,
                expires,
                scanner_version,
                RESUME_CONTRACT,
                json.dumps(effective_config, ensure_ascii=False),
                json.dumps(tools, ensure_ascii=False),
            ),
        )
        changed = con.execute(
            "UPDATE snapshot_runtime SET run_state='running',"
            " state_revision=?,resume_hint='none',active_session_id=?,"
            " updated_at_utc=?,last_checkpoint_at_utc=?,"
            " last_error_code=NULL,last_error_message=NULL"
            " WHERE id=1 AND run_state=? AND state_revision=?",
            (
                next_revision,
                new_session,
                now,
                now,
                runtime.run_state,
                runtime.state_revision,
            ),
        ).rowcount
        if changed != 1:
            raise core.PreflightError(
                "续传时状态已变化，新 session 未提交")
        con.execute(
            "UPDATE snapshot_info SET scan_status='running',"
            " database_integrity='pending',finished_at_utc=NULL WHERE id=1"
        )
        if runtime.current_stage:
            con.execute(
                "UPDATE stage_checkpoints SET state='running',session_id=?,"
                " updated_at_utc=?,finished_at_utc=NULL"
                " WHERE stage=? AND state IN"
                " ('paused','failed_recoverable','pause_requested')",
                (new_session, now, runtime.current_stage),
            )
        _append_state_event(
            con,
            new_session,
            now,
            "resume_started",
            runtime.run_state,
            "running",
            next_revision,
            {
                "parent_session_id": runtime.active_session_id,
                "manual": bool(manual),
            },
        )
    return load_runtime(con)


def start_publication_retry_session(
    con: sqlite3.Connection,
    *,
    config: dict[str, object],
    tools: dict[str, object],
    scanner_version: str = MIN_READER_VERSION,
    session_id: str | None = None,
    lease_id: str | None = None,
    hostname: str | None = None,
    pid: int | None = None,
    process_start_token: str | None = None,
    now_utc: str | None = None,
) -> RuntimeSnapshot:
    """为 sealed partial 创建只发布 session，不退回扫描或改写业务证据。"""
    runtime = load_runtime(con)
    if runtime.run_state != "sealed_unpublished":
        raise core.PreflightError(
            f"当前状态 {runtime.run_state} 不能只重试发布")
    old_session = con.execute(
        "SELECT ended_at_utc FROM run_sessions WHERE session_id=?",
        (runtime.active_session_id,),
    ).fetchone()
    if old_session is None:
        raise core.PreflightError("sealed partial 缺少 active session")
    now = _now_text(now_utc)
    new_session = _require_compact_uuid(
        session_id or _new_id(), "session_id")
    new_lease = _require_compact_uuid(
        lease_id or _new_id(), "lease_id")
    host = str(hostname or socket.gethostname())
    process_id = int(os.getpid() if pid is None else pid)
    if process_id <= 0:
        raise core.PreflightError("session PID 必须大于 0")
    expires = _add_seconds(now, LEASE_TTL_SECONDS)
    session_number = int(con.execute(
        "SELECT COALESCE(MAX(session_number),0) FROM run_sessions"
    ).fetchone()[0]) + 1
    next_revision = runtime.state_revision + 1
    effective_config = dict(config)
    effective_config.update({
        "data_contract": DATA_CONTRACT,
        "min_reader_version": MIN_READER_VERSION,
        "resume_contract": RESUME_CONTRACT,
        "projection_contract": PROJECTION_CONTRACT,
        "filename_layout_version": FILENAME_LAYOUT_VERSION,
    })
    with con:
        if old_session[0] is None:
            con.execute(
                "UPDATE run_sessions SET session_status='failed',"
                " updated_at_utc=?,ended_at_utc=?,"
                " end_reason='publication_owner_replaced'"
                " WHERE session_id=? AND ended_at_utc IS NULL",
                (now, now, runtime.active_session_id),
            )
        con.execute(
            "INSERT INTO run_sessions"
            " (session_id,session_number,parent_session_id,session_kind,"
            " session_status,started_at_utc,updated_at_utc,hostname,pid,"
            " process_start_token,lease_id,lease_acquired_at_utc,"
            " lease_heartbeat_at_utc,lease_expires_at_utc,scanner_version,"
            " resume_contract,config_json,tools_json)"
            " VALUES (?,?,?,'resume','active',?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                new_session,
                session_number,
                runtime.active_session_id,
                now,
                now,
                host,
                process_id,
                process_start_token,
                new_lease,
                now,
                now,
                expires,
                scanner_version,
                RESUME_CONTRACT,
                json.dumps(effective_config, ensure_ascii=False),
                json.dumps(tools, ensure_ascii=False),
            ),
        )
        changed = con.execute(
            "UPDATE snapshot_runtime SET state_revision=?,resume_hint='none',"
            " active_session_id=?,current_stage='publish',updated_at_utc=?,"
            " last_checkpoint_at_utc=?,last_error_code=NULL,"
            " last_error_message=NULL WHERE id=1"
            " AND run_state='sealed_unpublished' AND state_revision=?",
            (next_revision, new_session, now, now, runtime.state_revision),
        ).rowcount
        if changed != 1:
            raise core.PreflightError(
                "发布重试时状态已变化，新 session 未提交")
        con.execute(
            "UPDATE stage_checkpoints SET state='running',session_id=?,"
            " updated_at_utc=?,finished_at_utc=NULL,current_entry_id=NULL,"
            " checkpoint_json=? WHERE stage='publish'",
            (
                new_session,
                now,
                json.dumps({
                    "method": "sqlite_backup_no_clobber",
                    "retry": True,
                }, ensure_ascii=False),
            ),
        )
        _append_state_event(
            con,
            new_session,
            now,
            "publication_retry_started",
            "sealed_unpublished",
            "sealed_unpublished",
            next_revision,
            {"parent_session_id": runtime.active_session_id},
        )
        _refresh_sealed_runtime_documents(con)
    return load_runtime(con)


def fail_publication_retry(
    con: sqlite3.Connection,
    error_message: str,
    *,
    now_utc: str | None = None,
) -> RuntimeSnapshot:
    """结束当前只发布 session，同时保持 sealed partial 可再次发布。"""
    runtime = load_runtime(con)
    if runtime.run_state != "sealed_unpublished":
        raise core.PreflightError(
            f"当前状态 {runtime.run_state} 不能记录发布重试失败")
    session = con.execute(
        "SELECT ended_at_utc FROM run_sessions WHERE session_id=?",
        (runtime.active_session_id,),
    ).fetchone()
    if session is None or session[0] is not None:
        raise core.PreflightError("发布重试 session 已结束或不存在")
    now = _now_text(now_utc)
    next_revision = runtime.state_revision + 1
    detail = str(error_message)[:2000]
    with con:
        changed = con.execute(
            "UPDATE snapshot_runtime SET state_revision=?,"
            " resume_hint='suggest',updated_at_utc=?,"
            " last_checkpoint_at_utc=?,last_error_code='publish_retry_failed',"
            " last_error_message=? WHERE id=1"
            " AND run_state='sealed_unpublished' AND state_revision=?",
            (next_revision, now, now, detail, runtime.state_revision),
        ).rowcount
        if changed != 1:
            raise core.PreflightError(
                "发布失败记录检测到状态竞态")
        con.execute(
            "UPDATE stage_checkpoints SET state='failed_recoverable',"
            " updated_at_utc=?,finished_at_utc=?,current_entry_id=NULL,"
            " checkpoint_json=? WHERE stage='publish'",
            (
                now,
                now,
                json.dumps({
                    "reason": "publish_retry_failed",
                    "error": detail,
                }, ensure_ascii=False),
            ),
        )
        con.execute(
            "UPDATE run_sessions SET session_status='failed',"
            " updated_at_utc=?,ended_at_utc=?,"
            " end_reason='publish_retry_failed'"
            " WHERE session_id=? AND ended_at_utc IS NULL",
            (now, now, runtime.active_session_id),
        )
        _append_state_event(
            con,
            runtime.active_session_id,
            now,
            "publication_retry_failed",
            "sealed_unpublished",
            "sealed_unpublished",
            next_revision,
            {"error": detail},
        )
        _refresh_sealed_runtime_documents(con)
    return load_runtime(con)


def _refresh_sealed_runtime_documents(con: sqlite3.Connection) -> None:
    """同步 sealed 后新增的 session 计数，不改写文件业务投影。"""
    sessions = int(con.execute(
        "SELECT COUNT(*) FROM run_sessions").fetchone()[0])
    retry_sessions = int(con.execute(
        "SELECT COUNT(*) FROM run_state_events"
        " WHERE event='publication_retry_started'"
    ).fetchone()[0])
    failed_retries = int(con.execute(
        "SELECT COUNT(*) FROM run_state_events"
        " WHERE event='publication_retry_failed'"
    ).fetchone()[0])

    counts_row = con.execute(
        "SELECT counts_json FROM snapshot_info WHERE id=1"
    ).fetchone()
    if counts_row is not None and counts_row[0]:
        try:
            counts = json.loads(str(counts_row[0]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise core.PreflightError(
                "sealed snapshot_info.counts_json 无法解析") from exc
        if not isinstance(counts, dict):
            raise core.PreflightError(
                "sealed snapshot_info.counts_json 必须是对象")
        counts["sessions"] = sessions
        con.execute(
            "UPDATE snapshot_info SET counts_json=? WHERE id=1",
            (json.dumps(counts, ensure_ascii=False),),
        )

    manifest_row = con.execute(
        "SELECT manifest_json FROM snapshot_manifest WHERE id=1"
    ).fetchone()
    if manifest_row is None:
        return
    try:
        manifest = json.loads(str(manifest_row[0]))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise core.PreflightError(
            "sealed snapshot manifest_json 无法解析") from exc
    if not isinstance(manifest, dict):
        raise core.PreflightError(
            "sealed snapshot manifest_json 必须是对象")
    counts = manifest.get("counts")
    if not isinstance(counts, dict):
        raise core.PreflightError("sealed manifest 缺少 counts 对象")
    counts["sessions"] = sessions
    manifest["publication_recovery"] = {
        "retry_sessions": retry_sessions,
        "failed_retries": failed_retries,
        "source_rescanned": False,
    }
    con.execute(
        "UPDATE snapshot_manifest SET manifest_json=? WHERE id=1",
        (json.dumps(manifest, ensure_ascii=False),),
    )


def update_stage_checkpoint(
    con: sqlite3.Connection,
    stage: str,
    state: str,
    *,
    items_done: int | None = None,
    items_total: int | None | object = _UNSET,
    bytes_done: int | None = None,
    bytes_total: int | None | object = _UNSET,
    error_count: int | None = None,
    current_entry_id: int | None | object = _UNSET,
    checkpoint: dict[str, object] | None = None,
    now_utc: str | None = None,
) -> None:
    if stage not in STAGES:
        raise ValueError(f"未知阶段：{stage}")
    if state not in STAGE_STATES:
        raise ValueError(f"未知阶段状态：{state}")
    runtime = load_runtime(con)
    row = con.execute(
        "SELECT items_done,items_total,bytes_done,bytes_total,error_count,"
        " current_entry_id,started_at_utc FROM stage_checkpoints"
        " WHERE stage=?",
        (stage,),
    ).fetchone()
    if row is None:
        raise core.PreflightError(f"缺少阶段检查点：{stage}")
    now = _now_text(now_utc)
    values = {
        "items_done": int(row[0]) if items_done is None else int(items_done),
        "items_total": row[1] if items_total is _UNSET else items_total,
        "bytes_done": int(row[2]) if bytes_done is None else int(bytes_done),
        "bytes_total": row[3] if bytes_total is _UNSET else bytes_total,
        "error_count": (
            int(row[4]) if error_count is None else int(error_count)),
        "current_entry_id": (
            row[5] if current_entry_id is _UNSET else current_entry_id),
        "started_at_utc": row[6] or (
            now if state == "running" else None),
        "finished_at_utc": (
            now if state in (
                "completed", "skipped", "failed_recoverable",
                "failed_terminal",
            ) else None
        ),
    }
    with con:
        con.execute(
            "UPDATE stage_checkpoints SET state=?,session_id=?,items_done=?,"
            " items_total=?,bytes_done=?,bytes_total=?,error_count=?,"
            " current_entry_id=?,started_at_utc=?,updated_at_utc=?,"
            " finished_at_utc=?,checkpoint_json=? WHERE stage=?",
            (
                state,
                runtime.active_session_id,
                values["items_done"],
                values["items_total"],
                values["bytes_done"],
                values["bytes_total"],
                values["error_count"],
                values["current_entry_id"],
                values["started_at_utc"],
                now,
                values["finished_at_utc"],
                json.dumps(checkpoint or {}, ensure_ascii=False),
                stage,
            ),
        )
        con.execute(
            "UPDATE snapshot_runtime SET current_stage=?,updated_at_utc=?,"
            " last_checkpoint_at_utc=? WHERE id=1",
            (stage, now, now),
        )


def recover_interrupted(
    con: sqlite3.Connection,
    *,
    reason: str = "owner_terminated",
    now_utc: str | None = None,
) -> RecoveryResult:
    """把未提交的当前工作还原到文件边界，保留历史 attempt。"""
    runtime = load_runtime(con)
    if runtime.run_state not in (
        "running", "pause_requested", "paused", "sealing",
    ):
        raise core.PreflightError(
            f"状态 {runtime.run_state} 不需要异常中断修复")
    now = _now_text(now_utc)
    next_revision = runtime.state_revision + 1
    with con:
        attempts = con.execute(
            "UPDATE entry_attempts SET status='abandoned',ended_at_utc=?,"
            " end_reason=?,decision='stop_and_resume',"
            " decision_source='shutdown' WHERE status='running'",
            (now, reason),
        ).rowcount
        hash_reset = con.execute(
            "UPDATE entries SET hash_status='pending'"
            " WHERE hash_status='processing'"
        ).rowcount
        metadata_reset = con.execute(
            "UPDATE entries SET meta_status='pending'"
            " WHERE meta_status='processing'"
        ).rowcount
        format_reset = con.execute(
            "UPDATE format_checks SET status='pending',checked_at_utc=NULL"
            " WHERE status='processing'"
        ).rowcount
        stages_reset = con.execute(
            "UPDATE stage_checkpoints SET state='failed_recoverable',"
            " current_entry_id=NULL,updated_at_utc=?,finished_at_utc=?"
            " WHERE state IN ('running','pause_requested','paused')",
            (now, now),
        ).rowcount
        con.execute(
            "UPDATE run_sessions SET session_status='abandoned',"
            " updated_at_utc=?,ended_at_utc=?,end_reason=?"
            " WHERE session_id=?",
            (now, now, reason, runtime.active_session_id),
        )
        changed = con.execute(
            "UPDATE snapshot_runtime SET run_state='failed_recoverable',"
            " state_revision=?,resume_hint='suggest',updated_at_utc=?,"
            " last_checkpoint_at_utc=?,last_error_code=?,"
            " last_error_message=? WHERE id=1 AND run_state=?"
            " AND state_revision=?",
            (
                next_revision,
                now,
                now,
                "interrupted_owner",
                reason,
                runtime.run_state,
                runtime.state_revision,
            ),
        ).rowcount
        if changed != 1:
            raise core.PreflightError(
                "异常中断修复事务检测到状态竞态，未提交任何重置")
        con.execute(
            "UPDATE snapshot_info SET scan_status='interrupted',"
            " database_integrity='pending',finished_at_utc=NULL WHERE id=1"
        )
        _append_state_event(
            con,
            runtime.active_session_id,
            now,
            "interrupted_recovered",
            runtime.run_state,
            "failed_recoverable",
            next_revision,
            {
                "reason": reason,
                "attempts_abandoned": attempts,
                "hash_entries_reset": hash_reset,
                "metadata_entries_reset": metadata_reset,
                "format_entries_reset": format_reset,
            },
        )
    return RecoveryResult(
        attempts_abandoned=attempts,
        hash_entries_reset=hash_reset,
        metadata_entries_reset=metadata_reset,
        format_entries_reset=format_reset,
        stages_reset=stages_reset,
        runtime=load_runtime(con),
    )


def validate_resume_identity(
    con: sqlite3.Connection,
    output_dir: str,
    partial_path: str,
    publish_stem_path: str,
    event_log_path: str,
) -> RuntimeSnapshot:
    runtime = load_runtime(con)
    actual = _validate_output_identity(
        output_dir, partial_path, publish_stem_path, event_log_path)
    expected = (
        runtime.output_dir,
        runtime.partial_path,
        runtime.publish_stem_path,
        runtime.event_log_path,
    )
    labels = ("output_dir", "partial_path", "publish_stem_path", "event_log_path")
    mismatches = [
        f"{label}={got}（冻结值：{wanted}）"
        for label, got, wanted in zip(labels, actual, expected)
        if os.path.normcase(got) != os.path.normcase(wanted)
    ]
    if mismatches:
        raise core.PreflightError(
            "resume 输出身份不一致：" + "；".join(mismatches))
    row = con.execute(
        "SELECT resume_contract,filename_layout_version,data_contract"
        " FROM snapshot_runtime WHERE id=1"
    ).fetchone()
    if tuple(row) != (
        RESUME_CONTRACT, FILENAME_LAYOUT_VERSION, DATA_CONTRACT,
    ):
        raise core.PreflightError(
            "partial 的 data/resume/filename layout 契约不兼容")
    return runtime


def start_attempt(
    con: sqlite3.Connection,
    entry_id: int,
    stage: str,
    *,
    tool_name: str | None = None,
    tool_version: str | None = None,
    coverage: str = "full",
    validator: str = "unassigned",
    now_utc: str | None = None,
    _current_reset: Callable[
        [sqlite3.Connection, int, int], None] | None = None,
) -> int:
    """开始一个文件边界 attempt，并把对应当前状态标为 processing。"""
    if stage not in ATTEMPT_STAGES:
        raise ValueError(f"未知 attempt stage：{stage}")
    if coverage not in ("sample", "full"):
        raise ValueError(f"未知格式覆盖：{coverage}")
    if stage == "format" and not str(validator).strip():
        raise ValueError("格式 validator 不能为空")
    runtime = load_runtime(con)
    if runtime.run_state != "running":
        raise core.PreflightError(
            f"状态 {runtime.run_state} 不能领取新的 attempt")
    entry = con.execute(
        "SELECT size_bytes,modified_at_utc FROM entries WHERE entry_id=?",
        (entry_id,),
    ).fetchone()
    if entry is None:
        raise core.PreflightError(f"attempt entry_id 不存在：{entry_id}")
    row = con.execute(
        "SELECT COALESCE(MAX(attempt_number),0) FROM entry_attempts"
        " WHERE entry_id=? AND stage=?",
        (entry_id, stage),
    ).fetchone()
    running = con.execute(
        "SELECT attempt_id FROM entry_attempts"
        " WHERE entry_id=? AND stage=? AND status='running'",
        (entry_id, stage),
    ).fetchone()
    if running is not None:
        raise core.PreflightError(
            f"entry {entry_id} 的 {stage} attempt {running[0]} 尚未结束")
    attempt_number = int(row[0]) + 1
    now = _now_text(now_utc)
    with con:
        cursor = con.execute(
            "INSERT INTO entry_attempts"
            " (entry_id,session_id,stage,attempt_number,status,tool_name,"
            " tool_version,started_at_utc,last_progress_at_utc,"
            " source_size_bytes,source_modified_at_utc)"
            " VALUES (?,?,?,?,'running',?,?,?,?,?,?)",
            (
                entry_id,
                runtime.active_session_id,
                stage,
                attempt_number,
                tool_name,
                tool_version,
                now,
                now,
                int(entry[0]),
                str(entry[1]),
            ),
        )
        attempt_id = int(cursor.lastrowid)
        if _current_reset is not None:
            _current_reset(con, entry_id, attempt_id)
        if stage == "hash":
            con.execute(
                "UPDATE entries SET hash_status='processing'"
                " WHERE entry_id=?",
                (entry_id,),
            )
        elif stage == "metadata":
            con.execute(
                "UPDATE entries SET meta_status='processing'"
                " WHERE entry_id=?",
                (entry_id,),
            )
        elif stage == "format":
            con.execute(
                "INSERT INTO format_checks"
                " (entry_id,attempt_id,status,coverage,validator,tool_name,"
                " tool_version,checked_at_utc,result_revision)"
                " VALUES (?,?,'processing',?,?,?,?,NULL,1)"
                " ON CONFLICT(entry_id) DO UPDATE SET"
                " attempt_id=excluded.attempt_id,status='processing',"
                " coverage=excluded.coverage,validator=excluded.validator,"
                " tool_name=excluded.tool_name,"
                " tool_version=excluded.tool_version,stat_match=NULL,"
                " detail=NULL,checked_at_utc=NULL,"
                " result_revision=format_checks.result_revision+1",
                (
                    entry_id,
                    attempt_id,
                    coverage,
                    validator,
                    tool_name,
                    tool_version,
                ),
            )
        con.execute(
            "UPDATE stage_checkpoints SET state='running',session_id=?,"
            " current_entry_id=?,updated_at_utc=?,"
            " started_at_utc=COALESCE(started_at_utc,?) WHERE stage=?",
            (runtime.active_session_id, entry_id, now, now, stage),
        )
        con.execute(
            "UPDATE snapshot_runtime SET current_stage=?,updated_at_utc=?,"
            " last_checkpoint_at_utc=? WHERE id=1",
            (stage, now, now),
        )
    return attempt_id


def update_attempt_progress(
    con: sqlite3.Connection,
    attempt_id: int,
    *,
    bytes_read: int,
    final_offset: int,
    stall_count: int = 0,
    max_stall_seconds: float = 0.0,
    now_utc: str | None = None,
) -> None:
    runtime = load_runtime(con)
    if runtime.run_state not in ("running", "pause_requested"):
        raise core.PreflightError(
            f"状态 {runtime.run_state} 不能更新 attempt 进度")
    now = _now_text(now_utc)
    with con:
        changed = con.execute(
            "UPDATE entry_attempts SET last_progress_at_utc=?,bytes_read=?,"
            " final_offset=?,stall_count=?,max_stall_seconds=?"
            " WHERE attempt_id=? AND status='running' AND session_id=?",
            (
                now,
                int(bytes_read),
                int(final_offset),
                int(stall_count),
                float(max_stall_seconds),
                int(attempt_id),
                runtime.active_session_id,
            ),
        ).rowcount
        if changed != 1:
            raise core.PreflightError(
                f"attempt {attempt_id} 不存在或已经结束")


def _current_status_for_attempt(stage: str, status: str) -> str | None:
    if stage == "hash":
        return {
            "succeeded": "done",
            "unstable": "unstable",
            "unsupported": "skipped",
            "skipped_policy": "skipped",
            "cancelled": "pending",
            "abandoned": "pending",
        }.get(status, "error")
    if stage == "metadata":
        return {
            "succeeded": "done",
            "unstable": "unstable",
            "timeout": "timeout",
            "unsupported": "not_applicable",
            "skipped_policy": "skipped",
            "cancelled": "pending",
            "abandoned": "pending",
        }.get(status, "error")
    if stage == "format":
        return {
            "succeeded": "valid",
            "invalid": "invalid",
            "timeout": "timeout",
            "unstable": "unstable",
            "unsupported": "unsupported",
            "skipped_policy": "skipped_policy",
            "cancelled": "pending",
            "abandoned": "pending",
        }.get(status, "error")
    return None


def finish_attempt(
    con: sqlite3.Connection,
    attempt_id: int,
    status: str,
    *,
    bytes_read: int | None = None,
    final_offset: int | None = None,
    stall_count: int | None = None,
    max_stall_seconds: float | None = None,
    decision: str = "none",
    decision_source: str = "none",
    end_reason: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    result: dict[str, object] | None = None,
    stat_match: bool | None = None,
    detail: str | None = None,
    performance: dict[str, object] | None = None,
    now_utc: str | None = None,
    _current_writer: Callable[
        [sqlite3.Connection, int, int], None] | None = None,
) -> None:
    """结束 attempt；历史行保留，当前结果与性能摘要在同一事务更新。"""
    if status not in ATTEMPT_STATUSES or status == "running":
        raise ValueError(f"无效 attempt 结束状态：{status}")
    if decision not in ATTEMPT_DECISIONS:
        raise ValueError(f"未知 attempt decision：{decision}")
    if decision_source not in DECISION_SOURCES:
        raise ValueError(f"未知 decision source：{decision_source}")
    runtime = load_runtime(con)
    if runtime.run_state not in ("running", "pause_requested"):
        raise core.PreflightError(
            f"状态 {runtime.run_state} 不能提交 attempt 结果")
    row = con.execute(
        "SELECT entry_id,session_id,stage,status,bytes_read,final_offset,"
        " stall_count,max_stall_seconds FROM entry_attempts"
        " WHERE attempt_id=?",
        (attempt_id,),
    ).fetchone()
    if row is None or row[3] != "running":
        raise core.PreflightError(
            f"attempt {attempt_id} 不存在或已经结束")
    entry_id, session_id, stage = int(row[0]), str(row[1]), str(row[2])
    if session_id != runtime.active_session_id:
        raise core.PreflightError(
            f"attempt {attempt_id} 不属于当前 active session")
    final_bytes = int(row[4]) if bytes_read is None else int(bytes_read)
    offset = int(row[5]) if final_offset is None else int(final_offset)
    stalls = int(row[6]) if stall_count is None else int(stall_count)
    longest = (
        float(row[7])
        if max_stall_seconds is None else float(max_stall_seconds)
    )
    current_status = _current_status_for_attempt(stage, status)
    now = _now_text(now_utc)
    with con:
        changed = con.execute(
            "UPDATE entry_attempts SET status=?,last_progress_at_utc=?,"
            " ended_at_utc=?,bytes_read=?,final_offset=?,stall_count=?,"
            " max_stall_seconds=?,decision=?,decision_source=?,end_reason=?,"
            " error_code=?,error_message=?,result_json=?"
            " WHERE attempt_id=? AND status='running'",
            (
                status,
                now,
                now,
                final_bytes,
                offset,
                stalls,
                longest,
                decision,
                decision_source,
                end_reason,
                error_code,
                error_message,
                json.dumps(result or {}, ensure_ascii=False),
                attempt_id,
            ),
        ).rowcount
        if changed != 1:
            raise core.PreflightError(
                f"attempt {attempt_id} 结束时发生竞态")
        if _current_writer is not None:
            _current_writer(con, entry_id, attempt_id)
        if stage == "hash":
            con.execute(
                "UPDATE entries SET hash_status=? WHERE entry_id=?",
                (current_status, entry_id),
            )
        elif stage == "metadata":
            con.execute(
                "UPDATE entries SET meta_status=? WHERE entry_id=?",
                (current_status, entry_id),
            )
        elif stage == "format":
            format_changed = con.execute(
                "UPDATE format_checks SET status=?,stat_match=?,detail=?,"
                " checked_at_utc=? WHERE entry_id=? AND attempt_id=?",
                (
                    current_status,
                    None if stat_match is None else int(bool(stat_match)),
                    detail,
                    now,
                    entry_id,
                    attempt_id,
                ),
            ).rowcount
            if format_changed != 1:
                raise core.PreflightError(
                    f"attempt {attempt_id} 缺少对应的当前格式结果")
        if performance is not None:
            _insert_performance(
                con,
                attempt_id,
                entry_id,
                session_id,
                stage,
                performance,
                now,
            )
        con.execute(
            "UPDATE stage_checkpoints SET current_entry_id=NULL,"
            " updated_at_utc=? WHERE stage=? AND current_entry_id=?",
            (now, stage, entry_id),
        )
        con.execute(
            "UPDATE snapshot_runtime SET updated_at_utc=?,"
            " last_checkpoint_at_utc=? WHERE id=1",
            (now, now),
        )


def _insert_performance(
    con: sqlite3.Connection,
    attempt_id: int,
    entry_id: int,
    session_id: str,
    stage: str,
    performance: dict[str, object],
    recorded_at_utc: str,
) -> None:
    required = (
        "origin",
        "size_bytes",
        "bytes_read",
        "elapsed_seconds",
        "active_read_seconds",
        "stall_count",
        "longest_stall_seconds",
        "final_offset",
        "ended_reason",
    )
    missing = [key for key in required if key not in performance]
    if missing:
        raise ValueError(
            "performance 缺少字段：" + "、".join(missing))
    confidence = str(performance.get("candidate_confidence", "none"))
    reason_value = performance.get("candidate_reason")
    reason = None if reason_value is None else str(reason_value)
    if confidence not in ("none", "low", "high"):
        raise ValueError(f"未知 performance candidate_confidence：{confidence}")
    if confidence != "none" and (
            reason is None or "读取性能异常候选" not in reason):
        raise ValueError(
            "性能候选原因必须明确使用「读取性能异常候选」措辞")
    con.execute(
        "INSERT INTO read_performance"
        " (attempt_id,entry_id,session_id,stage,origin,size_bytes,bytes_read,"
        " elapsed_seconds,active_read_seconds,stall_count,"
        " longest_stall_seconds,first_stall_offset,last_stall_offset,"
        " final_offset,ended_reason,candidate_confidence,candidate_reason,"
        " recorded_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            attempt_id,
            entry_id,
            session_id,
            stage,
            performance["origin"],
            performance["size_bytes"],
            performance["bytes_read"],
            performance["elapsed_seconds"],
            performance["active_read_seconds"],
            performance["stall_count"],
            performance["longest_stall_seconds"],
            performance.get("first_stall_offset"),
            performance.get("last_stall_offset"),
            performance["final_offset"],
            performance["ended_reason"],
            confidence,
            reason,
            recorded_at_utc,
        ),
    )


def heartbeat_session(
    con: sqlite3.Connection,
    lease_id: str,
    *,
    now_utc: str | None = None,
) -> str:
    runtime = load_runtime(con)
    now = _now_text(now_utc)
    expires = _add_seconds(now, LEASE_TTL_SECONDS)
    with con:
        changed = con.execute(
            "UPDATE run_sessions SET lease_heartbeat_at_utc=?,"
            " lease_expires_at_utc=?,updated_at_utc=?"
            " WHERE session_id=? AND lease_id=?"
            " AND session_status IN ('active','paused')",
            (
                now,
                expires,
                now,
                runtime.active_session_id,
                lease_id,
            ),
        ).rowcount
        if changed != 1:
            raise core.PreflightError(
                "lease heartbeat 与当前 active session 不匹配")
        con.execute(
            "UPDATE snapshot_runtime SET updated_at_utc=? WHERE id=1",
            (now,),
        )
    return expires


def read_event_journal(path: str) -> EventJournalRead:
    """接纳合法 JSONL；只容忍最后一个未换行的截断 JSON。"""
    normalized = _normalized(path)
    try:
        with open(normalized, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        raise core.PreflightError(
            f"事件日志无法读取：{normalized}：{exc}") from exc
    records: list[dict[str, object]] = []
    lines = data.splitlines(keepends=True)
    truncated_tail = False
    for index, raw_line in enumerate(lines):
        content = raw_line.rstrip(b"\r\n")
        if not content.strip():
            continue
        try:
            value = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            is_last_unterminated = (
                index == len(lines) - 1
                and not raw_line.endswith((b"\n", b"\r"))
            )
            if is_last_unterminated:
                truncated_tail = True
                break
            raise core.PreflightError(
                f"事件日志第 {index + 1} 行损坏：{normalized}") from exc
        if not isinstance(value, dict) or not value.get("event"):
            raise core.PreflightError(
                f"事件日志第 {index + 1} 行缺少 event：{normalized}")
        records.append(value)
    return EventJournalRead(tuple(records), truncated_tail)


def _process_start_token(pid: int) -> str | None:
    """读取一个精确 PID 的 Windows 创建时间；不枚举系统进程。"""
    if sys.platform != "win32":
        return None
    import ctypes
    import ctypes.wintypes
    process_query = 0x1000
    kernel32 = ctypes.windll.kernel32
    open_process = kernel32.OpenProcess
    open_process.argtypes = (
        ctypes.wintypes.DWORD, ctypes.wintypes.BOOL,
        ctypes.wintypes.DWORD,
    )
    open_process.restype = ctypes.wintypes.HANDLE
    get_process_times = kernel32.GetProcessTimes
    get_process_times.argtypes = (
        ctypes.wintypes.HANDLE,
        ctypes.POINTER(ctypes.wintypes.FILETIME),
        ctypes.POINTER(ctypes.wintypes.FILETIME),
        ctypes.POINTER(ctypes.wintypes.FILETIME),
        ctypes.POINTER(ctypes.wintypes.FILETIME),
    )
    get_process_times.restype = ctypes.wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (ctypes.wintypes.HANDLE,)
    close_handle.restype = ctypes.wintypes.BOOL
    handle = open_process(process_query, False, pid)
    if not handle:
        return None
    try:
        creation = ctypes.wintypes.FILETIME()
        exit_time = ctypes.wintypes.FILETIME()
        kernel = ctypes.wintypes.FILETIME()
        user = ctypes.wintypes.FILETIME()
        ok = get_process_times(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        )
        if not ok:
            return None
        value = (int(creation.dwHighDateTime) << 32) | int(
            creation.dwLowDateTime)
        return str(value)
    finally:
        close_handle(handle)


def new_lease_record(
    session_id: str,
    *,
    lease_id: str | None = None,
    host: str | None = None,
    pid: int | None = None,
    process_start_token: str | None | object = _UNSET,
    now_utc: str | None = None,
) -> LeaseRecord:
    now = _now_text(now_utc)
    owner_session = _require_compact_uuid(session_id, "session_id")
    owner_lease = _require_compact_uuid(lease_id or _new_id(), "lease_id")
    process_id = int(os.getpid() if pid is None else pid)
    if process_id <= 0:
        raise core.PreflightError("lease PID 必须大于 0")
    owner_host = str(host or socket.gethostname()).strip()
    if not owner_host:
        raise core.PreflightError("lease host 不能为空")
    token = (
        _process_start_token(process_id)
        if process_start_token is _UNSET else process_start_token
    )
    return LeaseRecord(
        lease_id=owner_lease,
        session_id=owner_session,
        host=owner_host,
        pid=process_id,
        process_start_token=(str(token) if token is not None else None),
        acquired_at_utc=now,
        heartbeat_at_utc=now,
        expires_at_utc=_add_seconds(now, LEASE_TTL_SECONDS),
    )


def lease_record_from_dict(value: dict[str, object]) -> LeaseRecord:
    required = (
        "lease_id",
        "session_id",
        "host",
        "pid",
        "acquired_at_utc",
        "heartbeat_at_utc",
        "expires_at_utc",
    )
    missing = [key for key in required if value.get(key) in (None, "")]
    if missing:
        raise core.PreflightError(
            "lease 文件缺少字段：" + "、".join(missing))
    lease_id = _require_compact_uuid(str(value["lease_id"]), "lease_id")
    session_id = _require_compact_uuid(
        str(value["session_id"]), "session_id")
    record = LeaseRecord(
        lease_id=lease_id,
        session_id=session_id,
        host=str(value["host"]),
        pid=int(value["pid"]),
        process_start_token=(
            str(value["process_start_token"])
            if value.get("process_start_token") is not None else None
        ),
        acquired_at_utc=str(value["acquired_at_utc"]),
        heartbeat_at_utc=str(value["heartbeat_at_utc"]),
        expires_at_utc=str(value["expires_at_utc"]),
    )
    _parse_utc(record.acquired_at_utc)
    _parse_utc(record.heartbeat_at_utc)
    _parse_utc(record.expires_at_utc)
    if record.pid <= 0:
        raise core.PreflightError("lease PID 无效")
    return record


def read_lease_file(path: str) -> LeaseRecord:
    normalized = _normalized(path)
    try:
        with open(normalized, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise core.PreflightError(
            f"lease 文件损坏：{normalized}：{exc}") from exc
    if not isinstance(value, dict):
        raise core.PreflightError(f"lease 文件不是 JSON 对象：{normalized}")
    return lease_record_from_dict(value)


def classify_lease(
    record: LeaseRecord,
    *,
    now_utc: str,
    local_host: str | None = None,
    pid_alive: Callable[[int], bool] | None = None,
    process_token: Callable[[int], str | None] | None = None,
) -> str:
    host = str(local_host or socket.gethostname())
    if record.host.casefold() == host.casefold():
        alive = (pid_alive or core._pid_alive)(record.pid)
        if not alive:
            return "stale_dead"
        current_token = (process_token or _process_start_token)(record.pid)
        if (
            record.process_start_token is not None
            and current_token is not None
            and record.process_start_token != str(current_token)
        ):
            return "stale_pid_reused"
        return "active_local"
    if _parse_utc(now_utc) <= _parse_utc(record.expires_at_utc):
        return "active_foreign"
    return "expired_foreign"


def _lease_bytes(record: LeaseRecord) -> bytes:
    text = json.dumps(record.as_dict(), ensure_ascii=False, sort_keys=True)
    return (text + "\n").encode("utf-8")


def _write_new_lease(path: str, record: LeaseRecord) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(_lease_bytes(record))


def _replace_lease(
    path: str,
    record: LeaseRecord,
    expected_bytes: bytes,
) -> None:
    normalized = _normalized(path)
    temp_path = normalized + f".{record.lease_id}.tmp"
    try:
        with open(normalized, "rb") as handle:
            if handle.read() != expected_bytes:
                raise core.PreflightError(
                    "占用锁在替换前已被其他持有者修改")
        descriptor = os.open(
            temp_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_lease_bytes(record))
        with open(normalized, "rb") as handle:
            if handle.read() != expected_bytes:
                raise core.PreflightError(
                    "lease 在原子替换前发生竞态")
        os.replace(temp_path, normalized)
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass


def acquire_lease_file(
    path: str,
    session_id: str,
    *,
    takeover: bool = False,
    lease_id: str | None = None,
    host: str | None = None,
    pid: int | None = None,
    process_start_token: str | None | object = _UNSET,
    now_utc: str | None = None,
    pid_alive: Callable[[int], bool] | None = None,
    token_probe: Callable[[int], str | None] | None = None,
) -> LeaseRecord:
    normalized = _normalized(path)
    record = new_lease_record(
        session_id,
        lease_id=lease_id,
        host=host,
        pid=pid,
        process_start_token=process_start_token,
        now_utc=now_utc,
    )
    try:
        _write_new_lease(normalized, record)
        return record
    except FileExistsError:
        pass
    try:
        with open(normalized, "rb") as handle:
            existing_bytes = handle.read()
        existing_value = json.loads(existing_bytes.decode("utf-8"))
        if not isinstance(existing_value, dict):
            raise ValueError("not an object")
        existing = lease_record_from_dict(existing_value)
        classification = classify_lease(
            existing,
            now_utc=_now_text(now_utc),
            local_host=host,
            pid_alive=pid_alive,
            process_token=token_probe,
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError,
            core.PreflightError):
        classification = "invalid"
        try:
            with open(normalized, "rb") as handle:
                existing_bytes = handle.read()
        except OSError as exc:
            raise core.PreflightError(
                f"lease 竞态读取失败：{normalized}：{exc}") from exc
    if classification in ("active_local", "active_foreign"):
        raise core.PreflightError(
            f"partial lease 仍有效（{classification}）：{normalized}")
    if not takeover:
        raise core.PreflightError(
            f"存在失效或损坏 lease（{classification}）；"
            "必须通过明确的续传操作接管")
    _replace_lease(normalized, record, existing_bytes)
    return record


def refresh_lease_file(
    path: str,
    lease_id: str,
    *,
    now_utc: str | None = None,
) -> LeaseRecord:
    normalized = _normalized(path)
    with open(normalized, "rb") as handle:
        expected = handle.read()
    try:
        value = json.loads(expected.decode("utf-8"))
        current = lease_record_from_dict(value)
    except (UnicodeError, ValueError, json.JSONDecodeError,
            core.PreflightError) as exc:
        raise core.PreflightError(
            f"无法刷新损坏 lease：{normalized}") from exc
    if current.lease_id != lease_id:
        raise core.PreflightError("lease refresh 被非 owner 拒绝")
    now = _now_text(now_utc)
    refreshed = LeaseRecord(
        lease_id=current.lease_id,
        session_id=current.session_id,
        host=current.host,
        pid=current.pid,
        process_start_token=current.process_start_token,
        acquired_at_utc=current.acquired_at_utc,
        heartbeat_at_utc=now,
        expires_at_utc=_add_seconds(now, LEASE_TTL_SECONDS),
    )
    _replace_lease(normalized, refreshed, expected)
    return refreshed


def release_lease_file(path: str, lease_id: str) -> None:
    normalized = _normalized(path)
    try:
        with open(normalized, "rb") as handle:
            expected = handle.read()
        value = json.loads(expected.decode("utf-8"))
        current = lease_record_from_dict(value)
    except FileNotFoundError:
        return
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError,
            core.PreflightError) as exc:
        raise core.PreflightError(
            f"无法释放损坏 lease：{normalized}") from exc
    if current.lease_id != lease_id:
        raise core.PreflightError("lease release 被非 owner 拒绝")
    with open(normalized, "rb") as handle:
        if handle.read() != expected:
            raise core.PreflightError("lease release 检测到 owner 竞态")
    os.remove(normalized)


def _publish_no_clobber(working_path: str, final_path: str) -> None:
    if os.path.exists(final_path):
        raise core.PreflightError(
            f"发布冲突：目标已存在且不会覆盖：{final_path}")
    try:
        if os.name == "nt":
            os.rename(working_path, final_path)
        else:
            os.link(working_path, final_path)
            os.unlink(working_path)
    except OSError as exc:
        raise core.PreflightError(
            f"发布失败，目标保持不动：{final_path}：{exc}") from exc


def _write_issue_text_exclusive(path: str, content: str) -> None:
    descriptor = None
    created = False
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        created = True
        with os.fdopen(
                descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = None
            handle.write(content.replace("\r\n", "\n").replace("\r", "\n"))
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        if created:
            try:
                os.remove(path)
            except OSError:
                pass
        raise


def _write_binary_exclusive(path: str, content: bytes) -> None:
    descriptor = None
    created = False
    try:
        descriptor = os.open(
            path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY
            | getattr(os, "O_BINARY", 0),
        )
        created = True
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("伴随产物写入未取得进展")
            view = view[written:]
        os.fsync(descriptor)
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
            descriptor = None
        if created:
            try:
                os.remove(path)
            except OSError:
                pass
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _publish_with_artifacts_no_clobber(
    working_path: str,
    final_path: str,
    issue_markdown: str | None,
    additional_artifacts: Mapping[str, bytes],
) -> tuple[str | None, tuple[str, ...]]:
    """协调发布数据库、问题报告与额外伴随文件；失败回收本次精确目标。"""
    final = _normalized(final_path)
    working = _normalized(working_path)
    directory = os.path.dirname(final)
    issue_path = (
        core.artifact_issue_report_path(final)
        if issue_markdown is not None else None
    )
    normalized_artifacts: list[tuple[str, bytes]] = []
    occupied = {os.path.normcase(final), os.path.normcase(working)}
    if issue_path is not None:
        occupied.add(os.path.normcase(_normalized(issue_path)))
    for raw_path, payload in additional_artifacts.items():
        if not isinstance(raw_path, str) or not os.path.isabs(raw_path):
            raise core.PreflightError("额外伴随产物路径必须是绝对路径")
        path = _normalized(raw_path)
        if os.path.normcase(os.path.dirname(path)) != os.path.normcase(
                directory):
            raise core.PreflightError("额外伴随产物必须与最终数据库同目录")
        key = os.path.normcase(path)
        if key in occupied:
            raise core.PreflightError(f"额外伴随产物路径冲突：{path}")
        if not isinstance(payload, bytes):
            raise TypeError("额外伴随产物内容必须是 bytes")
        occupied.add(key)
        normalized_artifacts.append((path, payload))

    targets = [path for path, _payload in normalized_artifacts]
    if issue_path is not None:
        targets.append(_normalized(issue_path))
    targets.append(final)
    normalized_issue = _normalized(issue_path) if issue_path is not None else None
    conflict = next((path for path in targets if os.path.exists(path)), None)
    if conflict is not None:
        if normalized_issue is not None \
                and os.path.normcase(conflict) == os.path.normcase(
                    normalized_issue):
            raise core.PreflightError(
                "发布冲突：问题报告已存在且不会覆盖："
                f"{normalized_issue}")
        raise core.PreflightError(
            f"发布冲突：目标已存在且不会覆盖：{conflict}")

    created: list[str] = []
    staging_paths: list[str] = []
    try:
        for path, payload in normalized_artifacts:
            staging = os.path.join(
                directory,
                f".{os.path.basename(path)}.{uuid.uuid4().hex}.publishing",
            )
            staging_paths.append(staging)
            _write_binary_exclusive(staging, payload)
            expected_digest = hashlib.sha256(payload).hexdigest()
            if core.sha256_file(staging) != expected_digest:
                raise core.PreflightError(
                    f"额外伴随产物的暂存文件摘要校验失败：{path}")
            _publish_no_clobber(staging, path)
            staging_paths.remove(staging)
            created.append(path)
        if issue_path is not None:
            try:
                _write_issue_text_exclusive(issue_path, issue_markdown)
            except FileExistsError as exc:
                raise core.PreflightError(
                    "发布冲突：问题报告已存在且不会覆盖："
                    f"{issue_path}") from exc
            except OSError as exc:
                raise core.PreflightError(
                    f"问题报告无法创建：{issue_path}：{exc}") from exc
            created.append(_normalized(issue_path))
        _publish_no_clobber(working, final)
    except Exception:
        for path in reversed(created):
            try:
                os.remove(path)
            except OSError:
                pass
        raise
    finally:
        for staging in staging_paths:
            try:
                os.remove(staging)
            except OSError:
                pass
    return issue_path, tuple(path for path, _payload in normalized_artifacts)


def publish_sealed_snapshot(
    partial_path: str,
    staging_path: str,
    *,
    lease_path: str | None = None,
    lease_id: str | None = None,
    now_utc: str | None = None,
    issue_report_builder: Callable[
        [sqlite3.Connection, str], str | None
    ] | None = None,
    additional_artifact_builder: Callable[
        [sqlite3.Connection, str, str], Mapping[str, bytes]
    ] | None = None,
) -> PublicationResult:
    """复制并发布已封存的未完成快照；发布前可联动创建只读问题报告。"""
    if (lease_path is None) != (lease_id is None):
        raise ValueError("lease_path 与 lease_id 必须同时提供或同时省略")
    partial = _normalized(partial_path)
    staging = _normalized(staging_path)
    if not os.path.isfile(partial):
        raise core.PreflightError(f"sealed partial 不存在：{partial}")
    if os.path.normcase(partial) == os.path.normcase(staging):
        raise core.PreflightError("发布暂存文件不能覆盖已封存的未完成快照")
    if os.path.normcase(os.path.dirname(partial)) != os.path.normcase(
            os.path.dirname(staging)):
        raise core.PreflightError("发布暂存文件必须与已封存的未完成快照位于同一目录")

    source = None
    destination = None
    staging_created = False
    issue_report_path = None
    artifact_paths: tuple[str, ...] = ()
    try:
        source_uri = Path(partial).resolve(strict=True).as_uri() + "?mode=ro"
        source = sqlite3.connect(source_uri, uri=True)
        source_runtime = load_runtime(source)
        if source_runtime.run_state != "sealed_unpublished":
            raise core.PreflightError(
                "只有 sealed_unpublished partial 可以创建发布副本")
        if os.path.normcase(source_runtime.partial_path) != os.path.normcase(
                partial):
            raise core.PreflightError(
                "sealed partial 路径与数据库冻结身份不一致")
        descriptor = os.open(
            staging, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(descriptor)
        staging_created = True
        destination = sqlite3.connect(staging)
        source.backup(destination)
        destination.execute("PRAGMA foreign_keys=ON")
        update_stage_checkpoint(
            destination,
            "publish",
            "completed",
            items_done=1,
            items_total=1,
            current_entry_id=None,
            checkpoint={"method": "sqlite_backup_no_clobber"},
            now_utc=now_utc,
        )
        mark_published(destination, now_utc=now_utc)
        integrity = destination.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise core.PreflightError(
                f"发布副本 SQLite 完整性检查失败：{integrity}")
        foreign_key_error = destination.execute(
            "PRAGMA foreign_key_check").fetchone()
        if foreign_key_error is not None:
            raise core.PreflightError(
                "发布副本外键检查失败：" + str(tuple(foreign_key_error)))
        destination.execute("PRAGMA journal_mode=DELETE")
        destination.close()
        destination = None
        source.close()
        source = None

        digest = core.sha256_file(staging)
        final_path = (
            source_runtime.publish_stem_path
            + f"_{digest[:8].upper()}.sqlite"
        )
        issue_markdown = None
        additional_artifacts: Mapping[str, bytes] = {}
        if issue_report_builder is not None \
                or additional_artifact_builder is not None:
            report_source = None
            try:
                report_uri = (
                    Path(staging).resolve(strict=True).as_uri() + "?mode=ro")
                report_source = sqlite3.connect(report_uri, uri=True)
                if additional_artifact_builder is not None:
                    built = additional_artifact_builder(
                        report_source, final_path, digest)
                    if not isinstance(built, Mapping):
                        raise TypeError(
                            "additional_artifact_builder 必须返回 Mapping")
                    additional_artifacts = built
                if issue_report_builder is not None:
                    issue_markdown = issue_report_builder(
                        report_source, os.path.basename(final_path))
                    if issue_markdown is not None \
                            and not isinstance(issue_markdown, str):
                        raise TypeError(
                            "issue_report_builder 必须返回 str 或 None")
            finally:
                if report_source is not None:
                    report_source.close()
            digest_after_report = core.sha256_file(staging)
            if digest_after_report != digest:
                raise core.PreflightError(
                    "问题报告只读分析改变了发布副本，已拒绝发布")
        issue_report_path, artifact_paths = \
            _publish_with_artifacts_no_clobber(
                staging,
                final_path,
                issue_markdown,
                additional_artifacts,
            )
        staging_created = False
    except Exception:
        if destination is not None:
            destination.close()
        if source is not None:
            source.close()
        if staging_created:
            try:
                os.remove(staging)
            except OSError:
                pass
        raise

    warnings: list[str] = []
    partial_removed = False
    lease_released = lease_path is None
    try:
        os.remove(partial)
        partial_removed = True
    except OSError as exc:
        warnings.append(f"最终快照已发布，但 sealed partial 未删除：{exc}")
    if lease_path is not None and lease_id is not None:
        try:
            release_lease_file(lease_path, lease_id)
            lease_released = True
        except (OSError, core.PreflightError) as exc:
            warnings.append(f"最终快照已发布，但 lease 未释放：{exc}")
    return PublicationResult(
        final_path=final_path,
        sha256=digest,
        partial_removed=partial_removed,
        lease_released=lease_released,
        warnings=tuple(warnings),
        issue_report_path=issue_report_path,
        artifact_paths=artifact_paths,
    )

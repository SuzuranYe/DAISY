"""DAISY schema 4 运行文件、恢复预览与 lease 生命周期。

这里封装 partial 的独占创建和明确恢复，不负责扫描业务阶段。所有 lease 操作
只使用调用方给出的精确路径和 lease ID，不枚举或终止其它进程。
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import sqlite3
import threading
from typing import Callable
import uuid

import Script_DAISY_Lib_DBS_01_Core as core
import Script_DAISY_Lib_DBS_08_State as dbstate


LEASE_SUFFIX = ".lease"


@dataclass(frozen=True)
class ResumePreview:
    partial_path: str
    lease_path: str
    run_state: str
    resume_hint: str
    current_stage: str
    active_session_id: str
    active_session_ended: bool
    lease_classification: str
    roots: tuple[tuple[str, str], ...]
    config: dict[str, object]
    tools: dict[str, object]


@dataclass
class RunHandle:
    connection: sqlite3.Connection
    partial_path: str
    lease_path: str
    lease: dbstate.LeaseRecord
    resumed: bool


def _normalized(path: str) -> str:
    return os.path.normpath(os.path.abspath(os.fspath(path)))


def lease_path_for_partial(partial_path: str) -> str:
    partial = _normalized(partial_path)
    if not partial.casefold().endswith(".partial.sqlite"):
        raise core.PreflightError(
            f"schema 4 partial 路径后缀无效：{partial}")
    return partial + LEASE_SUFFIX


def _json_object(value: object, label: str) -> dict[str, object]:
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise core.PreflightError(f"{label} 不是有效 JSON：{exc}") from exc
    if not isinstance(parsed, dict):
        raise core.PreflightError(f"{label} 必须是 JSON 对象")
    return parsed


def _readonly_connection(path: str) -> sqlite3.Connection:
    try:
        uri = Path(path).resolve(strict=True).as_uri() + "?mode=ro"
        return sqlite3.connect(uri, uri=True)
    except (OSError, sqlite3.Error) as exc:
        raise core.PreflightError(
            f"无法只读打开 schema 4 partial：{path}：{exc}") from exc


def _readwrite_connection(path: str) -> sqlite3.Connection:
    try:
        uri = Path(path).resolve(strict=True).as_uri() + "?mode=rw"
        return sqlite3.connect(uri, uri=True)
    except (OSError, sqlite3.Error) as exc:
        raise core.PreflightError(
            f"无法读写打开 schema 4 partial：{path}：{exc}") from exc


def _session_payload(
    con: sqlite3.Connection,
    session_id: str,
) -> tuple[bool, dict[str, object], dict[str, object]]:
    session = con.execute(
        "SELECT ended_at_utc,config_json,tools_json FROM run_sessions"
        " WHERE session_id=?",
        (session_id,),
    ).fetchone()
    if session is None:
        raise core.PreflightError("partial 缺少 active session")
    return (
        session[0] is not None,
        _json_object(session[1], "session config_json"),
        _json_object(session[2], "session tools_json"),
    )


def inspect_resume(
    partial_path: str,
    *,
    now_utc: str | None = None,
    local_host: str | None = None,
    pid_alive: Callable[[int], bool] | None = None,
    process_token: Callable[[int], str | None] | None = None,
) -> ResumePreview:
    """只读检查恢复候选；不创建、接管或刷新 lease。"""
    partial = _normalized(partial_path)
    if not os.path.isfile(partial):
        raise core.PreflightError(f"schema 4 partial 不存在：{partial}")
    lease_path = lease_path_for_partial(partial)
    con = _readonly_connection(partial)
    try:
        runtime = dbstate.load_runtime(con)
        if os.path.normcase(runtime.partial_path) != os.path.normcase(partial):
            raise core.PreflightError(
                "partial 实际路径与数据库冻结身份不一致")
        session_ended, config, tools = _session_payload(
            con, runtime.active_session_id)
        roots = tuple(
            (str(label), str(path))
            for label, path in con.execute(
                "SELECT root_label,root_path FROM roots ORDER BY root_id")
        )
    finally:
        con.close()

    if not os.path.exists(lease_path):
        lease_classification = "missing"
    else:
        try:
            record = dbstate.read_lease_file(lease_path)
        except (OSError, core.PreflightError):
            lease_classification = "invalid"
        else:
            lease_classification = dbstate.classify_lease(
                record,
                now_utc=now_utc or core.now_utc_iso(),
                local_host=local_host,
                pid_alive=pid_alive,
                process_token=process_token,
            )
    return ResumePreview(
        partial_path=partial,
        lease_path=lease_path,
        run_state=runtime.run_state,
        resume_hint=runtime.resume_hint,
        current_stage=runtime.current_stage,
        active_session_id=runtime.active_session_id,
        active_session_ended=session_ended,
        lease_classification=lease_classification,
        roots=roots,
        config=config,
        tools=tools,
    )


def _reserve_partial(path: str) -> None:
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(descriptor)
    except FileExistsError as exc:
        raise core.PreflightError(
            f"partial 已存在且不会覆盖：{path}") from exc


def _remove_owned_sqlite_files(partial_path: str) -> None:
    for path in (partial_path, partial_path + "-wal", partial_path + "-shm"):
        try:
            os.remove(path)
        except OSError:
            pass


def create_run(
    partial_path: str,
    roots: list[tuple[str, str]],
    config: dict[str, object],
    *,
    output_dir: str,
    publish_stem_path: str,
    event_log_path: str | None = None,
    tool_versions: dict[str, object] | None = None,
    scanner_version: str = dbstate.MIN_READER_VERSION,
    snapshot_uuid: str | None = None,
    session_id: str | None = None,
    lease_id: str | None = None,
) -> RunHandle:
    """以 no-clobber 方式创建 schema 4 partial 和同身份 lease。"""
    if not roots:
        raise core.PreflightError("schema 4 至少需要一个 root")
    for _label, root_path in roots:
        core.validate_root(root_path)
    partial = _normalized(partial_path)
    lease_path = lease_path_for_partial(partial)
    active_session = session_id or uuid.uuid4().hex
    active_lease = lease_id or uuid.uuid4().hex
    _reserve_partial(partial)
    lease = None
    con = None
    try:
        lease = dbstate.acquire_lease_file(
            lease_path,
            active_session,
            lease_id=active_lease,
        )
        con = sqlite3.connect(partial)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        runtime = dbstate.initialize_v4_connection(
            con,
            roots,
            config,
            output_dir=output_dir,
            partial_path=partial,
            publish_stem_path=publish_stem_path,
            event_log_path=event_log_path,
            tool_versions=tool_versions,
            scanner_version=scanner_version,
            snapshot_uuid=snapshot_uuid,
            session_id=active_session,
            lease_id=active_lease,
            hostname=lease.host,
            pid=lease.pid,
            process_start_token=lease.process_start_token,
        )
        for root_id, (_label, root_path) in enumerate(roots, 1):
            volume_serial, filesystem = core.volume_info(root_path)
            con.execute(
                "UPDATE roots SET volume_serial=?,filesystem=?"
                " WHERE root_id=?",
                (volume_serial, filesystem, root_id),
            )
        con.commit()
        if runtime.active_session_id != lease.session_id:
            raise core.PreflightError(
                "数据库 session 与 lease session 不一致")
        return RunHandle(con, partial, lease_path, lease, False)
    except Exception:
        if con is not None:
            con.close()
        if lease is not None:
            try:
                dbstate.release_lease_file(lease_path, lease.lease_id)
            except (OSError, core.PreflightError):
                pass
        _remove_owned_sqlite_files(partial)
        raise


def resume_run(
    partial_path: str,
    *,
    manual: bool = False,
    scanner_version: str = dbstate.MIN_READER_VERSION,
    session_id: str | None = None,
    lease_id: str | None = None,
    pid_alive: Callable[[int], bool] | None = None,
    process_token: Callable[[int], str | None] | None = None,
) -> RunHandle:
    """明确接管可恢复 partial，并创建新的 resume session。"""
    preview = inspect_resume(
        partial_path,
        pid_alive=pid_alive,
        process_token=process_token,
    )
    if preview.run_state in ("published", "failed_terminal"):
        raise core.PreflightError(
            f"状态 {preview.run_state} 不能恢复")
    if preview.run_state == "stopped" and not manual:
        raise core.PreflightError("stopped partial 需要用户明确手动恢复")
    next_session = session_id or uuid.uuid4().hex
    next_lease = lease_id or uuid.uuid4().hex
    lease = dbstate.acquire_lease_file(
        preview.lease_path,
        next_session,
        takeover=True,
        lease_id=next_lease,
        pid_alive=pid_alive,
        token_probe=process_token,
    )
    con = None
    try:
        con = _readwrite_connection(preview.partial_path)
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        runtime = dbstate.load_runtime(con)
        if runtime.active_session_id != preview.active_session_id:
            raise core.PreflightError(
                "partial 在恢复预览后更换了 active session，拒绝接管")
        if runtime.run_state in ("published", "failed_terminal"):
            raise core.PreflightError(
                f"状态 {runtime.run_state} 不能恢复")
        if runtime.run_state == "stopped" and not manual:
            raise core.PreflightError(
                "stopped partial 需要用户明确手动恢复")
        session_ended, config, tools = _session_payload(
            con, runtime.active_session_id)
        dbstate.validate_resume_identity(
            con,
            runtime.output_dir,
            runtime.partial_path,
            runtime.publish_stem_path,
            runtime.event_log_path,
        )
        interrupted = runtime.run_state in (
            "running", "pause_requested", "sealing", "sealed_unpublished",
        ) or (runtime.run_state == "paused"
              and not session_ended)
        if interrupted:
            dbstate.recover_interrupted(con)
        runtime = dbstate.start_resume_session(
            con,
            config=config,
            tools=tools,
            manual=manual,
            scanner_version=scanner_version,
            session_id=next_session,
            lease_id=next_lease,
            hostname=lease.host,
            pid=lease.pid,
            process_start_token=lease.process_start_token,
        )
        if runtime.active_session_id != lease.session_id:
            raise core.PreflightError(
                "恢复 session 与 lease session 不一致")
        return RunHandle(
            con,
            preview.partial_path,
            preview.lease_path,
            lease,
            True,
        )
    except Exception:
        if con is not None:
            con.close()
        try:
            dbstate.release_lease_file(
                preview.lease_path, lease.lease_id)
        except (OSError, core.PreflightError):
            pass
        raise


def heartbeat_once(
    partial_path: str,
    lease_path: str,
    lease_id: str,
    *,
    now_utc: str | None = None,
) -> dbstate.LeaseRecord:
    """刷新精确 lease 文件和对应数据库 session。"""
    con = _readwrite_connection(_normalized(partial_path))
    try:
        con.execute("PRAGMA foreign_keys=ON")
        refreshed = dbstate.refresh_lease_file(
            lease_path, lease_id, now_utc=now_utc)
        dbstate.heartbeat_session(con, lease_id, now_utc=now_utc)
    finally:
        con.close()
    return refreshed


class LeaseHeartbeat:
    """仅刷新一个已知 partial/lease；失败后停止并暴露原异常。"""

    def __init__(
        self,
        handle: RunHandle,
        *,
        interval_seconds: float = dbstate.LEASE_HEARTBEAT_SECONDS,
        on_error: Callable[[BaseException], None] | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("lease heartbeat 间隔必须大于 0")
        self._partial_path = handle.partial_path
        self._lease_path = handle.lease_path
        self._lease_id = handle.lease.lease_id
        self._interval = float(interval_seconds)
        self._on_error = on_error
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: BaseException | None = None

    @property
    def error(self) -> BaseException | None:
        return self._error

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("lease heartbeat 已启动")
        heartbeat_once(
            self._partial_path, self._lease_path, self._lease_id)
        self._thread = threading.Thread(
            target=self._run,
            name="DAISY-LeaseHeartbeat",
            daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                heartbeat_once(
                    self._partial_path,
                    self._lease_path,
                    self._lease_id,
                )
            except Exception as exc:
                self._error = exc
                self._stop.set()
                if self._on_error is not None:
                    try:
                        self._on_error(exc)
                    except Exception:
                        pass

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)


def close_handle(handle: RunHandle, *, release_lease: bool) -> None:
    """关闭连接；仅在调用方明确声明时释放匹配的 lease。"""
    handle.connection.close()
    if release_lease:
        dbstate.release_lease_file(
            handle.lease_path, handle.lease.lease_id)

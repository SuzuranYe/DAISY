"""DAISY schema 4 运行文件、控制协议、阶段停点与占用锁生命周期。

这里封装未完成快照的独占创建和明确续传，以及扫描阶段的控制边界。所有占用锁
和工作进程操作只使用调用方给出的精确路径、lease ID 与进程句柄，不枚举或
终止其他进程。
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import queue
import sqlite3
import threading
import time
from typing import Callable, Mapping
import uuid

import Script_DAISY_Lib_Snapshot_Core as core
import Script_DAISY_Lib_Metadata as dbmeta
import Script_DAISY_Lib_File_Hash as dbhash
import Script_DAISY_Lib_Snapshot_Verify as dbverify
import Script_DAISY_Lib_Scan_State as dbstate
import Script_DAISY_Lib_Snapshot_Issues as dbissues
import Script_DAISY_Lib_Tool_Runtime as toolruntime


LEASE_SUFFIX = ".lease"
CONTROL_PROTOCOL = "daisy-control-v1"
CONTROL_MAX_LINE_BYTES = 4096
CONTROL_PIPE_POLL_SECONDS = 0.05
CONTROL_ACTIONS = frozenset((
    "pause", "continue", "save_exit", "stop", "timeout_decision",
))


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


@dataclass(frozen=True)
class ControlCommand:
    sequence: int
    action: str
    worker_pid: int | None = None
    decision: str | None = None
    request_id: str | None = None

    def as_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "protocol": CONTROL_PROTOCOL,
            "sequence": self.sequence,
            "action": self.action,
        }
        if self.worker_pid is not None:
            payload["worker_pid"] = self.worker_pid
        if self.decision is not None:
            payload["decision"] = self.decision
        if self.request_id is not None:
            payload["request_id"] = self.request_id
        return payload


@dataclass(frozen=True)
class ControlRejection:
    code: str
    detail: str


@dataclass(frozen=True)
class ControlReceipt:
    sequence: int
    action: str
    accepted: bool
    reason: str


def _normalized(path: str) -> str:
    return os.path.normpath(os.path.abspath(os.fspath(path)))


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} 必须是正整数")
    return value


def decode_control_line(line: str | bytes) -> ControlCommand:
    """严格解析一行 GUI→任务控制消息，不接受未知协议或多行内容。"""
    if isinstance(line, bytes):
        if len(line) > CONTROL_MAX_LINE_BYTES:
            raise ValueError("控制消息超过长度上限")
        try:
            text = line.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError("控制消息不是 UTF-8") from exc
    else:
        text = str(line)
        if len(text.encode("utf-8")) > CONTROL_MAX_LINE_BYTES:
            raise ValueError("控制消息超过长度上限")
    if text.endswith("\r\n"):
        text = text[:-2]
    elif text.endswith("\n"):
        text = text[:-1]
    if not text or "\n" in text or "\r" in text or "\x00" in text:
        raise ValueError("控制消息必须是单行非空 JSON")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("控制消息不是有效 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("控制消息必须是 JSON 对象")
    if payload.get("protocol") != CONTROL_PROTOCOL:
        raise ValueError("控制协议不兼容")
    sequence = _positive_int(payload.get("sequence"), "sequence")
    action = payload.get("action")
    if action not in CONTROL_ACTIONS:
        raise ValueError(f"未知控制动作：{action}")
    request_id = payload.get("request_id")
    if request_id is not None:
        if not isinstance(request_id, str) or not (1 <= len(request_id) <= 128):
            raise ValueError("request_id 必须是 1～128 字符字符串")

    worker_pid = payload.get("worker_pid")
    decision = payload.get("decision")
    if action == "timeout_decision":
        worker_pid = _positive_int(worker_pid, "worker_pid")
        if decision not in (
            "continue_waiting", "skip_and_record", "stop_and_resume",
        ):
            raise ValueError(f"未知超时处置：{decision}")
    elif worker_pid is not None or decision is not None:
        raise ValueError("只有 timeout_decision 可携带 worker_pid/decision")
    return ControlCommand(
        sequence=sequence,
        action=str(action),
        worker_pid=worker_pid,
        decision=decision,
        request_id=request_id,
    )


def encode_control_command(command: ControlCommand) -> bytes:
    """编码为单行 UTF-8 JSONL，并复用严格解析器自校验。"""
    encoded = (
        json.dumps(
            command.as_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n"
    ).encode("utf-8")
    decode_control_line(encoded)
    return encoded


class ControlInbox:
    """后台读取一个已知输入流；不关闭、不替换调用方的 stdin。

    Windows GUI 使用匿名管道传入控制消息。真实管道必须绕过
    ``BufferedReader.readline`` 做短间隔非阻塞轮询，否则读取线程会永久
    持有 ``sys.stdin.buffer`` 的内部锁，并与 ``multiprocessing`` 的 spawn
    子进程关闭标准输入及解释器退出流程互锁。
    """

    def __init__(
        self,
        stream,
        *,
        max_queue: int = 64,
        on_command: Callable[[ControlCommand], None] | None = None,
        on_rejected: Callable[[ControlRejection], None] | None = None,
    ) -> None:
        if max_queue <= 0:
            raise ValueError("控制队列上限必须大于 0")
        self._stream = stream
        self._queue: queue.Queue[ControlCommand] = queue.Queue(max_queue)
        self._on_command = on_command
        self._on_rejected = on_rejected
        self._last_sequence = 0
        self._stop = threading.Event()
        self._eof = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def eof(self) -> bool:
        return self._eof.is_set()

    @property
    def alive(self) -> bool:
        return bool(self._thread is not None and self._thread.is_alive())

    def _reject(self, code: str, detail: str) -> None:
        if self._on_rejected is not None:
            try:
                self._on_rejected(ControlRejection(code, detail))
            except Exception:
                pass

    @staticmethod
    def _has_line_end(value: str | bytes) -> bool:
        return value.endswith(b"\n") if isinstance(value, bytes) \
            else value.endswith("\n")

    def _discard_line_tail(self) -> None:
        while not self._stop.is_set():
            tail = self._stream.readline(CONTROL_MAX_LINE_BYTES + 1)
            if not tail or self._has_line_end(tail):
                return

    def _accept_line(self, line: str | bytes) -> None:
        try:
            command = decode_control_line(line)
        except ValueError as exc:
            self._reject("invalid_message", str(exc))
            return
        if command.sequence <= self._last_sequence:
            self._reject(
                "stale_sequence",
                f"sequence={command.sequence} 不大于 {self._last_sequence}",
            )
            return
        self._last_sequence = command.sequence
        if self._on_command is not None:
            try:
                self._on_command(command)
            except Exception as exc:
                self._reject("command_callback_failed", str(exc))
            return
        try:
            self._queue.put_nowait(command)
        except queue.Full:
            self._reject("queue_full", "控制队列已满")

    def _run_blocking_stream(self) -> None:
        """仅用于 BytesIO 等不会永久阻塞且没有真实 fd 的测试流。"""
        while not self._stop.is_set():
            line = self._stream.readline(CONTROL_MAX_LINE_BYTES + 1)
            if not line:
                return
            if self._stop.is_set():
                return
            byte_length = (
                len(line) if isinstance(line, bytes)
                else len(str(line).encode("utf-8"))
            )
            if byte_length > CONTROL_MAX_LINE_BYTES:
                if not self._has_line_end(line):
                    self._discard_line_tail()
                self._reject("line_too_long", "控制消息超过长度上限")
                continue
            if not self._has_line_end(line):
                self._reject(
                    "unterminated_line", "控制消息缺少 JSONL 换行边界")
                return
            self._accept_line(line)

    def _run_windows_pipe(self, fd: int) -> bool:
        """轮询 Windows 匿名管道；不是管道时返回 False 交给其它读取器。"""
        if os.name != "nt":
            return False
        try:
            import _winapi
            import msvcrt

            handle = msvcrt.get_osfhandle(fd)
            file_type = _winapi.GetFileType(handle)
        except (ImportError, OSError, ValueError):
            return False
        if file_type != _winapi.FILE_TYPE_PIPE:
            self._reject(
                "unsupported_stream",
                "Windows 控制输入必须使用管道",
            )
            return True

        pending = bytearray()
        discarding = False
        while not self._stop.is_set():
            try:
                available, _left_in_message = _winapi.PeekNamedPipe(handle)
            except BrokenPipeError:
                if pending:
                    self._reject(
                        "unterminated_line", "控制消息缺少 JSONL 换行边界")
                return True
            if available <= 0:
                self._stop.wait(CONTROL_PIPE_POLL_SECONDS)
                continue
            chunk = os.read(
                fd, min(int(available), CONTROL_MAX_LINE_BYTES + 1))
            if not chunk:
                if pending:
                    self._reject(
                        "unterminated_line", "控制消息缺少 JSONL 换行边界")
                return True
            for value in chunk:
                if discarding:
                    if value == 0x0A:
                        discarding = False
                    continue
                pending.append(value)
                if len(pending) > CONTROL_MAX_LINE_BYTES:
                    self._reject("line_too_long", "控制消息超过长度上限")
                    pending.clear()
                    discarding = value != 0x0A
                    continue
                if value == 0x0A:
                    self._accept_line(bytes(pending))
                    pending.clear()
        return True

    def _run_posix_fd(self, fd: int) -> bool:
        """用 select 轮询 POSIX fd，避免后台线程永久占用输入缓冲锁。"""
        if os.name == "nt":
            return False
        try:
            import select
        except ImportError:
            return False

        pending = bytearray()
        discarding = False
        while not self._stop.is_set():
            readable, _writable, _exceptional = select.select(
                [fd], [], [], CONTROL_PIPE_POLL_SECONDS)
            if not readable:
                continue
            chunk = os.read(fd, CONTROL_MAX_LINE_BYTES + 1)
            if not chunk:
                if pending:
                    self._reject(
                        "unterminated_line", "控制消息缺少 JSONL 换行边界")
                return True
            for value in chunk:
                if discarding:
                    if value == 0x0A:
                        discarding = False
                    continue
                pending.append(value)
                if len(pending) > CONTROL_MAX_LINE_BYTES:
                    self._reject("line_too_long", "控制消息超过长度上限")
                    pending.clear()
                    discarding = value != 0x0A
                    continue
                if value == 0x0A:
                    self._accept_line(bytes(pending))
                    pending.clear()
        return True

    def _run(self) -> None:
        try:
            try:
                fd = int(self._stream.fileno())
            except (AttributeError, OSError, TypeError, ValueError):
                fd = -1
            if fd >= 0 and (
                    self._run_windows_pipe(fd) or self._run_posix_fd(fd)):
                return
            self._run_blocking_stream()
        except (OSError, ValueError) as exc:
            self._reject("stream_failed", str(exc))
        finally:
            self._eof.set()

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("控制输入线程已启动")
        self._thread = threading.Thread(
            target=self._run,
            name="DAISY-ControlInbox",
            daemon=True,
        )
        self._thread.start()

    def poll(self) -> tuple[ControlCommand, ...]:
        commands = []
        while True:
            try:
                commands.append(self._queue.get_nowait())
            except queue.Empty:
                return tuple(commands)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)


class RunCommandRouter:
    """把已验证命令路由到当前哈希工作进程或暂停等待点。"""

    def __init__(
        self,
        *,
        on_receipt: Callable[[ControlReceipt], None] | None = None,
    ) -> None:
        self._condition = threading.Condition()
        self._state = "running"
        self._hash_control = dbhash.HashWorkerControl()
        self._paused_action: str | None = None
        self._on_receipt = on_receipt

    @property
    def state(self) -> str:
        with self._condition:
            return self._state

    @property
    def hash_control(self) -> dbhash.HashWorkerControl:
        with self._condition:
            return self._hash_control

    def _emit_receipt(self, receipt: ControlReceipt) -> None:
        if self._on_receipt is not None:
            try:
                self._on_receipt(receipt)
            except Exception:
                pass

    def route(self, command: ControlCommand) -> ControlReceipt:
        with self._condition:
            state = self._state
            if state == "ended":
                accepted, reason = False, "run_ended"
            elif state == "paused":
                if command.action not in ("continue", "save_exit", "stop"):
                    accepted, reason = False, "not_valid_while_paused"
                elif self._paused_action is not None:
                    accepted, reason = False, "paused_action_already_decided"
                else:
                    self._paused_action = command.action
                    self._condition.notify_all()
                    accepted, reason = True, "accepted"
            elif command.action == "continue":
                accepted, reason = False, "not_paused"
            elif command.action == "pause":
                accepted = self._hash_control.request_pause()
                reason = "accepted" if accepted else "action_already_decided"
            elif command.action == "save_exit":
                accepted = self._hash_control.request_save_exit()
                reason = "accepted" if accepted else "action_already_decided"
            elif command.action == "stop":
                accepted = self._hash_control.request_stop()
                reason = "accepted" if accepted else "action_already_decided"
            elif command.action == "timeout_decision":
                assert command.worker_pid is not None
                assert command.decision is not None
                accepted = self._hash_control.request_timeout_decision(
                    command.worker_pid,
                    command.decision,
                )
                reason = (
                    "accepted" if accepted
                    else "worker_or_decision_mismatch"
                )
            else:
                accepted, reason = False, "unsupported_action"
        receipt = ControlReceipt(
            command.sequence, command.action, accepted, reason)
        self._emit_receipt(receipt)
        return receipt

    def enter_paused(self) -> None:
        with self._condition:
            if self._state != "running":
                raise RuntimeError(
                    f"状态 {self._state} 不能进入 paused 等待点")
            self._state = "paused"
            self._paused_action = None

    def wait_paused_action(self, timeout: float | None = None) -> str | None:
        with self._condition:
            if self._state != "paused":
                raise RuntimeError("当前不在 paused 等待点")
            self._condition.wait_for(
                lambda: self._paused_action is not None
                or self._state == "ended",
                timeout,
            )
            return self._paused_action

    def begin_running(self) -> dbhash.HashWorkerControl:
        with self._condition:
            if self._state != "paused":
                raise RuntimeError(
                    f"状态 {self._state} 不能开始新的运行段")
            self._state = "running"
            self._paused_action = None
            self._hash_control = dbhash.HashWorkerControl()
            return self._hash_control

    def end(self) -> None:
        with self._condition:
            self._state = "ended"
            self._condition.notify_all()


def lease_path_for_partial(partial_path: str) -> str:
    partial = _normalized(partial_path)
    if not partial.casefold().endswith(".partial.sqlite"):
        raise core.PreflightError(
            f"schema 4 未完成快照的路径后缀无效：{partial}")
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
            f"无法只读打开 schema 4 未完成快照：{path}：{exc}") from exc


def _readwrite_connection(path: str) -> sqlite3.Connection:
    try:
        uri = Path(path).resolve(strict=True).as_uri() + "?mode=rw"
        return sqlite3.connect(uri, uri=True)
    except (OSError, sqlite3.Error) as exc:
        raise core.PreflightError(
            f"无法读写打开 schema 4 未完成快照：{path}：{exc}") from exc


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
        raise core.PreflightError("未完成快照缺少活动扫描会话")
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
    """只读检查续传候选；不创建、接管或刷新占用锁。"""
    partial = _normalized(partial_path)
    if not os.path.isfile(partial):
        raise core.PreflightError(f"schema 4 未完成快照不存在：{partial}")
    lease_path = lease_path_for_partial(partial)
    con = _readonly_connection(partial)
    try:
        runtime = dbstate.load_runtime(con)
        if os.path.normcase(runtime.partial_path) != os.path.normcase(partial):
            raise core.PreflightError(
                "未完成快照的实际路径与数据库冻结身份不一致")
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
            f"未完成快照已存在且不会覆盖：{path}") from exc


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
    """以不覆盖方式创建 schema 4 未完成快照和同身份占用锁。"""
    if not roots:
        raise core.PreflightError("数据库结构版本 4 至少需要一个根目录")
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
                "数据库扫描会话与占用锁会话不一致")
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
    """明确接管可续传的未完成快照，并创建新的续传会话。"""
    preview = inspect_resume(
        partial_path,
        pid_alive=pid_alive,
        process_token=process_token,
    )
    if preview.run_state in ("published", "failed_terminal"):
        raise core.PreflightError(
            f"状态 {preview.run_state} 不能续传")
    if preview.run_state == "sealed_unpublished":
        raise core.PreflightError(
            "已封存但未发布的工作快照只能重试发布，不能重新扫描")
    if preview.run_state == "stopped" and not manual:
        raise core.PreflightError("已停止的未完成快照需要用户明确手动续传")
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
                "未完成快照在续传预览后更换了活动会话，拒绝接管")
        if runtime.run_state in ("published", "failed_terminal"):
            raise core.PreflightError(
                f"状态 {runtime.run_state} 不能续传")
        if runtime.run_state == "sealed_unpublished":
            raise core.PreflightError(
                "已封存但未发布的工作快照只能重试发布，不能重新扫描")
        if runtime.run_state == "stopped" and not manual:
            raise core.PreflightError(
                "已停止的未完成快照需要用户明确手动续传")
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
            "running", "pause_requested", "sealing",
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
                "续传会话与占用锁会话不一致")
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


def resume_publication_run(
    partial_path: str,
    *,
    scanner_version: str = dbstate.MIN_READER_VERSION,
    session_id: str | None = None,
    lease_id: str | None = None,
    pid_alive: Callable[[int], bool] | None = None,
    process_token: Callable[[int], str | None] | None = None,
) -> RunHandle:
    """接管已封存但未发布的工作快照，只创建发布重试会话。"""
    preview = inspect_resume(
        partial_path,
        pid_alive=pid_alive,
        process_token=process_token,
    )
    if preview.run_state != "sealed_unpublished":
        raise core.PreflightError(
            f"状态 {preview.run_state} 不是待发布的已封存工作快照")
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
        if runtime.run_state != "sealed_unpublished":
            raise core.PreflightError(
                f"发布重试时状态已变为 {runtime.run_state}")
        if runtime.active_session_id != preview.active_session_id:
            raise core.PreflightError(
                "未完成快照在发布预览后更换了活动会话，拒绝接管")
        _ended, config, tools = _session_payload(
            con, runtime.active_session_id)
        dbstate.validate_resume_identity(
            con,
            runtime.output_dir,
            runtime.partial_path,
            runtime.publish_stem_path,
            runtime.event_log_path,
        )
        runtime = dbstate.start_publication_retry_session(
            con,
            config=config,
            tools=tools,
            scanner_version=scanner_version,
            session_id=next_session,
            lease_id=next_lease,
            hostname=lease.host,
            pid=lease.pid,
            process_start_token=lease.process_start_token,
        )
        if runtime.active_session_id != lease.session_id:
            raise core.PreflightError(
                "发布重试会话与占用锁会话不一致")
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


def record_publication_retry_failure(
    handle: RunHandle,
    error_message: str,
) -> None:
    """连接可能已关闭时，仍只写回同一已封存工作快照的失败证据。"""
    opened = None
    try:
        try:
            dbstate.load_runtime(handle.connection)
            con = handle.connection
        except sqlite3.Error:
            con = _readwrite_connection(handle.partial_path)
            opened = con
        dbstate.fail_publication_retry(con, error_message)
    finally:
        if opened is not None:
            opened.close()


def heartbeat_once(
    partial_path: str,
    lease_path: str,
    lease_id: str,
    *,
    now_utc: str | None = None,
) -> dbstate.LeaseRecord:
    """刷新精确占用锁文件和对应数据库扫描会话。"""
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
    """仅刷新一个已知未完成快照及其占用锁；失败后暴露原异常。"""

    def __init__(
        self,
        handle: RunHandle,
        *,
        interval_seconds: float = dbstate.LEASE_HEARTBEAT_SECONDS,
        on_error: Callable[[BaseException], None] | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("占用锁心跳间隔必须大于 0")
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

    @property
    def alive(self) -> bool:
        return bool(self._thread is not None and self._thread.is_alive())

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("占用锁心跳已启动")
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

    def stop(self, timeout_seconds: float = 2.0) -> bool:
        if timeout_seconds <= 0:
            raise ValueError("占用锁心跳停止等待时间必须大于 0")
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout_seconds)
        return not self.alive


def close_handle(handle: RunHandle, *, release_lease: bool) -> None:
    """关闭连接；仅在调用方明确声明时释放匹配的占用锁。"""
    handle.connection.close()
    if release_lease:
        dbstate.release_lease_file(
            handle.lease_path, handle.lease.lease_id)


def _emit_control_event(
    on_event: Callable[..., None] | None,
    event: str,
    **payload: object,
) -> None:
    if on_event is not None:
        try:
            on_event(event, **payload)
        except Exception:
            pass


def _wait_after_pause(
    con: sqlite3.Connection,
    stage: str,
    router: RunCommandRouter,
    *,
    on_event: Callable[..., None] | None,
    paused_wait_seconds: float,
) -> str:
    if paused_wait_seconds <= 0:
        raise ValueError("paused 等待轮询间隔必须大于 0")
    runtime = dbstate.load_runtime(con)
    if runtime.run_state != "paused" or runtime.resume_hint != "none":
        raise core.PreflightError(
            "同会话暂停点状态不一致，拒绝继续控制循环")
    router.enter_paused()
    _emit_control_event(
        on_event, "run_paused", stage=stage, state="paused")

    action = None
    while action is None:
        action = router.wait_paused_action(paused_wait_seconds)
        if action is None and router.state == "ended":
            raise core.PreflightError("控制器在 paused 等待期间结束")

    if action == "continue":
        dbstate.continue_running(con)
        router.begin_running()
        dbstate.update_stage_checkpoint(
            con,
            stage,
            "running",
            current_entry_id=None,
            checkpoint={"reason": "continued_after_pause"},
        )
        _emit_control_event(
            on_event, "run_resumed", stage=stage, state="running")
        return "running"
    if action == "save_exit":
        dbstate.save_paused_for_exit(con)
        dbstate.update_stage_checkpoint(
            con,
            stage,
            "paused",
            current_entry_id=None,
            checkpoint={"reason": "save_exit_after_pause"},
        )
        router.end()
        _emit_control_event(
            on_event, "run_saved", stage=stage, state="save_exit")
        return "save_exit"
    if action == "stop":
        dbstate.update_stage_checkpoint(
            con,
            stage,
            "failed_recoverable",
            current_entry_id=None,
            checkpoint={"reason": "user_stop_after_pause"},
        )
        dbstate.stop_run(con, reason="user_stop")
        router.end()
        _emit_control_event(
            on_event, "run_stopped", stage=stage, state="stopped")
        return "stopped"
    raise core.PreflightError(f"paused 等待点收到未知动作：{action}")


def settle_pending_stage_control(
    con: sqlite3.Connection,
    stage: str,
    router: RunCommandRouter,
    *,
    on_event: Callable[..., None] | None = None,
    paused_wait_seconds: float = 0.25,
) -> str:
    """在非哈希阶段的安全边界提交一个待处理生命周期动作。"""
    if stage not in dbstate.STAGES:
        raise ValueError(f"未知阶段：{stage}")
    if router.state != "running":
        raise core.PreflightError(
            f"阶段控制要求 running 控制器，实际为 {router.state}")
    control_action = router.hash_control.current()
    if control_action is None:
        return "running"
    action, _source = control_action
    if action in ("pause", "save_exit"):
        for_exit = action == "save_exit"
        dbstate.request_pause(con, for_exit=for_exit)
        dbstate.update_stage_checkpoint(
            con,
            stage,
            "pause_requested",
            current_entry_id=None,
            checkpoint={"reason": "stage_control"},
        )
        dbstate.mark_paused(con, for_exit=for_exit)
        dbstate.update_stage_checkpoint(
            con,
            stage,
            "paused",
            current_entry_id=None,
            checkpoint={"reason": "stage_control"},
        )
        if for_exit:
            router.end()
            _emit_control_event(
                on_event, "run_saved", stage=stage, state="save_exit")
            return "save_exit"
        return _wait_after_pause(
            con,
            stage,
            router,
            on_event=on_event,
            paused_wait_seconds=paused_wait_seconds,
        )

    dbstate.update_stage_checkpoint(
        con,
        stage,
        "failed_recoverable",
        current_entry_id=None,
        checkpoint={"reason": "stage_stop"},
    )
    dbstate.stop_run(con, reason="user_stop")
    router.end()
    _emit_control_event(
        on_event, "run_stopped", stage=stage, state="stopped")
    return "stopped"


def _run_controlled_boundary_operation(
    con: sqlite3.Connection,
    stage: str,
    router: RunCommandRouter,
    operation: Callable[[Callable[[], bool]], object],
    *,
    on_event: Callable[..., None] | None,
    paused_wait_seconds: float,
) -> tuple[str, object | None]:
    """执行可重跑阶段；收到控制时先落库，再按用户后续动作处理。"""
    while True:
        dbstate.update_stage_checkpoint(
            con, stage, "running", current_entry_id=None)
        boundary = settle_pending_stage_control(
            con,
            stage,
            router,
            on_event=on_event,
            paused_wait_seconds=paused_wait_seconds,
        )
        if boundary != "running":
            return boundary, None
        _emit_control_event(
            on_event, "stage_started", stage=stage)
        try:
            value = operation(
                lambda: router.hash_control.current() is not None)
        except core.StageControlBoundary:
            if router.hash_control.current() is None:
                raise core.PreflightError(
                    f"阶段 {stage} 在没有控制动作时中断")
            boundary = settle_pending_stage_control(
                con,
                stage,
                router,
                on_event=on_event,
                paused_wait_seconds=paused_wait_seconds,
            )
            if boundary == "running":
                _emit_control_event(
                    on_event, "stage_restarted", stage=stage)
                continue
            return boundary, None
        return "completed", value


def run_enumeration_stage_controlled(
    con: sqlite3.Connection,
    router: RunCommandRouter,
    *,
    collect_file_id: bool = True,
    exclude_paths: set | None = None,
    exclude_dirs: set | None = None,
    on_progress: Callable[[dict[str, object]], None] | None = None,
    on_event: Callable[..., None] | None = None,
    paused_wait_seconds: float = 0.25,
    max_files: int | None = None,
) -> dict[str, object]:
    """运行可重跑枚举阶段；旧枚举器只在显式回调下增加停点。"""

    def operation(should_stop: Callable[[], bool]) -> object:
        def progress(stats: dict[str, object]) -> None:
            dbstate.update_stage_checkpoint(
                con,
                "enumerate",
                "running",
                items_done=int(stats["files"]),
                bytes_done=int(stats["bytes"]),
                error_count=int(stats["dir_errors"]),
            )
            if on_progress is not None:
                on_progress(dict(stats))

        return core.enumerate_and_reconcile(
            con,
            collect_file_id=collect_file_id,
            exclude_paths=exclude_paths,
            exclude_dirs=exclude_dirs,
            on_progress=progress,
            max_files=max_files,
            should_stop=should_stop,
        )

    state, value = _run_controlled_boundary_operation(
        con,
        "enumerate",
        router,
        operation,
        on_event=on_event,
        paused_wait_seconds=paused_wait_seconds,
    )
    if state != "completed":
        return {"state": state}
    stats = dict(value)
    stats["state"] = "completed"
    dbstate.update_stage_checkpoint(
        con,
        "enumerate",
        "completed",
        items_done=int(stats["files"]),
        items_total=int(stats["files"]),
        bytes_done=int(stats["bytes"]),
        bytes_total=int(stats["bytes"]),
        error_count=int(stats["dir_errors"]),
        current_entry_id=None,
    )
    _emit_control_event(
        on_event, "stage_finished", stage="enumerate", **stats)
    return stats


def run_metadata_stage_controlled(
    con: sqlite3.Connection,
    tools: dict[str, object],
    router: RunCommandRouter,
    *,
    retain_original_metadata: bool = True,
    metadata_exiftool: bool = True,
    metadata_ffprobe: bool = True,
    retain_exiftool_payload: bool | None = None,
    retain_ffprobe_payload: bool | None = None,
    timeout_policy: dict[str, object] | None = None,
    show_current_file: bool = False,
    on_progress: Callable[[int, dict[str, object]], None] | None = None,
    on_event: Callable[..., None] | None = None,
    paused_wait_seconds: float = 0.25,
    tool_circuit_threshold: int =
    toolruntime.DEFAULT_CIRCUIT_THRESHOLD,
) -> dict[str, object]:
    """运行元数据阶段，并只在单文件处理边界接受生命周期动作。"""
    total_entries = int(con.execute(
        "SELECT COUNT(*) FROM entries"
    ).fetchone()[0])
    last_current_event = 0.0
    last_progress_event = 0.0

    def operation(should_stop: Callable[[], bool]) -> object:
        already_done = int(con.execute(
            "SELECT COUNT(*) FROM entries"
            " WHERE meta_status NOT IN ('pending','processing')"
        ).fetchone()[0])

        def progress(index: int, stats: dict[str, object]) -> None:
            nonlocal last_progress_event
            overall_done = min(total_entries, already_done + int(index))
            now = time.monotonic()
            if now - last_progress_event < 0.5 \
                    and overall_done != total_entries:
                return
            dbstate.update_stage_checkpoint(
                con,
                "metadata",
                "running",
                items_done=overall_done,
                items_total=total_entries,
                error_count=(
                    int(stats["error"]) + int(stats["timeout"])),
            )
            if on_progress is not None:
                current = dict(stats)
                current["processed"] = overall_done
                current["total"] = total_entries
                on_progress(overall_done, current)
            last_progress_event = now

        def current_item(rel_path: str) -> None:
            nonlocal last_current_event
            now = time.monotonic()
            if now - last_current_event >= 0.1:
                _emit_control_event(
                    on_event,
                    "current_item",
                    stage="metadata",
                    item=rel_path,
                )
                last_current_event = now

        return dbmeta.process_metadata_stage(
            con,
            tools,
            retain_original_metadata=retain_original_metadata,
            metadata_exiftool=metadata_exiftool,
            metadata_ffprobe=metadata_ffprobe,
            retain_exiftool_payload=retain_exiftool_payload,
            retain_ffprobe_payload=retain_ffprobe_payload,
            timeout_policy=timeout_policy,
            on_progress=progress,
            should_stop=should_stop,
            on_current=current_item if show_current_file else None,
            tool_circuit_threshold=tool_circuit_threshold,
        )

    try:
        state, value = _run_controlled_boundary_operation(
            con,
            "metadata",
            router,
            operation,
            on_event=on_event,
            paused_wait_seconds=paused_wait_seconds,
        )
    except dbmeta.MetadataToolCircuitOpen as exc:
        summary = dict(exc.summary)
        status_counts = {
            str(status): int(count)
            for status, count in con.execute(
                "SELECT meta_status,COUNT(*) FROM entries"
                " GROUP BY meta_status")
        }
        pending = status_counts.get("pending", 0)
        processing = status_counts.get("processing", 0)
        processed = total_entries - pending - processing
        error_count = (
            status_counts.get("error", 0)
            + status_counts.get("timeout", 0)
        )
        dbstate.update_stage_checkpoint(
            con,
            "metadata",
            "failed_recoverable",
            items_done=processed,
            items_total=total_entries,
            error_count=error_count,
            current_entry_id=None,
            checkpoint=summary,
        )
        dbstate.fail_run(
            con,
            recoverable=True,
            error_code="metadata_tool_circuit_open",
            error_message=str(exc),
            payload=summary,
        )
        router.end()
        _emit_control_event(
            on_event,
            "tool_circuit_open",
            stage="metadata",
            **summary,
        )
        _emit_control_event(
            on_event,
            "stage_failed",
            stage="metadata",
            state="failed_recoverable",
            reason="metadata_tool_circuit_open",
            tool=str(summary.get("tool") or "external_tool"),
            processed=processed,
            total=total_entries,
            not_processed=pending + processing,
        )
        return {
            "state": "failed_recoverable",
            "total": total_entries,
            "processed": processed,
            "done": status_counts.get("done", 0),
            "error": status_counts.get("error", 0),
            "timeout": status_counts.get("timeout", 0),
            "unstable": status_counts.get("unstable", 0),
            "not_processed": pending + processing,
            "tool_failure": summary,
        }
    if state != "completed":
        return {"state": state}
    stats = dict(value)
    status_counts = {
        str(status): int(count)
        for status, count in con.execute(
            "SELECT meta_status,COUNT(*) FROM entries GROUP BY meta_status")
    }
    processed = total_entries \
        - status_counts.get("pending", 0) \
        - status_counts.get("processing", 0)
    stats.update({
        "total": total_entries,
        "processed": processed,
        "done": status_counts.get("done", 0),
        "error": status_counts.get("error", 0),
        "timeout": status_counts.get("timeout", 0),
        "unstable": status_counts.get("unstable", 0),
        "not_applicable": status_counts.get("not_applicable", 0),
        "skipped": status_counts.get("skipped", 0),
    })
    stats["state"] = "completed"
    dbstate.update_stage_checkpoint(
        con,
        "metadata",
        "completed",
        items_done=processed,
        items_total=total_entries,
        error_count=int(stats["error"]) + int(stats["timeout"]),
        current_entry_id=None,
        checkpoint={
            "source_error": int(stats.get("source_error") or 0),
            "tool_error": int(stats.get("tool_error") or 0),
            "not_processed": 0,
            "tool_runtime": dict(stats.get("tool_runtime") or {}),
        },
    )
    _emit_control_event(
        on_event, "stage_finished", stage="metadata", **stats)
    return stats


def _format_sample_value(value: object) -> float:
    if isinstance(value, bool):
        raise core.PreflightError("格式校验抽样比例不能是布尔值")
    try:
        percent = float(value)
    except (TypeError, ValueError) as exc:
        raise core.PreflightError(
            f"格式校验抽样比例无效：{value!r}") from exc
    if not math.isfinite(percent) or percent < 0.0 or percent > 100.0:
        raise core.PreflightError(
            f"格式校验抽样比例必须在 0～100：{value!r}")
    return percent


def _prepare_format_selection(
    con: sqlite3.Connection,
    mode: str,
    sample_percent: float,
) -> tuple[int, int, str]:
    candidates = con.execute(
        "SELECT entry_id,size_bytes,extension,media_kind FROM entries"
        " WHERE is_placeholder=0 ORDER BY root_id,path_key,rel_path"
    ).fetchall()
    if mode == "all":
        selected = candidates
        coverage = "full"
    else:
        coverage = "sample"
        if sample_percent == 0.0:
            selected = []
        else:
            snapshot_uuid = str(con.execute(
                "SELECT snapshot_uuid FROM snapshot_info WHERE id=1"
            ).fetchone()[0])
            selected_ids = {
                int(entry_id)
                for entry_id, _size in dbhash.pick_sample(
                    [(row[0], row[1]) for row in candidates],
                    sample_percent,
                    100,
                    seed=snapshot_uuid + ":scan-format",
                )
            }
            selected = [
                row for row in candidates if int(row[0]) in selected_ids]
    existing_coverages = {
        str(row[0]) for row in con.execute(
            "SELECT DISTINCT coverage FROM format_checks")
    }
    if existing_coverages and existing_coverages != {coverage}:
        raise core.PreflightError(
            "format_checks 覆盖类型与冻结配置不一致")
    with con:
        con.executemany(
            "INSERT INTO format_checks"
            " (entry_id,attempt_id,status,coverage,validator,tool_name,"
            " tool_version,stat_match,detail,checked_at_utc,result_revision)"
            " VALUES (?,NULL,'pending',?,?,NULL,NULL,NULL,NULL,NULL,1)"
            " ON CONFLICT(entry_id) DO NOTHING",
            [
                (
                    int(entry_id),
                    coverage,
                    dbverify.pick_format_validator(
                        str(extension), str(media_kind)),
                )
                for entry_id, _size, extension, media_kind in selected
            ],
        )
    return len(candidates), len(selected), coverage


def run_format_stage_controlled(
    con: sqlite3.Connection,
    mode: str,
    tools: dict[str, object],
    router: RunCommandRouter,
    *,
    sample_percent: float = 10.0,
    show_current_file: bool = False,
    on_progress: Callable[[int, dict[str, object]], None] | None = None,
    on_event: Callable[..., None] | None = None,
    paused_wait_seconds: float = 0.25,
    defer_completion: bool = False,
    _session_factory=None,
) -> dict[str, object]:
    """运行完整扫描的可选格式校验；不支持的类型只统计，不写为错误。"""
    if mode not in ("off", "sample", "all"):
        raise core.PreflightError(f"格式校验模式无效：{mode!r}")
    if defer_completion and mode == "off":
        raise core.PreflightError("格式校验关闭时不能运行从属阶段")
    if router.state != "running":
        raise core.PreflightError(
            f"格式校验要求 running 控制器，实际为 {router.state}")
    percent = _format_sample_value(sample_percent)
    if router.hash_control.current() is not None:
        boundary = settle_pending_stage_control(
            con,
            "format",
            router,
            on_event=on_event,
            paused_wait_seconds=paused_wait_seconds,
        )
        if boundary != "running":
            return {"state": boundary}
    if mode == "off":
        existing = int(con.execute(
            "SELECT COUNT(*) FROM format_checks").fetchone()[0])
        if existing:
            raise core.PreflightError(
                "格式校验已关闭，但未完成快照中存在格式结果")
        dbstate.update_stage_checkpoint(
            con,
            "format",
            "skipped",
            items_done=0,
            items_total=0,
            current_entry_id=None,
            checkpoint={"reason": "format_validation_off"},
        )
        stats = {
            "state": "completed",
            "eligible": 0,
            "selected": 0,
            "processed": 0,
            "valid": 0,
            "invalid": 0,
            "unsupported": 0,
            "timeout": 0,
            "error": 0,
            "unstable": 0,
        }
        _emit_control_event(
            on_event,
            "stage_skipped",
            stage="format",
            reason="format_validation_off",
        )
        return stats

    last_current_event = 0.0
    last_progress_event = 0.0
    factory = _session_factory or dbverify.FormatValidationSession
    circuit = toolruntime.ConsecutiveToolFailureCircuit()

    def record_format_tool_success(
        spec: dbverify.FormatValidatorSpec,
        media_kind: str,
    ) -> None:
        if spec.validator in ("ole", "7z"):
            circuit.record_success("sevenzip")
        elif spec.validator not in ("zip", "pdf", "none"):
            circuit.record_success("exiftool")
            effective_kind = (
                "image_gif" if spec.validator == "gif" else media_kind)
            if effective_kind in dbverify._FFPROBE_KINDS:
                circuit.record_success("ffprobe")

    def operation(should_stop: Callable[[], bool]) -> object:
        nonlocal last_current_event, last_progress_event
        eligible, selected_now, coverage = _prepare_format_selection(
            con, mode, percent)
        rows = con.execute(
            "SELECT f.entry_id,e.root_id,e.rel_path,e.extension,e.media_kind,"
            " e.size_bytes,e.modified_at_utc,f.status"
            " FROM format_checks f JOIN entries e ON e.entry_id=f.entry_id"
            " WHERE e.is_placeholder=0 AND f.coverage=?"
            " ORDER BY e.root_id,e.path_key,e.rel_path",
            (coverage,),
        ).fetchall()
        counts = {
            "valid": 0,
            "invalid": 0,
            "unsupported": 0,
            "timeout": 0,
            "error": 0,
            "unstable": 0,
        }
        for row in rows:
            status = str(row[7])
            if status in counts:
                counts[status] += 1
        processed = sum(counts.values())
        stats: dict[str, object] = {
            "state": "running",
            "eligible": eligible,
            "selected": len(rows),
            "selected_now": selected_now,
            "processed": processed,
            **counts,
        }
        dbstate.update_stage_checkpoint(
            con,
            "format",
            "running",
            items_done=processed,
            items_total=len(rows),
            error_count=(
                counts["invalid"] + counts["timeout"]
                + counts["error"] + counts["unstable"]),
            current_entry_id=None,
            checkpoint={
                "mode": mode,
                "sample_percent": percent,
                "eligible": eligible,
                "selected": len(rows),
            },
        )
        if should_stop():
            raise core.StageControlBoundary(
                "format controlled stage boundary")
        if not rows:
            stats["state"] = "completed"
            return stats
        roots = dict(con.execute(
            "SELECT root_id,root_path FROM roots"))
        attempt_status = {
            "valid": "succeeded",
            "invalid": "invalid",
            "unsupported": "unsupported",
            "timeout": "timeout",
            "error": "error",
            "unstable": "unstable",
        }
        session = factory(tools)
        try:
            for (entry_id, root_id, rel_path, extension, media_kind,
                 size_bytes, modified_at_utc, current_status) in rows:
                if str(current_status) not in ("pending", "processing"):
                    continue
                if should_stop():
                    raise core.StageControlBoundary(
                        "format controlled stage boundary")
                now = time.monotonic()
                if show_current_file and on_event is not None \
                        and now - last_current_event >= 0.1:
                    _emit_control_event(
                        on_event,
                        "current_item",
                        stage="format",
                        item=str(rel_path),
                    )
                    last_current_event = now
                spec = session.describe(
                    str(extension), str(media_kind))
                attempt_id = dbstate.start_attempt(
                    con,
                    int(entry_id),
                    "format",
                    tool_name=spec.tool_name,
                    tool_version=spec.tool_version,
                    coverage=coverage,
                    validator=spec.validator,
                )
                path = os.path.join(roots[int(root_id)], str(rel_path))
                extended_path = core.to_extended_path(path)
                status = "error"
                detail = None
                stat_match = False
                tool_failure = None
                try:
                    before = os.stat(
                        extended_path, follow_symlinks=False)
                except OSError as exc:
                    status = "unstable"
                    detail = f"校验前文件不可读取：{exc}"
                else:
                    before_match = (
                        int(before.st_size) == int(size_bytes)
                        and core.ns_to_utc_iso(before.st_mtime_ns)
                        == str(modified_at_utc)
                    )
                    if not before_match:
                        status = "unstable"
                        detail = "校验前 size/mtime 已改变"
                    else:
                        status, detail = session.validate(
                            path, str(media_kind), spec)
                        tool_failure = getattr(
                            session, "last_tool_failure", None)
                        try:
                            after = os.stat(
                                extended_path, follow_symlinks=False)
                        except OSError as exc:
                            status = "unstable"
                            detail = f"校验后文件不可读取：{exc}"
                        else:
                            stat_match = (
                                int(after.st_size) == int(size_bytes)
                                and core.ns_to_utc_iso(after.st_mtime_ns)
                                == str(modified_at_utc)
                            )
                            if not stat_match:
                                status = "unstable"
                                detail = "校验期间 size/mtime 已改变"
                                tool_failure = None
                detail = None if detail is None else str(detail)[:2000]
                finish_status = attempt_status.get(status, "error")
                error_code = None
                if finish_status in (
                        "invalid", "timeout", "error", "unstable"):
                    error_code = (
                        "format_tool_error"
                        if isinstance(
                            tool_failure, toolruntime.ToolRuntimeFailure)
                        else f"format_{finish_status}"
                    )
                dbstate.finish_attempt(
                    con,
                    attempt_id,
                    finish_status,
                    end_reason=f"format_{finish_status}",
                    error_code=error_code,
                    error_message=detail if error_code else None,
                    result={
                        "validator": spec.validator,
                        "status": status,
                        **({
                            "tool_failure": tool_failure.as_dict(),
                        } if isinstance(
                            tool_failure, toolruntime.ToolRuntimeFailure)
                           else {}),
                    },
                    stat_match=stat_match,
                    detail=detail,
                )
                final_status = (
                    status if status in counts else "error")
                counts[final_status] += 1
                processed += 1
                if isinstance(
                        tool_failure, toolruntime.ToolRuntimeFailure):
                    opened = circuit.record_failure(
                        int(entry_id), tool_failure)
                    if opened.opened:
                        affected_ids = list(opened.entry_ids)
                        with con:
                            con.executemany(
                                "UPDATE format_checks SET status='pending',"
                                " attempt_id=NULL,tool_name=NULL,"
                                " tool_version=NULL,stat_match=NULL,detail=NULL,"
                                " checked_at_utc=NULL WHERE entry_id=?",
                                [(current_id,) for current_id in affected_ids],
                            )
                        counts["error"] = max(
                            0, counts["error"] - len(affected_ids))
                        processed = max(0, processed - len(affected_ids))
                        not_processed = max(0, len(rows) - processed)
                        payload = {
                            "reason": "format_tool_circuit_open",
                            "stage": "format",
                            "tool_circuit": opened.as_dict(),
                            "processed": processed,
                            "total": len(rows),
                            "not_processed": not_processed,
                            "tool_failure": tool_failure.as_dict(),
                        }
                        dbstate.update_stage_checkpoint(
                            con,
                            "format",
                            "failed_recoverable",
                            items_done=processed,
                            items_total=len(rows),
                            error_count=(
                                counts["invalid"] + counts["timeout"]
                                + counts["error"] + counts["unstable"]),
                            current_entry_id=None,
                            checkpoint=payload,
                        )
                        dbstate.fail_run(
                            con,
                            recoverable=True,
                            error_code="format_tool_circuit_open",
                            error_message=tool_failure.latest.message,
                            payload=payload,
                        )
                        stats.update({
                            "state": "failed_recoverable",
                            "processed": processed,
                            "not_processed": not_processed,
                            "tool_circuit": opened.as_dict(),
                            **counts,
                        })
                        _emit_control_event(
                            on_event,
                            "tool_circuit_open",
                            stage="format",
                            tool=tool_failure.latest.tool,
                            failure_kind=(
                                tool_failure.latest.failure_kind),
                            processed=processed,
                            total=len(rows),
                            not_processed=not_processed,
                            affected=len(affected_ids),
                        )
                        _emit_control_event(
                            on_event,
                            "stage_failed",
                            stage="format",
                            tool=tool_failure.latest.tool,
                            processed=processed,
                            total=len(rows),
                            not_processed=not_processed,
                        )
                        return stats
                else:
                    record_format_tool_success(
                        spec, str(media_kind))
                stats.update({"processed": processed, **counts})
                failures = (
                    counts["invalid"] + counts["timeout"]
                    + counts["error"] + counts["unstable"])
                dbstate.update_stage_checkpoint(
                    con,
                    "format",
                    "running",
                    items_done=processed,
                    items_total=len(rows),
                    error_count=failures,
                    current_entry_id=None,
                    checkpoint={
                        "mode": mode,
                        "sample_percent": percent,
                        "eligible": eligible,
                        "selected": len(rows),
                    },
                )
                _emit_control_event(
                    on_event,
                    "format_item_finished",
                    entry_id=int(entry_id),
                    validator=spec.validator,
                    status=final_status,
                )
                now = time.monotonic()
                if on_progress is not None and (
                        now - last_progress_event >= 0.5
                        or processed == len(rows)):
                    on_progress(processed, dict(stats))
                    last_progress_event = now
                if should_stop():
                    raise core.StageControlBoundary(
                        "format controlled stage boundary")
        finally:
            session.close()
        stats["state"] = "completed"
        return stats

    state, value = _run_controlled_boundary_operation(
        con,
        "format",
        router,
        operation,
        on_event=on_event,
        paused_wait_seconds=paused_wait_seconds,
    )
    if state != "completed":
        return {"state": state}
    stats = dict(value)
    if stats.get("state") == "failed_recoverable":
        router.end()
        return stats
    failures = (
        int(stats["invalid"]) + int(stats["timeout"])
        + int(stats["error"]) + int(stats["unstable"])
    )
    dbstate.update_stage_checkpoint(
        con,
        "format",
        "running" if defer_completion else "completed",
        items_done=int(stats["processed"]),
        items_total=int(stats["selected"]),
        error_count=failures,
        current_entry_id=None,
        checkpoint={
            "mode": mode,
            "sample_percent": percent,
            "eligible": int(stats["eligible"]),
            "selected": int(stats["selected"]),
            "unsupported": int(stats["unsupported"]),
            **({
                "primary_completed": True,
                "substage_pending": True,
            } if defer_completion else {}),
        },
    )
    _emit_control_event(
        on_event,
        "format_primary_finished" if defer_completion else "stage_finished",
        stage="format",
        **stats,
    )
    return stats


def run_rescan_stage_controlled(
    con: sqlite3.Connection,
    router: RunCommandRouter,
    *,
    on_progress: Callable[[int, int, int], None] | None = None,
    on_event: Callable[..., None] | None = None,
    paused_wait_seconds: float = 0.25,
) -> dict[str, object]:
    """运行可重跑复扫阶段，并在条目边界提交暂停／停止。"""
    total = int(con.execute(
        "SELECT COUNT(*) FROM entries WHERE is_placeholder=0"
    ).fetchone()[0])
    last_progress_event = 0.0

    def operation(should_stop: Callable[[], bool]) -> object:
        def progress(done: int, current_total: int, changed: int) -> None:
            nonlocal last_progress_event
            now = time.monotonic()
            if now - last_progress_event < 0.5 and done != current_total:
                return
            dbstate.update_stage_checkpoint(
                con,
                "rescan",
                "running",
                items_done=done,
                items_total=current_total,
                error_count=changed,
            )
            if on_progress is not None:
                on_progress(done, current_total, changed)
            last_progress_event = now

        return core.rescan_check(
            con, should_stop=should_stop, on_progress=progress)

    state, value = _run_controlled_boundary_operation(
        con,
        "rescan",
        router,
        operation,
        on_event=on_event,
        paused_wait_seconds=paused_wait_seconds,
    )
    if state != "completed":
        return {"state": state}
    changed = int(value)
    stats: dict[str, object] = {
        "state": "completed",
        "total": total,
        "changed": changed,
    }
    dbstate.update_stage_checkpoint(
        con,
        "rescan",
        "completed",
        items_done=total,
        items_total=total,
        error_count=changed,
        current_entry_id=None,
    )
    _emit_control_event(
        on_event, "stage_finished", stage="rescan", **stats)
    return stats


def run_hash_stage_controlled(
    con: sqlite3.Connection,
    mode: str,
    router: RunCommandRouter,
    *,
    previous: dbhash.PreviousSnapshot | None = None,
    retry_mode: str = "pending",
    chunk_bytes: int = core.HASH_CHUNK_BYTES,
    stall_seconds: float = dbhash.HASH_STALL_SECONDS,
    timeout_seconds: float | None = None,
    default_decision: str = "continue_waiting",
    show_current_file: bool = False,
    on_progress: Callable[[int, dict[str, object]], None] | None = None,
    on_event: Callable[..., None] | None = None,
    on_threshold: Callable[
        [dict[str, object], dbhash.AtomicTimeoutDecision], None] | None = None,
    poll_seconds: float = 0.05,
    paused_wait_seconds: float = 0.25,
    max_files: int | None = None,
    _worker_target=None,
) -> dict[str, object]:
    """运行可交互哈希阶段，并在文件边界处理暂停后的动作。"""
    if paused_wait_seconds <= 0:
        raise ValueError("paused 等待轮询间隔必须大于 0")
    if router.state != "running":
        raise core.PreflightError(
            f"哈希阶段要求 running 控制器，实际为 {router.state}")
    if router.hash_control.current() is not None:
        boundary = settle_pending_stage_control(
            con,
            "hash",
            router,
            on_event=on_event,
            paused_wait_seconds=paused_wait_seconds,
        )
        if boundary != "running":
            return {"state": boundary}

    _emit_control_event(on_event, "stage_started", stage="hash")
    while True:
        stats = dbhash.process_hash_stage_v4(
            con,
            mode,
            previous=previous,
            retry_mode=retry_mode,
            chunk_bytes=chunk_bytes,
            stall_seconds=stall_seconds,
            timeout_seconds=timeout_seconds,
            default_decision=default_decision,
            control=router.hash_control,
            save_on_pause=False,
            show_current_file=show_current_file,
            on_progress=on_progress,
            on_event=on_event,
            on_threshold=on_threshold,
            poll_seconds=poll_seconds,
            max_files=max_files,
            _worker_target=_worker_target,
        )
        outcome = str(stats["state"])
        if outcome == "completed":
            if mode == "none":
                _emit_control_event(
                    on_event,
                    "stage_skipped",
                    stage="hash",
                    reason="hash_mode_none",
                )
            else:
                _emit_control_event(
                    on_event, "stage_finished", stage="hash", **stats)
            return stats
        if outcome in ("save_exit", "stopped"):
            router.end()
            _emit_control_event(
                on_event,
                "run_saved" if outcome == "save_exit" else "run_stopped",
                stage="hash",
                state=outcome,
            )
            return stats
        if outcome == "failed_recoverable":
            router.end()
            return stats
        if outcome != "paused":
            raise core.PreflightError(
                f"哈希阶段返回未知控制状态：{outcome}")
        result = _wait_after_pause(
            con,
            "hash",
            router,
            on_event=on_event,
            paused_wait_seconds=paused_wait_seconds,
        )
        if result == "running":
            _emit_control_event(
                on_event, "stage_restarted", stage="hash")
            continue
        stats["state"] = result
        return stats


def _source_stat_signature(path: str) -> tuple[int, str]:
    stat_result = os.stat(
        core.to_extended_path(path), follow_symlinks=False)
    return (
        int(stat_result.st_size),
        core.ns_to_utc_iso(stat_result.st_mtime_ns),
    )


def _verify_hash_candidates(
    con: sqlite3.Connection,
    percent: float,
    min_count: int,
) -> tuple[int, list[tuple[int, int, int, str, str, str]]]:
    snapshot_uuid = str(con.execute(
        "SELECT snapshot_uuid FROM snapshot_info WHERE id=1"
    ).fetchone()[0])
    candidates = con.execute(
        "SELECT h.entry_id,e.size_bytes,e.root_id,e.rel_path,h.hash_hex,"
        " e.modified_at_utc FROM hashes h"
        " JOIN entries e ON e.entry_id=h.entry_id"
        " WHERE h.algorithm='sha256' AND h.origin='computed'"
        " AND h.hash_hex IS NOT NULL AND length(h.hash_hex)=64"
        " AND e.is_placeholder=0"
        " AND (h.status='valid' OR h.failure_reason LIKE 'verify_%')"
        " ORDER BY e.root_id,e.path_key,e.rel_path"
    ).fetchall()
    selected_ids = {
        int(entry_id)
        for entry_id, _size in dbhash.pick_sample(
            [(int(row[0]), int(row[1])) for row in candidates],
            percent,
            min_count,
            seed=snapshot_uuid + ":scan_verify",
        )
    }
    selected = [
        (
            int(row[0]),
            int(row[1]),
            int(row[2]),
            str(row[3]),
            str(row[4]).casefold(),
            str(row[5]),
        )
        for row in candidates if int(row[0]) in selected_ids
    ]
    return len(candidates), selected


def _latest_verify_hash_attempts(
    con: sqlite3.Connection,
) -> dict[int, tuple[str, str | None, int]]:
    return {
        int(row[0]): (
            str(row[1]),
            None if row[2] is None else str(row[2]),
            int(row[3]),
        )
        for row in con.execute(
            "SELECT a.entry_id,a.status,a.error_code,a.bytes_read"
            " FROM entry_attempts a WHERE a.stage='verify_hash'"
            " AND NOT EXISTS (SELECT 1 FROM entry_attempts newer"
            "  WHERE newer.entry_id=a.entry_id"
            "  AND newer.stage=a.stage"
            "  AND newer.attempt_number>a.attempt_number)"
        )
    }


_VERIFY_HASH_RETRIABLE_TOOL_ERRORS = frozenset((
    "independent_worker_start_failed",
    "independent_hash_tool_error",
    "verify_primary_recheck_start_failed",
    "verify_primary_recheck_tool_error",
    "independent_recheck_start_failed",
))


def _verify_hash_attempt_is_terminal(
    value: tuple[str, str | None, int],
) -> bool:
    status, error_code, _bytes_read = value
    return (
        status not in ("cancelled", "abandoned")
        and error_code not in _VERIFY_HASH_RETRIABLE_TOOL_ERRORS
    )


def _verify_hash_runtime_failure(
    *,
    tool: str,
    operation: str,
    failure_kind: str,
    message: str | None,
    pid: int | None = None,
    returncode: int | None = None,
    errno: int | None = None,
    recovered: bool = True,
) -> toolruntime.ToolRuntimeFailure:
    return toolruntime.ToolRuntimeFailure(
        toolruntime.ToolFaultEvidence(
            tool=tool,
            operation=operation,
            failure_kind=failure_kind,
            message=str(message or f"{tool} 运行故障")[:2048],
            pid=pid,
            returncode=returncode,
            errno=errno,
        ),
        recovered=recovered,
    )


def _independent_hash_tool_failure(
    outcome: dbhash.IndependentHashOutcome,
) -> toolruntime.ToolRuntimeFailure | None:
    if not outcome.failure_kind:
        return None
    return _verify_hash_runtime_failure(
        tool="powershell-get-filehash",
        operation="independent_hash",
        failure_kind=str(outcome.failure_kind),
        message=outcome.error,
        pid=outcome.worker_pid,
        returncode=outcome.worker_exitcode,
        recovered=outcome.failure_kind not in (
            "cleanup_failed", "monitor_start_failed", "supervision_failed",
        ),
    )


def _mark_hash_verification_unstable(
    con: sqlite3.Connection,
    entry_id: int,
    error_code: str,
    message: str,
) -> None:
    con.execute(
        "UPDATE hashes SET status='unstable',failure_reason=?"
        " WHERE entry_id=? AND algorithm='sha256'",
        (f"{error_code}: {message}", entry_id),
    )
    con.execute(
        "UPDATE entries SET hash_status='unstable' WHERE entry_id=?",
        (entry_id,),
    )
    con.execute(
        "INSERT INTO errors"
        " (entry_id,stage,error_code,message,occurred_at_utc)"
        " VALUES (?,'hash',?,?,?)",
        (entry_id, error_code, message, core.now_utc_iso()),
    )


def _verified_primary_digest(outcome: dbhash.HashWorkerOutcome) \
        -> str | None:
    result = outcome.result
    if outcome.outcome != "completed" or not isinstance(result, dict):
        return None
    digest = result.get("hash_hex")
    if result.get("status") != "valid" or not isinstance(digest, str):
        return None
    if len(digest) != 64 or any(
            character not in "0123456789abcdefABCDEF"
            for character in digest):
        return None
    if int(result.get("bytes_read") or -1) != outcome.size_bytes:
        return None
    return digest.casefold()


def _verify_controlled_attempt_state(
    con: sqlite3.Connection,
    router: RunCommandRouter,
    outcome,
    *,
    on_event: Callable[..., None] | None,
    paused_wait_seconds: float,
) -> str | None:
    if outcome.outcome in ("paused", "save_exit"):
        for_exit = outcome.outcome == "save_exit"
        dbstate.request_pause(con, for_exit=for_exit)
        dbstate.update_stage_checkpoint(
            con,
            "verify_hash",
            "pause_requested",
            current_entry_id=None,
            checkpoint={"reason": "worker_pause"},
        )
        dbstate.mark_paused(con, for_exit=for_exit)
        dbstate.update_stage_checkpoint(
            con,
            "verify_hash",
            "paused",
            current_entry_id=None,
            checkpoint={"reason": "worker_pause"},
        )
        if for_exit:
            router.end()
            _emit_control_event(
                on_event,
                "run_saved",
                stage="verify_hash",
                state="save_exit",
            )
            return "save_exit"
        return _wait_after_pause(
            con,
            "verify_hash",
            router,
            on_event=on_event,
            paused_wait_seconds=paused_wait_seconds,
        )
    if outcome.outcome == "stopped":
        dbstate.update_stage_checkpoint(
            con,
            "verify_hash",
            "failed_recoverable",
            current_entry_id=None,
            checkpoint={"reason": "stop_and_resume"},
        )
        dbstate.stop_run(con, reason="stop_and_resume")
        router.end()
        _emit_control_event(
            on_event,
            "run_stopped",
            stage="verify_hash",
            state="stopped",
        )
        return "stopped"
    return None


def run_independent_hash_stage_controlled(
    con: sqlite3.Connection,
    router: RunCommandRouter,
    *,
    percent: float = 1.0,
    min_count: int = 100,
    powershell_path: str,
    powershell_version: str,
    show_current_file: bool = False,
    stall_seconds: float = dbhash.HASH_STALL_SECONDS,
    timeout_seconds: float | None = None,
    default_decision: str = "continue_waiting",
    on_progress: Callable[[int, dict[str, object]], None] | None = None,
    on_event: Callable[..., None] | None = None,
    on_threshold: Callable[
        [dict[str, object], dbhash.AtomicTimeoutDecision], None] | None = None,
    poll_seconds: float = 0.05,
    paused_wait_seconds: float = 0.25,
    _independent_runner=None,
    _primary_runner=None,
) -> dict[str, object]:
    """以受控 PowerShell 进程抽验本次 computed 哈希并保留 attempt。"""
    dbstate.require_v4_connection(con)
    if router.state != "running":
        raise core.PreflightError(
            f"哈希复检要求控制器处于运行状态，实际状态为 {router.state}")
    if isinstance(min_count, bool) or not isinstance(min_count, int) \
            or min_count < 0:
        raise ValueError("min_count 不能小于 0")
    ratio = _format_sample_value(percent)
    runtime = dbstate.load_runtime(con)
    if runtime.run_state != "running":
        raise core.PreflightError(
            f"哈希复检要求任务处于运行状态，实际状态为 {runtime.run_state}")
    coverage = str(con.execute(
        "SELECT hash_coverage FROM snapshot_info WHERE id=1"
    ).fetchone()[0])
    config = _json_object(con.execute(
        "SELECT config_json FROM snapshot_info WHERE id=1"
    ).fetchone()[0], "snapshot config_json")
    configured_hash = str(config.get("hash") or coverage)
    if configured_hash == "none":
        dbstate.update_stage_checkpoint(
            con,
            "verify_hash",
            "skipped",
            items_done=0,
            items_total=0,
            current_entry_id=None,
            checkpoint={"reason": "hash_mode_none"},
        )
        _emit_control_event(
            on_event,
            "stage_skipped",
            stage="verify_hash",
            reason="hash_mode_none",
        )
        return {
            "state": "completed",
            "eligible": 0,
            "sampled": 0,
            "processed": 0,
            "matched": 0,
            "mismatched": 0,
            "tool_error": 0,
            "timeout": 0,
            "unstable": 0,
            "bytes_total": 0,
            "bytes_read": 0,
        }

    checkpoint = con.execute(
        "SELECT state,checkpoint_json FROM stage_checkpoints"
        " WHERE stage='verify_hash'"
    ).fetchone()
    if checkpoint is not None and checkpoint[0] in ("completed", "skipped"):
        saved = _json_object(checkpoint[1], "verify_hash checkpoint_json")
        saved["state"] = "completed"
        _emit_control_event(
            on_event,
            "stage_finished" if checkpoint[0] == "completed"
            else "stage_skipped",
            stage="verify_hash",
            **({} if checkpoint[0] == "completed" else {
                "reason": "checkpoint_already_terminal",
            }),
        )
        return saved
    if not isinstance(powershell_path, str) or not powershell_path:
        raise core.PreflightError("哈希复检缺少冻结的 PowerShell 路径")
    if not isinstance(powershell_version, str) or not powershell_version:
        raise core.PreflightError("哈希复检缺少冻结的 PowerShell 版本")

    _emit_control_event(
        on_event, "stage_started", stage="verify_hash")
    eligible, selected = _verify_hash_candidates(con, ratio, min_count)
    selected_ids = {row[0] for row in selected}
    latest = {
        entry_id: value
        for entry_id, value in _latest_verify_hash_attempts(con).items()
        if entry_id in selected_ids
    }
    terminal = {
        entry_id: value for entry_id, value in latest.items()
        if _verify_hash_attempt_is_terminal(value)
    }
    stats: dict[str, object] = {
        "state": "running",
        "eligible": eligible,
        "sampled": len(selected),
        "processed": len(terminal),
        "matched": sum(value[0] == "succeeded" for value in terminal.values()),
        "mismatched": sum(
            value[0] in ("invalid", "unstable")
            and value[1] == "verify_mismatch"
            for value in terminal.values()),
        "tool_error": sum(
            value[0] in ("error", "skipped_policy")
            for value in terminal.values()),
        "timeout": sum(value[0] == "timeout" for value in terminal.values()),
        "unstable": sum(
            value[0] == "unstable"
            and value[1] != "verify_mismatch"
            for value in terminal.values()),
        "bytes_total": sum(row[1] for row in selected),
        "bytes_read": sum(value[2] for value in terminal.values()),
    }
    dbstate.update_stage_checkpoint(
        con,
        "verify_hash",
        "running",
        items_done=int(stats["processed"]),
        items_total=int(stats["sampled"]),
        bytes_done=int(stats["bytes_read"]),
        bytes_total=int(stats["bytes_total"]),
        error_count=(
            int(stats["mismatched"])
            + int(stats["tool_error"])
            + int(stats["timeout"])
            + int(stats["unstable"])
        ),
        current_entry_id=None,
    )
    roots = dict(con.execute("SELECT root_id,root_path FROM roots"))
    independent_runner = (
        _independent_runner or dbhash.run_independent_hash_process)
    primary_runner = _primary_runner or dbhash.run_hash_worker
    last_current_event = 0.0
    last_progress_event = 0.0
    circuit = toolruntime.ConsecutiveToolFailureCircuit()
    fault_bytes: dict[int, int] = {}

    def fail_tool_stage(
        failure: toolruntime.ToolRuntimeFailure,
        opened: toolruntime.ToolCircuitSnapshot,
        *,
        current_entry_id: int,
    ) -> dict[str, object]:
        affected_ids = list(opened.entry_ids)
        prior_ids = [
            affected_id for affected_id in affected_ids
            if affected_id != current_entry_id
        ]
        stats["processed"] = max(
            0, int(stats["processed"]) - len(prior_ids))
        stats["tool_error"] = max(
            0, int(stats["tool_error"]) - len(prior_ids))
        stats["bytes_read"] = max(
            0,
            int(stats["bytes_read"])
            - sum(fault_bytes.get(affected_id, 0) for affected_id in prior_ids),
        )
        not_processed = max(
            0, int(stats["sampled"]) - int(stats["processed"]))
        payload = {
            "reason": "verify_hash_tool_circuit_open",
            "stage": "verify_hash",
            "tool_circuit": opened.as_dict(),
            "processed": int(stats["processed"]),
            "total": int(stats["sampled"]),
            "not_processed": not_processed,
            "tool_failure": failure.as_dict(),
        }
        dbstate.update_stage_checkpoint(
            con,
            "verify_hash",
            "failed_recoverable",
            items_done=int(stats["processed"]),
            items_total=int(stats["sampled"]),
            bytes_done=int(stats["bytes_read"]),
            bytes_total=int(stats["bytes_total"]),
            error_count=(
                int(stats["mismatched"])
                + int(stats["tool_error"])
                + int(stats["timeout"])
                + int(stats["unstable"])
            ),
            current_entry_id=None,
            checkpoint=payload,
        )
        dbstate.fail_run(
            con,
            recoverable=True,
            error_code="verify_hash_tool_circuit_open",
            error_message=failure.latest.message,
            payload=payload,
        )
        router.end()
        stats.update({
            "state": "failed_recoverable",
            "not_processed": not_processed,
            "tool_circuit": opened.as_dict(),
            "tool_failure": failure.as_dict(),
        })
        _emit_control_event(
            on_event,
            "tool_circuit_open",
            stage="verify_hash",
            tool=failure.latest.tool,
            failure_kind=failure.latest.failure_kind,
            processed=int(stats["processed"]),
            total=int(stats["sampled"]),
            not_processed=not_processed,
            affected=len(affected_ids),
        )
        _emit_control_event(
            on_event,
            "stage_failed",
            stage="verify_hash",
            state="failed_recoverable",
            tool=failure.latest.tool,
            processed=int(stats["processed"]),
            total=int(stats["sampled"]),
            not_processed=not_processed,
        )
        return stats

    for entry_id, size_bytes, root_id, rel_path, recorded, recorded_mtime \
            in selected:
        previous = latest.get(entry_id)
        if previous is not None and _verify_hash_attempt_is_terminal(previous):
            continue
        boundary = settle_pending_stage_control(
            con,
            "verify_hash",
            router,
            on_event=on_event,
            paused_wait_seconds=paused_wait_seconds,
        )
        if boundary != "running":
            stats["state"] = boundary
            return stats
        path = os.path.join(roots[root_id], rel_path)
        now = time.monotonic()
        if show_current_file and on_event is not None \
                and now - last_current_event >= 0.1:
            on_event("current_item", stage="verify_hash", item=rel_path)
            last_current_event = now
        attempt_id = dbstate.start_attempt(
            con,
            entry_id,
            "verify_hash",
            tool_name="powershell-get-filehash",
            tool_version=powershell_version,
            coverage="sample",
        )
        try:
            pre_signature = _source_stat_signature(path)
        except OSError as exc:
            pre_signature = None
            pre_error = f"pre_stat: {exc}"
        else:
            pre_error = None
            if pre_signature != (size_bytes, recorded_mtime):
                pre_error = (
                    "source_changed_before_verify: "
                    f"recorded=({size_bytes},{recorded_mtime}) "
                    f"current={pre_signature}"
                )
        if pre_error is not None:
            dbstate.finish_attempt(
                con,
                attempt_id,
                "unstable",
                end_reason="verify_source_changed",
                error_code="verify_source_changed",
                error_message=pre_error,
                result={"phase": "pre_stat"},
                _current_writer=lambda current, current_entry_id, _attempt_id,
                message=pre_error: _mark_hash_verification_unstable(
                    current,
                    current_entry_id,
                    "verify_source_changed",
                    message,
                ),
            )
            stats["unstable"] = int(stats["unstable"]) + 1
            stats["processed"] = int(stats["processed"]) + 1
            continue

        try:
            independent = independent_runner(
                path,
                powershell_path,
                expected_size=size_bytes,
                stall_seconds=stall_seconds,
                timeout_seconds=timeout_seconds,
                default_decision=default_decision,
                display_name=rel_path,
                control=router.hash_control,
                on_event=on_event,
                on_threshold=on_threshold,
                poll_seconds=poll_seconds,
            )
        except Exception as exc:
            failure = _verify_hash_runtime_failure(
                tool="powershell-get-filehash",
                operation="independent_hash",
                failure_kind="start_failed",
                message=(
                    "PowerShell 独立哈希进程无法启动："
                    f"{type(exc).__name__}: {exc}"
                ),
                errno=getattr(exc, "errno", None),
                recovered=False,
            )
            dbstate.finish_attempt(
                con,
                attempt_id,
                "error",
                end_reason="independent_worker_start_failed",
                error_code="independent_worker_start_failed",
                error_message=str(exc),
                result={
                    "worker_outcome": "start_failed",
                    "tool_failure": failure.as_dict(),
                },
            )
            fault_bytes[int(entry_id)] = 0
            opened = circuit.record_failure(int(entry_id), failure)
            return fail_tool_stage(
                failure,
                opened,
                current_entry_id=int(entry_id),
            )

        final_outcome = independent
        attempt_status = "succeeded"
        error_code = None
        error_message = None
        tool_failure = _independent_hash_tool_failure(independent)
        result: dict[str, object] = {
            "initial_independent": independent.hash_hex,
            "recorded": recorded,
            "worker_exitcode": independent.worker_exitcode,
            "worker_reaped": independent.worker_reaped,
            "threshold_count": independent.threshold_count,
            "failure_kind": independent.failure_kind,
        }
        if independent.outcome in ("paused", "save_exit", "stopped"):
            attempt_status = "cancelled"
        elif independent.outcome == "timeout":
            attempt_status = "timeout"
            error_code = "independent_hash_timeout"
            error_message = independent.error or error_code
            circuit.record_success("powershell-get-filehash")
        elif tool_failure is not None:
            attempt_status = "error"
            error_code = "independent_hash_tool_error"
            error_message = independent.error or error_code
        elif independent.outcome != "completed" \
                or independent.hash_hex is None:
            attempt_status = "error"
            error_code = "independent_hash_source_error"
            error_message = independent.error or error_code
            circuit.record_success("powershell-get-filehash")
        elif independent.hash_hex != recorded:
            circuit.record_success("powershell-get-filehash")
            try:
                primary = primary_runner(
                    path,
                    expected_size=size_bytes,
                    stall_seconds=stall_seconds,
                    timeout_seconds=timeout_seconds,
                    default_decision=default_decision,
                    display_name=rel_path,
                    control=router.hash_control,
                    on_event=on_event,
                    on_threshold=on_threshold,
                    poll_seconds=poll_seconds,
                )
            except Exception as exc:
                tool_failure = _verify_hash_runtime_failure(
                    tool=dbhash.HASH_TOOL,
                    operation="verify_primary_recheck",
                    failure_kind="worker_start_failed",
                    message=(
                        "主哈希复核工作进程无法启动："
                        f"{type(exc).__name__}: {exc}"
                    ),
                    errno=getattr(exc, "errno", None),
                    recovered=False,
                )
                attempt_status = "error"
                error_code = "verify_primary_recheck_start_failed"
                error_message = str(exc)
                result["primary_recheck_outcome"] = "start_failed"
            else:
                final_outcome = primary
                result["primary_recheck"] = _verified_primary_digest(primary)
                result["primary_recheck_outcome"] = primary.outcome
                result["primary_recheck_failure_kind"] = primary.failure_kind
                primary_failure = dbhash._hash_worker_tool_failure(primary)
                if primary.outcome in ("paused", "save_exit", "stopped"):
                    attempt_status = "cancelled"
                elif primary.outcome == "timeout":
                    attempt_status = "timeout"
                    error_code = "verify_recheck_timeout"
                    error_message = error_code
                    circuit.record_success(dbhash.HASH_TOOL)
                elif primary_failure is not None:
                    tool_failure = primary_failure
                    attempt_status = "error"
                    error_code = "verify_primary_recheck_tool_error"
                    error_message = primary_failure.latest.message
                elif result["primary_recheck"] is None:
                    attempt_status = "error"
                    error_code = "verify_primary_recheck_source_error"
                    error_message = error_code
                    circuit.record_success(dbhash.HASH_TOOL)
                else:
                    circuit.record_success(dbhash.HASH_TOOL)
                    try:
                        independent_again = independent_runner(
                            path,
                            powershell_path,
                            expected_size=size_bytes,
                            stall_seconds=stall_seconds,
                            timeout_seconds=timeout_seconds,
                            default_decision=default_decision,
                            display_name=rel_path,
                            control=router.hash_control,
                            on_event=on_event,
                            on_threshold=on_threshold,
                            poll_seconds=poll_seconds,
                        )
                    except Exception as exc:
                        tool_failure = _verify_hash_runtime_failure(
                            tool="powershell-get-filehash",
                            operation="independent_hash_recheck",
                            failure_kind="start_failed",
                            message=(
                                "PowerShell 独立哈希复核进程无法启动："
                                f"{type(exc).__name__}: {exc}"
                            ),
                            errno=getattr(exc, "errno", None),
                            recovered=False,
                        )
                        attempt_status = "error"
                        error_code = "independent_recheck_start_failed"
                        error_message = str(exc)
                        result["independent_recheck_outcome"] = \
                            "start_failed"
                    else:
                        final_outcome = independent_again
                        result["independent_recheck"] = \
                            independent_again.hash_hex
                        result["independent_recheck_outcome"] = \
                            independent_again.outcome
                        result["independent_recheck_failure_kind"] = \
                            independent_again.failure_kind
                        independent_again_failure = \
                            _independent_hash_tool_failure(independent_again)
                        if independent_again.outcome in (
                                "paused", "save_exit", "stopped"):
                            attempt_status = "cancelled"
                        elif independent_again.outcome == "timeout":
                            attempt_status = "timeout"
                            error_code = "verify_recheck_timeout"
                            error_message = error_code
                            circuit.record_success(
                                "powershell-get-filehash")
                        elif independent_again_failure is not None:
                            tool_failure = independent_again_failure
                            attempt_status = "error"
                            error_code = "independent_hash_tool_error"
                            error_message = (
                                independent_again_failure.latest.message)
                        elif independent_again.outcome != "completed" \
                                or independent_again.hash_hex is None:
                            attempt_status = "error"
                            error_code = "independent_hash_source_error"
                            error_message = (
                                independent_again.error or error_code)
                            circuit.record_success(
                                "powershell-get-filehash")
                        elif result["primary_recheck"] == recorded \
                                and independent_again.hash_hex == recorded:
                            attempt_status = "succeeded"
                            result["initial_mismatch_resolved"] = True
                            circuit.record_success(
                                "powershell-get-filehash")
                        else:
                            attempt_status = "unstable"
                            error_code = "verify_mismatch"
                            error_message = (
                                f"recorded={recorded} "
                                f"independent={independent.hash_hex} "
                                "primary_recheck="
                                f"{result['primary_recheck']} "
                                "independent_recheck="
                                f"{independent_again.hash_hex}"
                            )
                            circuit.record_success(
                                "powershell-get-filehash")
        else:
            circuit.record_success("powershell-get-filehash")

        if tool_failure is not None:
            result["tool_failure"] = tool_failure.as_dict()

        if attempt_status not in ("cancelled", "timeout", "error"):
            try:
                post_signature = _source_stat_signature(path)
            except OSError as exc:
                post_signature = None
                post_error = f"post_stat: {exc}"
            else:
                post_error = None
                if post_signature != pre_signature:
                    post_error = (
                        "source_changed_during_verify: "
                        f"before={pre_signature} after={post_signature}"
                    )
            if post_error is not None:
                attempt_status = "unstable"
                error_code = "verify_source_changed"
                error_message = post_error
        performance = independent.performance()
        performance["ended_reason"] = error_code or attempt_status

        def write_unstable(
            current: sqlite3.Connection,
            current_entry_id: int,
            _current_attempt_id: int,
        ) -> None:
            if attempt_status == "unstable" and error_code is not None:
                _mark_hash_verification_unstable(
                    current,
                    current_entry_id,
                    error_code,
                    str(error_message or error_code),
                )

        dbstate.finish_attempt(
            con,
            attempt_id,
            attempt_status,
            bytes_read=independent.bytes_read,
            final_offset=independent.final_offset,
            stall_count=independent.stall_count,
            max_stall_seconds=independent.longest_stall_seconds,
            decision=final_outcome.decision,
            decision_source=final_outcome.decision_source,
            end_reason=error_code or attempt_status,
            error_code=error_code,
            error_message=error_message,
            result=result,
            performance=performance,
            _current_writer=write_unstable,
        )
        if tool_failure is not None:
            fault_bytes[int(entry_id)] = int(independent.bytes_read)
            opened = circuit.record_failure(int(entry_id), tool_failure)
            if opened.opened:
                return fail_tool_stage(
                    tool_failure,
                    opened,
                    current_entry_id=int(entry_id),
                )
        if attempt_status == "cancelled":
            state = _verify_controlled_attempt_state(
                con,
                router,
                final_outcome,
                on_event=on_event,
                paused_wait_seconds=paused_wait_seconds,
            )
            if state == "running":
                return run_independent_hash_stage_controlled(
                    con,
                    router,
                    percent=ratio,
                    min_count=min_count,
                    powershell_path=powershell_path,
                    powershell_version=powershell_version,
                    show_current_file=show_current_file,
                    stall_seconds=stall_seconds,
                    timeout_seconds=timeout_seconds,
                    default_decision=default_decision,
                    on_progress=on_progress,
                    on_event=on_event,
                    on_threshold=on_threshold,
                    poll_seconds=poll_seconds,
                    paused_wait_seconds=paused_wait_seconds,
                    _independent_runner=independent_runner,
                    _primary_runner=primary_runner,
                )
            stats["state"] = state or final_outcome.outcome
            return stats

        stats["processed"] = int(stats["processed"]) + 1
        stats["bytes_read"] = (
            int(stats["bytes_read"]) + int(independent.bytes_read))
        if attempt_status == "succeeded":
            stats["matched"] = int(stats["matched"]) + 1
        elif attempt_status == "timeout":
            stats["timeout"] = int(stats["timeout"]) + 1
        elif attempt_status == "error":
            stats["tool_error"] = int(stats["tool_error"]) + 1
        elif error_code == "verify_mismatch":
            stats["mismatched"] = int(stats["mismatched"]) + 1
        else:
            stats["unstable"] = int(stats["unstable"]) + 1
        errors = (
            int(stats["mismatched"])
            + int(stats["tool_error"])
            + int(stats["timeout"])
            + int(stats["unstable"])
        )
        checkpoint_payload = {
            key: value for key, value in stats.items() if key != "state"
        }
        dbstate.update_stage_checkpoint(
            con,
            "verify_hash",
            "running",
            items_done=int(stats["processed"]),
            items_total=int(stats["sampled"]),
            bytes_done=int(stats["bytes_read"]),
            bytes_total=int(stats["bytes_total"]),
            error_count=errors,
            current_entry_id=None,
            checkpoint=checkpoint_payload,
        )
        now = time.monotonic()
        if on_progress is not None and (
                now - last_progress_event >= 0.5
                or int(stats["processed"]) == int(stats["sampled"])):
            on_progress(int(stats["processed"]), dict(stats))
            last_progress_event = now

    stats["state"] = "completed"
    final_payload = {
        key: value for key, value in stats.items() if key != "state"
    }
    dbstate.update_stage_checkpoint(
        con,
        "verify_hash",
        "completed",
        items_done=int(stats["processed"]),
        items_total=int(stats["sampled"]),
        bytes_done=int(stats["bytes_read"]),
        bytes_total=int(stats["bytes_total"]),
        error_count=(
            int(stats["mismatched"])
            + int(stats["tool_error"])
            + int(stats["timeout"])
            + int(stats["unstable"])
        ),
        current_entry_id=None,
        checkpoint=final_payload,
    )
    _emit_control_event(
        on_event, "stage_finished", stage="verify_hash", **final_payload)
    return stats


def _configured_root_mapping(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return {str(old): str(new) for old, new in value.items()}
    if not isinstance(value, list):
        raise core.PreflightError("map_root 必须是对象或字符串列表")
    mapping: dict[str, str] = {}
    for raw in value:
        old, separator, new = str(raw).partition("=")
        if not separator or not old or not new or old in mapping:
            raise core.PreflightError(
                f"冻结的 map_root 无效：{raw!r}")
        mapping[old] = new
    return mapping


_METADATA_TOOL_MODES = frozenset(("off", "normalized", "complete"))


def metadata_tool_modes(config: dict[str, object]) -> dict[str, str]:
    """读取每个元数据工具的冻结范围，并兼容旧全局范围配置。"""
    storage = config.get("metadata_storage", "complete")
    if storage not in ("complete", "normalized"):
        raise core.PreflightError("冻结的 metadata_storage 无效")
    modes: dict[str, str] = {}
    for tool_name in ("exiftool", "ffprobe"):
        enabled = config.get(f"metadata_{tool_name}", True)
        if not isinstance(enabled, bool):
            raise core.PreflightError("冻结的元数据工具开关必须是布尔值")
        explicit = config.get(f"metadata_{tool_name}_mode")
        mode = str(explicit) if explicit is not None else (
            str(storage) if enabled else "off")
        if mode not in _METADATA_TOOL_MODES:
            raise core.PreflightError(
                f"冻结的 {tool_name} 元数据范围无效：{mode!r}")
        if explicit is not None and enabled != (mode != "off"):
            raise core.PreflightError(
                f"冻结的 {tool_name} 元数据范围与工具开关冲突")
        modes[tool_name] = mode
    return modes


def _metadata_tools(
    tools: dict[str, object],
    *,
    metadata_exiftool: bool,
    metadata_ffprobe: bool,
) -> dict[str, dict[str, object]]:
    selected: dict[str, dict[str, object]] = {}
    names = ["sevenzip"]
    if metadata_exiftool:
        names.append("exiftool")
    if metadata_ffprobe:
        names.append("ffprobe")
    for name in names:
        value = tools.get(name)
        if not isinstance(value, dict):
            raise core.PreflightError(
                f"当前扫描会话缺少 {name} 的路径／版本能力")
        path = value.get("path")
        version = value.get("version")
        if not isinstance(path, str) or not path \
                or not isinstance(version, str) or not version:
            raise core.PreflightError(
                f"当前扫描会话中的 {name} 路径／版本无效")
        selected[name] = dict(value)
    return selected


def run_scan_evidence_stages(
    handle: RunHandle,
    router: RunCommandRouter,
    *,
    show_current_file: bool = False,
    on_progress: Callable[[str, dict[str, object]], None] | None = None,
    on_event: Callable[..., None] | None = None,
    paused_wait_seconds: float = 0.25,
    hash_stall_seconds: float = dbhash.HASH_STALL_SECONDS,
    hash_timeout_seconds: float | None = None,
    hash_default_decision: str = "continue_waiting",
    hash_retry_mode: str = "pending",
    hash_poll_seconds: float = 0.05,
    format_substage: Callable[
        [sqlite3.Connection, RunCommandRouter], dict[str, object]
    ] | None = None,
    _hash_worker_target=None,
) -> dict[str, object]:
    """运行扫描证据阶段；包含可选格式校验，不执行抽验、封存或发布。"""
    con = handle.connection
    runtime = dbstate.load_runtime(con)
    if runtime.run_state != "running":
        raise core.PreflightError(
            f"证据采集要求 running，实际为 {runtime.run_state}")
    session_ended, config, tools = _session_payload(
        con, runtime.active_session_id)
    if session_ended:
        raise core.PreflightError("当前扫描会话已结束，不能采集证据")
    phase = str(config.get("phase") or "")
    if phase not in ("full", "quick"):
        raise core.PreflightError(f"冻结的扫描模式无效：{phase!r}")
    hash_mode = str(config.get("hash") or (
        "none" if phase == "quick" else "full"))
    if phase == "quick" and hash_mode != "none":
        raise core.PreflightError("快速扫描的冻结配置不能启用哈希")
    if hash_mode not in ("none", "incremental", "full"):
        raise core.PreflightError(f"冻结的哈希模式无效：{hash_mode!r}")
    if hash_retry_mode not in ("pending", "transient", "all_unsuccessful"):
        raise core.PreflightError(
            f"哈希重试范围无效：{hash_retry_mode!r}")
    format_mode = str(
        config.get("format_validation") or "off"
    ).strip().casefold()
    if format_mode not in ("off", "sample", "all"):
        raise core.PreflightError(
            f"冻结的格式校验模式无效：{format_mode!r}")
    if phase == "quick" and format_mode != "off":
        raise core.PreflightError("快速扫描的冻结配置不能启用格式校验")
    metadata_modes = metadata_tool_modes(config)
    metadata_exiftool = metadata_modes["exiftool"] != "off"
    metadata_ffprobe = metadata_modes["ffprobe"] != "off"

    def progress(stage: str, payload: dict[str, object]) -> None:
        if on_progress is not None:
            on_progress(stage, dict(payload))

    exclude_paths = {
        handle.partial_path,
        handle.partial_path + "-wal",
        handle.partial_path + "-shm",
        handle.lease_path,
        runtime.event_log_path,
    }
    stages: dict[str, dict[str, object]] = {}
    enumeration = run_enumeration_stage_controlled(
        con,
        router,
        collect_file_id=not bool(config.get("no_file_id", False)),
        exclude_paths=exclude_paths,
        exclude_dirs={runtime.output_dir},
        on_progress=lambda payload: progress("enumerate", payload),
        on_event=on_event,
        paused_wait_seconds=paused_wait_seconds,
    )
    stages["enumerate"] = enumeration
    if enumeration["state"] != "completed":
        return {
            "state": enumeration["state"],
            "stage": "enumerate",
            "stages": stages,
        }

    if phase == "quick":
        con.execute(
            "UPDATE entries SET meta_status='not_applicable'"
            " WHERE meta_status='pending' AND media_kind='other'")
        con.execute(
            "UPDATE entries SET meta_status='skipped'"
            " WHERE meta_status='pending'")
        con.execute(
            "UPDATE entries SET hash_status='skipped'"
            " WHERE hash_status='pending'")
        con.commit()
        total = int(con.execute(
            "SELECT COUNT(*) FROM entries").fetchone()[0])
        for stage in ("hash", "metadata"):
            dbstate.update_stage_checkpoint(
                con,
                stage,
                "skipped",
                items_done=total,
                items_total=total,
                current_entry_id=None,
                checkpoint={"reason": "quick_scan"},
            )
            stages[stage] = {
                "state": "completed",
                "total": total,
                "skipped": total,
            }
            _emit_control_event(
                on_event,
                "stage_skipped",
                stage=stage,
                reason="quick_scan",
            )
    else:
        previous = None
        if hash_mode == "incremental":
            previous_path = config.get("previous_snapshot")
            if not isinstance(previous_path, str) or not previous_path:
                raise core.PreflightError(
                    "增量扫描的冻结配置缺少 previous_snapshot")
            previous = dbhash.load_previous(
                os.path.abspath(previous_path),
                _configured_root_mapping(config.get("map_root")),
            )
            con.execute(
                "UPDATE snapshot_info SET previous_snapshot_uuid=?",
                (previous.uuid,),
            )
            con.commit()
            _emit_control_event(
                on_event,
                "previous_snapshot_loaded",
                uuid=previous.uuid,
                path=os.path.basename(previous_path),
                has_file_issues=previous.has_file_issues,
            )
        hashed = run_hash_stage_controlled(
            con,
            hash_mode,
            router,
            previous=previous,
            retry_mode=hash_retry_mode,
            stall_seconds=hash_stall_seconds,
            timeout_seconds=hash_timeout_seconds,
            default_decision=hash_default_decision,
            show_current_file=show_current_file,
            on_progress=lambda _index, payload: progress("hash", payload),
            on_event=on_event,
            poll_seconds=hash_poll_seconds,
            paused_wait_seconds=paused_wait_seconds,
            _worker_target=_hash_worker_target,
        )
        stages["hash"] = hashed
        if hashed["state"] != "completed":
            return {
                "state": hashed["state"],
                "stage": "hash",
                "stages": stages,
            }
        metadata = run_metadata_stage_controlled(
            con,
            _metadata_tools(
                tools,
                metadata_exiftool=metadata_exiftool,
                metadata_ffprobe=metadata_ffprobe,
            ),
            router,
            retain_original_metadata=(
                metadata_modes["exiftool"] == "complete"
                and metadata_modes["ffprobe"] == "complete"),
            metadata_exiftool=metadata_exiftool,
            metadata_ffprobe=metadata_ffprobe,
            retain_exiftool_payload=(
                metadata_modes["exiftool"] == "complete"),
            retain_ffprobe_payload=(
                metadata_modes["ffprobe"] == "complete"),
            timeout_policy=config.get("exiftool_timeout_policy"),
            show_current_file=show_current_file,
            on_progress=lambda _index, payload: progress(
                "metadata", payload),
            on_event=on_event,
            paused_wait_seconds=paused_wait_seconds,
        )
        stages["metadata"] = metadata
        if metadata["state"] != "completed":
            return {
                "state": metadata["state"],
                "stage": "metadata",
                "stages": stages,
            }

    formatted = run_format_stage_controlled(
        con,
        format_mode,
        tools,
        router,
        sample_percent=config.get("format_sample_percent", 10.0),
        show_current_file=show_current_file,
        on_progress=lambda _index, payload: progress("format", payload),
        on_event=on_event,
        paused_wait_seconds=paused_wait_seconds,
        defer_completion=format_substage is not None,
    )
    stages["format"] = formatted
    if formatted["state"] != "completed":
        return {
            "state": formatted["state"],
            "stage": "format",
            "stages": stages,
        }

    if format_substage is not None:
        subordinate = format_substage(con, router)
        if not isinstance(subordinate, dict):
            raise TypeError("格式从属阶段必须返回 dict")
        stages["format_substage"] = subordinate
        if subordinate.get("state") != "completed":
            return {
                "state": str(subordinate.get("state") or "unknown"),
                "stage": "format",
                "stages": stages,
            }
        primary_processed = int(formatted.get("processed") or 0)
        primary_selected = int(formatted.get("selected") or 0)
        sub_processed = int(subordinate.get("processed") or 0)
        sub_selected = int(subordinate.get("selected") or 0)
        primary_failures = sum(
            int(formatted.get(key) or 0)
            for key in ("invalid", "timeout", "error", "unstable")
        )
        sub_failures = sum(
            int(subordinate.get(key) or 0)
            for key in ("invalid", "timeout", "error")
        )
        dbstate.update_stage_checkpoint(
            con,
            "format",
            "completed",
            items_done=primary_processed + sub_processed,
            items_total=primary_selected + sub_selected,
            error_count=primary_failures + sub_failures,
            current_entry_id=None,
            checkpoint={
                "mode": format_mode,
                "sample_percent": _format_sample_value(
                    config.get("format_sample_percent", 10.0)),
                "eligible": int(formatted.get("eligible") or 0),
                "selected": primary_selected,
                "unsupported": int(formatted.get("unsupported") or 0),
                "primary_completed": True,
                "substage": dict(subordinate),
            },
        )
        _emit_control_event(
            on_event,
            "stage_finished",
            stage="format",
            processed=primary_processed + sub_processed,
            selected=primary_selected + sub_selected,
            invalid=(
                int(formatted.get("invalid") or 0)
                + int(subordinate.get("invalid") or 0)),
            timeout=(
                int(formatted.get("timeout") or 0)
                + int(subordinate.get("timeout") or 0)),
            error=(
                int(formatted.get("error") or 0)
                + int(subordinate.get("error") or 0)),
            raw=dict(subordinate),
        )

    rescanned = run_rescan_stage_controlled(
        con,
        router,
        on_progress=lambda done, total, changed: progress(
            "rescan",
            {"processed": done, "total": total, "changed": changed},
        ),
        on_event=on_event,
        paused_wait_seconds=paused_wait_seconds,
    )
    stages["rescan"] = rescanned
    if rescanned["state"] != "completed":
        return {
            "state": rescanned["state"],
            "stage": "rescan",
            "stages": stages,
        }
    return {
        "state": "completed",
        "stage": "rescan",
        "stages": stages,
    }


def _scan_stage_states(con: sqlite3.Connection) -> dict[str, str]:
    return {
        str(stage): str(state)
        for stage, state in con.execute(
            "SELECT stage,state FROM stage_checkpoints ORDER BY stage_order"
        )
    }


def _require_scan_seal_ready(con: sqlite3.Connection) -> None:
    runtime = dbstate.load_runtime(con)
    if runtime.run_state != "running":
        raise core.PreflightError(
            f"封存要求 running，实际为 {runtime.run_state}")
    states = _scan_stage_states(con)
    required = {
        "enumerate": frozenset(("completed",)),
        "hash": frozenset(("completed", "skipped")),
        "metadata": frozenset(("completed", "skipped")),
        "format": frozenset(("completed", "skipped")),
        "rescan": frozenset(("completed",)),
        "verify_hash": frozenset(("completed", "skipped")),
        "verify_format": frozenset(("completed", "skipped")),
    }
    incomplete = [
        f"{stage}={states.get(stage, 'missing')}"
        for stage, accepted in required.items()
        if states.get(stage) not in accepted
    ]
    if incomplete:
        raise core.PreflightError(
            "封存被拒：阶段尚未形成终态：" + "、".join(incomplete))
    running_attempts = int(con.execute(
        "SELECT COUNT(*) FROM entry_attempts WHERE status='running'"
    ).fetchone()[0])
    residue = int(con.execute(
        "SELECT COUNT(*) FROM entries"
        " WHERE meta_status IN ('pending','processing')"
        " OR hash_status IN ('pending','processing')"
    ).fetchone()[0])
    format_residue = int(con.execute(
        "SELECT COUNT(*) FROM format_checks"
        " WHERE status IN ('pending','processing')"
    ).fetchone()[0])
    if running_attempts or residue or format_residue:
        raise core.PreflightError(
            "封存被拒：仍有未提交边界："
            f"attempt={running_attempts}、entry={residue}、"
            f"format={format_residue}"
        )


def _latest_attempt_counts(
    con: sqlite3.Connection,
) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for stage, status, count in con.execute(
        "SELECT a.stage,a.status,COUNT(*) FROM entry_attempts a"
        " WHERE NOT EXISTS (SELECT 1 FROM entry_attempts newer"
        "  WHERE newer.entry_id=a.entry_id AND newer.stage=a.stage"
        "  AND newer.attempt_number>a.attempt_number)"
        " GROUP BY a.stage,a.status ORDER BY a.stage,a.status"
    ):
        result.setdefault(str(stage), {})[str(status)] = int(count)
    return result


def _collect_v4_snapshot_counts(
    con: sqlite3.Connection,
) -> dict[str, object]:
    counts: dict[str, object] = dict(core.collect_snapshot_counts(con))
    format_status = {
        str(status): int(count)
        for status, count in con.execute(
            "SELECT status,COUNT(*) FROM format_checks"
            " GROUP BY status ORDER BY status"
        )
    }
    performance_confidence = {
        str(confidence): int(count)
        for confidence, count in con.execute(
            "SELECT candidate_confidence,COUNT(*) FROM read_performance"
            " GROUP BY candidate_confidence ORDER BY candidate_confidence"
        )
    }
    attempts = _latest_attempt_counts(con)
    metadata_total = int(con.execute(
        "SELECT COUNT(*) FROM entries").fetchone()[0])
    metadata_status = counts.get("meta_status") or {}
    metadata_applicable = max(
        0,
        metadata_total
        - int(metadata_status.get("not_applicable") or 0)
        - int(metadata_status.get("skipped") or 0),
    )
    metadata_attempted = sum(
        int(metadata_status.get(status) or 0)
        for status in ("done", "error", "timeout", "unstable")
    )
    metadata_successful = int(metadata_status.get("done") or 0)
    metadata_coverage = {
        "total": metadata_total,
        "applicable": metadata_applicable,
        "attempted": metadata_attempted,
        "successful": metadata_successful,
        "not_processed": sum(
            int(metadata_status.get(status) or 0)
            for status in ("pending", "processing")),
        "attempted_percent": (
            round(metadata_attempted / metadata_applicable * 100, 4)
            if metadata_applicable else None),
        "successful_percent": (
            round(metadata_successful / metadata_applicable * 100, 4)
            if metadata_applicable else None),
    }
    tool_error_records = int(con.execute(
        "SELECT COUNT(*) FROM errors"
        " WHERE error_code LIKE 'metadata\\_%\\_tool\\_error' ESCAPE '\\'"
    ).fetchone()[0])
    metadata_source_error_files = int(con.execute(
        "SELECT COUNT(*) FROM entries e WHERE e.meta_status='error' AND ("
        " EXISTS (SELECT 1 FROM errors x WHERE x.entry_id=e.entry_id"
        "  AND x.stage='metadata'"
        "  AND x.error_code NOT LIKE 'metadata\\_%\\_tool\\_error'"
        "  ESCAPE '\\')"
        " OR NOT EXISTS (SELECT 1 FROM errors x WHERE x.entry_id=e.entry_id"
        "  AND x.stage='metadata'))"
    ).fetchone()[0])
    tool_failure_sessions = int(con.execute(
        "SELECT COUNT(*) FROM run_sessions"
        " WHERE end_reason LIKE '%tool%circuit%'")
        .fetchone()[0])
    attempt_tool_errors = int(con.execute(
        "SELECT COUNT(*) FROM entry_attempts"
        " WHERE error_code LIKE '%tool%' OR error_code GLOB 'worker_*'"
    ).fetchone()[0])
    timeout_issues = bool(
        metadata_status.get("timeout")
        or format_status.get("timeout")
        or any(
            status_counts.get("timeout")
            for status_counts in attempts.values()
        )
    )
    has_tool_issues = bool(
        tool_error_records or tool_failure_sessions or attempt_tool_errors)
    has_source_file_issues = bool(
        counts.get("has_enumeration_gaps")
        or (counts.get("hash_status") or {}).get("error")
        or (counts.get("hash_status") or {}).get("unstable")
        or metadata_source_error_files
        or metadata_status.get("unstable")
        or format_status.get("invalid")
        or format_status.get("unstable")
    )
    counts.update({
        "format_status": format_status,
        "latest_attempt_status": attempts,
        "performance_confidence": performance_confidence,
        "metadata_coverage": metadata_coverage,
        "has_source_file_issues": has_source_file_issues,
        "has_tool_issues": has_tool_issues,
        "has_timeout_issues": timeout_issues,
        "tool_error_records": tool_error_records,
        "tool_failure_sessions": tool_failure_sessions,
        "metadata_source_error_files": metadata_source_error_files,
        "sessions": int(con.execute(
            "SELECT COUNT(*) FROM run_sessions").fetchone()[0]),
    })
    hash_status = counts.get("hash_status") or {}
    meta_status = counts.get("meta_status") or {}
    counts["has_file_issues"] = bool(
        counts.get("has_file_issues")
        or hash_status.get("error")
        or meta_status.get("error")
        or meta_status.get("timeout")
        or format_status.get("invalid")
        or format_status.get("error")
        or format_status.get("timeout")
        or has_tool_issues
    )
    counts["has_unstable_entries"] = bool(
        counts.get("has_unstable_entries")
        or format_status.get("unstable")
        or any(
            status_counts.get("unstable")
            for status_counts in attempts.values()
        )
    )
    return counts


def _scan_manifest_payload(
    con: sqlite3.Connection,
    config: dict[str, object],
    tools: dict[str, object],
    counts: dict[str, object],
    supplied: dict[str, object] | None,
) -> dict[str, object]:
    manifest = dict(supplied or {})
    metadata_checkpoint_row = con.execute(
        "SELECT state,checkpoint_json FROM stage_checkpoints"
        " WHERE stage='metadata'"
    ).fetchone()
    metadata_checkpoint: dict[str, object] = {}
    metadata_stage_state = None
    if metadata_checkpoint_row is not None:
        metadata_stage_state = str(metadata_checkpoint_row[0])
        try:
            decoded_checkpoint = json.loads(metadata_checkpoint_row[1] or "{}")
        except (TypeError, ValueError):
            decoded_checkpoint = {}
        if isinstance(decoded_checkpoint, dict):
            metadata_checkpoint = decoded_checkpoint
    manifest.update({
        "scanner_version": str(con.execute(
            "SELECT scanner_version FROM snapshot_info WHERE id=1"
        ).fetchone()[0]),
        "tools": dict(tools),
        "runtime_contract": {
            "data_contract": dbstate.DATA_CONTRACT,
            "resume_contract": dbstate.RESUME_CONTRACT,
            "projection_contract": dbstate.PROJECTION_CONTRACT,
            "stage_storage": "stage_checkpoints",
            "session_storage": "run_sessions",
            "attempt_storage": "entry_attempts",
            "authoritative_event_storage": "run_state_events",
        },
        "verification": {
            "hash": counts.get("latest_attempt_status", {}).get(
                "verify_hash", {}),
            "format": counts.get("latest_attempt_status", {}).get(
                "verify_format", {}),
        },
        "metadata": {
            "stage_state": metadata_stage_state,
            "selected_tools": {
                "exiftool": config.get("metadata_exiftool", True),
                "ffprobe": config.get("metadata_ffprobe", True),
            },
            "tool_modes": metadata_tool_modes(config),
            "coverage": counts.get("metadata_coverage", {}),
            "has_source_file_issues": bool(
                counts.get("has_source_file_issues")),
            "has_tool_issues": bool(counts.get("has_tool_issues")),
            "has_timeout_issues": bool(counts.get("has_timeout_issues")),
            "tool_runtime": metadata_checkpoint.get("tool_runtime", {}),
        },
        "format_validation": {
            "mode": config.get("format_validation", "off"),
            "status": counts.get("format_status", {}),
        },
        "performance_analysis": {
            "storage": "read_performance",
            "confidence": counts.get("performance_confidence", {}),
            "physical_location_claimed": False,
        },
    })
    return manifest


def _skip_scan_verify_format_stage(con: sqlite3.Connection) -> None:
    state = str(con.execute(
        "SELECT state FROM stage_checkpoints WHERE stage='verify_format'"
    ).fetchone()[0])
    if state in ("completed", "skipped"):
        return
    if state != "pending":
        raise core.PreflightError(
            f"扫描封存不能解释 verify_format={state}")
    dbstate.update_stage_checkpoint(
        con,
        "verify_format",
        "skipped",
        items_done=0,
        items_total=0,
        current_entry_id=None,
        checkpoint={
            "reason": "not_applicable_scan_pipeline",
            "format_stage": "format",
        },
    )


def seal_and_publish_scan(
    handle: RunHandle,
    *,
    staging_path: str | None = None,
    manifest: dict[str, object] | None = None,
    issue_report_builder=dbissues.build_snapshot_issue_report_from_connection,
    additional_artifact_builder: Callable[
        [sqlite3.Connection, str, str], Mapping[str, bytes]
    ] | None = None,
    remove_event_log: bool = True,
    now_utc: str | None = None,
    on_event: Callable[..., None] | None = None,
) -> dbstate.PublicationResult:
    """验证终态、封存 schema 4 未完成快照，并以不覆盖方式发布数据库和问题报告。"""
    con = handle.connection
    _require_scan_seal_ready(con)
    runtime = dbstate.load_runtime(con)
    session_ended, config, tools = _session_payload(
        con, runtime.active_session_id)
    if session_ended:
        raise core.PreflightError("当前扫描会话已结束，不能封存")
    hash_coverage = str(config.get("hash") or "none")
    if hash_coverage not in ("none", "incremental", "full"):
        raise core.PreflightError(
            f"冻结的 hash coverage 无效：{hash_coverage!r}")
    staging = _normalized(
        staging_path or runtime.publish_stem_path + ".publishing.sqlite")
    if os.path.normcase(os.path.dirname(staging)) != os.path.normcase(
            runtime.output_dir):
        raise core.PreflightError("发布暂存文件必须位于冻结的输出目录")
    if os.path.normcase(staging) in {
        os.path.normcase(runtime.partial_path),
        os.path.normcase(runtime.event_log_path),
        os.path.normcase(runtime.publish_stem_path),
        os.path.normcase(handle.lease_path),
    }:
        raise core.PreflightError("发布暂存文件与冻结的运行路径冲突")

    seal_started = False
    sealed = False
    connection_closed = False
    finished_at = str(now_utc or core.now_utc_iso())
    try:
        dbstate.update_stage_checkpoint(
            con,
            "seal",
            "running",
            items_done=0,
            items_total=1,
            current_entry_id=None,
        )
        dbstate.begin_sealing(con, now_utc=finished_at)
        seal_started = True
        _emit_control_event(on_event, "stage_started", stage="seal")
        counts = _collect_v4_snapshot_counts(con)
        con.execute(
            "UPDATE snapshot_info SET has_file_issues=?,"
            " has_unstable_entries=?,has_enumeration_gaps=?,"
            " hash_coverage=?,counts_json=? WHERE id=1",
            (
                int(bool(counts["has_file_issues"])),
                int(bool(counts["has_unstable_entries"])),
                int(bool(counts["has_enumeration_gaps"])),
                hash_coverage,
                json.dumps(counts, ensure_ascii=False),
            ),
        )
        core._embed_snapshot_evidence(
            con,
            runtime.publish_stem_path,
            hash_coverage,
            counts,
            finished_at,
            runtime.event_log_path,
            _scan_manifest_payload(con, config, tools, counts, manifest),
        )
        dbstate.update_stage_checkpoint(
            con,
            "seal",
            "completed",
            items_done=1,
            items_total=1,
            current_entry_id=None,
            checkpoint={
                "integrity": "pending_final_check",
                "manifest": "snapshot_manifest",
            },
            now_utc=finished_at,
        )
        dbstate.update_stage_checkpoint(
            con,
            "publish",
            "running",
            items_done=0,
            items_total=1,
            current_entry_id=None,
            checkpoint={"method": "sqlite_backup_no_clobber"},
            now_utc=finished_at,
        )
        con.commit()
        integrity = con.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise core.PreflightError(
                f"SQLite 完整性检查失败：{integrity}")
        foreign_key_error = con.execute(
            "PRAGMA foreign_key_check").fetchone()
        if foreign_key_error is not None:
            raise core.PreflightError(
                "SQLite 外键检查失败：" + str(tuple(foreign_key_error)))
        dbstate.update_stage_checkpoint(
            con,
            "seal",
            "completed",
            items_done=1,
            items_total=1,
            current_entry_id=None,
            checkpoint={
                "integrity": "ok",
                "foreign_keys": "ok",
                "manifest": "snapshot_manifest",
            },
            now_utc=finished_at,
        )
        dbstate.mark_sealed_unpublished(con, now_utc=finished_at)
        sealed = True
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.execute("PRAGMA journal_mode=DELETE")
        con.close()
        connection_closed = True
        _emit_control_event(
            on_event, "stage_finished", stage="seal", state="completed")
    except Exception as exc:
        if sealed and not connection_closed:
            try:
                con.close()
                connection_closed = True
            except sqlite3.Error:
                pass
        if seal_started and not sealed and not connection_closed:
            try:
                dbstate.update_stage_checkpoint(
                    con,
                    "seal",
                    "failed_recoverable",
                    current_entry_id=None,
                    checkpoint={"reason": "seal_failed"},
                )
                dbstate.fail_run(
                    con,
                    recoverable=True,
                    error_code="seal_failed",
                    error_message=str(exc),
                )
            except Exception:
                pass
        raise

    publication = dbstate.publish_sealed_snapshot(
        runtime.partial_path,
        staging,
        lease_path=handle.lease_path,
        lease_id=handle.lease.lease_id,
        now_utc=finished_at,
        issue_report_builder=issue_report_builder,
        additional_artifact_builder=additional_artifact_builder,
    )
    warnings = list(publication.warnings)
    if remove_event_log and os.path.exists(runtime.event_log_path):
        try:
            os.remove(runtime.event_log_path)
        except OSError as exc:
            warnings.append(
                f"最终快照已发布，但临时事件日志未删除：{exc}")
    if warnings != list(publication.warnings):
        publication = dbstate.PublicationResult(
            final_path=publication.final_path,
            sha256=publication.sha256,
            partial_removed=publication.partial_removed,
            lease_released=publication.lease_released,
            warnings=tuple(warnings),
            issue_report_path=publication.issue_report_path,
            artifact_paths=publication.artifact_paths,
        )
    _emit_control_event(
        on_event,
        "stage_finished",
        stage="publish",
        state="published",
        final_path=publication.final_path,
        issue_report_path=publication.issue_report_path,
    )
    return publication


def run_scan_completion_stages(
    handle: RunHandle,
    router: RunCommandRouter,
    *,
    show_current_file: bool = False,
    hash_stall_seconds: float = dbhash.HASH_STALL_SECONDS,
    hash_timeout_seconds: float | None = None,
    hash_default_decision: str = "continue_waiting",
    hash_poll_seconds: float = 0.05,
    paused_wait_seconds: float = 0.25,
    staging_path: str | None = None,
    manifest: dict[str, object] | None = None,
    issue_report_builder=dbissues.build_snapshot_issue_report_from_connection,
    additional_artifact_builder: Callable[
        [sqlite3.Connection, str, str], Mapping[str, bytes]
    ] | None = None,
    on_progress: Callable[[str, dict[str, object]], None] | None = None,
    on_event: Callable[..., None] | None = None,
    on_threshold: Callable[
        [dict[str, object], dbhash.AtomicTimeoutDecision], None] | None = None,
    before_seal: Callable[[], None] | None = None,
    _independent_runner=None,
    _primary_runner=None,
) -> dict[str, object]:
    """完成哈希复检、扫描专用阶段收尾、封存与发布。"""
    con = handle.connection
    runtime = dbstate.load_runtime(con)
    if runtime.run_state != "running":
        raise core.PreflightError(
            f"扫描收尾要求 running，实际为 {runtime.run_state}")
    session_ended, config, tools = _session_payload(
        con, runtime.active_session_id)
    if session_ended:
        raise core.PreflightError("当前扫描会话已结束，不能完成扫描")
    powershell = tools.get("powershell")
    powershell_path = ""
    powershell_version = ""
    if isinstance(powershell, dict):
        powershell_path = str(powershell.get("path") or "")
        powershell_version = str(powershell.get("version") or "")
    verification = run_independent_hash_stage_controlled(
        con,
        router,
        percent=config.get("verify_sample_percent", 1.0),
        min_count=100,
        powershell_path=powershell_path,
        powershell_version=powershell_version,
        show_current_file=show_current_file,
        stall_seconds=hash_stall_seconds,
        timeout_seconds=hash_timeout_seconds,
        default_decision=hash_default_decision,
        on_progress=(
            None if on_progress is None else
            lambda _index, payload: on_progress("verify_hash", payload)
        ),
        on_event=on_event,
        on_threshold=on_threshold,
        poll_seconds=hash_poll_seconds,
        paused_wait_seconds=paused_wait_seconds,
        _independent_runner=_independent_runner,
        _primary_runner=_primary_runner,
    )
    if verification["state"] != "completed":
        return {
            "state": verification["state"],
            "stage": "verify_hash",
            "verification": verification,
        }
    performance = dbhash.classify_read_performance_candidates(con)
    _emit_control_event(
        on_event,
        "performance_analysis_finished",
        stage="verify_hash",
        **performance,
    )
    _skip_scan_verify_format_stage(con)
    if before_seal is not None:
        before_seal()
    publication = seal_and_publish_scan(
        handle,
        staging_path=staging_path,
        manifest=manifest,
        issue_report_builder=issue_report_builder,
        additional_artifact_builder=additional_artifact_builder,
        on_event=on_event,
    )
    return {
        "state": "published",
        "stage": "publish",
        "verification": verification,
        "performance": performance,
        "publication": publication,
    }


def run_scan_to_publication(
    handle: RunHandle,
    router: RunCommandRouter,
    *,
    show_current_file: bool = False,
    on_progress: Callable[[str, dict[str, object]], None] | None = None,
    on_event: Callable[..., None] | None = None,
    paused_wait_seconds: float = 0.25,
    hash_stall_seconds: float = dbhash.HASH_STALL_SECONDS,
    hash_timeout_seconds: float | None = None,
    hash_default_decision: str = "continue_waiting",
    hash_retry_mode: str = "pending",
    hash_poll_seconds: float = 0.05,
    staging_path: str | None = None,
    manifest: dict[str, object] | None = None,
    issue_report_builder=dbissues.build_snapshot_issue_report_from_connection,
    additional_artifact_builder: Callable[
        [sqlite3.Connection, str, str], Mapping[str, bytes]
    ] | None = None,
    format_substage: Callable[
        [sqlite3.Connection, RunCommandRouter], dict[str, object]
    ] | None = None,
    on_threshold: Callable[
        [dict[str, object], dbhash.AtomicTimeoutDecision], None] | None = None,
    before_seal: Callable[[], None] | None = None,
    _hash_worker_target=None,
    _independent_runner=None,
    _primary_runner=None,
) -> dict[str, object]:
    """运行完整 schema 4 扫描生产链，非终态控制结果不进入封存。"""
    evidence = run_scan_evidence_stages(
        handle,
        router,
        show_current_file=show_current_file,
        on_progress=on_progress,
        on_event=on_event,
        paused_wait_seconds=paused_wait_seconds,
        hash_stall_seconds=hash_stall_seconds,
        hash_timeout_seconds=hash_timeout_seconds,
        hash_default_decision=hash_default_decision,
        hash_retry_mode=hash_retry_mode,
        hash_poll_seconds=hash_poll_seconds,
        format_substage=format_substage,
        _hash_worker_target=_hash_worker_target,
    )
    if evidence["state"] != "completed":
        return {
            "state": evidence["state"],
            "stage": evidence["stage"],
            "evidence": evidence,
        }
    completion = run_scan_completion_stages(
        handle,
        router,
        show_current_file=show_current_file,
        hash_stall_seconds=hash_stall_seconds,
        hash_timeout_seconds=hash_timeout_seconds,
        hash_default_decision=hash_default_decision,
        hash_poll_seconds=hash_poll_seconds,
        paused_wait_seconds=paused_wait_seconds,
        staging_path=staging_path,
        manifest=manifest,
        issue_report_builder=issue_report_builder,
        additional_artifact_builder=additional_artifact_builder,
        on_progress=on_progress,
        on_event=on_event,
        on_threshold=on_threshold,
        before_seal=before_seal,
        _independent_runner=_independent_runner,
        _primary_runner=_primary_runner,
    )
    completion["evidence"] = evidence
    return completion

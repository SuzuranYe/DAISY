"""只供工作区内哈希 worker 测试使用的可控子进程入口。"""
from __future__ import annotations

import os
import time


def _result(size: int) -> dict[str, object]:
    return {
        "hash_hex": "00" * 32,
        "bytes_read": size,
        "chunk_bytes": 1,
        "started_at_utc": "2026-08-06T00:00:00.000000Z",
        "finished_at_utc": "2026-08-06T00:00:01.000000Z",
        "pre_size": size,
        "pre_mtime_utc": "2026-08-06T00:00:00.000000Z",
        "post_size": size,
        "post_mtime_utc": "2026-08-06T00:00:00.000000Z",
        "status": "valid",
        "failure_reason": None,
    }


def blocking_worker(connection, _path, _expected_size, _chunk_bytes) -> None:
    try:
        connection.send({"kind": "ready"})
        while True:
            time.sleep(60)
    finally:
        connection.close()


def progressive_worker(connection, _path, expected_size, _chunk_bytes) -> None:
    size = int(expected_size or 5)
    try:
        connection.send({"kind": "ready"})
        for offset in range(1, size + 1):
            time.sleep(0.03)
            connection.send({
                "kind": "progress",
                "bytes_read": offset,
                "active_read_seconds": offset * 0.01,
            })
        connection.send({
            "kind": "result",
            "result": _result(size),
            "active_read_seconds": size * 0.01,
        })
    finally:
        connection.close()


def delayed_worker(connection, _path, expected_size, _chunk_bytes) -> None:
    size = int(expected_size or 1)
    try:
        connection.send({"kind": "ready"})
        time.sleep(0.08)
        connection.send({
            "kind": "result",
            "result": _result(size),
            "active_read_seconds": 0.01,
        })
    finally:
        connection.close()


def crashing_worker(connection, _path, _expected_size, _chunk_bytes) -> None:
    connection.close()
    os._exit(7)

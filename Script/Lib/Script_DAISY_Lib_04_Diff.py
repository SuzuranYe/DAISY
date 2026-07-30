"""DAISY 快照对比引擎。

实现快照准入、root 配对、枚举失败传播、状态优先级、证据等级、
移动／复制／硬链接分组和目录维度对比。精确 DDL 以本文件为准。
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
import sqlite3
import sys
import uuid as uuid_mod
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import Script_DAISY_Lib_01_Core as core

# Diff 库 DDL（精确定义以本处为准）
DIFF_DDL = r"""
PRAGMA foreign_keys = ON;

CREATE TABLE diff_info (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    diff_uuid         TEXT    NOT NULL UNIQUE,
    schema_version    INTEGER NOT NULL,
    old_snapshot_uuid TEXT    NOT NULL,
    new_snapshot_uuid TEXT    NOT NULL,
    old_snapshot_file TEXT    NOT NULL,
    new_snapshot_file TEXT    NOT NULL,
    old_hash_coverage TEXT    NOT NULL,
    new_hash_coverage TEXT    NOT NULL,
    root_mapping_json TEXT    NOT NULL,
    forced            INTEGER NOT NULL DEFAULT 0,
    tool_version      TEXT    NOT NULL,
    created_at_utc    TEXT    NOT NULL,
    counts_json       TEXT
);

CREATE TABLE diff_subtrees (
    subtree_id        INTEGER PRIMARY KEY,
    side              TEXT    NOT NULL CHECK (side IN ('old','new')),
    root_label        TEXT,
    rel_path          TEXT    NOT NULL,
    enum_status       TEXT    NOT NULL,
    affected_estimate INTEGER
);

CREATE TABLE diff_dirs (
    diff_dir_id  INTEGER PRIMARY KEY,
    path_key     TEXT    NOT NULL,
    old_root_label TEXT,
    new_root_label TEXT,
    old_rel_path TEXT,
    new_rel_path TEXT,
    status       TEXT    NOT NULL
                 CHECK (status IN ('unchanged','added','deleted','enum_status_changed','unknown')),
    old_enum_status TEXT,
    new_enum_status TEXT,
    reason       TEXT,
    CHECK (old_rel_path IS NOT NULL OR new_rel_path IS NOT NULL)
);

CREATE TABLE diff_hash_groups (
    group_id           INTEGER PRIMARY KEY,
    hash_hex           TEXT    NOT NULL UNIQUE,
    old_count          INTEGER NOT NULL,
    new_count          INTEGER NOT NULL,
    old_hardlink_sets  INTEGER,
    new_hardlink_sets  INTEGER,
    classification     TEXT
);

CREATE TABLE diff_entries (
    diff_entry_id    INTEGER PRIMARY KEY,
    path_key         TEXT,
    old_root_label   TEXT,
    new_root_label   TEXT,
    old_rel_path     TEXT,
    new_rel_path     TEXT,
    status           TEXT    NOT NULL
                     CHECK (status IN ('unchanged','stat_changed_content_same',
                            'metadata_extraction_changed','content_changed','added','deleted',
                            'moved_or_renamed','copied','hash_missing','unstable','unknown')),
    evidence         TEXT    NOT NULL
                     CHECK (evidence IN ('independent_computation','propagated_single_computation',
                            'heuristic_file_id','stat_only','insufficient')),
    reason           TEXT,
    old_size         INTEGER,
    new_size         INTEGER,
    old_mtime_utc    TEXT,
    new_mtime_utc    TEXT,
    old_hash_hex     TEXT,
    new_hash_hex     TEXT,
    old_hash_origin  TEXT,
    new_hash_origin  TEXT,
    metadata_changed INTEGER,
    group_id         INTEGER REFERENCES diff_hash_groups(group_id),
    CHECK (old_rel_path IS NOT NULL OR new_rel_path IS NOT NULL)
);

CREATE INDEX idx_diff_status ON diff_entries(status);
CREATE INDEX idx_diff_group  ON diff_entries(group_id);
"""


# === 准入与载入 ===
def _check_and_open(path: str, force: bool) -> tuple[sqlite3.Connection, int]:
    path = os.path.abspath(path)
    if path.endswith(".partial.sqlite") or not path.endswith(".sqlite"):
        raise core.PreflightError(f"输入必须是封存快照 .sqlite：{path}")
    if not os.path.isfile(path):
        raise core.PreflightError(f"快照不存在：{path}")
    recorded = core.filename_sha256_high32(path)
    forced = 0
    if recorded is not None:
        if recorded != core.sha256_file(path)[:8].upper():
            raise core.PreflightError(
                f"文件名 SHA-256 高32bit 指纹不符（硬性项，--force 不可越过）：{path}")
    elif force:
        forced = 1                       # 软性项：仅文件名指纹缺失可越过
    else:
        raise core.PreflightError(
            f"文件名缺少 SHA-256 高32bit 指纹（--force 可越过）：{path}")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    ok, = con.execute("PRAGMA integrity_check").fetchone()
    if ok != "ok":
        con.close()
        raise core.PreflightError(f"SQLite 完整性检查失败：{path}")
    status, = con.execute("SELECT scan_status FROM snapshot_info").fetchone()
    if status != "complete":
        con.close()
        raise core.PreflightError(f"快照未封存（scan_status={status}）：{path}")
    return con, forced


def _event(side_uuid: str, hrec: dict) -> tuple:
    """返回哈希记录的最初计算事件。"""
    if hrec["origin"] == "computed":
        return (side_uuid, hrec["fin"])
    return hrec["src"]


def load_side(path: str, force: bool = False) -> dict:
    con, forced = _check_and_open(path, force)
    try:
        uuid_, schema_v, pk_rule, coverage = con.execute(
            "SELECT snapshot_uuid, schema_version, path_key_rule,"
            " hash_coverage FROM snapshot_info").fetchone()
        roots = {rid: {"label": lb, "path": rp, "enum": st}
                 for rid, lb, rp, st in con.execute(
                     "SELECT root_id, root_label, root_path, enum_status"
                     " FROM roots")}
        dirs: dict = {}
        for rid, rel, pk, st in con.execute(
                "SELECT root_id, rel_path, path_key, enum_status FROM dirs"):
            dirs.setdefault(rid, {})[pk] = {"rel": rel, "enum": st}
        hashes: dict = {}
        for eid, hx, origin, su, st_, fin, hst in con.execute(
                "SELECT entry_id, hash_hex, origin, source_snapshot_uuid,"
                " source_computed_at_utc, finished_at_utc, status FROM hashes"
                " WHERE algorithm='sha256'"):
            hashes[eid] = {"hex": hx, "origin": origin, "src": (su, st_),
                           "fin": fin, "status": hst}
        payloads: dict = {}
        n_payload = 0
        for eid, prov, sha, ver in con.execute(
                "SELECT entry_id, provider, payload_sha256, provider_version"
                " FROM raw_payloads"):
            payloads.setdefault(eid, {})[prov] = (sha, ver)
            n_payload += 1
        entries: dict = {}
        hash_count: dict = {}
        hash_fileids: dict = {}
        hash_events: dict = {}
        for (eid, rid, rel, pk, size, mtime, attrs, ph, ms, hs, vs,
             fih) in con.execute(
                "SELECT entry_id, root_id, rel_path, path_key, size_bytes,"
                " modified_at_utc, attributes, is_placeholder, meta_status,"
                " hash_status, volume_serial, file_index_hex FROM entries"):
            h = hashes.get(eid)
            valid = h if (h and h["status"] == "valid") else None
            e = {"eid": eid, "rid": rid, "rel": rel, "pk": pk, "size": size,
                 "mtime": mtime, "attrs": attrs, "ph": ph,
                 "unstable": (ms == "unstable" or hs == "unstable"
                              or (h is not None and h["status"] == "unstable")),
                 "hash": valid, "payloads": payloads.get(eid) or {},
                 "vs": vs, "fih": fih}
            entries.setdefault(rid, {}).setdefault(pk, []).append(e)
            if valid:
                hx = valid["hex"]
                hash_count[hx] = hash_count.get(hx, 0) + 1
                hash_events.setdefault(hx, set()).add(_event(uuid_, valid))
                if vs and fih:
                    hash_fileids.setdefault(hx, []).append((vs, fih))
        return {"path": path, "file": os.path.basename(path), "uuid": uuid_,
                "schema": schema_v, "pk_rule": pk_rule, "coverage": coverage,
                "roots": roots, "dirs": dirs, "entries": entries,
                "hash_count": hash_count, "hash_fileids": hash_fileids,
                "hash_events": hash_events, "n_payload": n_payload,
                "forced": forced}
    finally:
        con.close()


def _root_consistency(side: dict) -> None:
    """一致性检查：failed root 必须有失败的根目录行。"""
    for rid, r in side["roots"].items():
        if r["enum"] == "failed":
            rootdir = side["dirs"].get(rid, {}).get("")
            if rootdir is None or rootdir["enum"] == "ok":
                raise core.PreflightError(
                    f"快照数据不一致：root「{r['label']}」级失败未落根目录行，"
                    "请重建快照")


def _failed_pks(side: dict) -> dict:
    """rid → 枚举失败目录 path_key 列表（含 not_enumerated 等一切非 ok）。"""
    res = {}
    for rid, d in side["dirs"].items():
        f = [pk for pk, v in d.items() if v["enum"] != "ok"]
        if f:
            res[rid] = f
    return res


def _under(pk: str, failed: list[str]) -> bool:
    for f in failed:
        if f == "" or pk == f or pk.startswith(f + "/"):
            return True
    return False


_META_EXIFTOOL_PENDING = object()
_VOLATILE_EXIFTOOL_TAGS = {
    "sourcefile",
    "directory",
    "fileaccessdate",
}


def _meta_eval(po: dict, pn: dict):
    """先比较摘要与版本；同版 ExifTool 差异延后排除环境字段复核。"""
    common = set(po) & set(pn)
    if not common:
        return None
    exiftool_pending = False
    for provider in common:
        old_sha, old_version = po[provider]
        new_sha, new_version = pn[provider]
        if old_sha == new_sha and old_version == new_version:
            continue
        if provider == "exiftool" and old_version == new_version:
            exiftool_pending = True
            continue
        return 1
    return _META_EXIFTOOL_PENDING if exiftool_pending else 0


def _drop_volatile_exiftool_tags(value):
    """复制 JSON 结构，同时移除访问时间与提取目标路径等环境字段。"""
    if isinstance(value, dict):
        return {
            key: _drop_volatile_exiftool_tags(item)
            for key, item in value.items()
            if not (
                isinstance(key, str)
                and key.rsplit(":", 1)[-1].casefold()
                in _VOLATILE_EXIFTOOL_TAGS
            )
        }
    if isinstance(value, list):
        return [_drop_volatile_exiftool_tags(item) for item in value]
    return value


def _stable_exiftool_digest(con: sqlite3.Connection, entry_id: int,
                            cache: dict[int, str | None]) -> str | None:
    """按需计算排除易变标签后的摘要；载荷异常时返回 None，维持保守判异。"""
    if entry_id in cache:
        return cache[entry_id]
    row = con.execute(
        "SELECT payload_zlib FROM raw_payloads"
        " WHERE entry_id=? AND provider='exiftool'", (entry_id,)
    ).fetchone()
    digest = None
    if row is not None:
        try:
            raw = zlib.decompress(row[0])
            document = json.loads(raw.decode("utf-8"))
            stable = _drop_volatile_exiftool_tags(document)
            canonical = json.dumps(
                stable, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            digest = hashlib.sha256(canonical).hexdigest()
        except (OSError, sqlite3.Error, TypeError, ValueError,
                UnicodeError, zlib.error):
            pass
    cache[entry_id] = digest
    return digest


def _resolve_exiftool_candidates(old_path: str, new_path: str,
                                 candidates: list[tuple[dict, int, int]]) -> None:
    """只为摘要不同的候选项读取原载荷，并就地修正 Diff 行。"""
    if not candidates:
        return
    old_cache: dict[int, str | None] = {}
    new_cache: dict[int, str | None] = {}
    with closing(sqlite3.connect(
            f"file:{old_path}?mode=ro", uri=True)) as old_con, \
            closing(sqlite3.connect(
                f"file:{new_path}?mode=ro", uri=True)) as new_con:
        for row, old_eid, new_eid in candidates:
            old_digest = _stable_exiftool_digest(
                old_con, old_eid, old_cache)
            new_digest = _stable_exiftool_digest(
                new_con, new_eid, new_cache)
            same = old_digest is not None and old_digest == new_digest
            row["metadata_changed"] = 0 if same else 1
            if same and row["status"] == "metadata_extraction_changed":
                row["status"] = "unchanged"


def _hash_evidence(old: dict, new: dict, oh: dict, nh: dict) -> str:
    return ("propagated_single_computation"
            if _event(old["uuid"], oh) == _event(new["uuid"], nh)
            else "independent_computation")


def _hardlink_sets(fileids: list[tuple]) -> int:
    seen: dict = {}
    for fid in fileids:
        seen[fid] = seen.get(fid, 0) + 1
    return sum(1 for c in seen.values() if c >= 2)


def _classify_pair(old: dict, new: dict, o: dict, n: dict) -> tuple:
    """对同路径的两侧条目分类，返回状态、证据、原因和元数据变化。"""
    meta = _meta_eval(o["payloads"], n["payloads"])
    if o["unstable"] or n["unstable"]:
        return "unstable", "insufficient", "unstable", meta
    if o["size"] != n["size"]:
        return "content_changed", "stat_only", None, meta
    oh, nh = o["hash"], n["hash"]
    if oh and nh:
        ev = _hash_evidence(old, new, oh, nh)
        if oh["hex"] != nh["hex"]:
            return "content_changed", ev, None, meta
        if o["mtime"] != n["mtime"] or o["attrs"] != n["attrs"]:
            return "stat_changed_content_same", ev, None, meta
        if meta in (1, _META_EXIFTOOL_PENDING):
            return "metadata_extraction_changed", ev, None, meta
        return "unchanged", ev, None, meta
    return "hash_missing", "insufficient", None, meta   # 禁止 size+mtime 判同


class _Item:
    """待定单侧条目（初判 added/deleted）。"""
    __slots__ = ("side", "e", "label", "base_reason", "row")

    def __init__(self, side, e, label, base_reason=None):
        self.side = side              # 'old'（deleted 候选）或 'new'（added 候选）
        self.e = e
        self.label = label
        self.base_reason = base_reason
        self.row = None               # 定案后填 dict


def compare(old_path: str, new_path: str, out_path: str,
            map_root: dict | None = None, force: bool = False) -> dict:
    """执行对比，写出 Diff 数据库，返回计数摘要。"""
    old = load_side(old_path, force)
    new = load_side(new_path, force)
    if old["schema"] != new["schema"] or old["pk_rule"] != new["pk_rule"]:
        raise core.PreflightError(
            f"schema_version/path_key_rule 双侧不等（硬性项）："
            f"{old['schema']}/{old['pk_rule']} vs {new['schema']}/{new['pk_rule']}")
    if old["schema"] != core.SCHEMA_VERSION:
        raise core.PreflightError(
            f"快照 schema_version={old['schema']} 非本工具支持的"
            f" {core.SCHEMA_VERSION}")
    _root_consistency(old)
    _root_consistency(new)

    # 按 root label 配对
    mapping = map_root or {}
    old_lb = {r["label"]: rid for rid, r in old["roots"].items()}
    new_lb = {r["label"]: rid for rid, r in new["roots"].items()}
    pairs = []
    unpaired_old = []
    for lb, orid in sorted(old_lb.items()):
        target = mapping.get(lb, lb)
        if target in new_lb:
            pairs.append((orid, new_lb[target], lb, target))
        else:
            unpaired_old.append(orid)
    mapped_targets = {mapping.get(lb, lb) for lb in old_lb}
    unpaired_new = [rid for lb, rid in sorted(new_lb.items())
                    if lb not in mapped_targets]
    # 单根自动配对：零配对且两侧各恰一个 root 时，
    # 直接对比挂载内容——效果等同自动 --map-root；多 root 无唯一解，不猜。
    auto_paired = None
    if not pairs and len(old_lb) == 1 and len(new_lb) == 1:
        (olb, orid), = old_lb.items()
        (nlb, nrid), = new_lb.items()
        pairs.append((orid, nrid, olb, nlb))
        unpaired_old = []
        unpaired_new = []
        auto_paired = [olb, nlb]

    failed_old = _failed_pks(old)
    failed_new = _failed_pks(new)
    o2n = {orid: nrid for orid, nrid, _, _ in pairs}
    n2o = {nrid: orid for orid, nrid, _, _ in pairs}

    # === 逐文件分类 ===
    rows: list[dict] = []
    pending: list[_Item] = []
    exiftool_candidates: list[tuple[dict, int, int]] = []

    def base_row(**kw):
        r = {"path_key": None, "old_root_label": None, "new_root_label": None,
             "old_rel_path": None, "new_rel_path": None, "status": None,
             "evidence": "insufficient", "reason": None,
             "old_size": None, "new_size": None,
             "old_mtime_utc": None, "new_mtime_utc": None,
             "old_hash_hex": None, "new_hash_hex": None,
             "old_hash_origin": None, "new_hash_origin": None,
             "metadata_changed": None, "group_id": None}
        r.update(kw)
        return r

    def fill_old(r, e, label):
        r.update(old_root_label=label, old_rel_path=e["rel"],
                 old_size=e["size"], old_mtime_utc=e["mtime"])
        if e["hash"]:
            r.update(old_hash_hex=e["hash"]["hex"],
                     old_hash_origin=e["hash"]["origin"])

    def fill_new(r, e, label):
        r.update(new_root_label=label, new_rel_path=e["rel"],
                 new_size=e["size"], new_mtime_utc=e["mtime"])
        if e["hash"]:
            r.update(new_hash_hex=e["hash"]["hex"],
                     new_hash_origin=e["hash"]["origin"])

    for orid, nrid, olb, nlb in pairs:
        o_by = old["entries"].get(orid, {})
        n_by = new["entries"].get(nrid, {})
        ofail = failed_old.get(orid, [])
        nfail = failed_new.get(nrid, [])
        for pk in sorted(set(o_by) | set(n_by)):
            olist = o_by.get(pk, [])
            nlist = n_by.get(pk, [])
            if len(olist) > 1 or len(nlist) > 1:      # path_key 碰撞组
                for e in olist:
                    r = base_row(path_key=pk, status="unknown",
                                 reason="path_key_collision")
                    fill_old(r, e, olb)
                    rows.append(r)
                for e in nlist:
                    r = base_row(path_key=pk, status="unknown",
                                 reason="path_key_collision")
                    fill_new(r, e, nlb)
                    rows.append(r)
                continue
            under = [x for x, c in (("enum_failed_old", _under(pk, ofail)),
                                    ("enum_failed_new", _under(pk, nfail)))
                     if c]
            if under:                                  # 枚举失败向子树传播
                r = base_row(path_key=pk, status="unknown",
                             reason=",".join(under))
                if olist:
                    fill_old(r, olist[0], olb)
                if nlist:
                    fill_new(r, nlist[0], nlb)
                rows.append(r)
                continue
            if olist and nlist:
                o, n = olist[0], nlist[0]
                status, ev, reason, meta = _classify_pair(old, new, o, n)
                if o["rel"] != n["rel"]:               # 大小写或规范化形式变化
                    reason = (reason + "," if reason else "") \
                        + "case_or_form_rename"
                r = base_row(path_key=pk, status=status, evidence=ev,
                             reason=reason, metadata_changed=meta)
                fill_old(r, o, olb)
                fill_new(r, n, nlb)
                rows.append(r)
                if meta == _META_EXIFTOOL_PENDING:
                    exiftool_candidates.append((r, o["eid"], n["eid"]))
            elif olist:
                pending.append(_Item("old", olist[0], olb))
            else:
                pending.append(_Item("new", nlist[0], nlb))

    _resolve_exiftool_candidates(
        old["path"], new["path"], exiftool_candidates)

    for orid in unpaired_old:
        lb = old["roots"][orid]["label"]
        for pk in sorted(old["entries"].get(orid, {})):
            for e in old["entries"][orid][pk]:
                pending.append(_Item("old", e, lb, "unpaired_root"))
    for nrid in unpaired_new:
        lb = new["roots"][nrid]["label"]
        for pk in sorted(new["entries"].get(nrid, {})):
            for e in new["entries"][nrid][pk]:
                pending.append(_Item("new", e, lb, "unpaired_root"))

    # === 移动／复制／硬链接：按全快照哈希多重集计数 ===
    groups: dict = {}
    for it in pending:
        if it.e["hash"]:
            groups.setdefault(it.e["hash"]["hex"],
                              {"old": [], "new": []})[it.side].append(it)
    group_rows: list[dict] = []
    group_ids: dict = {}
    for i, hx in enumerate(sorted(groups), start=1):
        group_ids[hx] = i
        m = old["hash_count"].get(hx, 0)
        n_ = new["hash_count"].get(hx, 0)
        group_rows.append(
            {"group_id": i, "hash_hex": hx, "old_count": m, "new_count": n_,
             "old_hardlink_sets":
                 _hardlink_sets(old["hash_fileids"].get(hx, [])),
             "new_hardlink_sets":
                 _hardlink_sets(new["hash_fileids"].get(hx, [])),
             "classification": f"{m}->{n_}"})
    for hx, g in groups.items():
        gid = group_ids[hx]
        m = old["hash_count"].get(hx, 0)
        adds = sorted(g["new"], key=lambda it: it.e["pk"])
        dels = sorted(g["old"], key=lambda it: it.e["pk"])
        k = min(len(adds), len(dels))
        for i in range(k):                 # 组内按 path_key 升序机械配对
            o_it, n_it = dels[i], adds[i]
            ev = _hash_evidence(old, new, o_it.e["hash"], n_it.e["hash"])
            r = base_row(path_key=n_it.e["pk"], status="moved_or_renamed",
                         evidence=ev, reason="group_paired", group_id=gid)
            fill_old(r, o_it.e, o_it.label)
            fill_new(r, n_it.e, n_it.label)
            o_it.row = n_it.row = r
            rows.append(r)
        new_fileids = new["hash_fileids"].get(hx, [])
        for n_it in adds[k:]:
            e = n_it.e
            is_hardlink = (e["vs"] and e["fih"] and
                           new_fileids.count((e["vs"], e["fih"])) >= 2)
            if is_hardlink:                # 同一文件标识的硬链接不计 copied
                r = base_row(path_key=e["pk"], status="added",
                             reason="hardlink_same_file_id", group_id=gid)
            elif m >= 1:                   # 旧快照存在该内容 → copied
                ev = ("propagated_single_computation"
                      if _event(new["uuid"], e["hash"])
                      in old["hash_events"].get(hx, set())
                      else "independent_computation")
                r = base_row(path_key=e["pk"], status="copied", evidence=ev,
                             group_id=gid)
            else:                          # 全新内容即便多副本仍 added
                r = base_row(path_key=e["pk"], status="added",
                             reason=("unstable_content" if e["unstable"]
                                     else n_it.base_reason), group_id=gid)
            fill_new(r, e, n_it.label)
            n_it.row = r
            rows.append(r)
        for o_it in dels[k:]:
            e = o_it.e
            r = base_row(path_key=e["pk"], status="deleted",
                         reason=("unstable_content" if e["unstable"]
                                 else o_it.base_reason), group_id=gid)
            fill_old(r, e, o_it.label)
            o_it.row = r
            rows.append(r)

    # file_id 启发：处理至少一侧缺少有效哈希的残余条目
    leftovers = [it for it in pending if it.row is None
                 or it.row["status"] in ("added", "deleted")]
    del_idx: dict = {}
    for it in leftovers:
        e = it.e
        if it.side == "old" and e["vs"] and e["fih"]:
            del_idx.setdefault((e["vs"], e["fih"], e["size"], e["mtime"]),
                               []).append(it)
    for it in leftovers:
        e = it.e
        if it.side != "new" or not (e["vs"] and e["fih"]):
            continue
        cands = del_idx.get((e["vs"], e["fih"], e["size"], e["mtime"]))
        if not cands:
            continue
        # 先窥视后消耗：双侧均有哈希者由哈希层定案，
        # 不得白白弹出候选——否则后续无哈希条目失去配对机会漏判 moved
        pick = next((i for i, c in enumerate(cands)
                     if not (e["hash"] and c.e["hash"])), None)
        if pick is None:
            continue
        o_it = cands.pop(pick)
        r = base_row(path_key=e["pk"], status="moved_or_renamed",
                     evidence="heuristic_file_id", reason="file_id_heuristic")
        fill_old(r, o_it.e, o_it.label)
        fill_new(r, e, it.label)
        # 按对象同一性移除（等值行按值 remove 会删错对象）
        rows[:] = [x for x in rows if x is not o_it.row and x is not it.row]
        o_it.row = it.row = r
        rows.append(r)

    for it in pending:                     # 残余定案 added/deleted
        if it.row is not None:
            continue
        e = it.e
        reason = "unstable_content" if e["unstable"] else it.base_reason
        r = base_row(path_key=e["pk"],
                     status="deleted" if it.side == "old" else "added",
                     reason=reason)
        (fill_old if it.side == "old" else fill_new)(r, e, it.label)
        it.row = r
        rows.append(r)

    # === 目录维度 ===
    dir_rows: list[dict] = []
    for orid, nrid, olb, nlb in pairs:
        od = old["dirs"].get(orid, {})
        nd = new["dirs"].get(nrid, {})
        ofail = failed_old.get(orid, [])
        nfail = failed_new.get(nrid, [])
        for pk in sorted(set(od) | set(nd)):
            o = od.get(pk)
            n = nd.get(pk)
            if o and n:
                status = ("enum_status_changed" if o["enum"] != n["enum"]
                          else "unchanged")
                reason = ("case_or_form_rename" if o["rel"] != n["rel"]
                          else None)
            elif o:
                status = "unknown" if _under(pk, nfail) else "deleted"
                reason = "enum_failed_new" if status == "unknown" else None
            else:
                status = "unknown" if _under(pk, ofail) else "added"
                reason = "enum_failed_old" if status == "unknown" else None
            dir_rows.append(
                {"path_key": pk,
                 "old_root_label": olb if o else None,
                 "new_root_label": nlb if n else None,
                 "old_rel_path": o["rel"] if o else None,
                 "new_rel_path": n["rel"] if n else None,
                 "status": status,
                 "old_enum_status": o["enum"] if o else None,
                 "new_enum_status": n["enum"] if n else None,
                 "reason": reason})
    for rid, side, label_key in [(r, "old", None) for r in unpaired_old] \
            + [(r, "new", None) for r in unpaired_new]:
        s = old if side == "old" else new
        lb = s["roots"][rid]["label"]
        for pk, v in sorted(s["dirs"].get(rid, {}).items()):
            dir_rows.append(
                {"path_key": pk,
                 "old_root_label": lb if side == "old" else None,
                 "new_root_label": lb if side == "new" else None,
                 "old_rel_path": v["rel"] if side == "old" else None,
                 "new_rel_path": v["rel"] if side == "new" else None,
                 "status": "deleted" if side == "old" else "added",
                 "old_enum_status": v["enum"] if side == "old" else None,
                 "new_enum_status": v["enum"] if side == "new" else None,
                 "reason": "unpaired_root"})

    # === 失败子树表 ===
    subtree_rows: list[dict] = []
    for side_name, side, failed, pair_map in (
            ("old", old, failed_old, o2n), ("new", new, failed_new, n2o)):
        other = new if side_name == "old" else old
        for rid, pks in sorted(failed.items()):
            other_rid = pair_map.get(rid)
            for pk in sorted(pks):
                est = None
                if other_rid is not None:
                    est = sum(len(lst) for opk, lst in
                              other["entries"].get(other_rid, {}).items()
                              if _under(opk, [pk]))
                d = side["dirs"][rid][pk]
                subtree_rows.append(
                    {"side": side_name,
                     "root_label": side["roots"][rid]["label"],
                     "rel_path": d["rel"], "enum_status": d["enum"],
                     "affected_estimate": est})

    # === 写出 Diff 数据库 ===
    out = sqlite3.connect(out_path)
    out.executescript(DIFF_DDL)
    se_counts: dict = {}
    for r in rows:
        se_counts.setdefault(r["status"], {}).setdefault(r["evidence"], 0)
        se_counts[r["status"]][r["evidence"]] += 1
    dir_counts: dict = {}
    for r in dir_rows:
        dir_counts[r["status"]] = dir_counts.get(r["status"], 0) + 1
    counts = {"status_evidence": se_counts, "dirs_status": dir_counts,
              "subtrees": {"old": sum(1 for s in subtree_rows
                                      if s["side"] == "old"),
                           "new": sum(1 for s in subtree_rows
                                      if s["side"] == "new")},
              "payload_rows": {"old": old["n_payload"],
                               "new": new["n_payload"]}}
    root_mapping = {"pairs": [[olb, nlb] for _, _, olb, nlb in pairs],
                    "auto_paired": auto_paired,
                    "unpaired_old": [old["roots"][r]["label"]
                                     for r in unpaired_old],
                    "unpaired_new": [new["roots"][r]["label"]
                                     for r in unpaired_new]}
    out.execute(
        "INSERT INTO diff_info (id, diff_uuid, schema_version,"
        " old_snapshot_uuid, new_snapshot_uuid, old_snapshot_file,"
        " new_snapshot_file, old_hash_coverage, new_hash_coverage,"
        " root_mapping_json, forced, tool_version, created_at_utc,"
        " counts_json) VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (uuid_mod.uuid4().hex, old["schema"], old["uuid"], new["uuid"],
         old["file"], new["file"], old["coverage"], new["coverage"],
         json.dumps(root_mapping, ensure_ascii=False),
         1 if (old["forced"] or new["forced"]) else 0,
         core.SCANNER_VERSION, core.now_utc_iso(),
         json.dumps(counts, ensure_ascii=False)))
    out.executemany(
        "INSERT INTO diff_hash_groups (group_id, hash_hex, old_count,"
        " new_count, old_hardlink_sets, new_hardlink_sets, classification)"
        " VALUES (:group_id, :hash_hex, :old_count, :new_count,"
        " :old_hardlink_sets, :new_hardlink_sets, :classification)",
        group_rows)
    out.executemany(
        "INSERT INTO diff_entries (path_key, old_root_label, new_root_label,"
        " old_rel_path, new_rel_path, status, evidence, reason, old_size,"
        " new_size, old_mtime_utc, new_mtime_utc, old_hash_hex, new_hash_hex,"
        " old_hash_origin, new_hash_origin, metadata_changed, group_id)"
        " VALUES (:path_key, :old_root_label, :new_root_label, :old_rel_path,"
        " :new_rel_path, :status, :evidence, :reason, :old_size, :new_size,"
        " :old_mtime_utc, :new_mtime_utc, :old_hash_hex, :new_hash_hex,"
        " :old_hash_origin, :new_hash_origin, :metadata_changed, :group_id)",
        rows)
    out.executemany(
        "INSERT INTO diff_dirs (path_key, old_root_label, new_root_label,"
        " old_rel_path, new_rel_path, status, old_enum_status,"
        " new_enum_status, reason) VALUES (:path_key, :old_root_label,"
        " :new_root_label, :old_rel_path, :new_rel_path, :status,"
        " :old_enum_status, :new_enum_status, :reason)",
        dir_rows)
    out.executemany(
        "INSERT INTO diff_subtrees (side, root_label, rel_path, enum_status,"
        " affected_estimate) VALUES (:side, :root_label, :rel_path,"
        " :enum_status, :affected_estimate)",
        subtree_rows)
    out.commit()
    out.close()
    return {"out": out_path, "counts": counts, "root_mapping": root_mapping,
            "subtrees": subtree_rows,
            "forced": 1 if (old["forced"] or new["forced"]) else 0,
            "coverage": (old["coverage"], new["coverage"])}

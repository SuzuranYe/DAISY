r"""Script_DAISY_Module_DBS_32_Check_Format：DBS-32 文件结构核验。

哈希证明「没变」，本脚本回答「是不是好的」。从快照读目标清单，对当前磁盘
文件执行结构级校验（每格式一档，不做深度解码），产出 JSON＋CSV＋MD 报告，
只读、不回写快照。

校验规则：
  zip 与 OOXML（docx/xlsx/pptx）→ 双层：zipfile 结构＋testzip 全成员 CRC；
    OLE 魔数（加密 Office）→ unsupported；加密成员 → unsupported
  旧 DOC OLE 复合文档 → 魔数确认＋`7z t` 结构测试
  pdf → %PDF 头＋%%EOF 尾＋startxref 存在性
  照片/GIF/视频/RAW/CRM → ExifTool -validate（stay_open 通道）；判据为经验模式表：
    Error 键，或命中损坏模式的非 minor 警告
    （format error/Truncated/Error reading/corrupt/坏头等）→ invalid；
    相机文件常见的合规性警告（Missing required tag、[minor]）不误伤；
    GIF、视频和音频叠加 ffprobe 解析退出码与流计数双保险
  7z/rar/tar/gz/bz2/xz → `7z t`（结构＋CRC）；需密码 → unsupported
  其他 → unsupported（不读取，哈希层兜底）

用法：
  python .\Script\Script_DAISY_MAIN.py check-format --snapshot .\Output\Snapshots\Scan_x.sqlite ^
      --root "Archive2024=E:\Archive2024" [--sample-percent 100]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
import zipfile
import zlib

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_LIB_DIR = os.path.join(os.path.dirname(_MODULE_DIR), "Lib")
sys.path.insert(0, _LIB_DIR)
import Script_DAISY_Lib_DBS_01_Core as core
import Script_DAISY_Lib_DBS_03_Hash as dbh
import Script_DAISY_Lib_DBS_02_Meta as meta
import Script_DAISY_Lib_DBS_05_Reader as dbreader

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_OOXML_EXTS = {"docx", "xlsx", "pptx"}
_MEDIA_KINDS = {"photo_raw", "photo_jpeg", "image_gif", "photo_working",
                "video_mp4", "video_crm", "audio"}
_FFPROBE_KINDS = {"image_gif", "video_mp4", "video_crm", "audio"}

# 损坏模式表（v1，判据来自截断 JPG/MP4/CR3 与坏头 JPG 的探针输出）
_CORRUPT_RE = re.compile(
    r"(format error|truncated|error reading|corrupt|unknown file type"
    r"|file is empty|processing .+ after unknown .*header"
    r"|bad atom|invalid atom|not a valid)", re.I)


def validate_zip(path: str) -> tuple[str, str | None]:
    """双层：① zipfile 打开即验中央目录结构 ② testzip 逐成员头＋CRC。"""
    try:
        with open(path, "rb") as f:
            if f.read(8).startswith(_OLE_MAGIC):
                return "unsupported", "OLE 容器（加密 Office/旧格式），非 zip"
        with zipfile.ZipFile(path) as z:
            bad = z.testzip()
            if bad is not None:
                return "invalid", f"成员 CRC 不符或成员头损坏：{bad}"
        return "valid", None
    except RuntimeError as exc:
        text = str(exc)
        if "password" in text.lower() or "encrypted" in text.lower():
            return "unsupported", f"加密成员无法 CRC 校验：{text}"
        return "invalid", text
    except Exception as exc:                       # BadZipFile/zlib/OSError 等
        return "invalid", f"{type(exc).__name__}: {exc}"


def validate_pdf(path: str) -> tuple[str, str | None]:
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            head = f.read(1024)
            f.seek(max(0, size - 2048))
            tail = f.read()
    except OSError as exc:
        return "invalid", str(exc)
    if b"%PDF-" not in head:
        return "invalid", "缺少 %PDF 头"
    if b"%%EOF" not in tail:
        return "invalid", "缺少 %%EOF 尾"
    if b"startxref" not in tail:
        return "invalid", "缺少 startxref（交叉引用结构）"
    return "valid", None


def validate_legacy_office(
    path: str, sevenzip: str,
) -> tuple[str, str | None]:
    """旧 Office 仅在确认是 OLE 复合文档后交给 7-Zip 做结构测试。"""
    try:
        with open(path, "rb") as f:
            magic = f.read(len(_OLE_MAGIC))
    except OSError as exc:
        return "invalid", str(exc)
    if magic != _OLE_MAGIC:
        return "unsupported", "不是 OLE 复合文档；可能是 RTF 或扩展名不符"
    return validate_sevenzip(path, sevenzip)


def validate_sevenzip(path: str, sevenzip: str) -> tuple[str, str | None]:
    try:
        r = subprocess.run([sevenzip, "t", "-p", "-y", "-sccUTF-8", path],
                           capture_output=True, timeout=3600)
    except subprocess.TimeoutExpired:
        return "invalid", "7z t 超时"
    if r.returncode == 0:
        return "valid", None
    text = (r.stderr + r.stdout).decode("utf-8", "replace")
    if "Wrong password" in text or "Enter password" in text:
        return "unsupported", "加密压缩包无法完整性测试"
    tail = " | ".join(l for l in text.splitlines() if l.strip())[-300:]
    return "invalid", f"7z t 退出码 {r.returncode}：{tail}"


def parse_et_text(text: str) -> list[tuple[str, str]]:
    """解析 exiftool -s 文本输出为 (标签, 值) 列表。"""
    out = []
    for line in text.splitlines():
        tag, sep, val = line.partition(":")
        if sep:
            out.append((tag.strip(), val.strip()))
    return out


def classify_et_findings(findings: list[tuple[str, str]]) -> list[str]:
    """判据 v1：Error 一律算；非 minor 警告命中损坏模式才算。"""
    bad = []
    for tag, val in findings:
        if tag == "Error":
            bad.append(f"Error: {val}")
        elif tag == "Warning" and not val.startswith("[minor]") \
                and _CORRUPT_RE.search(val):
            bad.append(f"Warning: {val}")
    return bad


def validate_media(path: str, kind: str, worker, ffprobe: str | None) \
        -> tuple[str, str | None]:
    out = worker.execute(["-validate", "-a", "-s", "-Warning", "-Error",
                          "-charset", "filename=utf8", path])
    bad = classify_et_findings(parse_et_text(out.decode("utf-8", "replace")))
    if kind in _FFPROBE_KINDS:                     # 双保险：容器可解析＋有流
        if not ffprobe:
            raise core.PreflightError(
                f"{kind} 格式校验需要 ffprobe")
        try:
            r = subprocess.run(
                [ffprobe, "-v", "error", "-print_format", "json",
                 "-show_format", "-show_streams", path],
                capture_output=True, timeout=600)
        except subprocess.TimeoutExpired:
            bad.append("ffprobe: 超时")
            return "invalid", "；".join(bad)
        document = {}
        streams = []
        try:
            document = json.loads(r.stdout.decode("utf-8", "replace"))
            streams = document.get("streams", [])
        except ValueError:
            pass
        if r.returncode != 0 or not streams:
            err = r.stderr.decode("utf-8", "replace").strip()[-200:]
            bad.append(f"ffprobe: rc={r.returncode}, streams={len(streams)}"
                       + (f"，{err}" if err else ""))
        elif kind == "audio" and os.path.getsize(path) <= 44:
            audio_streams = [
                stream for stream in streams
                if stream.get("codec_type") == "audio"
            ]
            format_duration = meta.first_float(
                (document.get("format", {}) or {}).get("duration"))
            stream_durations = [
                meta.first_float(stream.get("duration"))
                for stream in audio_streams
            ]
            if (not audio_streams
                    or not (format_duration and format_duration > 0)
                    and not any(value and value > 0
                                for value in stream_durations)):
                bad.append("ffprobe: 音频容器只有头部且没有可确认的音频样本")
    return ("invalid", "；".join(bad)) if bad else ("valid", None)


def _pick_validator(ext: str, kind: str) -> str:
    if ext == "zip" or ext in _OOXML_EXTS:
        return "zip"
    if ext == "pdf":
        return "pdf"
    if ext == "doc":
        return "ole"
    if ext == "gif":
        return "gif"
    if ext == "jfif":
        return "media"
    if kind in _MEDIA_KINDS:
        return "media"
    if kind == "archive":
        return "7z"
    return "none"


def validate_snapshot(snapshot_path: str, root_map: dict | None = None,
                      sample_percent: float = 100.0,
                      report_dir: str | None = None,
                      exiftool: str | None = None,
                      ffprobe: str | None = None,
                      sevenzip: str | None = None,
                      force: bool = False, on_progress=None,
                      root_specs: list[str] | None = None) -> dict:
    snapshot_path = os.path.abspath(snapshot_path)
    if not os.path.isfile(snapshot_path):
        raise core.PreflightError(f"快照不存在：{snapshot_path}")
    recorded = core.filename_sha256_high32(snapshot_path)
    if recorded is not None:
        if recorded != core.sha256_file(snapshot_path)[:8].upper():
            raise core.PreflightError("快照文件名高32bit指纹不符")
    elif not force:
        raise core.PreflightError(
            f"快照文件名缺少高32bit指纹（--force 可越过）：{snapshot_path}")
    con, descriptor = dbreader.open_database(
        snapshot_path, expected_type="snapshot")
    try:
        dbreader.require_capabilities(descriptor, "files")
        uuid_, = con.execute(
            "SELECT snapshot_uuid"
            " FROM snapshot_info").fetchone()
        roots = {}
        root_rows = list(con.execute(
            "SELECT root_id, root_label, root_path FROM roots"))
        labels = [label for _rid, label, _rpath in root_rows]
        specs = (
            root_specs if root_specs is not None
            else [f"{label}={path}" for label, path in (root_map or {}).items()]
        )
        current_roots = core.resolve_current_root_specs(labels, specs)
        label_by_rid = {}
        for rid, label, _recorded_path in root_rows:
            cur = current_roots[label]
            if not os.path.isdir(cur):
                raise core.PreflightError(
                    f"root「{label}」当前路径不存在：{cur}"
                    f"（用 --root \"{label}=当前路径\" 指定）")
            roots[rid] = cur
            label_by_rid[rid] = label
        entries = con.execute(
            "SELECT entry_id, root_id, rel_path, extension, media_kind,"
            " size_bytes, modified_at_utc FROM entries WHERE is_placeholder=0"
            " ORDER BY root_id, rel_path").fetchall()
    finally:
        con.close()

    if sample_percent < 100.0:
        ids = {eid for eid, _ in dbh.pick_sample(
            [(e[0], e[5]) for e in entries], sample_percent, 100,
            seed=uuid_ + ":validate")}
        entries = [e for e in entries if e[0] in ids]

    worker = None
    rows = []
    counts: dict = {}
    used_tools: dict[str, dict] = {}
    t0 = time.monotonic()

    def resolve_once(name: str, supplied: str | None) -> str:
        if name in used_tools:
            return used_tools[name]["path"]
        path = supplied or core.discover_tool(name, None)
        info = core.resolved_tool_info(
            name, path, explicit=bool(supplied))
        used_tools[name] = info
        core.emit_gui_event("tools_detected", tools={name: info})
        return info["path"]

    try:
        for i, (eid, rid, rel, ext, kind, size, mtime) in enumerate(entries, 1):
            path = os.path.join(roots[rid], rel)
            validator = _pick_validator(ext, kind)
            ext_path = core.to_extended_path(path)
            try:
                st = os.stat(ext_path, follow_symlinks=False)
                stat_match = 1 if (st.st_size == size and
                                   core.ns_to_utc_iso(st.st_mtime_ns) == mtime) else 0
            except OSError:
                rows.append({"path": label_by_rid[rid] + "\\" + rel,
                             "rel_path": rel, "root_id": rid,
                             "media_kind": kind, "validator": validator,
                             "status": "missing", "detail": None,
                             "stat_match": 0})
                counts["missing"] = counts.get("missing", 0) + 1
                if on_progress and i % 20 == 0:
                    on_progress(i, len(entries))
                continue
            if validator == "none":
                status_, detail = "unsupported", None
            elif validator == "zip":
                status_, detail = validate_zip(ext_path)
            elif validator == "pdf":
                status_, detail = validate_pdf(ext_path)
            elif validator == "ole":
                status_, detail = validate_legacy_office(
                    path, resolve_once("sevenzip", sevenzip))
            elif validator == "7z":
                status_, detail = validate_sevenzip(
                    path, resolve_once("sevenzip", sevenzip))
            else:
                if worker is None:
                    worker = meta.ExifToolWorker(
                        resolve_once("exiftool", exiftool))
                try:
                    effective_kind = (
                        "image_gif" if validator == "gif" else kind)
                    ffprobe_path = (
                        resolve_once("ffprobe", ffprobe)
                        if effective_kind in _FFPROBE_KINDS else None
                    )
                    status_, detail = validate_media(path, effective_kind, worker,
                                                     ffprobe_path)
                except TimeoutError:
                    status_, detail = "invalid", "exiftool -validate 超时"
            rows.append({"path": label_by_rid[rid] + "\\" + rel,
                         "rel_path": rel, "root_id": rid, "media_kind": kind,
                         "validator": validator, "status": status_,
                         "detail": detail, "stat_match": stat_match})
            counts[status_] = counts.get(status_, 0) + 1
            if on_progress and i % 20 == 0:
                on_progress(i, len(entries))
    finally:
        if worker is not None:
            worker.close()
    if on_progress and entries:
        on_progress(len(entries), len(entries))

    ok = not counts.get("invalid") and not counts.get("missing")
    report = {"report_metadata": core.report_metadata(
                  "DBS-32 文件结构核验"),
              "snapshot": os.path.basename(snapshot_path),
              "snapshot_uuid": uuid_,
              "mode": ("full" if sample_percent >= 100.0
                       else f"sample_{sample_percent}pct"),
              "checked": len(rows), "counts": counts, "ok": ok,
              "elapsed_s": round(time.monotonic() - t0, 1),
              "checked_at_utc": core.now_utc_iso(), "tools": used_tools,
              "rows": rows}

    rdir = os.path.abspath(report_dir or "Output/Reports")   # 可删报告统一进 Output\Reports\
    os.makedirs(rdir, exist_ok=True)
    # 报告名遵循 根文件夹名_类型_日期；问题状态只写入报告内容。
    report_stem = core.snapshot_working_name(
        core.snapshot_name(labels, "Check_Format"))
    base = os.path.join(rdir, report_stem)
    files = []
    with open(base + ".json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    files.append(base + ".json")
    with open(base + ".csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["path", "rel_path", "media_kind", "validator", "status",
                    "detail", "stat_match"])
        for r in rows:
            w.writerow([r["path"], r["rel_path"], r["media_kind"],
                        r["validator"], r["status"], r["detail"],
                        r["stat_match"]])
    files.append(base + ".csv")
    info_path = base + "_Info.csv"
    identity = core.report_metadata("DBS-32 文件结构核验")
    with open(info_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["key", "value"])
        writer.writerows(identity.items())
    files.append(info_path)
    md = ["# 文件结构核验报告", "",
          *core.report_markdown_lines("DBS-32 文件结构核验"), "",
          f"- 快照：`{report['snapshot']}`（uuid `{uuid_}`）",
          f"- 口径：{report['mode']}；核对 {report['checked']:,} 条；"
          f"用时 {report['elapsed_s']}s",
          f"- 结果：" + "，".join(f"{k}={v:,}"
                                  for k, v in sorted(counts.items())),
          f"- 结论：{'全部通过（在本次口径内）' if ok else '**发现问题**'}", ""]
    problems = [r for r in rows if r["status"] in ("invalid", "missing")]
    if problems:
        md.append("## 问题清单")
        md.append("")
        for r in problems[:50]:
            md.append(f"- [{r['status']}] `{r['path']}`"
                      + (f"：{r['detail']}" if r["detail"] else ""))
        if len(problems) > 50:
            md.append(f"- …共 {len(problems)} 条，详见 CSV")
        md.append("")
        md.append("与哈希层交叉解读：哈希未变＋校验失败＝登记时就已损坏；"
                  "哈希已变＋校验失败＝文件在登记后发生损坏。")
        md.append("")
    with open(base + ".md", "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(md))
    files.append(base + ".md")
    report["files"] = files
    return report


def main() -> int:
    core.force_utf8_io()
    ap = argparse.ArgumentParser(
        description="DBS-32 文件结构核验（只读，独立报告）")
    ap.add_argument("--snapshot", required=True)
    ap.add_argument(
        "--root", action="append", required=True,
        help="当前根目录；单根可直接给路径，多根须逐项 label=当前路径")
    ap.add_argument("--sample-percent", type=float, default=100.0)
    ap.add_argument("--report-dir")
    ap.add_argument("--exiftool-path")
    ap.add_argument("--ffprobe-path")
    ap.add_argument("--sevenzip-path")
    ap.add_argument("--force", action="store_true",
                    help="文件名高32bit指纹缺失时仍继续（不符仍拒绝）")
    args = ap.parse_args()
    try:
        prog = core.Progress(1, 1, "格式校验")
        rep = validate_snapshot(
            args.snapshot, sample_percent=args.sample_percent,
            report_dir=args.report_dir, exiftool=args.exiftool_path,
            ffprobe=args.ffprobe_path, sevenzip=args.sevenzip_path,
            force=args.force, root_specs=args.root,
            on_progress=lambda i, n: prog.update(i, total=n))
    except core.PreflightError as exc:
        print(f"校验失败：{exc}", file=sys.stderr)
        return 2
    prog.finish(f"核对 {rep['checked']:,} 条")
    print(f"核对 {rep['checked']:,} 条 | "
          + "，".join(f"{k}={v:,}" for k, v in sorted(rep["counts"].items()))
          + f" | 用时 {rep['elapsed_s']}s")
    for f in rep["files"]:
        print(f"报告：{f}")
    if rep["ok"]:
        print("结论：全部通过（在本次口径内）")
        return 0
    print("结论：发现 invalid/missing——详见报告；结合哈希层交叉解读",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())

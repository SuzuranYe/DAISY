"""DAISY DBS 元数据管线：ExifTool stay_open＋ffprobe 双后端＋压缩包摘要。

实现后端调用、profile v7、规范化映射和元数据汇合状态机。
ExifTool 仅允许白名单读取参数，任何写语法直接拒绝。
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import sqlite3
import subprocess
import threading
import time
import zipfile
import zlib
from dataclasses import dataclass

import Script_DAISY_Lib_DBS_01_Core as core

PROFILE_VERSION = 7
ET_PHOTO_ARGS = ["-charset", "filename=utf8", "-j", "-G1:3:4", "-a", "-u", "-D", "-l", "-ee"]
ET_VIDEO_ARGS = ["-charset", "filename=utf8", "-j", "-G1:3:4", "-a", "-u", "-D", "-l"]
FF_ARGS = ["-v", "error", "-print_format", "json", "-show_format", "-show_streams",
           "-show_chapters", "-show_programs", "-show_stream_groups", "-show_data"]
ET_TIMEOUT_S = 90
ET_TIMEOUT_STEP_BYTES = 9 * 1024 ** 3
ET_TIMEOUT_STEP_SECONDS = 90
FF_TIMEOUT_S = 60
ET_RESTART_EVERY = 5000

_BANNED_ET = core.EXIFTOOL_BANNED_ARGS   # 与 DBS_01 共享只读参数黑名单


def exiftool_timeout_policy() -> dict:
    return {
        "minimum_seconds": ET_TIMEOUT_S,
        "size_step_bytes": ET_TIMEOUT_STEP_BYTES,
        "seconds_per_step": ET_TIMEOUT_STEP_SECONDS,
        "rounding": "ceiling",
    }


def exiftool_timeout_for_size(size_bytes: int | None,
                              policy: dict | None = None) -> int:
    """不足或等于 9 GiB 为 90 秒，此后每跨一个 9 GiB 阶梯增加 90 秒。"""
    selected = dict(exiftool_timeout_policy() if policy is None else policy)
    try:
        minimum = int(selected["minimum_seconds"])
        step_bytes = int(selected["size_step_bytes"])
        step_seconds = int(selected["seconds_per_step"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("ExifTool timeout policy 字段无效") from exc
    if minimum <= 0 or step_bytes <= 0 or step_seconds <= 0:
        raise ValueError("ExifTool timeout policy 必须全部为正数")
    if selected.get("rounding") != "ceiling":
        raise ValueError("ExifTool timeout policy 只支持 ceiling")
    size = max(0, int(size_bytes or 0))
    steps = max(1, (size + step_bytes - 1) // step_bytes)
    return max(minimum, steps * step_seconds)


def guard_exiftool_args(args: list[str]) -> None:
    """只读防护：拒绝一切写语法与写参数。"""
    for a in args:
        low = a.lower()
        if low in _BANNED_ET:
            raise core.PreflightError(f"ExifTool 写参数被只读防护拦截：{a!r}")
        if a.startswith("-") and "=" in a and not low.startswith("-charset"):
            raise core.PreflightError(f"ExifTool 写语法被只读防护拦截：{a!r}")


# === 标签索引与取值 ===
def _score(mid_parts: list[str]) -> int:
    if not mid_parts or mid_parts[0].lower() == "main":
        return 0
    return 1


def build_tag_index(doc: dict) -> dict:
    """(family1_lower, tag_lower) → 显示值／数值；Main 优先。附 tag-only 索引。"""
    idx: dict = {}
    for key, raw in doc.items():
        if key == "SourceFile":
            continue
        parts = key.split(":")
        fam, tag = parts[0].lower(), parts[-1].lower()
        val = raw.get("val") if isinstance(raw, dict) and "val" in raw else raw
        num = raw.get("num") if isinstance(raw, dict) else None
        score = _score(parts[1:-1])
        for k in ((fam, tag), ("*", tag)):
            if k not in idx or score < idx[k][0]:
                idx[k] = (score, val, num)
    return idx


def tval(idx: dict, spec: str):
    fam, _, tag = spec.partition(":")
    hit = idx.get((fam.lower(), tag.lower()))
    return hit[1] if hit else None


def tchain(idx: dict, specs: list[str]):
    for s in specs:
        v = tval(idx, s)
        if v not in (None, "", "n/a"):
            return v
    return None


def tchain_src(idx: dict, specs: list[str]):
    for s in specs:
        v = tval(idx, s)
        if v not in (None, "", "n/a"):
            return v, s
    return None, None


def tnum(idx: dict, spec: str) -> float | None:
    """优先返回 ExifTool 的机器数值 num，缺失时解析显示值。"""
    fam, _, tag = spec.partition(":")
    hit = idx.get((fam.lower(), tag.lower()))
    if not hit:
        return None
    num = hit[2] if len(hit) > 2 else None
    return first_float(num if num not in (None, "", "n/a") else hit[1])


def tnum_chain(idx: dict, specs: list[str]) -> float | None:
    for spec in specs:
        value = tnum(idx, spec)
        if value is not None:
            return value
    return None


def _native_number(hit) -> float | None:
    """只接受 ExifTool num 或原生数值 val，不解析展示字符串。"""
    if not hit:
        return None
    num = hit[2] if len(hit) > 2 else None
    if num not in (None, "", "n/a"):
        return first_float(num)
    value = hit[1]
    return float(value) if isinstance(value, (int, float)) else None


def tnum_native(idx: dict, spec: str) -> float | None:
    fam, _, tag = spec.partition(":")
    return _native_number(idx.get((fam.lower(), tag.lower())))


def tnum_native_chain(idx: dict, specs: list[str]) -> float | None:
    for spec in specs:
        value = tnum_native(idx, spec)
        if value is not None:
            return value
    return None


def tany(idx: dict, tag: str):
    hit = idx.get(("*", tag.lower()))
    return hit[1] if hit else None


def tany_chain(idx: dict, tags: list[str]):
    for t in tags:
        v = tany(idx, t)
        if v not in (None, "", "n/a"):
            return v
    return None


def tany_native_number(idx: dict, tag: str) -> float | None:
    return _native_number(idx.get(("*", tag.lower())))


def as_json_text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return json.dumps([value], ensure_ascii=False)


def scalar_or_json_text(value) -> str | None:
    """标量保持标量文本；真实数组／对象才保存 JSON。"""
    if value in (None, ""):
        return None
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


# === 数值解析 ===
_NUMBER_TOKEN = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
_FLOAT_RE = re.compile(_NUMBER_TOKEN)
_RATIO_RE = re.compile(rf"({_NUMBER_TOKEN})\s*/\s*({_NUMBER_TOKEN})")


def first_float(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    text = str(v)
    first = _FLOAT_RE.search(text)
    ratio = _RATIO_RE.search(text)
    if ratio and first and ratio.start() == first.start():
        denominator = float(ratio.group(2))
        return (float(ratio.group(1)) / denominator
                if denominator != 0 else None)
    return float(first.group()) if first else None


def first_int(v) -> int | None:
    f = first_float(v)
    return int(f) if f is not None else None


def first_positive_int_pair(v) -> tuple[int, int] | None:
    """读取首两个正数，供 DNG DefaultCropSize 等二元尺寸字段使用。"""
    if v is None:
        return None
    values = _FLOAT_RE.findall(str(v))
    if len(values) < 2:
        return None
    pair = int(float(values[0])), int(float(values[1]))
    return pair if pair[0] > 0 and pair[1] > 0 else None


def gps_decimal(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.match(r"\s*(\d+(?:\.\d+)?)\s*deg\s*(?:(\d+(?:\.\d+)?)')?\s*"
                 r"(?:(\d+(?:\.\d+)?)\")?\s*([NSEW])?", str(v))
    if not m:
        return None
    deg = float(m.group(1)) + float(m.group(2) or 0) / 60 + float(m.group(3) or 0) / 3600
    if m.group(4) in ("S", "W"):
        deg = -deg
    return deg


_ISO6709_LOCATION_RE = re.compile(
    r"\s*([+-]\d{1,2}(?:\.\d+)?)"
    r"([+-]\d{1,3}(?:\.\d+)?)"
    r"([+-]\d+(?:\.\d+)?)?/\s*"
)


def parse_iso6709_location(v) -> tuple[float, float, float | None] | None:
    """解析 QuickTime/ffprobe location 的 ISO 6709 十进制度表示。"""
    if not isinstance(v, str):
        return None
    m = _ISO6709_LOCATION_RE.fullmatch(v)
    if not m:
        return None
    latitude = float(m.group(1))
    longitude = float(m.group(2))
    altitude = float(m.group(3)) if m.group(3) is not None else None
    if not (-90.0 <= latitude <= 90.0):
        return None
    if not (-180.0 <= longitude <= 180.0):
        return None
    return latitude, longitude, altitude


def offset_minutes(v) -> int | None:
    if v is None:
        return None
    m = re.fullmatch(r"\s*([+-])(\d{2}):(\d{2})\s*", str(v))
    if not m:
        return None
    sign = 1 if m.group(1) == "+" else -1
    return sign * (int(m.group(2)) * 60 + int(m.group(3)))


def offset_minutes_from_value(v) -> int | None:
    if v is None:
        return None
    m = re.search(r"([+-]\d{2}:\d{2})\s*$", str(v))
    return offset_minutes(m.group(1)) if m else None


_CAPTURE_TIME_RE = re.compile(
    r"(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})"
    r"(?P<fraction>\.\d+)?")

_ISO_TIME_RE = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})[T ]"
    r"(?P<clock>\d{2}:\d{2}:\d{2})(?P<fraction>\.\d+)?"
    r"(?P<zone>Z|[+-]\d{2}:\d{2})\Z", re.IGNORECASE)


def matching_datetime_offset(raw, candidates: list) -> int | None:
    """仅在候选与拍摄时间的本地钟面完全一致时借用其显式时区。"""
    base = _CAPTURE_TIME_RE.search(str(raw)) if raw is not None else None
    if not base:
        return None
    for candidate in candidates:
        other = (_CAPTURE_TIME_RE.search(str(candidate))
                 if candidate is not None else None)
        if other and other.groups()[:6] == base.groups()[:6]:
            offset = offset_minutes_from_value(candidate)
            if offset is not None:
                return offset
    return None


def capture_utc(raw, offset_min: int | None) -> str | None:
    """拍摄时间原文＋显式偏移 → UTC；无偏移不推断（原则）。"""
    if raw is None or offset_min is None:
        return None
    m = _CAPTURE_TIME_RE.match(str(raw))
    if not m:
        return None
    import calendar
    y, mo, d, h, mi, s = (int(x) for x in m.groups()[:6])
    epoch = calendar.timegm((y, mo, d, h, mi, s, 0, 0, 0)) - offset_min * 60
    t = time.gmtime(epoch)
    fraction = m.group("fraction") or ""
    return (f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}"
            f"T{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}{fraction}Z")


def normalize_explicit_utc(raw) -> str | None:
    """规范化带 Z／显式偏移的 ISO 时间，并保留原有小数秒位数。"""
    if raw is None:
        return None
    m = _ISO_TIME_RE.fullmatch(str(raw).strip())
    if not m:
        return None
    date = m.group("date")
    clock = m.group("clock")
    fraction = m.group("fraction") or ""
    zone = m.group("zone").upper()
    if zone == "Z":
        return f"{date}T{clock}{fraction}Z"
    colon_raw = f"{date.replace('-', ':')} {clock}{fraction}"
    return capture_utc(colon_raw, offset_minutes(zone))


def ffprobe_creation_time(ff: dict) -> tuple[str | None, str | None, object]:
    tags = ((ff.get("format", {}) or {}).get("tags", {}) or {}) if ff else {}
    if not isinstance(tags, dict):
        return None, None, None
    for key, value in tags.items():
        if str(key).casefold() == "creation_time":
            return (normalize_explicit_utc(value),
                    f"ffprobe:format.tags.{key}", value)
    return None, None, None


# === raw payload ===
@dataclass
class Payload:
    zlib_blob: bytes
    sha256: str
    uncompressed_bytes: int


def make_payload(doc: dict) -> Payload:
    canon = json.dumps(doc, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")).encode("utf-8")
    return Payload(zlib.compress(canon, 6),
                   hashlib.sha256(canon).hexdigest(), len(canon))


# === 压缩包后端（不解压、不递归） ===
# zip 压缩方法与建档系统编号 → 可读名（PKWARE APPNOTE 4.4.2/4.4.5）
_ZIP_METHOD = {0: "store", 8: "deflate", 9: "deflate64", 12: "bzip2",
               14: "lzma", 93: "zstd", 95: "xz", 98: "ppmd", 99: "aes"}
_ZIP_HOST = {0: "msdos", 3: "unix", 6: "os2_hpfs", 7: "macintosh",
             10: "ntfs", 11: "vfat", 14: "vms", 19: "osx"}


def _zip_member(idx: int, i) -> dict:
    return {"member_index": idx,
            "member_path": i.filename,
            "is_dir": 1 if i.is_dir() else 0,
            "size_bytes": i.file_size,
            "packed_bytes": i.compress_size,
            "crc32_hex": f"{i.CRC:08x}",
            "method": _ZIP_METHOD.get(i.compress_type, str(i.compress_type)),
            "flag_bits": i.flag_bits,
            "host_os": _ZIP_HOST.get(i.create_system, str(i.create_system)),
            "create_version": i.create_version,
            "extract_version": i.extract_version,
            "header_offset": i.header_offset,
            "modified_raw": "%04d-%02d-%02d %02d:%02d:%02d" % i.date_time,
            "attributes": f"0x{i.external_attr:08x}",
            "encrypted": 1 if i.flag_bits & 0x1 else 0}


def zip_summary(path: str) -> dict:
    """中央目录读取（不解压）：聚合摘要＋逐成员清单。"""
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        return {"archive_format": "zip",
                "member_count": len(infos),
                "uncompressed_bytes": sum(i.file_size for i in infos),
                "compressed_bytes": sum(i.compress_size for i in infos),
                "has_encrypted": 1 if any(i.flag_bits & 0x1 for i in infos) else 0,
                "members": [_zip_member(idx, i)
                            for idx, i in enumerate(infos)]}


def _new_7z_member(index: int, path: str) -> dict:
    return {"member_index": index, "member_path": path, "is_dir": 0,
            "size_bytes": None, "packed_bytes": None, "crc32_hex": None,
            "method": None, "flag_bits": None, "host_os": None,
            "create_version": None, "extract_version": None,
            "header_offset": None, "modified_raw": None, "attributes": None,
            "encrypted": 0}


def parse_7z_slt(text: str) -> dict:
    """`7z l -slt` 解析：聚合摘要＋逐成员（CRC/Method/Attributes/Host OS 等；
    7z 列表不暴露局部偏移与 zip 版本字段，相应列为 NULL）。"""
    members: list[dict] = []
    in_body = False
    cur: dict | None = None
    for line in text.splitlines():
        if line.startswith("----------"):
            in_body = True
            continue
        if not in_body:
            continue
        if line.startswith("Path = "):
            if cur is not None:
                members.append(cur)
            cur = _new_7z_member(len(members), line[7:])
        elif cur is None:
            continue
        elif line.startswith("Size = "):
            cur["size_bytes"] = first_int(line[7:])
        elif line.startswith("Packed Size = "):
            cur["packed_bytes"] = first_int(line[14:])
        elif line.startswith("Modified = "):
            cur["modified_raw"] = line[11:].strip() or None
        elif line.startswith("Attributes = "):
            v = line[13:].strip()
            cur["attributes"] = v or None
            if v and "D" in v.split()[0]:
                cur["is_dir"] = 1
        elif line.startswith("Folder = ") and line[9:].strip() == "+":
            cur["is_dir"] = 1
        elif line.startswith("CRC = "):
            v = line[6:].strip().lower()
            cur["crc32_hex"] = v or None
        elif line.startswith("Method = "):
            cur["method"] = line[9:].strip() or None
        elif line.startswith("Host OS = "):
            cur["host_os"] = line[10:].strip() or None
        elif line.startswith("Encrypted = ") and line.endswith("+"):
            cur["encrypted"] = 1
    if cur is not None:
        members.append(cur)
    return {"member_count": len(members),
            "uncompressed_bytes": sum(m["size_bytes"] or 0 for m in members),
            "compressed_bytes": sum(m["packed_bytes"] or 0 for m in members),
            "has_encrypted": 1 if any(m["encrypted"] for m in members) else 0,
            "members": members}


def sevenzip_summary(path: str, sevenzip: str, fmt: str) -> dict:
    # -sccUTF-8：控制台输出定为 UTF-8，否则非 ASCII 成员名按 ANSI 码页输出致乱码
    r = subprocess.run([sevenzip, "l", "-slt", "-sccUTF-8", path],
                       capture_output=True, timeout=FF_TIMEOUT_S)
    if r.returncode != 0:
        raise RuntimeError("7z l 失败：" +
                           r.stderr.decode("utf-8", "replace")[:200])
    s = parse_7z_slt(r.stdout.decode("utf-8", "replace"))
    s["archive_format"] = fmt
    return s


# === 规范化映射 ===
_CAMERA_CHAINS = {
    "camera_make": ["IFD0:Make"],
    "camera_model": ["IFD0:Model", "Canon:CanonModelID"],
    "camera_serial": ["ExifIFD:SerialNumber", "Canon:InternalSerialNumber"],
    "lens_model": ["ExifIFD:LensModel", "Canon:LensModel", "Canon:RFLensType"],
    "lens_serial": ["ExifIFD:LensSerialNumber", "Canon:LensSerialNumber"],
}


def diagnostic(provider: str, severity: str, code: str, message,
               field_name: str | None = None, raw_value=None) -> dict:
    if isinstance(raw_value, (dict, list)):
        raw_text = json.dumps(raw_value, ensure_ascii=False, sort_keys=True)
    elif raw_value is None:
        raw_text = None
    else:
        raw_text = str(raw_value)
    return {
        "provider": provider,
        "severity": severity,
        "diagnostic_code": code,
        "field_name": field_name,
        "message": str(message),
        "raw_value": raw_text,
    }


def reported_diagnostics(doc: dict | None) -> list[dict]:
    """提取 ExifTool JSON 中任意 family 的 Warning／Error 标签。"""
    rows = []
    for key, raw in (doc or {}).items():
        tag = str(key).split(":")[-1].casefold()
        if tag not in ("warning", "error"):
            continue
        value = raw.get("val") if isinstance(raw, dict) and "val" in raw else raw
        values = value if isinstance(value, list) else [value]
        for item in values:
            rows.append(diagnostic(
                "exiftool", tag, f"exiftool_reported_{tag}",
                item if item not in (None, "") else tag,
                str(key), raw))
    return rows


def _add_validation(rows: list[dict] | None, code: str, field: str,
                    raw_value, message: str) -> None:
    if rows is not None:
        rows.append(diagnostic(
            "normalizer", "validation", code, message, field, raw_value))


def _is_all_zero_identifier(value) -> bool:
    return bool(value is not None
                and re.fullmatch(r"0+", str(value).strip()))


def _capture_fields(idx, time_chain, tz_chain=None, tz_value_chain=None):
    raw, src = tchain_src(idx, time_chain)
    tz = None
    tz_src = None
    if tz_chain:
        tz_value, explicit_src = tchain_src(idx, tz_chain)
        tz = offset_minutes(tz_value)
        if tz is not None:
            tz_src = explicit_src
    if tz is None:
        tz = offset_minutes_from_value(raw)
        if tz is not None:
            tz_src = "embedded"
    if tz is None and tz_value_chain:
        for spec in tz_value_chain:
            tz = matching_datetime_offset(raw, [tval(idx, spec)])
            if tz is not None:
                tz_src = spec
                break
    source = src
    if source and tz_src:
        source = f"{source}|offset={tz_src}"
    return {"capture_time_raw": str(raw) if raw is not None else None,
            "capture_time_source": source,
            "capture_tz_offset_min": tz,
            "capture_time_utc": capture_utc(raw, tz)}


def photo_row(idx: dict, ext: str | None = None,
              diagnostics: list[dict] | None = None) -> dict:
    row = _capture_fields(
        idx, ["Composite:SubSecDateTimeOriginal",
              "ExifIFD:DateTimeOriginal", "ExifIFD:CreateDate",
              "IFD0:ModifyDate"],
        ["ExifIFD:OffsetTimeOriginal", "ExifIFD:OffsetTimeDigitized",
         "ExifIFD:OffsetTime"],
        ["XMP-exif:DateTimeOriginal", "XMP-xmp:CreateDate"])
    for col, chain in _CAMERA_CHAINS.items():
        v = tchain(idx, chain)
        row[col] = str(v) if v is not None else None
    if _is_all_zero_identifier(row["lens_serial"]):
        _add_validation(
            diagnostics, "invalid_all_zero_lens_serial", "lens_serial",
            row["lens_serial"], "全零镜头序列号已转为 NULL")
        row["lens_serial"] = None
    is_dng = str(ext or "").casefold().lstrip(".") == "dng"
    crop_size = (first_positive_int_pair(
        tval(idx, "SubIFD:DefaultCropSize")) if is_dng else None)
    if crop_size:
        row["width"], row["height"] = crop_size
    else:
        width_chain = ["ExifIFD:ExifImageWidth", "File:ImageWidth"]
        height_chain = ["ExifIFD:ExifImageHeight", "File:ImageHeight"]
        if is_dng:
            width_chain.insert(0, "SubIFD:ImageWidth")
            height_chain.insert(0, "SubIFD:ImageHeight")
        row["width"] = first_int(tchain(idx, width_chain))
        row["height"] = first_int(tchain(idx, height_chain))
    v = tval(idx, "IFD0:Orientation")
    row["orientation"] = str(v) if v is not None else None
    row["iso"] = first_int(tval(idx, "ExifIFD:ISO"))
    f_number_raw = tval(idx, "ExifIFD:FNumber")
    row["f_number"] = tnum(idx, "ExifIFD:FNumber")
    if row["f_number"] is not None and row["f_number"] <= 0:
        _add_validation(
            diagnostics, "invalid_nonpositive_f_number", "f_number",
            f_number_raw, "非正数光圈值已转为 NULL")
        row["f_number"] = None
    v = tval(idx, "ExifIFD:ExposureTime")
    row["exposure_time"] = str(v) if v is not None else None
    row["exposure_compensation"] = tnum_native_chain(
        idx, ["ExifIFD:ExposureCompensation", "Canon:ExposureCompensation"])
    focal_raw = tval(idx, "ExifIFD:FocalLength")
    row["focal_length_mm"] = tnum(idx, "ExifIFD:FocalLength")
    focal_35_raw = tchain(
        idx, ["ExifIFD:FocalLengthIn35mmFormat",
              "Composite:FocalLength35efl"])
    row["focal_length_35mm"] = tnum_chain(
        idx, ["ExifIFD:FocalLengthIn35mmFormat",
              "Composite:FocalLength35efl"])
    if row["focal_length_mm"] is not None and row["focal_length_mm"] <= 0:
        _add_validation(
            diagnostics, "invalid_nonpositive_focal_length",
            "focal_length_mm", focal_raw,
            "非正数实际焦距及其 35mm 换算值已转为 NULL")
        row["focal_length_mm"] = None
        row["focal_length_35mm"] = None
    elif (row["focal_length_35mm"] is not None
          and row["focal_length_35mm"] <= 0):
        _add_validation(
            diagnostics, "invalid_nonpositive_focal_length_35mm",
            "focal_length_35mm", focal_35_raw,
            "非正数 35mm 换算焦距已转为 NULL")
        row["focal_length_35mm"] = None
    canon_wb = tval(idx, "Canon:WhiteBalance")
    generic_wb = tval(idx, "ExifIFD:WhiteBalance")
    is_canon = str(row.get("camera_make") or "").casefold().startswith("canon")
    v = canon_wb if canon_wb not in (None, "", "n/a") else (
        None if is_canon else generic_wb)
    row["white_balance"] = str(v) if v is not None else None
    as_shot_temp = tany_native_number(idx, "ColorTempAsShot")
    row["color_temperature"] = (
        int(as_shot_temp) if as_shot_temp is not None else None)
    v = tval(idx, "ExifIFD:ColorSpace")
    row["color_space"] = str(v) if v is not None else None
    v = tval(idx, "ICC_Profile:ProfileDescription")
    row["icc_profile"] = str(v) if v is not None else None
    v = tchain(idx, ["IFD0:Software", "XMP-xmp:CreatorTool"])
    row["software"] = str(v) if v is not None else None
    bit_depth_chain = ["ExifIFD:BitsPerSample", "File:BitsPerSample"]
    if is_dng:
        bit_depth_chain.insert(0, "SubIFD:BitsPerSample")
    row["bit_depth"] = first_int(tchain(idx, bit_depth_chain))
    latitude_raw = tval(idx, "Composite:GPSLatitude")
    longitude_raw = tval(idx, "Composite:GPSLongitude")
    altitude_raw = tval(idx, "Composite:GPSAltitude")
    latitude = tnum_native(idx, "Composite:GPSLatitude")
    longitude = tnum_native(idx, "Composite:GPSLongitude")
    altitude = tnum_native(idx, "Composite:GPSAltitude")
    if latitude is None:
        latitude = gps_decimal(latitude_raw)
    if longitude is None:
        longitude = gps_decimal(longitude_raw)
    if altitude is None:
        altitude = first_float(altitude_raw)
    if latitude == 0.0 and longitude == 0.0:
        _add_validation(
            diagnostics, "invalid_zero_gps_placeholder", "gps_latitude",
            {"latitude": latitude_raw, "longitude": longitude_raw,
             "altitude": altitude_raw},
            "零经纬度占位坐标已整体转为 NULL")
        latitude = longitude = altitude = None
    row["gps_latitude"] = latitude
    row["gps_longitude"] = longitude
    row["gps_altitude"] = altitude
    return row


def working_row(idx: dict, ext: str) -> dict:
    v_names = tchain(idx, ["Photoshop:LayerUnicodeNames", "Photoshop:LayerNames"])
    cm = tchain(idx, ["Photoshop:ColorMode", "XMP-photoshop:ColorMode"])
    app = tchain(idx, ["IFD0:Software", "XMP-xmp:CreatorTool"])
    return {"file_variant": ext,
            "creator_app": str(app) if app is not None else None,
            "color_mode": str(cm) if cm is not None else None,
            "bit_depth": first_int(tval(idx, "Photoshop:BitDepth")),
            "width": first_int(tchain(idx, ["ExifIFD:ExifImageWidth",
                                            "File:ImageWidth"])),
            "height": first_int(tchain(idx, ["ExifIFD:ExifImageHeight",
                                             "File:ImageHeight"])),
            "layer_count": first_int(tval(idx, "Photoshop:LayerCount")),
            "layer_names": as_json_text(v_names),
            "has_thumbnail": 1 if tval(idx, "Photoshop:PhotoshopThumbnail")
                              is not None else 0}


def document_row(idx: dict, ext: str) -> dict:
    def s(v):
        return str(v) if v not in (None, "") else None
    return {"doc_format": ext,
            "title": s(tany(idx, "Title")),
            "author": s(tany_chain(idx, ["Author", "Creator"])),
            "last_modified_by": s(tany(idx, "LastModifiedBy")),
            "creator_app": s(tany_chain(idx, ["Application", "Producer", "Software"])),
            "created_prop_raw": s(tany_chain(idx, ["CreateDate", "CreationDate"])),
            "modified_prop_raw": s(tany_chain(idx, ["ModifyDate", "ModDate"])),
            "page_count": first_int(tany_chain(idx, ["Pages", "PageCount"])),
            "is_encrypted": 1 if tany(idx, "Encryption") is not None else 0}


def _dji_model_from_category(value) -> str | None:
    match = re.search(
        r"(?:^|;)\s*model_name\s*:\s*([^;]+)", str(value or ""),
        re.IGNORECASE)
    return match.group(1).strip() if match else None


def video_row(idx: dict, ff: dict | None,
              diagnostics: list[dict] | None = None) -> dict:
    fmt = ff.get("format", {}) if ff else {}
    ftags = fmt.get("tags", {}) or {}
    vstreams = [s for s in (ff.get("streams", []) if ff else [])
                if s.get("codec_type") == "video"]
    timecode = ftags.get("timecode")
    encoder = ftags.get("encoder")
    for s in vstreams:
        st = s.get("tags", {}) or {}
        timecode = timecode or st.get("timecode")
        encoder = encoder or st.get("encoder")
    row = _capture_fields(
        idx, ["Composite:SubSecDateTimeOriginal", "QuickTime:CreateDate",
              "Keys:CreationDate", "XMP-xmp:CreateDate"],
        ["ExifIFD:OffsetTimeOriginal", "ExifIFD:OffsetTimeDigitized",
         "ExifIFD:OffsetTime"])
    ff_utc, ff_utc_src, ff_utc_raw = ffprobe_creation_time(ff or {})
    if ff_utc:
        if row["capture_time_raw"] is None:
            row["capture_time_raw"] = str(ff_utc_raw)
            row["capture_tz_offset_min"] = 0
            row["capture_time_source"] = ff_utc_src
            row["capture_time_utc"] = ff_utc
        elif row["capture_time_utc"] is None:
            row["capture_time_source"] = (
                f"{row['capture_time_source']}|utc={ff_utc_src}")
            row["capture_time_utc"] = ff_utc
    for col, chain in _CAMERA_CHAINS.items():
        v = tchain(idx, chain)
        row[col] = str(v) if v is not None else None
    if _is_all_zero_identifier(row["lens_serial"]):
        _add_validation(
            diagnostics, "invalid_all_zero_lens_serial", "lens_serial",
            row["lens_serial"], "全零镜头序列号已转为 NULL")
        row["lens_serial"] = None
    category = tval(idx, "Microsoft:Category")
    dji_model = _dji_model_from_category(category)
    dji_evidence = (dji_model is not None
                    or any(k[0] != "*" and "dji" in k[0]
                           for k in idx))
    if dji_evidence:
        row["camera_make"] = row["camera_make"] or "DJI"
        row["camera_model"] = row["camera_model"] or dji_model
    exif_encoder = tval(idx, "ItemList:Encoder")
    encoder = encoder or exif_encoder
    canon_wb = tval(idx, "Canon:WhiteBalance")
    generic_wb = tval(idx, "ExifIFD:WhiteBalance")
    is_canon = str(row.get("camera_make") or "").casefold().startswith("canon")
    white_balance = (canon_wb if canon_wb not in (None, "", "n/a")
                     else (None if is_canon else generic_wb))
    container = fmt.get("format_name")
    if not container:
        file_type = tval(idx, "File:FileType")
        container = str(file_type).casefold() if file_type else None
    row.update({
        "container_format": container,
        "duration_seconds": (first_float(fmt.get("duration"))
                             or first_float(tany(idx, "Duration"))),
        "bit_rate": first_int(fmt.get("bit_rate")),
        "stream_count": first_int(fmt.get("nb_streams")),
        "timecode": timecode,
        "iso": first_int(tchain(idx, ["ExifIFD:ISO", "Canon:AutoISO"])),
        "white_balance": (str(white_balance)
                          if white_balance is not None else None),
        "shutter": (lambda v: str(v) if v is not None else None)(
            tchain(idx, ["ExifIFD:ExposureTime", "Canon:ExposureTime"])),
        "gamma": (lambda v: str(v) if v is not None else None)(
            tval(idx, "Canon:CanonLogVersion")),
        "color_gamut": (lambda v: str(v) if v is not None else None)(
            tval(idx, "Canon:ColorSpace2")),
        "encoder": encoder,
        # 作者/来源尽量获取。优先 ffprobe 容器 tags
        # （按格式正确处理编码——RIFF INFO 无标准编码，ExifTool 默认按
        # Latin-1 解、UTF-8 标签会乱码，实测确认），ExifTool 任意组兜底
        "title": scalar_or_json_text(ftags.get("title")
                                      or tany_chain(idx, ["Title"])),
        "author": scalar_or_json_text(
            ftags.get("artist") or ftags.get("author")
            or tany_chain(idx, ["Artist", "Author", "Creator",
                                "AlbumArtist", "Composer"])),
        "album": scalar_or_json_text(ftags.get("album")
                                      or tany_chain(idx, ["Album"])),
        "copyright": scalar_or_json_text(
            ftags.get("copyright")
            or tany_chain(idx, ["Copyright", "CopyrightNotice"])),
    })
    return row


def audio_stream_rows_from_exif(idx: dict) -> list[dict]:
    """ffprobe 无有效流时，从 ExifTool 结果恢复 AAC 基础音频流。"""
    mime = tval(idx, "File:MIMEType")
    file_type = tval(idx, "File:FileType")
    sample_rate = first_int(tval(idx, "AAC:SampleRate"))
    if not (sample_rate or str(mime or "").casefold().startswith("audio/")
            or str(file_type or "").casefold() == "aac"):
        return []
    channels = first_int(tval(idx, "AAC:Channels"))
    if channels is not None and channels <= 0:
        channels = None
    return [{
        "stream_index": 0,
        "codec_name": str(file_type or "aac").casefold(),
        # ExifTool 的 AAC ProfileType 与 ffprobe codec profile 在真实样本中
        # 可能冲突（Main vs LC），没有 ffprobe 证据时不冒充同一语义。
        "profile": None,
        "sample_rate": sample_rate,
        "channels": channels,
        "channel_layout": None,
        "bit_rate": None,
        "duration_seconds": first_float(tany(idx, "Duration")),
    }]


def av_validation_diagnostics(kind: str, size_bytes: int,
                              ff: dict | None) -> list[dict]:
    """识别可成功返回 JSON、但实际没有有效媒体内容的文件。"""
    if ff is None:
        return []
    streams = ff.get("streams", []) or []
    if not streams:
        return [diagnostic(
            "ffprobe", "error", "media_no_streams",
            "ffprobe 未发现任何媒体流", "streams", streams)]
    if kind == "audio":
        audio = [s for s in streams if s.get("codec_type") == "audio"]
        if not audio:
            return [diagnostic(
                "ffprobe", "error", "audio_stream_missing",
                "音频文件中未发现音频流", "streams", streams)]
        format_duration = first_float((ff.get("format", {}) or {}).get("duration"))
        durations = [first_float(s.get("duration")) for s in audio]
        if (size_bytes <= 44 and not (format_duration and format_duration > 0)
                and not any(v and v > 0 for v in durations)):
            return [diagnostic(
                "ffprobe", "error", "audio_no_samples",
                "音频容器只有头部且没有可确认的音频样本",
                "size_bytes", size_bytes)]
    return []


def stream_rows(ff: dict) -> tuple[list[dict], list[dict]]:
    vids, auds = [], []
    for s in (ff.get("streams", []) if ff else []):
        ctype = s.get("codec_type")
        if ctype == "video":
            vids.append({
                "stream_index": s.get("index"),
                "codec_name": s.get("codec_name"),
                "codec_tag": s.get("codec_tag_string"),
                "profile": s.get("profile"),
                "width": s.get("width"), "height": s.get("height"),
                "r_frame_rate": s.get("r_frame_rate"),
                "avg_frame_rate": s.get("avg_frame_rate"),
                "pix_fmt": s.get("pix_fmt"),
                "bit_depth": first_int(s.get("bits_per_raw_sample")),
                "color_space": s.get("color_space"),
                "color_transfer": s.get("color_transfer"),
                "color_primaries": s.get("color_primaries"),
                "bit_rate": first_int(s.get("bit_rate")),
                "nb_frames": first_int(s.get("nb_frames")),
                "duration_seconds": first_float(s.get("duration"))})
        elif ctype == "audio":
            auds.append({
                "stream_index": s.get("index"),
                "codec_name": s.get("codec_name"),
                "profile": s.get("profile"),
                "sample_rate": first_int(s.get("sample_rate")),
                "channels": first_int(s.get("channels")),
                "channel_layout": s.get("channel_layout"),
                "bit_rate": first_int(s.get("bit_rate")),
                "duration_seconds": first_float(s.get("duration"))})
    return vids, auds


def video_gps_rows(ff: dict) -> list[dict]:
    """把 ffprobe 文件级 location tag 规范化为静态 GPS 点。"""
    fmt = ff.get("format", {}) if ff else {}
    tags = fmt.get("tags", {}) or {}
    if not isinstance(tags, dict):
        return []
    rows = []
    for key in sorted(tags, key=lambda x: (str(x).casefold(), str(x))):
        if str(key).casefold() != "location":
            continue
        raw_values = tags[key] if isinstance(tags[key], list) else [tags[key]]
        for raw in raw_values:
            parsed = parse_iso6709_location(raw)
            if parsed is None:
                continue
            latitude, longitude, altitude = parsed
            rows.append({
                "point_index": len(rows),
                "timestamp_seconds": None,
                "gps_latitude": latitude,
                "gps_longitude": longitude,
                "gps_altitude": altitude,
                "source": f"ffprobe:format.tags.{key}",
                "raw_value": raw,
            })
    return rows


# === ExifTool stay_open 工作器 ===
class ExifToolWorker:
    def __init__(self, exiftool_path: str):
        self.path = exiftool_path
        self.count = 0
        self._proc = None
        self._reader_thread = None
        self._q: queue.Queue = queue.Queue()
        self._start()

    def _start(self):
        self._proc = subprocess.Popen(
            [self.path, "-stay_open", "True", "-@", "-"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL)
        self._q = queue.Queue()
        t = threading.Thread(target=self._reader, args=(self._proc, self._q),
                             daemon=True)
        t.start()
        self._reader_thread = t

    @staticmethod
    def _reader(proc, q):
        try:
            for line in proc.stdout:
                q.put(line)
        except (OSError, ValueError):
            pass
        finally:
            q.put(None)

    def _release_process(self, proc):
        thread = self._reader_thread
        if thread is not None:
            thread.join(timeout=1)
        for stream in (proc.stdin, proc.stdout):
            if stream is not None and not stream.closed:
                try:
                    stream.close()
                except OSError:
                    pass
        if self._proc is proc:
            self._proc = None
            self._reader_thread = None

    def _kill(self):
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
        finally:
            self._release_process(proc)

    def restart(self):
        self._kill()
        self._start()
        self.count = 0

    def execute(self, args: list[str], timeout: float = ET_TIMEOUT_S) -> bytes:
        guard_exiftool_args(args)
        payload = "\n".join(args) + "\n-execute\n"
        self._proc.stdin.write(payload.encode("utf-8"))
        self._proc.stdin.flush()
        out = bytearray()
        deadline = time.monotonic() + timeout
        while True:
            remain = deadline - time.monotonic()
            if remain <= 0:
                self.restart()
                raise TimeoutError("exiftool 命令超时")
            try:
                line = self._q.get(timeout=min(remain, 1.0))
            except queue.Empty:
                continue
            if line is None:
                self.restart()
                raise RuntimeError("exiftool 进程意外退出")
            if line.strip() == b"{ready}":
                break
            out += line
        self.count += 1
        if self.count >= ET_RESTART_EVERY:
            self.restart()
        return bytes(out)

    def extract(self, file_path: str, photo_profile: bool,
                timeout: float = ET_TIMEOUT_S) -> dict:
        args = (ET_PHOTO_ARGS if photo_profile else ET_VIDEO_ARGS) + [file_path]
        out = self.execute(args, timeout)
        docs = json.loads(out.decode("utf-8", "replace")) if out.strip() else []
        if not docs:
            raise RuntimeError("exiftool 无输出（文件不可解析？）")
        return docs[0]

    def close(self):
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.stdin.write(b"-stay_open\nFalse\n")
                proc.stdin.flush()
                proc.wait(timeout=5)
        except Exception:
            self._kill()
            return
        self._release_process(proc)


def ffprobe_full(ffprobe_path: str, file_path: str,
                 timeout: float = FF_TIMEOUT_S) -> dict:
    r = subprocess.run([ffprobe_path] + FF_ARGS + [file_path],
                       capture_output=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError("ffprobe 失败：" +
                           r.stderr.decode("utf-8", "replace")[:200])
    return json.loads(r.stdout.decode("utf-8", "replace"))


# === 阶段执行（汇合状态机；逐文件断点续传） ===
_PHOTO_KINDS = {"photo_raw", "photo_jpeg", "image_gif", "photo_working"}
_VIDEO_KINDS = {"video_mp4", "video_crm"}
_AV_KINDS = _VIDEO_KINDS | {"audio"}      # 音频与视频共用双后端管线


def _insert_row(con, table: str, entry_id: int, row: dict, parser: str,
                parser_version: str):
    row = dict(row)
    row["entry_id"] = entry_id
    row["parser"] = parser
    row["parser_version"] = parser_version
    row["parsed_at_utc"] = core.now_utc_iso()
    cols = ", ".join(row)
    ph = ", ".join("?" for _ in row)
    con.execute(f"INSERT INTO {table} ({cols}) VALUES ({ph})",
                tuple(row.values()))


def _insert_payload(con, entry_id: int, provider: str, doc: dict, version: str):
    p = make_payload(doc)
    con.execute("INSERT INTO raw_payloads (entry_id, provider, payload_zlib,"
                " payload_sha256, uncompressed_bytes, provider_version,"
                " profile_version, parsed_at_utc) VALUES (?,?,?,?,?,?,?,?)",
                (entry_id, provider, p.zlib_blob, p.sha256,
                 p.uncompressed_bytes, version, PROFILE_VERSION,
                 core.now_utc_iso()))


def _record_error(con, entry_id: int, code: str, msg: str):
    con.execute("INSERT INTO errors (entry_id, stage, error_code, message,"
                " occurred_at_utc) VALUES (?, 'metadata', ?, ?, ?)",
                (entry_id, code, str(msg)[:500], core.now_utc_iso()))


def _persist_diagnostics(con, entry_id: int, rows: list[dict]) -> dict:
    counts = {"warning": 0, "error": 0, "validation": 0}
    observed = core.now_utc_iso()
    for row in rows:
        con.execute(
            "INSERT INTO metadata_diagnostics"
            " (entry_id,provider,severity,diagnostic_code,field_name,"
            " message,raw_value,observed_at_utc) VALUES (?,?,?,?,?,?,?,?)",
            (entry_id, row["provider"], row["severity"],
             row["diagnostic_code"], row.get("field_name"),
             str(row["message"])[:1000], row.get("raw_value"), observed))
        severity = row["severity"]
        counts[severity] = counts.get(severity, 0) + 1
        if severity == "error":
            _record_error(
                con, entry_id, row["diagnostic_code"], row["message"])
    return counts


def _merge_diagnostic_stats(stats: dict, counts: dict) -> None:
    for severity, value in counts.items():
        key = f"diagnostic_{severity}"
        stats[key] = stats.get(key, 0) + value


def process_metadata_stage(con: sqlite3.Connection, tools: dict,
                           retain_original_metadata: bool = True,
                           timeout_policy: dict | None = None,
                           on_progress=None,
                           should_stop=None,
                           on_current=None) -> dict:
    core.ensure_metadata_diagnostics_table(con)
    et_ver = tools["exiftool"]["version"]
    ff_ver = tools["ffprobe"]["version"]
    zip_ver = "python-zipfile " + ".".join(map(str, __import__("sys").version_info[:3]))
    sz_ver = tools["sevenzip"]["version"]
    roots = dict(con.execute("SELECT root_id, root_path FROM roots").fetchall())
    if not retain_original_metadata:
        # 基础元数据仍解析有规范化落点的格式；真正没有规范化表的 other
        # 才是不适用。
        con.execute("UPDATE entries SET meta_status='not_applicable'"
                    " WHERE meta_status='pending' AND media_kind='other'")
    con.execute("UPDATE entries SET meta_status='skipped'"
                " WHERE meta_status='pending' AND is_placeholder=1")
    con.commit()
    todo = con.execute(
        "SELECT entry_id, root_id, rel_path, extension, media_kind, size_bytes,"
        " modified_at_utc FROM entries WHERE meta_status='pending'"
        " ORDER BY entry_id").fetchall()
    selected_timeout_policy = dict(
        exiftool_timeout_policy() if timeout_policy is None else timeout_policy)
    exiftool_timeout_for_size(0, selected_timeout_policy)  # 启动前验证策略
    stats = {
        "total": len(todo), "done": 0, "error": 0, "timeout": 0,
        "unstable": 0, "ffprobe_payloads": 0,
        "ffprobe_optional_unreadable": 0,
        "ffprobe_optional_timeouts": 0,
        "diagnostic_warning": 0, "diagnostic_error": 0,
        "diagnostic_validation": 0,
        "exiftool_timeout_policy": selected_timeout_policy,
    }
    if should_stop is not None and should_stop():
        raise core.StageControlBoundary(
            "metadata controlled stage boundary")
    worker = ExifToolWorker(tools["exiftool"]["path"])
    try:
        for i, (eid, rid, rel, ext, kind, size0, mtime0) in enumerate(todo, 1):
            if should_stop is not None and should_stop():
                con.commit()
                raise core.StageControlBoundary(
                    "metadata controlled stage boundary")
            if on_current is not None:
                on_current(rel)
            path = os.path.join(roots[rid], rel)
            ext_path = core.to_extended_path(path)
            et_timeout = exiftool_timeout_for_size(
                size0, selected_timeout_policy)
            status = "done"
            try:
                for tbl in ("photo_metadata", "video_metadata", "working_metadata",
                            "document_metadata", "archive_metadata",
                            "archive_members",
                            "video_gps_points", "video_streams",
                            "audio_streams", "raw_payloads",
                            "metadata_diagnostics"):
                    con.execute(f"DELETE FROM {tbl} WHERE entry_id=?", (eid,))
                con.execute(
                    "DELETE FROM errors WHERE entry_id=? AND stage='metadata'",
                    (eid,))
                if kind in _PHOTO_KINDS:
                    doc = worker.extract(
                        path, photo_profile=True, timeout=et_timeout)
                    idx = build_tag_index(doc)
                    diagnostics = reported_diagnostics(doc)
                    normalized = photo_row(idx, ext, diagnostics)
                    _insert_row(con, "photo_metadata", eid, normalized,
                                "exiftool", et_ver)
                    if kind == "photo_working":
                        _insert_row(con, "working_metadata", eid,
                                    working_row(idx, ext), "exiftool", et_ver)
                    if retain_original_metadata:
                        _insert_payload(con, eid, "exiftool", doc, et_ver)
                    diagnostic_counts = _persist_diagnostics(
                        con, eid, diagnostics)
                    _merge_diagnostic_stats(stats, diagnostic_counts)
                    if diagnostic_counts["error"]:
                        status = "error"
                elif kind in _AV_KINDS:
                    doc = None
                    ff = None
                    errors = []
                    try:
                        doc = worker.extract(
                            path, photo_profile=False, timeout=et_timeout)
                    except TimeoutError:
                        status = "timeout"
                        _record_error(
                            con, eid, "exiftool_timeout",
                            f"{path}（timeout={et_timeout}s；size_bytes={size0}）")
                    except Exception as exc:
                        errors.append(("exiftool_error", exc))
                    try:
                        ff = ffprobe_full(tools["ffprobe"]["path"], path)
                    except subprocess.TimeoutExpired:
                        status = "timeout"
                        _record_error(con, eid, "ffprobe_timeout", path)
                    except Exception as exc:
                        errors.append(("ffprobe_error", exc))
                    idx = build_tag_index(doc) if doc else {}
                    if doc or ff:
                        diagnostics = reported_diagnostics(doc)
                        diagnostics.extend(
                            av_validation_diagnostics(kind, size0, ff))
                        vids, auds = stream_rows(ff or {})
                        if kind == "audio" and not auds:
                            auds = audio_stream_rows_from_exif(idx)
                        normalized = video_row(idx, ff, diagnostics)
                        if normalized["stream_count"] is None and (vids or auds):
                            normalized["stream_count"] = len(vids) + len(auds)
                        _insert_row(con, "video_metadata", eid, normalized,
                                    "exiftool+ffprobe",
                                    f"exiftool {et_ver}; ffprobe {ff_ver}")
                        gps_points = (video_gps_rows(ff or {})
                                      if kind in _VIDEO_KINDS else [])
                        for r in gps_points:
                            r2 = dict(r)
                            r2["entry_id"] = eid
                            cols = ", ".join(r2)
                            con.execute(
                                f"INSERT INTO video_gps_points ({cols}) VALUES"
                                f" ({', '.join('?' for _ in r2)})",
                                tuple(r2.values()))
                        for r in vids:
                            r2 = dict(r)
                            r2["entry_id"] = eid
                            cols = ", ".join(r2)
                            con.execute(f"INSERT INTO video_streams ({cols}) VALUES"
                                        f" ({', '.join('?' for _ in r2)})",
                                        tuple(r2.values()))
                        diagnostic_counts = _persist_diagnostics(
                            con, eid, diagnostics)
                        _merge_diagnostic_stats(stats, diagnostic_counts)
                        if diagnostic_counts["error"] and status == "done":
                            status = "error"
                        for r in auds:
                            r2 = dict(r)
                            r2["entry_id"] = eid
                            cols = ", ".join(r2)
                            con.execute(f"INSERT INTO audio_streams ({cols}) VALUES"
                                        f" ({', '.join('?' for _ in r2)})",
                                        tuple(r2.values()))
                    if retain_original_metadata:
                        if doc:
                            _insert_payload(con, eid, "exiftool", doc, et_ver)
                        if ff:
                            _insert_payload(con, eid, "ffprobe", ff, ff_ver)
                            stats["ffprobe_payloads"] += 1
                    for code, exc in errors:
                        status = "error" if status == "done" else status
                        _record_error(con, eid, code, exc)
                elif kind == "document":
                    doc = worker.extract(
                        path, photo_profile=False, timeout=et_timeout)
                    idx = build_tag_index(doc)
                    _insert_row(con, "document_metadata", eid,
                                document_row(idx, ext), "exiftool", et_ver)
                    if retain_original_metadata:
                        _insert_payload(con, eid, "exiftool", doc, et_ver)
                    diagnostic_counts = _persist_diagnostics(
                        con, eid, reported_diagnostics(doc))
                    _merge_diagnostic_stats(stats, diagnostic_counts)
                    if diagnostic_counts["error"]:
                        status = "error"
                elif kind == "archive":
                    if ext == "zip":
                        s = zip_summary(ext_path)
                        parser, ver = "python-zipfile", zip_ver
                    else:
                        s = sevenzip_summary(path, tools["sevenzip"]["path"], ext)
                        parser, ver = "7-Zip", sz_ver
                    members = s.pop("members", [])
                    _insert_row(con, "archive_metadata", eid, s, parser, ver)
                    con.executemany(
                        "INSERT INTO archive_members (entry_id, member_index,"
                        " member_path, is_dir, size_bytes, packed_bytes,"
                        " crc32_hex, method, flag_bits, host_os,"
                        " create_version, extract_version, header_offset,"
                        " modified_raw, attributes, encrypted)"
                        " VALUES (:eid, :member_index, :member_path, :is_dir,"
                        " :size_bytes, :packed_bytes, :crc32_hex, :method,"
                        " :flag_bits, :host_os, :create_version,"
                        " :extract_version, :header_offset, :modified_raw,"
                        " :attributes, :encrypted)",
                        [{**m, "eid": eid} for m in members])
                    if retain_original_metadata:
                        doc = worker.extract(
                            path, photo_profile=False, timeout=et_timeout)
                        _insert_payload(con, eid, "exiftool", doc, et_ver)
                        diagnostic_counts = _persist_diagnostics(
                            con, eid, reported_diagnostics(doc))
                        _merge_diagnostic_stats(stats, diagnostic_counts)
                        if diagnostic_counts["error"]:
                            status = "error"
                elif kind == "other":
                    # 没有规范化落点不等于 ExifTool 不可读取；全量元数据仍
                    # 为本地所有文件保存原文。
                    if retain_original_metadata:
                        doc = worker.extract(
                            path, photo_profile=False, timeout=et_timeout)
                        _insert_payload(con, eid, "exiftool", doc, et_ver)
                        diagnostic_counts = _persist_diagnostics(
                            con, eid, reported_diagnostics(doc))
                        _merge_diagnostic_stats(stats, diagnostic_counts)
                        if diagnostic_counts["error"]:
                            status = "error"
            except TimeoutError:
                status = "timeout"
                _record_error(
                    con, eid, "exiftool_timeout",
                    f"{path}（timeout={et_timeout}s；size_bytes={size0}）")
            except Exception as exc:
                status = "error"
                _record_error(con, eid, type(exc).__name__, exc)
            # GIF 的 ffprobe 是 Raw 增补探测，用于保留帧时序等动画证据；
            # 失败不覆盖 ExifTool 主解析状态。其他非音视频类型不调用 ffprobe。
            if retain_original_metadata and ext == "gif":
                try:
                    ff_optional = ffprobe_full(
                        tools["ffprobe"]["path"], path)
                except subprocess.TimeoutExpired:
                    stats["ffprobe_optional_timeouts"] += 1
                except Exception:
                    stats["ffprobe_optional_unreadable"] += 1
                else:
                    try:
                        _insert_payload(
                            con, eid, "ffprobe", ff_optional, ff_ver)
                    except Exception as exc:
                        status = "error" if status == "done" else status
                        _record_error(
                            con, eid, "ffprobe_payload_error", exc)
                    else:
                        stats["ffprobe_payloads"] += 1
            # 逐文件即时核对解析前后的 size/mtime
            try:
                st = os.stat(ext_path, follow_symlinks=False)
                if st.st_size != size0 or core.ns_to_utc_iso(st.st_mtime_ns) != mtime0:
                    status = "unstable"
            except OSError:
                status = "unstable"
            con.execute("UPDATE entries SET meta_status=? WHERE entry_id=?",
                        (status, eid))
            stats[status if status in stats else "error"] = \
                stats.get(status, 0) + 1
            if i % 200 == 0:
                con.commit()
            if on_progress and i % 10 == 0:
                on_progress(i, stats)
        con.commit()
    finally:
        worker.close()
    if should_stop is not None and should_stop():
        raise core.StageControlBoundary(
            "metadata controlled stage boundary")
    return stats

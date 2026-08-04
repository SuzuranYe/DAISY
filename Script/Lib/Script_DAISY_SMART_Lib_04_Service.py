"""GUI 与 CLI 共用的扫描、目标确认和采集服务。"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import Script_DAISY_SMART_Lib_01_Core as core
import Script_DAISY_SMART_Lib_02_Windows as windows
import Script_DAISY_SMART_Lib_03_Smartctl as smartctl


_CRITICAL_ATA_ATTRIBUTE_IDS = (
    0x05,
    0x0A,
    0xB8,
    0xBB,
    0xBC,
    0xC4,
    0xC5,
    0xC6,
    0xC7,
    0xC8,
)
_RAW_NONZERO_ATTENTION_IDS = frozenset(
    {0x05, 0x0A, 0xB8, 0xBB, 0xBC, 0xC4, 0xC5, 0xC6, 0xC7}
)


def _display_bool(value: Any) -> str:
    if value is True:
        return "是"
    if value is False:
        return "否"
    return "未提供"


def _display_optional(value: Any, *, suffix: str = "") -> str:
    if value is None or core.clean_text(value) == "":
        return "未提供"
    return f"{value}{suffix}"


def _wear_summary(record: core.WindowsDiskRecord, value: Any) -> str:
    text = _display_optional(value, suffix="%")
    physical_disk = record.data.get("physical_disk")
    media_type = (
        core.clean_text(physical_disk.get("media_type")).upper()
        if isinstance(physical_disk, dict) else ""
    )
    if text != "未提供" and media_type == "HDD":
        return f"{text}（Windows 返回；HDD 不一定适用）"
    return text


def _critical_smart_lines(payload: dict[str, Any]) -> list[str]:
    attributes = payload.get("ata_smart_attributes")
    table = attributes.get("table") if isinstance(attributes, dict) else None
    by_id = {
        item_id: item
        for item in core.as_list(table)
        if isinstance(item, dict)
        for item_id in (core.int_or_none(item.get("id")),)
        if item_id is not None
    }
    lines: list[str] = []
    for item_id in _CRITICAL_ATA_ATTRIBUTE_IDS:
        item = by_id.get(item_id)
        if item is None:
            continue
        raw = item.get("raw")
        if not isinstance(raw, dict):
            raw = {}
        raw_value = raw.get("string")
        if raw_value is None or core.clean_text(raw_value) == "":
            raw_value = raw.get("value")
        failed = core.clean_text(item.get("when_failed"))
        raw_number = core.int_or_none(raw.get("value"))
        if failed:
            status = f"异常（{failed}）"
        elif (
            item_id in _RAW_NONZERO_ATTENTION_IDS
            and raw_number is not None
            and raw_number > 0
        ):
            status = "注意（RAW 非零，未触发阈值）"
        else:
            status = "未触发阈值"
        lines.append(
            f"  {item_id:02X} {item.get('name') or '未命名属性'}｜"
            f"RAW {_display_optional(raw_value)}｜"
            f"当前 {_display_optional(item.get('value'))}｜"
            f"最差 {_display_optional(item.get('worst'))}｜"
            f"阈值 {_display_optional(item.get('thresh'))}｜状态 {status}"
        )
    if lines:
        return lines

    nvme = payload.get("nvme_smart_health_information_log")
    if isinstance(nvme, dict):
        critical_warning = core.int_or_none(nvme.get("critical_warning"))
        fields = (
            (
                "Critical Warning",
                f"0x{critical_warning:02X}" if critical_warning is not None else None,
                "",
            ),
            ("可用备用空间", nvme.get("available_spare"), "%"),
            ("备用空间阈值", nvme.get("available_spare_threshold"), "%"),
            ("寿命已用", nvme.get("percentage_used"), "%"),
            ("非安全关机次数", nvme.get("unsafe_shutdowns"), ""),
            ("介质与数据完整性错误", nvme.get("media_errors"), ""),
            ("错误日志条目", nvme.get("num_err_log_entries"), ""),
        )
        return [
            f"  {label}：{_display_optional(value, suffix=suffix)}"
            for label, value, suffix in fields
        ]
    return ["  设备未提供预设关键 SMART 属性。"]


def scan_targets(
    *,
    smartctl_path: str | os.PathLike[str] | None = None,
    powershell_path: str | os.PathLike[str] | None = None,
) -> core.ScanResult:
    warnings: list[str] = []
    windows_records: tuple[core.WindowsDiskRecord, ...] = ()
    smart_devices: tuple[core.SmartDevice, ...] = ()
    executable: str | None = None
    version: str | None = None

    try:
        inventory = windows.read_inventory(
            detailed=False,
            powershell=powershell_path,
            timeout=60,
        )
        windows_records = inventory.records
        warnings.extend(inventory.warnings)
    except core.DaisySmartError as exc:
        warnings.append(f"Windows 存储清单不可用：{exc}")

    try:
        scan = smartctl.scan(smartctl_path)
        smart_devices = scan.devices
        executable = scan.executable
        version = scan.version
        warnings.extend(scan.warnings)
    except core.DaisySmartError as exc:
        warnings.append(f"smartctl 扫描不可用：{exc}")

    if not windows_records and not smart_devices:
        raise core.DaisySmartError("没有取得任何物理盘清单。" + "；".join(warnings))

    windows_by_number = {record.disk_number: record for record in windows_records}
    smart_by_number: dict[int, list[core.SmartDevice]] = {}
    unmatched_smart: list[core.SmartDevice] = []
    for device in smart_devices:
        if device.disk_number is None:
            unmatched_smart.append(device)
        else:
            smart_by_number.setdefault(device.disk_number, []).append(device)

    targets: list[core.DiskTarget] = []
    all_numbers = sorted(set(windows_by_number) | set(smart_by_number))
    for disk_number in all_numbers:
        devices = smart_by_number.get(disk_number, [])
        if len(devices) > 1:
            warnings.append(
                f"PhysicalDrive{disk_number} 对应多个 smartctl 扫描项；"
                f"默认使用 {devices[0].name} -d {devices[0].device_type}。"
            )
        targets.append(
            core.DiskTarget(
                disk_number=disk_number,
                windows=windows_by_number.get(disk_number),
                smart_device=devices[0] if devices else None,
            )
        )
    for device in unmatched_smart:
        targets.append(
            core.DiskTarget(
                disk_number=None,
                windows=None,
                smart_device=device,
            )
        )
    targets.sort(
        key=lambda target: (
            target.disk_number is None,
            target.disk_number if target.disk_number is not None else 1_000_000,
            target.stable_key,
        )
    )
    return core.ScanResult(
        targets=tuple(targets),
        warnings=core.unique_nonempty(warnings),
        smartctl_executable=executable,
        smartctl_version=version,
    )

def target_by_disk_number(scan: core.ScanResult, disk_number: int) -> core.DiskTarget:
    matches = [target for target in scan.targets if target.disk_number == disk_number]
    if len(matches) != 1:
        raise core.DaisySmartError(
            f"PhysicalDrive{disk_number} 在当前清单中不是唯一目标，请重新扫描。"
        )
    return matches[0]


def _health_summary(payload: dict[str, Any]) -> str:
    passed = payload.get("smart_status")
    if isinstance(passed, dict) and isinstance(passed.get("passed"), bool):
        return "PASSED" if passed["passed"] else "FAILED"
    nvme = payload.get("nvme_smart_health_information_log")
    if isinstance(nvme, dict):
        critical = core.int_or_none(nvme.get("critical_warning"))
        if critical is not None:
            return "PASSED" if critical == 0 else f"警告位 0x{critical:02X}"
    return "smartctl 未提供统一健康结论"


def render_collection_report(
    target: core.DiskTarget,
    record: core.WindowsDiskRecord,
    smart: core.SmartRead,
    *,
    started_at_utc: str,
    collected_at_utc: str,
    collected_at_local: str,
    warnings: tuple[str, ...],
) -> str:
    labels = "；".join(record.explorer_names) or "无盘符或无卷标"
    collection_status = core.classify_collection_status(
        smart.exit_status,
        has_warnings=bool(warnings),
    )
    status_label = {
        "complete": "完整",
        "complete_with_warnings": "完整，但有提示",
        "incomplete": "不完整，仅可作为诊断记录",
    }[collection_status]
    disk = record.disk
    smart_flags = core.decode_smartctl_exit_status(smart.exit_status)
    critical_smart_lines = _critical_smart_lines(smart.payload)
    lines = [
        f"{core.APP_TITLE} v{core.APP_VERSION}",
        "=" * 72,
        f"采集开始（UTC）：{started_at_utc}",
        f"采集完成（UTC）：{collected_at_utc}",
        f"采集完成（本地）：{collected_at_local}",
        f"目标：{target.physical_label}",
        f"资源管理器名称：{labels}",
        f"型号：{record.model or '未提供'}",
        f"序列号：{record.serial or '未提供'}",
        f"容量：{core.format_bytes(record.size)}",
        f"总线／分区样式：{record.bus_type or '未提供'} / "
        f"{record.partition_style or '未提供'}",
        f"Windows 健康状态：{disk.get('health_status') or '未提供'}",
        f"Windows 只读属性：{_display_bool(disk.get('is_read_only'))}",
        f"SMART 结论：{_health_summary(smart.payload)}",
        f"smartctl 退出状态：{smart.exit_status}",
        "smartctl 状态说明：" + ("；".join(smart_flags) if smart_flags else "无"),
        f"采集完整性：{status_label}（{collection_status}）",
        "",
        "关键 SMART 属性：",
        *critical_smart_lines,
        "",
        "分区与卷：",
    ]
    if not record.partitions:
        lines.append("  未返回分区。")
    for partition in record.partitions:
        volume = (
            partition.get("volume")
            if isinstance(partition.get("volume"), dict) else {}
        )
        letter = volume.get("drive_letter") or partition.get("drive_letter") or "无盘符"
        label = core.clean_text(volume.get("file_system_label")) or "无卷标"
        file_system = core.clean_text(volume.get("file_system")) or "无文件系统／未提供"
        percent = volume.get("used_percent")
        percent_text = f"{float(percent):.2f}%" if percent is not None else "—"
        lines.append(
            f"  分区 {partition.get('partition_number')}｜"
            f"{partition.get('type') or '未知类型'}｜{letter}｜{label}｜"
            f"{file_system}｜容量 {core.format_bytes(core.int_or_none(partition.get('size')))}"
        )
        if volume:
            lines.append(
                f"    已用 {core.format_bytes(core.int_or_none(volume.get('used_bytes')))}｜"
                f"剩余 {core.format_bytes(core.int_or_none(volume.get('size_remaining')))}｜"
                f"使用率 {percent_text}｜状态 {volume.get('health_status') or '未提供'}"
            )

    reliability = record.data.get("storage_reliability_counter")
    if isinstance(reliability, dict):
        lines.extend(
            [
                "",
                "Windows 可靠性摘要：",
                "  通电小时："
                + _display_optional(reliability.get("power_on_hours")),
                "  磨损：" + _wear_summary(record, reliability.get("wear_percent")),
                "  未校正读／写错误："
                + _display_optional(reliability.get("read_errors_uncorrected"))
                + " / "
                + _display_optional(reliability.get("write_errors_uncorrected")),
            ]
        )
    if warnings:
        lines.extend(["", "采集提示：", *(f"  - {warning}" for warning in warnings)])
    lines.extend(
        [
            "",
            "采集边界：只使用 Windows 查询接口与 smartctl 扫描／-x 读取；"
            "不启动自检、不修改 SMART 或磁盘设置。",
            "注意：读取可能唤醒休眠硬盘；SMART 不能保证未来不会故障。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def collect_target(
    target: core.DiskTarget,
    *,
    smartctl_path: str | os.PathLike[str] | None = None,
    powershell_path: str | os.PathLike[str] | None = None,
    smartctl_version: str | None = None,
) -> core.CollectionResult:
    if target.disk_number is None:
        raise core.DaisySmartError("该 smartctl 项无法可靠关联 Windows 物理盘编号。")
    if target.windows is None:
        raise core.DaisySmartError("该目标缺少 Windows 物理盘清单，不能建立完整归档。")
    if target.smart_device is None:
        raise core.DaisySmartError("该物理盘没有可用的 smartctl 扫描项。")

    started_at_utc = core.utc_now_iso()
    inventory = windows.read_inventory(
        target.disk_number,
        detailed=True,
        powershell=powershell_path,
        timeout=90,
    )
    record = inventory.records[0]
    windows.assert_same_disk(target.windows, record)
    smart = smartctl.read_all(
        target.smart_device,
        explicit=smartctl_path,
        timeout=150,
        known_version=smartctl_version,
    )
    collected_local: datetime = core.local_now()
    collected_at_utc = core.utc_iso(collected_local)
    collected_at_local = collected_local.isoformat(timespec="seconds")
    warnings = list(inventory.warnings)
    if target.smart_device.open_error:
        warnings.append("smartctl 扫描提示：" + target.smart_device.open_error)
    warnings.extend(
        f"smartctl：{message}"
        for message in smartctl.messages(smart.payload)
    )
    merged_warnings = core.unique_nonempty(warnings)
    current_target = core.DiskTarget(
        disk_number=target.disk_number,
        windows=record,
        smart_device=target.smart_device,
    )
    report = render_collection_report(
        current_target,
        record,
        smart,
        started_at_utc=started_at_utc,
        collected_at_utc=collected_at_utc,
        collected_at_local=collected_at_local,
        warnings=merged_warnings,
    )
    return core.CollectionResult(
        target=current_target,
        windows=record,
        smart=smart,
        started_at_utc=started_at_utc,
        collected_at_utc=collected_at_utc,
        collected_at_local=collected_at_local,
        warnings=merged_warnings,
        report=report,
    )

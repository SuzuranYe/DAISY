"""使用 Windows 自带只读接口登记物理盘、分区与卷信息。"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import Script_DAISY_Lib_STG_01_Core as core


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SOURCE_CMDLETS_SUMMARY = ("Get-Disk", "Get-Partition", "Get-Volume")
SOURCE_CMDLETS_DETAILED = SOURCE_CMDLETS_SUMMARY + (
    "Get-PhysicalDisk",
    "Get-StorageReliabilityCounter",
    "Get-StorageAdvancedProperty",
    "Get-CimInstance Win32_DiskDrive",
    "Get-CimInstance Win32_DiskPartition",
    "Get-CimInstance Win32_LogicalDisk",
    "Get-CimInstance Win32_Volume",
    "Get-BitLockerVolume（可选）",
)

FORBIDDEN_STORAGE_COMMANDS = (
    "Clear-Disk",
    "Disable-BitLocker",
    "Enable-BitLocker",
    "Format-Volume",
    "Initialize-Disk",
    "New-Partition",
    "Optimize-Volume",
    "Remove-Partition",
    "Repair-Volume",
    "Resize-Partition",
    "Set-Disk",
    "Set-Partition",
    "Set-Volume",
)


@dataclass(frozen=True)
class WindowsInventory:
    records: tuple[core.WindowsDiskRecord, ...]
    warnings: tuple[str, ...]
    powershell_executable: str
    powershell_version: str
    collected_at_utc: str
    detail_level: str


def find_powershell(explicit: str | os.PathLike[str] | None = None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    for name in ("powershell.exe", "powershell"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    candidates.append(
        Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0"
        / "powershell.exe"
    )
    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(os.path.abspath(candidate))
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_file():
            return candidate.resolve()
    raise core.DaisySmartError("未找到 Windows PowerShell，无法读取磁盘、分区和卷信息。")


def _powershell_script(
    disk_number: int | None = None,
    *,
    detailed: bool = False,
) -> str:
    target = "$null" if disk_number is None else str(int(disk_number))
    detail = "$true" if detailed else "$false"
    return rf"""
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$WarningPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'
$InformationPreference = 'SilentlyContinue'
$VerbosePreference = 'SilentlyContinue'
$TargetDiskNumber = {target}
$Detailed = {detail}
$Issues = [System.Collections.Generic.List[object]]::new()

function Add-Issue {{
    param([string]$Scope, [string]$Message)
    $Issues.Add([PSCustomObject]@{{ scope = $Scope; message = $Message }})
}}

function As-Strings {{
    param($Value)
    return @($Value | Where-Object {{ $null -ne $_ }} | ForEach-Object {{ [string]$_ }})
}}

function Clean-Token {{
    param($Value)
    return ([string]$Value).Trim().Replace(' ', '').ToUpperInvariant()
}}

$Disks = @()
try {{
    $Disks = @(Get-Disk -ErrorAction Stop | Sort-Object Number)
}} catch {{
    Add-Issue 'Get-Disk' $_.Exception.Message
}}
if ($null -ne $TargetDiskNumber) {{
    $Disks = @($Disks | Where-Object {{ $_.Number -eq $TargetDiskNumber }})
}}

$PhysicalDisks = @()
if ($Detailed) {{
    try {{
        $PhysicalDisks = @(Get-PhysicalDisk -ErrorAction Stop)
    }} catch {{
        Add-Issue 'Get-PhysicalDisk' $_.Exception.Message
    }}
}}

$BitLockerAvailable = $false
if ($Detailed) {{
    $BitLockerAvailable = $null -ne (Get-Command Get-BitLockerVolume -ErrorAction SilentlyContinue)
}}

$DiskRecords = foreach ($Disk in $Disks) {{
    $DiskScope = 'PhysicalDrive' + [string]$Disk.Number
    $PartitionRecords = @()
    $Partitions = @()
    try {{
        $Partitions = @(Get-Partition -DiskNumber $Disk.Number -ErrorAction Stop | Sort-Object Offset)
    }} catch {{
        Add-Issue $DiskScope ('Get-Partition: ' + $_.Exception.Message)
    }}

    foreach ($Partition in $Partitions) {{
        $PartitionScope = $DiskScope + '/Partition' + [string]$Partition.PartitionNumber
        $Volume = $null
        try {{
            $VolumeResults = @(Get-Volume -Partition $Partition -ErrorAction Stop)
            if ($VolumeResults.Count -gt 0) {{ $Volume = $VolumeResults[0] }}
        }} catch {{
            if ($Partition.DriveLetter) {{
                Add-Issue $PartitionScope ('Get-Volume: ' + $_.Exception.Message)
            }}
        }}

        $Win32LogicalDisk = $null
        $Win32Volume = $null
        $BitLocker = $null
        if ($Detailed -and $Partition.DriveLetter) {{
            $Drive = ([string]$Partition.DriveLetter).ToUpperInvariant() + ':'
            try {{
                $Win32LogicalDisk = @(Get-CimInstance -ClassName Win32_LogicalDisk -Filter ("DeviceID='{{0}}'" -f $Drive) -ErrorAction Stop)[0]
            }} catch {{
                Add-Issue $PartitionScope ('Win32_LogicalDisk: ' + $_.Exception.Message)
            }}
            try {{
                $Win32Volume = @(Get-CimInstance -ClassName Win32_Volume -Filter ("DriveLetter='{{0}}'" -f $Drive) -ErrorAction Stop)[0]
            }} catch {{
                Add-Issue $PartitionScope ('Win32_Volume: ' + $_.Exception.Message)
            }}
            if ($BitLockerAvailable) {{
                try {{
                    $Bl = @(Get-BitLockerVolume -MountPoint $Drive -ErrorAction Stop)[0]
                    if ($null -ne $Bl) {{
                        $BitLocker = [PSCustomObject]@{{
                            mount_point = [string]$Bl.MountPoint
                            volume_type = [string]$Bl.VolumeType
                            capacity_gb = $Bl.CapacityGB
                            volume_status = [string]$Bl.VolumeStatus
                            encryption_percentage = $Bl.EncryptionPercentage
                            encryption_method = [string]$Bl.EncryptionMethod
                            protection_status = [string]$Bl.ProtectionStatus
                            lock_status = [string]$Bl.LockStatus
                            auto_unlock_enabled = $Bl.AutoUnlockEnabled
                            key_protector_types = @(As-Strings @($Bl.KeyProtector | ForEach-Object {{ $_.KeyProtectorType }}))
                        }}
                    }}
                }} catch {{
                    Add-Issue $PartitionScope ('Get-BitLockerVolume: ' + $_.Exception.Message)
                }}
            }}
        }}

        $VolumeRecord = $null
        if ($null -ne $Volume) {{
            $VolumeRecord = [PSCustomObject]@{{
                object_id = [string]$Volume.ObjectId
                unique_id = [string]$Volume.UniqueId
                path = [string]$Volume.Path
                drive_letter = if ($Volume.DriveLetter) {{ ([string]$Volume.DriveLetter).ToUpperInvariant() + ':' }} else {{ $null }}
                file_system_label = [string]$Volume.FileSystemLabel
                file_system = [string]$Volume.FileSystem
                file_system_type = [string]$Volume.FileSystemType
                drive_type = [string]$Volume.DriveType
                health_status = [string]$Volume.HealthStatus
                operational_status = @(As-Strings $Volume.OperationalStatus)
                size = $Volume.Size
                size_remaining = $Volume.SizeRemaining
                allocation_unit_size = $Volume.AllocationUnitSize
                dedup_mode = [string]$Volume.DedupMode
                refs_dedup_mode = [string]$Volume.ReFSDedupMode
                is_dax = $Volume.IsDAX
                win32_logical_disk = if ($null -ne $Win32LogicalDisk) {{
                    [PSCustomObject]@{{
                        device_id = [string]$Win32LogicalDisk.DeviceID
                        volume_name = [string]$Win32LogicalDisk.VolumeName
                        volume_serial_number = [string]$Win32LogicalDisk.VolumeSerialNumber
                        file_system = [string]$Win32LogicalDisk.FileSystem
                        description = [string]$Win32LogicalDisk.Description
                        provider_name = [string]$Win32LogicalDisk.ProviderName
                        drive_type = $Win32LogicalDisk.DriveType
                        media_type = $Win32LogicalDisk.MediaType
                        size = $Win32LogicalDisk.Size
                        free_space = $Win32LogicalDisk.FreeSpace
                        compressed = $Win32LogicalDisk.Compressed
                        supports_disk_quotas = $Win32LogicalDisk.SupportsDiskQuotas
                        supports_file_based_compression = $Win32LogicalDisk.SupportsFileBasedCompression
                        maximum_component_length = $Win32LogicalDisk.MaximumComponentLength
                        status = [string]$Win32LogicalDisk.Status
                    }}
                }} else {{ $null }}
                win32_volume = if ($null -ne $Win32Volume) {{
                    [PSCustomObject]@{{
                        device_id = [string]$Win32Volume.DeviceID
                        drive_letter = [string]$Win32Volume.DriveLetter
                        label = [string]$Win32Volume.Label
                        serial_number = $Win32Volume.SerialNumber
                        file_system = [string]$Win32Volume.FileSystem
                        drive_type = $Win32Volume.DriveType
                        capacity = $Win32Volume.Capacity
                        free_space = $Win32Volume.FreeSpace
                        block_size = $Win32Volume.BlockSize
                        automount = $Win32Volume.Automount
                        boot_volume = $Win32Volume.BootVolume
                        system_volume = $Win32Volume.SystemVolume
                        page_file_present = $Win32Volume.PageFilePresent
                        crashdump = $Win32Volume.Crashdump
                        compressed = $Win32Volume.Compressed
                        dirty_bit_set = $Win32Volume.DirtyBitSet
                        indexing_enabled = $Win32Volume.IndexingEnabled
                        quotas_enabled = $Win32Volume.QuotasEnabled
                        quotas_incomplete = $Win32Volume.QuotasIncomplete
                        quotas_rebuilding = $Win32Volume.QuotasRebuilding
                        supports_disk_quotas = $Win32Volume.SupportsDiskQuotas
                        supports_file_based_compression = $Win32Volume.SupportsFileBasedCompression
                        status = [string]$Win32Volume.Status
                    }}
                }} else {{ $null }}
                bitlocker = $BitLocker
            }}
        }}

        $PartitionRecords += [PSCustomObject]@{{
            disk_number = $Partition.DiskNumber
            partition_number = $Partition.PartitionNumber
            drive_letter = if ($Partition.DriveLetter) {{ ([string]$Partition.DriveLetter).ToUpperInvariant() + ':' }} else {{ $null }}
            access_paths = @($Partition.AccessPaths | Where-Object {{ $_ }} | ForEach-Object {{ [string]$_ }})
            offset = $Partition.Offset
            size = $Partition.Size
            type = [string]$Partition.Type
            mbr_type = [string]$Partition.MbrType
            gpt_type = [string]$Partition.GptType
            guid = [string]$Partition.Guid
            is_read_only = $Partition.IsReadOnly
            is_offline = $Partition.IsOffline
            is_active = $Partition.IsActive
            is_boot = $Partition.IsBoot
            is_system = $Partition.IsSystem
            is_hidden = $Partition.IsHidden
            is_shadow_copy = $Partition.IsShadowCopy
            no_default_drive_letter = $Partition.NoDefaultDriveLetter
            operational_status = @(As-Strings $Partition.OperationalStatus)
            transition_state = [string]$Partition.TransitionState
            volume = $VolumeRecord
        }}
    }}

    $PhysicalDisk = $null
    $PhysicalDiskMatch = $null
    if ($Detailed -and $PhysicalDisks.Count -gt 0) {{
        $Matches = @($PhysicalDisks | Where-Object {{ [string]$_.DeviceId -eq [string]$Disk.Number }})
        if ($Matches.Count -eq 1) {{
            $PhysicalDisk = $Matches[0]
            $PhysicalDiskMatch = 'device_id'
        }} else {{
            $DiskSerial = Clean-Token $Disk.SerialNumber
            if ($DiskSerial) {{
                $Matches = @($PhysicalDisks | Where-Object {{ (Clean-Token $_.SerialNumber) -eq $DiskSerial }})
                if ($Matches.Count -eq 1) {{
                    $PhysicalDisk = $Matches[0]
                    $PhysicalDiskMatch = 'serial_number'
                }}
            }}
        }}
    }}

    $PhysicalDiskRecord = $null
    $AdvancedRecord = $null
    if ($null -ne $PhysicalDisk) {{
        $PhysicalDiskRecord = [PSCustomObject]@{{
            match_method = $PhysicalDiskMatch
            device_id = [string]$PhysicalDisk.DeviceId
            friendly_name = [string]$PhysicalDisk.FriendlyName
            manufacturer = [string]$PhysicalDisk.Manufacturer
            model = [string]$PhysicalDisk.Model
            serial_number = [string]$PhysicalDisk.SerialNumber
            unique_id = [string]$PhysicalDisk.UniqueId
            media_type = [string]$PhysicalDisk.MediaType
            bus_type = [string]$PhysicalDisk.BusType
            health_status = [string]$PhysicalDisk.HealthStatus
            operational_status = @(As-Strings $PhysicalDisk.OperationalStatus)
            usage = [string]$PhysicalDisk.Usage
            size = $PhysicalDisk.Size
            allocated_size = $PhysicalDisk.AllocatedSize
            can_pool = $PhysicalDisk.CanPool
            cannot_pool_reason = @(As-Strings $PhysicalDisk.CannotPoolReason)
            spindle_speed = $PhysicalDisk.SpindleSpeed
            firmware_version = [string]$PhysicalDisk.FirmwareVersion
            logical_sector_size = $PhysicalDisk.LogicalSectorSize
            physical_sector_size = $PhysicalDisk.PhysicalSectorSize
            physical_location = [string]$PhysicalDisk.PhysicalLocation
            object_id = [string]$PhysicalDisk.ObjectId
        }}
        try {{
            $Advanced = Get-StorageAdvancedProperty -PhysicalDisk $PhysicalDisk -ErrorAction Stop
            $AdvancedRecord = [PSCustomObject]@{{
                is_power_protected = $Advanced.IsPowerProtected
            }}
        }} catch {{
            Add-Issue $DiskScope ('Get-StorageAdvancedProperty: ' + $_.Exception.Message)
        }}
    }}

    $ReliabilityRecord = $null
    if ($Detailed) {{
        try {{
            $Reliability = Get-StorageReliabilityCounter -Disk $Disk -ErrorAction Stop
            $ReliabilityRecord = [PSCustomObject]@{{
                temperature_celsius = $Reliability.Temperature
                temperature_max_celsius = $Reliability.TemperatureMax
                wear_percent = $Reliability.Wear
                power_on_hours = $Reliability.PowerOnHours
                read_errors_total = $Reliability.ReadErrorsTotal
                read_errors_uncorrected = $Reliability.ReadErrorsUncorrected
                write_errors_total = $Reliability.WriteErrorsTotal
                write_errors_uncorrected = $Reliability.WriteErrorsUncorrected
                read_latency_max_ms = $Reliability.ReadLatencyMax
                write_latency_max_ms = $Reliability.WriteLatencyMax
                flush_latency_max_ms = $Reliability.FlushLatencyMax
            }}
        }} catch {{
            Add-Issue $DiskScope ('Get-StorageReliabilityCounter: ' + $_.Exception.Message)
        }}
    }}

    $Win32DiskDrive = $null
    $Win32Partitions = @()
    if ($Detailed) {{
        try {{
            $Win32 = @(Get-CimInstance -ClassName Win32_DiskDrive -Filter ("Index={{0}}" -f $Disk.Number) -ErrorAction Stop)[0]
            if ($null -ne $Win32) {{
                $Win32DiskDrive = [PSCustomObject]@{{
                    index = $Win32.Index
                    device_id = [string]$Win32.DeviceID
                    pnp_device_id = [string]$Win32.PNPDeviceID
                    caption = [string]$Win32.Caption
                    name = [string]$Win32.Name
                    model = [string]$Win32.Model
                    manufacturer = [string]$Win32.Manufacturer
                    serial_number = [string]$Win32.SerialNumber
                    firmware_revision = [string]$Win32.FirmwareRevision
                    interface_type = [string]$Win32.InterfaceType
                    media_type = [string]$Win32.MediaType
                    status = [string]$Win32.Status
                    status_info = $Win32.StatusInfo
                    config_manager_error_code = $Win32.ConfigManagerErrorCode
                    size = $Win32.Size
                    partitions = $Win32.Partitions
                    bytes_per_sector = $Win32.BytesPerSector
                    sectors_per_track = $Win32.SectorsPerTrack
                    total_cylinders = $Win32.TotalCylinders
                    total_heads = $Win32.TotalHeads
                    total_sectors = $Win32.TotalSectors
                    total_tracks = $Win32.TotalTracks
                    tracks_per_cylinder = $Win32.TracksPerCylinder
                    capabilities = @($Win32.Capabilities)
                    capability_descriptions = @(As-Strings $Win32.CapabilityDescriptions)
                    system_name = [string]$Win32.SystemName
                }}
            }}
        }} catch {{
            Add-Issue $DiskScope ('Win32_DiskDrive: ' + $_.Exception.Message)
        }}
        try {{
            $Win32Partitions = @(Get-CimInstance -ClassName Win32_DiskPartition -Filter ("DiskIndex={{0}}" -f $Disk.Number) -ErrorAction Stop | Sort-Object StartingOffset | ForEach-Object {{
                [PSCustomObject]@{{
                    index = $_.Index
                    disk_index = $_.DiskIndex
                    device_id = [string]$_.DeviceID
                    name = [string]$_.Name
                    caption = [string]$_.Caption
                    description = [string]$_.Description
                    type = [string]$_.Type
                    size = $_.Size
                    starting_offset = $_.StartingOffset
                    block_size = $_.BlockSize
                    number_of_blocks = $_.NumberOfBlocks
                    bootable = $_.Bootable
                    boot_partition = $_.BootPartition
                    primary_partition = $_.PrimaryPartition
                    status = [string]$_.Status
                }}
            }})
        }} catch {{
            Add-Issue $DiskScope ('Win32_DiskPartition: ' + $_.Exception.Message)
        }}
    }}

    [PSCustomObject]@{{
        disk_number = $Disk.Number
        disk = [PSCustomObject]@{{
            number = $Disk.Number
            path = [string]$Disk.Path
            location = [string]$Disk.Location
            friendly_name = [string]$Disk.FriendlyName
            manufacturer = [string]$Disk.Manufacturer
            model = [string]$Disk.Model
            serial_number = [string]$Disk.SerialNumber
            adapter_serial_number = [string]$Disk.AdapterSerialNumber
            firmware_version = [string]$Disk.FirmwareVersion
            unique_id = [string]$Disk.UniqueId
            unique_id_format = [string]$Disk.UniqueIdFormat
            number_of_partitions = $Disk.NumberOfPartitions
            operational_status = @(As-Strings $Disk.OperationalStatus)
            health_status = [string]$Disk.HealthStatus
            bus_type = [string]$Disk.BusType
            partition_style = [string]$Disk.PartitionStyle
            provisioning_type = [string]$Disk.ProvisioningType
            is_offline = $Disk.IsOffline
            offline_reason = [string]$Disk.OfflineReason
            is_read_only = $Disk.IsReadOnly
            is_system = $Disk.IsSystem
            is_boot = $Disk.IsBoot
            is_clustered = $Disk.IsClustered
            is_highly_available = $Disk.IsHighlyAvailable
            logical_sector_size = $Disk.LogicalSectorSize
            physical_sector_size = $Disk.PhysicalSectorSize
            size = $Disk.Size
            allocated_size = $Disk.AllocatedSize
            largest_free_extent = $Disk.LargestFreeExtent
        }}
        partitions = $PartitionRecords
        physical_disk = $PhysicalDiskRecord
        storage_reliability_counter = $ReliabilityRecord
        storage_advanced_properties = $AdvancedRecord
        win32_disk_drive = $Win32DiskDrive
        win32_disk_partitions = $Win32Partitions
    }}
}}

[PSCustomObject]@{{
    collected_at_utc = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
    detail_level = if ($Detailed) {{ 'detailed' }} else {{ 'summary' }}
    powershell_version = $PSVersionTable.PSVersion.ToString()
    administrator = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    source_cmdlets = if ($Detailed) {{
        @('Get-Disk','Get-Partition','Get-Volume','Get-PhysicalDisk','Get-StorageReliabilityCounter','Get-StorageAdvancedProperty','Get-CimInstance Win32_DiskDrive','Get-CimInstance Win32_DiskPartition','Get-CimInstance Win32_LogicalDisk','Get-CimInstance Win32_Volume','Get-BitLockerVolume (optional)')
    }} else {{ @('Get-Disk','Get-Partition','Get-Volume') }}
    disks = @($DiskRecords)
    issues = @($Issues)
}} | ConvertTo-Json -Depth 12 -Compress
"""


def assert_read_only_script(script: str) -> None:
    for command in FORBIDDEN_STORAGE_COMMANDS:
        if re.search(rf"(?i)(?<![\w-]){re.escape(command)}(?![\w-])", script):
            raise AssertionError(f"PowerShell 清单脚本包含禁止的写操作：{command}")


def _run_json_script(
    script: str,
    *,
    powershell: str | os.PathLike[str] | None = None,
    timeout: int = 75,
) -> tuple[dict[str, Any], Path]:
    assert_read_only_script(script)
    executable = find_powershell(powershell)
    try:
        process = subprocess.run(
            [
                str(executable),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        raise core.DaisySmartError(f"Windows 存储清单读取超过 {timeout} 秒。") from exc
    except OSError as exc:
        raise core.DaisySmartError(f"无法启动 Windows PowerShell：{exc}") from exc

    stdout = core.normalise_text(process.stdout).lstrip("\ufeff").strip()
    stderr = core.normalise_text(process.stderr).strip()
    if process.returncode != 0 or not stdout:
        detail = stderr or f"退出码 {process.returncode}，没有返回 JSON"
        raise core.DaisySmartError(f"Windows 存储清单读取失败：{detail}")
    stdout_noise = ""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as original_exc:
        payload = None
        lines = stdout.splitlines()
        for index in range(len(lines) - 1, -1, -1):
            candidate = "\n".join(lines[index:]).strip()
            if not candidate.startswith("{"):
                continue
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                payload = parsed
                stdout_noise = "\n".join(lines[:index]).strip()
                break
        if payload is None:
            raise core.DaisySmartError(
                f"Windows 存储清单返回了无效 JSON：{original_exc}\n\n{stdout[:800]}"
            ) from original_exc
    if not isinstance(payload, dict):
        raise core.DaisySmartError("Windows 存储清单 JSON 根节点不是对象。")
    if stdout_noise:
        payload.setdefault("issues", []).append(
            {"scope": "PowerShell warning output", "message": stdout_noise}
        )
    if stderr:
        payload.setdefault("issues", []).append(
            {"scope": "PowerShell stderr", "message": stderr}
        )
    return payload, executable


def _normalise_volume(volume: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(volume, dict):
        return None
    result = dict(volume)
    letter = core.clean_text(result.get("drive_letter")).upper().rstrip(":")
    result["drive_letter"] = f"{letter}:" if letter else None
    size = core.int_or_none(result.get("size"))
    remaining = core.int_or_none(result.get("size_remaining"))
    used: int | None = None
    percent: float | None = None
    if size is not None and remaining is not None and size >= remaining >= 0:
        used = size - remaining
        if size > 0:
            percent = round(used * 100.0 / size, 4)
    result["size"] = size
    result["size_remaining"] = remaining
    result["used_bytes"] = used
    result["used_percent"] = percent
    result["operational_status"] = [
        core.clean_text(item) for item in core.as_list(result.get("operational_status"))
        if core.clean_text(item)
    ]
    return result


def _layout_gaps(disk: dict[str, Any], partitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    disk_size = core.int_or_none(disk.get("size"))
    if disk_size is None or disk_size <= 0:
        return []
    ordered = sorted(
        partitions,
        key=lambda item: core.int_or_none(item.get("offset")) or 0,
    )
    gaps: list[dict[str, Any]] = []
    cursor = 0
    for partition in ordered:
        offset = core.int_or_none(partition.get("offset"))
        size = core.int_or_none(partition.get("size"))
        if offset is None or size is None or offset < 0 or size < 0:
            continue
        if offset > cursor:
            gaps.append(
                {
                    "kind": "leading_layout_gap" if cursor == 0 else "inter_partition_gap",
                    "offset": cursor,
                    "size": offset - cursor,
                    "end_offset_exclusive": offset,
                    "note": "地址布局间隙；可能包含 GPT/MBR 保留区，不等同于可分配空间。",
                }
            )
        cursor = max(cursor, offset + size)
    if cursor < disk_size:
        gaps.append(
            {
                "kind": "trailing_layout_gap",
                "offset": cursor,
                "size": disk_size - cursor,
                "end_offset_exclusive": disk_size,
                "note": "地址布局间隙；可能包含 GPT/MBR 保留区，不等同于可分配空间。",
            }
        )
    return gaps


def _record_from_payload(
    item: dict[str, Any],
    *,
    warnings: tuple[str, ...],
    detail_level: str,
    collection: dict[str, Any],
) -> core.WindowsDiskRecord | None:
    disk_number = core.int_or_none(item.get("disk_number"))
    if disk_number is None:
        return None
    data = dict(item)
    data.pop("disk_number", None)
    disk = data.get("disk") if isinstance(data.get("disk"), dict) else {}
    partitions: list[dict[str, Any]] = []
    for raw in core.as_list(data.get("partitions")):
        if not isinstance(raw, dict):
            continue
        partition = dict(raw)
        offset = core.int_or_none(partition.get("offset"))
        size = core.int_or_none(partition.get("size"))
        partition["offset"] = offset
        partition["size"] = size
        partition["end_offset_exclusive"] = (
            offset + size if offset is not None and size is not None else None
        )
        partition["volume"] = _normalise_volume(partition.get("volume"))
        partition["operational_status"] = [
            core.clean_text(value)
            for value in core.as_list(partition.get("operational_status"))
            if core.clean_text(value)
        ]
        partitions.append(partition)
    data["disk"] = disk
    data["partitions"] = partitions
    data["layout_gaps"] = _layout_gaps(disk, partitions)
    data["collection"] = collection
    return core.WindowsDiskRecord(
        disk_number=disk_number,
        data=data,
        warnings=warnings,
        detail_level=detail_level,
    )


def read_inventory(
    disk_number: int | None = None,
    *,
    detailed: bool = False,
    powershell: str | os.PathLike[str] | None = None,
    timeout: int = 75,
) -> WindowsInventory:
    if os.name != "nt":
        raise core.DaisySmartError("Windows 存储清单仅支持 Windows。")
    script = _powershell_script(disk_number, detailed=detailed)
    payload, executable = _run_json_script(
        script, powershell=powershell, timeout=timeout
    )
    warnings = tuple(
        f"{core.clean_text(item.get('scope'))}：{core.clean_text(item.get('message'))}"
        for item in core.as_list(payload.get("issues"))
        if isinstance(item, dict) and core.clean_text(item.get("message"))
    )
    detail_level = core.clean_text(payload.get("detail_level")) or (
        "detailed" if detailed else "summary"
    )
    collected_at = core.clean_text(payload.get("collected_at_utc")) or core.utc_now_iso()
    source_cmdlets = [
        core.clean_text(item) for item in core.as_list(payload.get("source_cmdlets"))
        if core.clean_text(item)
    ]
    collection = {
        "collected_at_utc": collected_at,
        "detail_level": detail_level,
        "powershell_executable": str(executable),
        "powershell_version": core.clean_text(payload.get("powershell_version")),
        "administrator": bool(payload.get("administrator")),
        "source_cmdlets": source_cmdlets,
    }
    records: list[core.WindowsDiskRecord] = []
    for raw in core.as_list(payload.get("disks")):
        if not isinstance(raw, dict):
            continue
        record = _record_from_payload(
            raw,
            warnings=warnings,
            detail_level=detail_level,
            collection=collection,
        )
        if record is not None:
            records.append(record)
    records.sort(key=lambda record: record.disk_number)
    if disk_number is not None and not records:
        detail = "；".join(warnings) if warnings else "Get-Disk 未返回该编号"
        raise core.DaisySmartError(f"Windows 未找到 PhysicalDrive{disk_number}：{detail}")
    return WindowsInventory(
        records=tuple(records),
        warnings=warnings,
        powershell_executable=str(executable),
        powershell_version=collection["powershell_version"],
        collected_at_utc=collected_at,
        detail_level=detail_level,
    )


def assert_same_disk(
    earlier: core.WindowsDiskRecord,
    current: core.WindowsDiskRecord,
) -> None:
    if earlier.disk_number != current.disk_number:
        raise core.DaisySmartError("物理盘编号在采集前发生变化，请刷新硬盘列表。")
    if earlier.size is not None and current.size is not None and earlier.size != current.size:
        raise core.DaisySmartError("物理盘容量在采集前发生变化，可能发生了热插拔，请刷新列表。")
    if earlier.unique_id and current.unique_id and earlier.unique_id != current.unique_id:
        raise core.DaisySmartError("物理盘唯一标识在采集前发生变化，请刷新硬盘列表。")
    old_serial = re.sub(r"\s+", "", earlier.serial).casefold()
    new_serial = re.sub(r"\s+", "", current.serial).casefold()
    if old_serial and new_serial and old_serial != new_serial:
        raise core.DaisySmartError("物理盘序列号在采集前发生变化，请刷新硬盘列表。")


def render_report(record: core.WindowsDiskRecord) -> str:
    disk = record.disk
    read_only = disk.get("is_read_only")
    read_only_text = (
        "未提供" if read_only is None else "是" if read_only else "否"
    )
    lines = [
        "Windows 存储信息",
        "-" * 72,
        f"物理盘：PhysicalDrive{record.disk_number}",
        f"资源管理器名称：{'；'.join(record.explorer_names) or '无盘符或无卷标'}",
        f"型号：{record.model or '未提供'}",
        f"序列号：{record.serial or '未提供'}",
        f"总线：{record.bus_type or '未提供'}",
        f"分区样式：{record.partition_style or '未提供'}",
        f"容量：{core.format_bytes(record.size)}"
        + (f"（{record.size} 字节）" if record.size is not None else ""),
        f"逻辑／物理扇区：{disk.get('logical_sector_size') or '—'}／"
        f"{disk.get('physical_sector_size') or '—'} 字节",
        f"Windows 健康状态：{disk.get('health_status') or '未提供'}",
        f"只读属性：{read_only_text}",
        "",
        "分区与卷：",
    ]
    if not record.partitions:
        lines.append("  未返回分区。")
    for partition in record.partitions:
        number = partition.get("partition_number")
        volume = partition.get("volume") if isinstance(partition.get("volume"), dict) else {}
        letter = volume.get("drive_letter") or partition.get("drive_letter") or "无盘符"
        label = core.clean_text(volume.get("file_system_label")) or "无卷标"
        file_system = core.clean_text(volume.get("file_system")) or "无文件系统／未提供"
        lines.append(
            f"  分区 {number}｜{partition.get('type') or '未知类型'}｜{letter}｜"
            f"{label}｜{file_system}｜{core.format_bytes(core.int_or_none(partition.get('size')))}"
        )
        if volume:
            used = core.int_or_none(volume.get("used_bytes"))
            free = core.int_or_none(volume.get("size_remaining"))
            percent = volume.get("used_percent")
            percent_text = f"{float(percent):.2f}%" if percent is not None else "—"
            lines.append(
                f"    已用 {core.format_bytes(used)}｜剩余 {core.format_bytes(free)}｜"
                f"使用率 {percent_text}｜状态 {volume.get('health_status') or '未提供'}"
            )
    if record.layout_gaps:
        lines.extend(["", "地址布局间隙（可能含分区表保留区）："])
        for gap in record.layout_gaps:
            lines.append(
                f"  偏移 {gap.get('offset')}｜{core.format_bytes(core.int_or_none(gap.get('size')))}｜"
                f"{gap.get('kind')}"
            )
    reliability = record.data.get("storage_reliability_counter")
    if isinstance(reliability, dict):
        lines.extend(
            [
                "",
                "Windows 存储可靠性计数器：",
                f"  温度：{reliability.get('temperature_celsius')} °C",
                f"  通电小时：{reliability.get('power_on_hours')}",
                f"  磨损：{reliability.get('wear_percent')}",
                f"  未校正读／写错误：{reliability.get('read_errors_uncorrected')}／"
                f"{reliability.get('write_errors_uncorrected')}",
            ]
        )
    if record.warnings:
        lines.extend(["", "Windows 信息缺口／提示："])
        lines.extend(f"  - {warning}" for warning in record.warnings)
    return "\n".join(lines).rstrip() + "\n"

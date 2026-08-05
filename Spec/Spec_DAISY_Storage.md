# DAISY v1.5.0 STG 物理硬盘信息规格

## 一、定位与版本

DAISY v1.5.0 同时包含两个并列的信息域：DBS 获取文件树、文件信息、元数据、
哈希及其变化并保存为 SQLite；STG 获取物理硬盘、分区、卷和 smartctl 证据并
保存为 ZIP。两者共用统一 GUI、CLI、环境检测、管理员模式和测试入口，但没有
共享数据表，也不自动建立 SQLite 条目到某块物理盘的关联。

STG 功能域用于 Windows 单机上的只读硬盘信息登记与证据归档。GUI 只有一个硬盘
功能模块：`STG-11 硬盘信息登记`。同一页的「检测物理硬盘」按钮调用该模块脚本的
内部列盘模式并刷新硬盘池，不另占功能编号。统一 CLI 提供 `storage-list` 和
`storage-collect`，二者均由同一个 STG-11 Module 脚本分派。归档类型标识为
`PROFILE`，源码统一使用 `DAISY_Lib_STG` 和 `DAISY_Module_STG` 命名空间。

`archive_schema_version=3` 只表示 STG ZIP 协议，和快照／Diff 的 SQLite
`schema_version=3` 没有数据模型关系。STG 不导入 `sqlite3`，不创建、读取或修改
数据库。默认产物目录为 `Output/Storage`。当前只读取存储归档 schema 3，不兼容
早期协议；Manifest 中的应用版本为 `1.5.0`。

代码权威边界：

| 范围 | 文件 |
|---|---|
| 数据模型、命名、编码与摘要 | `Script/Lib/Script_DAISY_Lib_STG_01_Core.py` |
| Windows 存储清单 | `Script/Lib/Script_DAISY_Lib_STG_02_Windows.py` |
| smartctl 命令与解析 | `Script/Lib/Script_DAISY_Lib_STG_03_Smartctl.py` |
| 扫描关联、身份确认与报告 | `Script/Lib/Script_DAISY_Lib_STG_04_Service.py` |
| ZIP 生成、发布与核验 | `Script/Lib/Script_DAISY_Lib_STG_05_Archive.py` |
| `STG-11` 列盘与登记入口 | `Script/Module/Script_DAISY_Module_STG_11_Collect.py` |
| 统一 GUI／CLI 接入 | `Script/Script_DAISY_GUI.py`、`Script/Script_DAISY_MAIN.py` |

## 二、系统不变量

1. **物理盘只读**：不执行修改磁盘、分区、卷、文件系统、BitLocker 或 SMART
   设置的命令，也不启动 SMART 自检。
2. **物理盘优先**：采集目标以 Windows `DiskNumber` 和 smartctl 扫描项共同
   标识，不把盘符当作物理盘身份。
3. **采集前重新确认身份**：详细 Windows 查询后，必须与选择时的容量、
   `UniqueId` 和序列号比较；发现热插拔或编号复用即中止。
4. **原始证据保留**：smartctl 原始 JSON 及其中的 `smartctl.output` 不被摘要
   字段替代。
5. **缺口透明**：不支持、权限不足、超时和空字段都保留，不能伪装为 0、健康
   或通过。
6. **归档不可覆盖**：最终 ZIP 目标存在即失败，旧文件不得改变。
7. **文本统一**：正式 JSON 和可选 TXT 为 UTF-8 无 BOM、LF；ZIP 成员全部平铺。
8. **时间可审计**：清单保存 UTC 与本地偏移时间；文件名只用于人类排序。
   同一事件的 UTC 与本地字段必须由同一个带时区时间生成，并代表同一时刻。
9. **本地运行**：采集、归档和验证不联网、不上传、不遥测。
10. **完整性显式**：访问或命令层错误必须标为 `incomplete`，不得仅凭 ZIP
    成功生成就宣称登记完整。
11. **权限显式**：`STG-11` 的列盘与登记模式需要管理员权限才能完整运行。
    GUI 在模块说明、悬停说明、任务设置页和启动确认中显示要求，并提供顶部管理员
    模式开关，通过 Windows UAC 重启；开关悬停说明明确当前仅「硬盘信息登记」及其
    检测步骤需要此模式。
12. **发布后自检**：最终 ZIP 发布后必须自动执行完整归档核验；不提供可被误认为
    独立业务功能的手动核验模块。

## 三、只读命令边界

STG-11 的列盘和登记模式应在管理员模式下运行，以获得完整的 Windows
存储与 smartctl 资料。GUI 顶部管理员模式开关会先确认，再通过 Windows UAC 重启
当前应用；任务运行期间不可切换权限。未提权运行不放宽只读边界，只会如实记录
权限缺口、失败或 `incomplete` 诊断结果。

smartctl 扫描固定为：

```text
smartctl --scan-open --json=c
```

完整读取固定为：

```text
smartctl -x --json=ov -d <scan.type> <scan.name>
```

`-d` 的类型和设备名称只能来自当次扫描对象。实现显式禁止 `-t`、`--test`、
`-s`、`--smart`、`--set`、`-X`、`--abort` 等主动或修改选项，并始终使用参数
数组和 `shell=False` 默认语义。

PowerShell 脚本只允许查询命令。只读测试禁止 `Clear-Disk`、`Initialize-Disk`、
`Format-Volume`、`Set-Disk`、`Set-Partition`、`Set-Volume`、`New-Partition`、
`Remove-Partition`、`Resize-Partition`、`Repair-Volume`、`Optimize-Volume` 和
BitLocker 修改命令。

## 四、目标发现与关联

Windows 清单和 smartctl 扫描独立执行，任一失败时仍保留另一侧实际结果。关联
规则依次识别：

1. `PhysicalDriveN`；
2. `/dev/pdN`；
3. Windows smartmontools 的 `/dev/sdX` 编号规则。

Windows 盘存在而 smartctl 未发现时，STG-11 仍列出 Windows 目标并说明关联
缺口，但禁止为该项建立完整归档。smartctl 项无法关联 Windows
`DiskNumber` 时也列出，但不能当作完整目标。
同一物理盘出现多个 smartctl 项时保留提示并使用扫描顺序中的第一项。
GUI 硬盘池列出当次扫描到的全部有效 DiskNumber。脱机、Windows 资料缺失或
smartctl 未关联的设备保留在池中并显示原因，但复选框禁用。用户可逐项勾选，也可
点击「选择所有联机硬盘」选择所有联机且资料完整的设备。每块已选硬盘拆成独立
`队列 i/n` 子进程和独立 ZIP；即使只选一块也显示 `队列 1/1`。每次点击「检测物理
硬盘」都会先清除上一轮清单与选择。选择框使用 20 px 自绘指示器；检测结果显示
「若接入硬盘发生变化，请重新进行检测。」接入状态改变后不得沿用旧 DiskNumber。

## 五、Windows 数据模型

### 5.1 `disk`

保存 `Get-Disk` 的编号、路径、位置、FriendlyName、型号、序列号、固件、
UniqueId、运行和健康状态、总线、分区样式、离线／只读／系统／启动盘状态、
逻辑和物理扇区、总容量、已分配容量及最大空闲范围。

### 5.2 `partitions`

每个分区保存编号、盘符、全部 AccessPath、偏移、长度、结束偏移、类型、GPT／
MBR 类型、GUID、只读／离线／活动／启动／系统／隐藏／影子副本状态以及运行
状态。无盘符和无文件系统分区不得丢弃。

### 5.3 `volume`

卷保存资源管理器卷标、盘符、卷 GUID 路径、文件系统、驱动器类型、健康和运行
状态、容量、剩余、计算所得已用、使用率、分配单元、去重和 DAX 字段。

详细模式补充 `Win32_LogicalDisk`、`Win32_Volume` 与可选 BitLocker 状态。
BitLocker 只登记算法、加密百分比、保护／锁定状态和保护器类型；不保存恢复密钥
或 KeyProtector ID。

### 5.4 物理与可靠性补充

`Get-PhysicalDisk` 保存介质类型、转速、固件、池状态和物理位置。匹配方法必须
登记为 `device_id` 或 `serial_number`。`Get-StorageReliabilityCounter` 保存驱动
实际提供的温度、磨损、通电小时、错误和最大延迟；缺失不能解释为 0。

Win32 数据保留 PNP Device ID、传统几何与能力描述，用于兼容性和诊断，不作为
容量或身份的第一权威来源。

### 5.5 布局间隙

实现按磁盘大小、分区 offset 和 size 推导地址空间间隙。前导和尾部间隙可能是
GPT／MBR 元数据，不能直接称为可分配未分配空间。正式 JSON 同时保留
`AllocatedSize` 与 `LargestFreeExtent`，由调用方自行解释。

## 六、空间语义

```text
used_bytes = size - size_remaining
used_percent = used_bytes / size * 100
```

仅在 `size >= size_remaining >= 0` 时计算。无文件系统、锁定卷、未挂载卷或驱动
未提供容量时为 `null`，不是 0。

## 七、产物与 ZIP

归档内部固定包含：

| 路径 | 语义 |
|---|---|
| `<前缀>_Manifest.json` | 版本、身份、命令、来源、缺口及成员声明 |
| `<前缀>_Smartctl.json` | smartctl 原始 JSON，包含结构化字段和 `output` |
| `<前缀>_Storage.json` | 完整 Windows 物理盘、分区、卷和可靠性数据 |

`<前缀>` 为 `<卷标或回退>_PROFILE_YYYY-MM-DD_HH-MM-SS`。3 个文件全部位于 ZIP
根目录；成员名不含最终 ZIP 指纹，避免哈希自引用。内部不保存逐文件 SHA-256。
Manifest 与 Storage JSON 的应用字段记录 DAISY 名称、版本和作者。

GUI 在“外部简化 TXT”下拉项选择“生成 ZIP 外部 TXT”，或 CLI 使用
`--summary-txt` 时，在 ZIP 同目录生成
`<完整ZIP基名>_Report.txt`。该文件不属于归档，记录人类可读的硬盘身份、SMART
总体结论、关键 SMART 属性、分区、空间、可靠性和警告；不记录温度、关联 ZIP
文件名或 SHA-256，默认不生成。缺失值显示为“未提供”，布尔值显示为“是／否”；
HDD 的 Windows 磨损值明确注明不一定适用。关键风险计数 RAW 非零时显示“注意”，
但只有 smartctl 的 `when_failed` 非空时才标为“异常”。TXT 标题区记录生成工具名、
DAISY 版本和作者。

Manifest 的 `collection.status` 取值如下：

- `complete`：smartctl 读取完整，且没有采集提示；
- `complete_with_warnings`：读取完整，但存在可选 Windows 查询缺口、健康位或
  历史记录提示；
- `incomplete`：smartctl 退出状态的 `0x01`、`0x02` 或 `0x04` 位存在，说明命令
  解析、设备打开／识别或 SMART 命令失败。该 ZIP 仅为诊断归档。

ZIP 发布顺序：

1. 在目标目录以微秒和随机 ID 创建 `.partial.zip`；
2. 写入两个原始／结构化 payload 和 Manifest；
3. 重新打开并执行 CRC 与成员集合复核；
4. 对稳定 ZIP 字节计算完整 SHA-256；
5. 取摘要前 8 个十六进制字符并转大写；
6. 构造最终名称；
7. 以目标存在即失败的方式发布；冲突时保留 partial。
8. 对最终 ZIP 自动执行完整核验：文件名指纹、安全路径、成员集合、Manifest、
   时间对、成员字节数和 CRC 必须全部通过；失败时保留已发布 ZIP 并明确报错。
9. 若选择简化报告，再以 no-clobber 方式发布外部 TXT；极端竞态导致 TXT 发布
   失败时，已经发布的 ZIP 保留，并明确报告 TXT partial 的位置。

最终名称：

```text
<卷标或回退>_PROFILE_YYYY-MM-DD_HH-MM-SS_XXXXXXXX.zip
```

多卷标按分区顺序去重后用 `+` 连接。无卷标则回退盘符，再回退
`PhysicalDriveN`。文件名不使用序列号作为默认人类标识。

完整 ZIP SHA-256 不写回 ZIP 内部，避免自引用。文件名只保留高 32 bit；完整
摘要由生成结果和核验命令输出。

## 八、创建后自动核验准入

核验必须同时满足：

- 文件名存在 8 位十六进制后缀且与 ZIP 实际 SHA-256 高 32 bit 相同；
- ZIP 无重复、目录、不安全或穿越路径；
- 按 ZIP 文件名前缀推导的 3 个 schema 3 文件精确匹配，不多不少；
- ZIP CRC 全部通过；
- Manifest schema 为当前版本；
- Manifest 的类型、平铺布局和成员名前缀与 ZIP 文件名一致；
- Manifest 中同一事件的 UTC 与本地时间可以解析、带时区且代表同一时刻；
- Manifest 的 payload 名称、角色及字节数与 ZIP 成员一致。

任一失败均令创建流程返回失败，不提供 `--force` 绕过。底层核验函数保留供创建
流程和测试调用，但不暴露为独立 GUI 功能、Module 脚本或统一 CLI 子命令。

## 九、错误与退出码

- CLI `0`：完整或带提示的完整采集；
- CLI `1`：诊断 ZIP 已生成，但采集状态为 `incomplete`；
- CLI `2`：环境、参数、采集、发布或创建后自动核验失败；
- smartctl 的位掩码不直接作为 DAISY 进程退出码；它写入 Manifest，并在选择
  外部简化报告时写入报告；
- smartctl 返回健康或历史错误位但 JSON 可解析时，采集仍可归档；
- 无 JSON、目标身份变化或完整关联缺失时拒绝建立完整归档。

## 十、测试边界

默认 `unittest` 使用合成设备、内存数据和系统临时目录，不读取真实硬盘。测试
入口为 `Script_DAISY_Test_Storage_Unit.py` 和
`Script_DAISY_Test_Storage_Read_Only.py`，覆盖：

- 盘号映射；
- Windows 卷空间与布局间隙；
- 热插拔身份保护；
- smartctl 固定只读模板；
- GUI／CLI 共用目标关联；
- ZIP 平铺内容、UTF-8／LF、CRC、文件名指纹、篡改和 no-clobber；
- PowerShell 禁止命令和 `shell=True` 审计。

实盘验证是显式的额外步骤，必须先重新列盘并按物理盘编号选择。

## 十一、已知限制

- RAID 控制器、厂商驱动、USB 桥和虚拟磁盘可能隐藏或改写 SMART；
- Windows `Healthy` 与 smartctl 结论来自不同层，不能互相替代；
- Storage Reliability Counter 不保证所有设备都实现；
- 卷空间是采集瞬间值，可能在 ZIP 写入前发生变化；
- 单个物理盘包含多个卷标时，文件名只承担人类提示，不是权威身份；
- 32 bit 文件名指纹存在碰撞，不能替代完整摘要、数字签名或外部校验清单。

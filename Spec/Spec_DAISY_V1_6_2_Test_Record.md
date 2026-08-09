# DAISY v1.6.2 测试记录

- 对应计划：[v1.6.2 正式发布计划](Spec_DAISY_V1_6_2_Release_Plan.md)
- 测试日期：2026-08-10
- 目标版本：v1.6.2
- 状态：本地发布门禁通过；分支与标签按第六节发布
- 前一稳定标签：v1.6.0
- v1.6.1 状态：未打标签的阶段性修改

## 一、安全与测试边界

- 测试只使用仓库文件、仓库测试生成物和系统临时目录。
- 不启动真实档案扫描、真实硬盘检测或真实外部媒体工具。
- 不读取本轮未授权的工作区外数据库，不影响用户已有进程。
- v1.4.1 FULL 专项使用仓库内冻结契约和测试动态生成的 schema 3 FULL 封存金样。
- 本记录中的“兼容”表示当前消费者只读兼容 v1.4.1 封存库，不表示旧程序可读取 schema 4，
  也不表示 v1.4.1 未完成快照可以续传。

## 二、环境与版本

| 项目 | 结果 |
|---|---|
| 操作系统 | Windows |
| Python | 3.14.5 |
| Tcl/Tk | 8.6/8.6，真实 Tk 窗口测试 |
| Git | 2.54.0.windows.1 |
| 当前分支与基线 | `Codex`；前一稳定基线 `v1.6.0` (`e06f9db`) |
| DBS 应用版本 | 1.6.2 |
| STG 应用版本 | 1.6.2 |
| 兼容基线 | v1.4.1/schema 3 FULL |

## 三、完整自动化回归

命令：

```powershell
python -B -W error -m unittest discover -s .\Script\Test -p "Script_DAISY_Test_*.py" -q
```

最终有效结果：**658/658 通过**，耗时 **160.460 秒**，退出码 0，failure 0、error 0；
`-W error` 下没有 warning。输出中的“无法创建运行事件证据，已拒绝开始扫描：fixture”是
故障注入用例的预期诊断，不是失败。

回归过程没有隐藏中间问题：

1. 第一轮 658 项中有 6 项失败。4 项是报告标题和中文错误提示已经统一、测试仍断言旧文案；
   1 项来自未启用 DPI 感知的测试根窗口污染后续 Tk 几何；1 项是核验按钮在
   `1100×850`、1.5 scaling、特大字号下横向溢出。
2. 修复后增加窄宽响应式核验布局、顶部六按钮内部留白适配和 DPI 测试隔离。第二轮仍发现
   2 项：异步布局回调在窗口销毁后残留 Tcl 命令，以及隐藏窗口没有执行真实几何。
3. 布局改为同步响应并使用透明真实窗口后，发现默认 1080p 中重复的单字段分区标题占用
   高度。单字段或与首字段重名的分区不再单列标题；默认完整解析不再显示无操作的统计栏。
4. `Script_DAISY_Test_GUI_Scan` 随后 40/40 通过；最终重新执行完整套件得到上述 658/658。

补充 UI 证据：6 个直接缺陷用例 6/6 通过；多字体／多字号／多比例两组矩阵 2/2 通过，
耗时 56.052 秒；最终 GUI_Scan 文件 40/40 通过，耗时 38.458 秒。默认 1080p 的完整扫描、
续传卡和完整数据库解析均不显示无意义滚动，真实内容溢出时仍可滚动到末项。

## 四、v1.4.1 FULL 专项

命令覆盖 Reader 金样契约、跨版本 Diff、统一核验兼容、问题报告、数据库解析
识别／规划／投影／写出／人读报告／CLI：

```powershell
python -B -W error -m unittest -q `
  Script.Test.Script_DAISY_Test_DBS_Reader `
  Script.Test.Script_DAISY_Test_DBS_Diff_Compatibility `
  Script.Test.Script_DAISY_Test_DBS_Verify `
  Script.Test.Script_DAISY_Test_DBS_Verify_CLI `
  Script.Test.Script_DAISY_Test_DBS_Verify_Compatibility `
  Script.Test.Script_DAISY_Test_DBS_Verify_Unified `
  Script.Test.Script_DAISY_Test_DBS_Issues `
  Script.Test.Script_DAISY_Test_DBS_Parse `
  Script.Test.Script_DAISY_Test_DBS_Parse_Planning `
  Script.Test.Script_DAISY_Test_DBS_Parse_Projection `
  Script.Test.Script_DAISY_Test_DBS_Parse_Run `
  Script.Test.Script_DAISY_Test_DBS_Parse_Human `
  Script.Test.Script_DAISY_Test_DBS_Parse_CLI
```

结果：**128/128 通过**，耗时 **16.971 秒**，退出码 0。

- 冻结契约：`daisy-v1.4.1-schema3-golden-v1`；来源版本 1.4.1、来源标签 v1.4.1、
  schema 3，包含 `full` profile、19 张快照表和 15 项 schema 3 能力。
- FULL 金样使用 `scan_kind=full`、`hash_coverage=full`、`metadata_storage=complete`，
  覆盖逐文件 SHA-256、规范化元数据、原始 payload 和重复内容读取。
- Diff 覆盖 3→3、3→4、4→3、4→4；反向比较正确交换 added/deleted，缺少的新证据保持
  `NULL`／不可用。
- 核验和问题报告区分未执行、0 条问题、不支持格式与真实问题；不支持格式只计数。
- 解析覆盖识别、方案、15 个 schema 3 模块投影、HTML/XLSX/CSV/JSONL、运行清单和 CLI。
- 只读用例在调用前后比较输入文件 SHA-256、字节数和 mtime，结果全部不变。

工作区内没有用户提供的真实 v1.4.1 数据库，因此本轮不会把合成金样描述为真实用户库。
v1.6.1 历史记录中的 44 个真实库只读清点与代表库导出结果仅作既有补充证据，本轮没有重新
访问其工作区外来源。

## 五、静态与文档审计

| 检查 | 结果 |
|---|---|
| Python 编译 | 通过；全部 `Script` 和启动脚本，缓存写入专用系统临时目录后清理 |
| PowerShell 解析 | 通过；1 个 `.ps1`，解析错误 0 |
| UTF-8 无 BOM／LF | 通过；84 个受跟踪文本文件 |
| Markdown 相对链接 | 通过；11 个 Markdown、57 个相对链接 |
| Wiki／Obsidian 双方括号链接 | 通过；0 个 |
| `git diff --check` | 通过 |
| v1.6.0 冻结文件差异 | 通过；Full Scan、Diff、Reader 三文件均为 0 |
| 版本一致性 | 通过；DBS、STG、README、技术规格和版本演进均为 v1.6.2 |
| 临时文件与未跟踪残留 | 通过；删除测试生成的 `.test_runtime` 与 `__pycache__`，缓存残留 0 |

## 六、发布记录

| 项目 | 结果 |
|---|---|
| `Codex` 发布提交 | `v1.6.2^{}` 所指提交 |
| `main` 快进 | `main == v1.6.2^{}` |
| 远端 `Codex`／`main` | `origin/Codex == origin/main == v1.6.2^{}` |
| 注释标签 `v1.6.2` | 标签说明 `DAISY v1.6.2` |
| 远端引用一致性 | 发布后以 `git ls-remote` 和本地解引用结果复核 |

第六节使用标签和分支名而非自引用提交哈希；实际哈希由 Git 引用提供。若远端发布失败，
不得把本地标签存在误报为远端发布成功。

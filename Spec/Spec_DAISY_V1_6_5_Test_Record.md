# DAISY v1.6.5 测试记录

- 对应计划：[v1.6.5 发布计划](Spec_DAISY_V1_6_5_Release_Plan.md)
- 测试日期：2026-08-10
- 状态：发布门禁通过，待提交与推送
- 基线提交：`6b54f57c390aaa8073a4357b71c2cfeccc8e61f9`

## 一、版本与数据契约

| 项目 | 值 |
|---|---|
| 发布性质 | v1.6.4 长期生产基线的维护补丁 |
| DBS 应用版本 | 1.6.5 |
| STG 应用版本 | 1.6.5 |
| 旧版 DBS schema | 3 |
| 统一扫描 schema | 4 |
| STG 归档 schema | 3 |
| 元数据 profile | 7 |
| 最低只读封存库兼容版本 | v1.4.1/schema 3 FULL |

应用版本发生变化，数据库、归档、元数据和恢复契约版本均未变化。

## 二、完整自动化套件

```powershell
python -B -W error -m unittest discover -s .\Script\Test -p "Script_DAISY_Test_*.py" -q
```

第一次运行结果：`675` 项中 `674` 项通过、`1` 项失败，用时 167.611 秒。失败用例仍断言
旧文案「检测前不能开始任务」，实现已按本版规范改为「完成环境检测后，才可选择并开始
核验」。修正过时断言后，先定向复测该用例：`1/1` 通过，用时 0.508 秒。

随后对修正后的最终代码原样重跑完整套件：`675/675` 通过，用时 157.529 秒，命令总耗时
158.3 秒，退出码 0。输出中的「无法创建运行事件证据，已拒绝开始扫描：fixture」来自故意
注入失败证据的拒绝启动测试，不是实际扫描故障。

## 三、静态与兼容门禁

v1.4.1 FULL 兼容专项覆盖 Reader、跨版本 Diff、统一核验、问题报告和数据库解析：

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

结果：`128/128` 通过，用时 21.798 秒，命令总耗时 22.4 秒，退出码 0。

| 门禁 | 结果 |
|---|---|
| Python 进程内逐文件编译 | 68/68 通过 |
| PowerShell AST 解析 | 1/1 通过 |
| UTF-8 无 BOM，且仅使用 LF | 88/88 通过 |
| Markdown 相对链接与无双方括号链接 | 19/19 通过 |
| `git diff --check` | 通过 |
| v1.6.4 至当前 `Script/Lib`／`Script/Module` 差异 | 仅 DBS、STG 两处应用版本号 |
| DBS／STG DDL、扫描、Diff 与冻结兼容入口 | 无修改 |

静态检查使用 `git ls-files --cached --others --exclude-standard`，覆盖已跟踪文件及本轮新增但
尚未提交的发布文档。第一次只统计已跟踪文件的结果未被采用；发现遗漏后扩大清单并在最终
文档固化后重新执行，表中记录的是最终完整结果。

## 四、发布引用

发布提交为本记录所在提交，精确对象以 `v1.6.5^{}` 为准。发布完成后从远端重新获取并确认
`origin/Codex == origin/main == v1.6.5^{}`；最终哈希在发布回执中报告。

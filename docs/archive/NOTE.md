# Archive

这个目录里的文档是 rawkit 2025-12 至 2026-06 早期版本,**已经被推翻不少**,留下来只是为了历史参照。最新文档在仓库根目录:`README.md` / `USAGE.md` / `TODO.md`。

## 主要分歧

- **定位变了**:从"瑞士军刀 + 五年弧线 + 替代 LrC 旁边那一格"收窄为"read-only / local / stateless 的 RAW 浏览 + 整理 CLI"。野心层全删,只留 V1。
- **命令面变了**:V1 = `ls` / `info` / `extract` / `render` / `organize` 五个,围绕"浏览 + 整理"。
- **命名变了**:`preview` → `extract`;`peek` / `stats` 被并进 `info`(同一个命令按输入类型多态:单文件 = 全字段,文件夹 = 整体 summary,`--by FOO` 钻维度)。
- **几乎所有"未来命令候选"被砍**:`stat` / `dupes` / `rate` / `keyword` / `preset` / `serve` / `caption` / `cluster` / `map` 等绝大多数都不再属于 V1。`verify` / `duplicates` 留作 V1.x 候选,但 V1 不做。

## 仍然成立的部分

- "硬约束"里的精神基本仍在,只是不再写成 6 条而是浓缩成一句:**Read-only · Local · Stateless · One question, one screen**。
- Python + uv + typer + rich + lark + rawpy + exiftool 这套技术栈不动。
- `--where` DSL 语法基本不动,只是字段集会扩(加 `hour` / `year` / `month` / `day` 4 个派生整数字段)。
- "Year 1 服务作者自己一个人就够它活下来"这条原则继承。

—— 2026-06-19 归档

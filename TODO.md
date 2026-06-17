# rawkit TODO — v0.1 MVP

> 范围：覆盖"拍完一卷照片"的一次真实工作流。MVP 跑通 = 作者本人能在不打开 LrC 的前提下完成预览/筛选/重命名/导出。

## MVP 工作流（验收剧本）

带最近一卷 ≥100 张 RAW，全程不开 LrC，跑通这两条管道：

```bash
raw ls ~/Pictures/2026-06-17/ --where 'iso<800 and lens~"50"' --json \
  | jq -r '.path' \
  | raw thumb - -o cull/ --size 1024

raw rename ~/Pictures/2026-06-17/ --pattern '{date}_{model}_{seq:04d}' --apply
```

跑通 = MVP 成立；卡壳处 = v0.2 的第一批 issue。

---

## 命令清单（v0.1 全部范围，5 个）

### 1. `raw thumb`
- [ ] 签名：`raw thumb FILES... [-o thumbs/] [--size 1024] [-f]`
- [ ] 抽**内嵌 JPEG 缩略图**（不解 RAW，快）；缺缩略图时 stderr 警告并跳过
- [ ] 默认输出 `./thumbs/`，文件已存在则跳过；`-f` 强制覆盖
- [ ] 接受：目录（递归） / 文件列表 / `-`（从 stdin 读路径）
- [ ] `--size` 暂时只裁不放大（内嵌缩略图通常 1620px 左右）

### 2. `raw exif`
- [ ] 签名：`raw exif FILE [--json] [--fields iso,lens,fnumber]`
- [ ] 默认人读对齐表
- [ ] `--json` 给脚本
- [ ] `--fields` 只输出指定字段
- [ ] 后端：先包 `exiftool`（subprocess + `-j`）

### 3. `raw ls` —— **MVP 心脏**
- [ ] 签名：`raw ls [DIR] [--where EXPR] [--json] [--sort time|name|iso]`
- [ ] 默认输出：`文件名  时间  机型  镜头  ISO  光圈  快门`（对齐表）
- [ ] `--json` 输出 JSONL（每行一对象，便于 `jq`）
- [ ] `--where` 表达式 v1（见下方"表达式语法"）
- [ ] 递归 + 自动识别 RAW 后缀（ARW/CR2/CR3/NEF/RAF/DNG/ORF/RW2…）
- [ ] 进度条：>50 文件且 stdout 是 TTY 才显示

### 4. `raw rename`
- [ ] 签名：`raw rename FILES... --pattern '{date}_{model}_{seq:04d}' [--dry-run|--apply]`
- [ ] **默认 `--dry-run`**：打印"原 → 新"对照表
- [ ] `--apply` 才真重命名
- [ ] 模式变量：`{date}` `{datetime}` `{model}` `{lens}` `{iso}` `{seq:NNNd}` `{orig}`
- [ ] **自动连带改 sidecar**（`.xmp` / `.json` / `.dop`）— 与 `mv` 的关键差异
- [ ] 冲突检测：目标重名直接报错 + 退出码 1

### 5. `raw export`
- [ ] 签名：`raw export FILES... [-o exports/] [--format jpeg|png] [--quality 90] [--size 2048]`
- [ ] 走 `rawpy` 全解码（非内嵌缩略图）
- [ ] v0.1 用 rawpy 默认参数；**不暴露**白平衡/曲线（那是 LrC 的活）
- [ ] 长边 resize 到 `--size`

---

## 表达式语法 v1（`--where`）

**这是公开 API，一旦发布只增不删。**

字段：
- 数值：`iso`、`fnumber`、`shutter`（秒，浮点）、`focal`（mm）
- 字符串：`lens`、`model`、`maker`
- 时间：`date`（YYYY-MM-DD）、`time`（HH:MM）

操作：
- 比较：`>` `<` `>=` `<=` `==` `!=`
- 字符串子串：`~"50mm"`（大小写不敏感）
- 布尔：`and` `or` `not`、括号 `()`

示例：
- `iso>3200 and lens~"50"`
- `(focal>=70 and focal<=200) or lens~"70-200"`
- `date>="2026-06-01" and not model~"iPhone"`

实现：50 行手写递归下降 或 `lark`。**禁止 `eval()`**。

---

## 全局约定（一次定死）

- [ ] 通用开关：`-v/--verbose`、`-q/--quiet`、`--json`、`-n/--dry-run`、`-f/--force`、`-o/--output`、`-h/--help`、`--version`
- [ ] 退出码：`0` 成功 / `1` 部分失败（详情进 stderr）/ `2` 用法错
- [ ] **stdout 只走数据，日志/进度走 stderr**（管道契约）
- [ ] `isatty()` 判断 TTY：非 TTY 自动关颜色/进度条
- [ ] `-` 代表 stdin/stdout；`--` 之后全部当文件名
- [ ] 路径全用 `pathlib.Path`，不写死分隔符
- [ ] `pipx install rawkit` 可装；shell 补全靠 `typer --install-completion`

---

## 不做的事（v0.1 明确划线）

- ❌ web UI / `raw serve`（→ v0.3）
- ❌ 任何编辑/调色（永远不做）
- ❌ SQLite 索引（→ v0.2，等 `ls` 慢到忍不了）
- ❌ sidecar 语义解析/合并（→ v0.3，Rosetta Stone 入口）
- ❌ 配置文件 `~/.config/rawkit/`（等第三次喊"默认值烦死了"再加）
- ❌ `--engine apple` / Core Image 后端（→ v0.2+）
- ❌ 交互式 TUI / 向导
- ❌ 自己实现 RAW 解码（永远依赖 libraw/rawpy）

---

## 施工顺序（建议）

1. [ ] **基础设施**：`output.py`（table/json 双轨打印 + TTY 检测）；`exit_codes.py`；统一日志到 stderr
2. [ ] **`raw exif`**（最简单，先打通 exiftool 链路与 `--json` 模式）
3. [ ] **`raw ls`**（不带 where；先能列）
4. [ ] **`--where` 表达式解析器**（独立模块 `query.py` + 单测覆盖全部 v1 语法）
5. [ ] **`raw thumb`**（移植 `dotbox/extract_thumb_from_raw.py`，加 stdin/`-`、`-f`、stderr 警告）
6. [ ] **`raw rename`**（dry-run 优先；sidecar 同步是亮点）
7. [ ] **`raw export`**（rawpy 全解码 + resize）
8. [ ] **Dogfood**：跑一次验收剧本，记 issue → v0.2

---

## 测试

- [ ] `tests/samples/` 放 2–3 个小 RAW（不同机型），git LFS 或外链
- [ ] `typer.testing.CliRunner` 端到端测每个子命令的 happy path + 一个错误路径
- [ ] `query.py` 单测覆盖所有操作符与组合
- [ ] CI：GitHub Actions matrix（macOS + Linux，py3.10/3.11/3.12）

---

## 之后（不在 v0.1）

- v0.2：SQLite 索引（`raw index build` / `raw ls` 改走索引）、`--engine apple`、`raw stats`（按镜头/ISO 分布）
- v0.3：`raw serve`（本地静态预览 UI）、sidecar 解析（XMP → EditOp）
- v1.0：Rosetta Stone 第一刀（LrC ↔ darktable 的曝光/对比度/高光阴影映射表）

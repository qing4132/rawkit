# TODO

> 从当前实际状态(`ls` / `extract` / `render` / `stats` 四个命令)到 V1 目标 surface(`ls` / `info` / `extract` / `render` / `organize` 五个)之间的差距。
> 项目还在内测期,所有这些都可能再被推翻。

---

## P0 · 命名与品牌

### 品牌名(`rawkit` 在 PyPI 被占)

候选:`lightbox` / `shutter` / `flip` / `darkroom` / `filmstrip` / 别的。
**未拍板**。这个不定,pyproject / git remote / CLI entry point 都没法改。

### 子命令改名

- ~~`preview` → `extract`~~ ✅ 完成(2026-06-19) — 改名同时:默认输出目录 `./previews` → `./jpegs`;模块 `preview.py` → `extract.py`;函数 `extract_preview` → `extract_jpeg`;异常 `PreviewExtractError` → `ExtractError`。文桃1依然 `tests/test_extract.py` 覆盖。
- `stats` → 并进 `info`(同一命令两态:`info FILE` = 全字段;`info DIR` = 整体 summary;`info DIR --by FOO` = 钻维度)
- `render` 可能改名为 `bake` / `develop` / `export`,**未拍板**。倾向暂时不改。

---

## P0 · 缺失的命令

### `info`(合并 `stats` + 新增单文件全字段视图)

- **`info <file>`**:把单个 RAW 的所有 EXIF 字段纵向 key-value 铺开(包括 GPS / Orientation / Flash / 图像尺寸 / 嵌入预览尺寸 / 文件大小 等)。目前完全不存在。
- **`info <dir>`**:把现有 `stats <dir>` 默认输出直接搬过来(已经稳定)。
- **`info <dir> --by FOO`**:把现有 `stats <dir> --by FOO` 直接搬过来。

实现路径:把 `stats` 整个 rename 成 `info`,然后给它加单文件分支。

### `info` 开工准备(2026-06-20)

- [ ] 确认 `info` CLI 约定: `info FILE` 走单文件 KV; `info DIR` 走现有 summary; `info DIR --by ...` 走现有维度分布。
- [ ] 明确 FILE/DIR 判定规则(混合输入时报错还是自动分流),并钉死退出码契约。
- [ ] 复用 `safe_batch_read` 的字段集,补单文件额外字段(文件大小、像素尺寸、嵌入预览尺寸)的读取来源。
- [ ] 新建 `tests/test_info.py`:先把 `stats` 的目录行为回归搬过去,再加单文件快照测试。
- [ ] `stats` 迁移策略:保留一版过渡 alias(`rawkit stats` 输出 deprecation 提示)还是直接删;待拍板。
- [ ] 文档同步: README 的 V1 surface 状态与 USAGE 命令章节统一改成 `info`。

> 目标是先做到“无行为变化地接管 stats 目录模式”,再增量上 `info FILE`。

### `organize`

按 EXIF 字段把文件 move 到分层目录。

```
rawkit organize ~/Pictures/卡里乱七八糟 -o ~/Pictures/sorted --by date
```

**待拍板的细节**(留到正式立项时):
- 默认目录格式:`2024/2024-11/` 还是 `2024-11-07/` 还是 `--layout` 模板?
- 多维 `--by`:`--by maker,date` → `~/sorted/Canon/2024-11/...`?(我建议 V1 支持)
- 碰撞:跳过 / 报错 / 重命名(`IMG_0001 (1).CR3`)?
- 默认 dry-run 与否?
- 允许 source == dest(原地整理)?

---

## ~~P1 · `--by` ↔ `--where` 字段对称性~~ ✅ 已完成(2026-06-19)

实现:`hour` / `year` / `month` / `day` 4 个整数派生字段:
- `exif._normalize` 从 `date` / `time` 切片出来,存进 record
- `query.py` grammar + `_NUMERIC_FIELDS` 加 4 个
- `stats.py` 改读 record 上的 `hour` 字段(替代原本字符串切片)
- 4 个 where DSL 单元测试

### 语义钉死(写进 USAGE)

> `hour` / `year` / `month` / `day` 是**整数桶 ID**,比较即比较桶号。
> - `hour > 6` ≡ `hour >= 7`,意思是"在 7 点桶或之后的桶",6:30 不在内。
> - 想做"6:00:00 这个时刻之后"请用 `time > "06:00:00"`。
> - `>` 与 `>=` 在整数桶上自然重合(SQL `WHERE month > 6` 同义),不是 bug。
> - `--where month==11` 跟 `--by month`(YYYY-MM 历时桶)语义不同但能配合:
>   `stats --by month -w 'month==11'` = 历年 11 月密度对比。

---

## P1 · 设计原则(写进 README / USAGE 顶部当护栏)

1. **Read-only · Local · Stateless · One question, one screen** —— 任何违反其中一条的新功能砍。
2. **任何能 `--by` 的 dim 都必须是 `--where` 的 field,反之亦然。**
3. **`--by` 一律单维**(`info` / `stats`),只有 `organize` 例外(目录结构必须多维)。

---

## P2 · 各命令的展示规则(已经默认遵守,但写下来当护栏)

`info --by` 三类维度的展示要求:

- **时间 / 周期类**(`hour` / `day` / `month` / `year`):空段是信息,要么显式保留零桶要么用分段缩写表达。**不要自动合并成 2-hour bucket** 那种歧义压缩。
- **频次类**(`camera` / `lens` / `maker`):空段无意义,按计数排序、top-N 截断。
- **光学 / 器材类**(`aperture` / `focal` / `shutter` / `iso`):空段更多反映"没那支镜头",关心的是分布形状。

每个 `--by` 维度有自己的展示味道,**不强求跨维度的视觉一致性**——用户一次只看一个。真正的分布分析靠 `--json | pandas`,我们不竞争。

---

## P2 · 不紧急但确定要做

- ~~`render` 对齐 `extract` 的输出路径与 resize 语义~~ ✅ 完成(2026-06-20)
	- resize 统一为 `--long/--short/--mp` 三互斥
	- `render -R` 输出镜像源目录结构
	- 同次运行输出冲突(含 case-insensitive) fail-fast

- `--by hour --full`:可选 flag,把空小时也打出来(默认就是分段缩写)。等用户真的提出再加。
- `extract --watch`:监卡新文件自动 extract?违反 stateless,**不做**。

---

## P3 · V1.x 候选(V1 不做)

- `verify`:检查 RAW 文件完整性(magic number / exiftool 能读全 / 字节读完不报错)。卡传输丢字节、长期 bit rot 时有用。
- `duplicates`:找重复 RAW(按内容 hash 或 datetime+model)。合并卡 / 整理旧硬盘时有用。
- `info` 列出 RAW 里**全部**内嵌 JPEG(不止 libraw 选的那张):当前 info 的 Embedded 行复用 extract,只有 1 张。要列全得另起 exiftool 枚举 PreviewImage / ThumbnailImage / JpgFromRaw / OtherImage。**仅当 extract 自身长出选择某张预览的能力时再做**——否则 info 看到 3 张、extract 只能拿 1 张的不对称会立刻引发新需求。dogfood 撞到再说。

---

## 永久砍掉(避免反复讨论)

| 候选            | 砍的原因 |
| -------------- | -------- |
| `cull` / `rate` | 跟 LrC 形成两层皮,作者明确反复否决 |
| `tag` / `edit` | 违反 read-only |
| `import`       | `organize` 把 source 指到 SD 卡就是 import |
| `map` / `gps`  | 要打开外部 viewer,违反单一职责 |
| `diff`         | `info a; info b; diff` 解决 |
| `serve` / `watch` / `daemon` | 违反 stateless |
| `caption` / `embed` / `cluster` / AI 层 | V2+ 才谈,V1 内别想 |
| `preset extract/apply` | LrC sidecar 写回方向,V2+,V1 不碰 |
| `analyze`(`stats` 单独存在做百分位 / cross-tab / time series) | 不跟 pandas 竞争 |

---

## 持续 dogfood 撞到的细节

ISO / aperture 这种 MakerNotes 污染坑大概率还有别的字段同病,但**不主动排查**,撞到再修(每次修法都是"锁 `EXIF:` 组 + 必要时 fallback")。这套模式已经写在 `src/rawkit/exif.py` 的 `_FIELD_MAP` 注释里。

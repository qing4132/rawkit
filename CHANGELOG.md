# rawkit 技术变更日志

> 按时间倒叙汇总性能、解析器、缓存等子系统的设计文档。最新进展置顶；
> 历史章节(`PERF.md` / `PERF-FIX.md` / `CACHE.md`)按原文逐字保留,
> 仅在标题前加发生日期,便于快速翻阅。

**索引(从新到旧):**

- [2026-06-23 — 解析器格式覆盖大扩与跨后端等价测试加固](#2026-06-23--解析器格式覆盖大扩与跨后端等价测试加固)
- [2026-06-22 — EXIF 缓存:把 20 秒打成 1 秒](#2026-06-22--exif-缓存把-20-秒打成-1-秒)(原 `CACHE.md`)
- [2026-06-22 — EXIF 后端重写:性能 + 内部架构](#2026-06-22--exif-后端重写性能--内部架构)(原 `PERF-FIX.md`)
- [2026-06-22 — 性能调研:为什么慢、瓶颈在哪、能换什么](#2026-06-22--性能调研为什么慢瓶颈在哪能换什么)(原 `PERF.md`)

---

<a id="2026-06-23--解析器格式覆盖大扩与跨后端等价测试加固"></a>

# 2026-06-23 — 解析器格式覆盖大扩与跨后端等价测试加固

> 这段是 `CACHE.md` 落地之后追加的工作。重点是把 lite 解析器从"5 种主流"
> 扩到"15+ 种主流 + 边角",并把跨后端等价测试从"漏字段静默通过"修成严格断言。

## TL;DR

- **新增格式分支**：X3F (Sigma Foveon)、MRW (Minolta/KONICA MINOLTA)；ORF 通过
  在 `_parse_tiff` magic 接受集加入 `0x4F52` (`'IIRO'` 头) 直接走通。
- **关键 RAF 修正**：`PERF-FIX.md §3.2` 旧描述说"偏移 `0x54` 处 BE-uint32 指向
  TIFF"，**错**。实际指向**嵌入 JPEG 的 SOI**，EXIF 在 JPEG 的 APP1/`Exif\0\0`
  段里。新增 `_find_jpeg_exif_tiff()` 走 JPEG marker 序列定位。
- **Phase One IIQ 维度修正**：IIQ 把 thumbnail (640×480) 塞在 IFD0、把真 raw
  (11608×8708) 塞在 ExifIFD 的 `ExifImageWidth/Height`。`_copy_exif` 加了
  "当前 < 1 MP 时认作 thumbnail，用 Exif* 覆盖"的启发式，不影响 Canon CR2 这种
  IFD0 = 1936×1288 合法 preview 的情况。
- **Leaf MOS 兜底**：新增 `_xmp_fill_missing()` 在 head 里 tag-soup 扫
  `<x:xmpmeta>...</x:xmpmeta>`，把 `tiff:Make` / `tiff:Model` /
  `exif:DateTimeOriginal` 补进缺字段——MOS 的这三项只活在 XMP 里。
- **LibRaw 1970 哨兵过滤**：`_augment_from_rawpy` 跳过 `ts.year == 1970`。
  LibRaw 把 "无 DateTime" 用 Unix epoch 0 表示，本地时区一转就变成
  `1970-01-01 08:00:00` 这种假数据进用户记录。
- **测试覆盖**：`samples/extras/` 从 10 个边角文件扩到 **18 个**，新增 8 个
  2022+ 主流机型样本(R5 II / R6 III / A7R V / A9 III / Z 8 / Zf / X-T5 /
  X100VI)，全部跨后端等价 PASS。
- **测试漏洞修复**：原 `if key in et and key in lite` 让"lite 丢字段"静默通过；
  新增 `REQUIRED_IF_EXIFTOOL_HAS = (maker, model, date, year, month)` 严格断言，
  exiftool 有的话 lite 必须也有。
- **基线**：`298 passed`；`samples/extras` 集 28 个 lite 测试 ×  18 个真样本 ×
  15+ 种格式的 lite↔exiftool 等价对比，全绿。

## 1. 起因

`CACHE.md` 落地后跑全库,稳定可用。但又一回审视 `_exif_lite.py` 的格式覆盖,
发现：

1. PERF-FIX 时代的"跨后端等价测试"只覆盖 5 种格式(CR3/ARW/DNG/RW2/3FR)、
   13 个文件。`_exif_lite.read_metadata` 表面声称"format-agnostic",**所有非
   这 5 种格式从来没真跑过**。
2. `cli.RAW_EXTS` 列了 38 种扩展名,而测试发现器 `_sample_files()` 硬编码 7 种,
   等于让 90% 的扩展名沉默。
3. RAF 的"现有实现"对常见 Fuji 文件不报错只是因为 fallback 兜得住——切到
   实测才发现 `_parse_raf` 把 JPEG 偏移当 TIFF 偏移解，必然空返。

于是这一轮的工作目标 = **诚实地把 lite 覆盖到主流相机用户会遇到的所有
RAW 格式,并让测试在丢字段时大声 fail**。

## 2. 下载真样本的工程化:`scripts/fetch_samples.py`

新增 nix/bazel 风格的样本拉取管线:

- 清单在 `tests/fixtures/extra_samples.toml`,每条 `[[file]]` 记
  `url / sha256 / format / camera / license / source / notes`。
- `sha256 = ""` 时下载后打印观察值,贴回再 verify。
- `samples/extras/` 整个目录 `.gitignored`(~520 MB)。
- 失败的 URL 不致整批中断,继续往下走。

样本源主用 [raw.pixls.us](https://raw.pixls.us/) (CC0 的 Apache autoindex,
路径 `/data/<Make>/<Model>/<file>`)。

清单 18 条:
- **2022+ 主流(8 条新增)**:Canon EOS R5 Mark II / R6 Mark III、
  Sony A7R V / A9 III、Nikon Z 8 / Zf、Fujifilm X-T5 / X100VI。
- **冷门/老格式覆盖(10 条早期建立)**:CR2 (40D)、CRW (PowerShot A610)、
  X3F (Sigma DP1 Merrill)、PEF (Pentax K-r)、NRW (Coolpix P1100)、
  SRW (Samsung EX1)、IIQ (Phase One IQ3 100MP)、ERF (Epson R-D1s)、
  MOS (Leaf Aptus 22)、MRW (Minolta α-7 Digital)。

跑法：

```bash
uv run python scripts/fetch_samples.py
RAWKIT_TEST_SAMPLES=samples/extras uv run pytest tests/test_exif_lite.py -v
```

## 3. 新解析器分支

### 3.1 X3F (Sigma Foveon) — `_parse_x3f`

X3F 是 Sigma 自己的容器:

- 文件头 `'FOVb'` magic + 版本
- 中段是若干"section":`SECi`(image)、`SECp`(properties)、`SECc`(CAMF)
- 文件末尾有 `SECd` directory：12 字节 entry × N (`offset[4] + length[4] + type[4]`)
- 文件最后 4 字节 = directory offset 指针(LE uint32)

走法:

1. 读文件末 4 字节 → directory offset
2. seek 过去,验 `SECd`,读 count
3. 在 entries 里找 type=`'IMA2'` 且 SECi 头里 `format == 18` (JPEG) 的
4. SECi header 28 字节后是 JPEG 数据
5. `_find_jpeg_exif_tiff` 走 APP1 找 `Exif\0\0` + TIFF block
6. `_parse_tiff` 出结果

### 3.2 MRW (Minolta / KONICA MINOLTA) — `_parse_mrw`

容器:

- 4 字节 `\x00MRM` magic + BE uint32 总长
- 子块循环,每块 4 字节 tag(实际是 `\x00` + 3 ASCII,如 `\x00PRD` / `\x00WBG` /
  `\x00RIF` / `\x00TTW`) + BE uint32 长 + payload
- **`\x00TTW`** 块的 payload 就是一个自包含标准 TIFF

实现是 30 行的循环 + 命中 TTW 就交给 `_parse_tiff(head, data_off)`。

> 第一次写的时候 tag 比较成 `b"TTW"` 直接错过——MRW 的 tag 全部都有 `\x00`
> 前缀(可能是 32-bit alignment 的产物)。

### 3.3 ORF (Olympus) — `_parse_tiff` magic 接受集扩展

OM 系机型(OM-1、OM-5 系列)的 ORF 文件 magic 是 `IIRO` = `II` little-endian +
0x4F52,标准 TIFF magic 检查会拒。修法:`magic in (0x002A, 0x0055, 0x4F52)`。

之前的测试漏 ORF 是因为 `_sample_files()` 没扫 `.orf` 后缀,
samples/ 里那张 `P6080123.ORF` 一直没被等价测试覆盖,**有 bug 没被发现**。
拓展扩展名集后直接暴露。

### 3.4 RAF 的根本性修正

旧代码：

```python
if suffix == ".raf":
    head = _read_file_head(path, HEAD_SIZE)
    if len(head) < 0x58: return {}
    tiff_off = int.from_bytes(head[0x54:0x58], "big")
    return _parse_tiff(head, tiff_off)
```

**这把 `0x54..0x58` 当 TIFF 偏移用,实际它是 JPEG 偏移**。RAF 把 EXIF 藏在
嵌入 JPEG 的 APP1 段里。新代码：

```python
if suffix == ".raf":
    head = _read_file_head(path, HEAD_SIZE)
    if len(head) < 0x58: return {}
    jpeg_off = int.from_bytes(head[0x54:0x58], "big")
    tiff_off = _find_jpeg_exif_tiff(head, jpeg_off)
    if tiff_off is None: return {}
    return _parse_tiff(head, tiff_off)
```

`_find_jpeg_exif_tiff(buf, jpeg_off)` 走 JPEG marker 序列:
- 跳过 fill 0xFF
- 标准 marker 后跟 2 字节 BE length
- 命中 APP1 (0xFFE1) 且 payload 前 6 字节是 `Exif\0\0` → 返回 `payload_off + 6`
- 命中 SOS (0xFFDA) 前还没找到 → None

这个工具也被 X3F 复用。

### 3.5 Phase One IIQ 维度修正

IIQ 把 thumbnail (640×480) 写在 IFD0:ImageWidth/Height,真 raw 维度
(11608×8708) 只在 ExifIFD:ExifImageWidth (`0xA002`) / ExifImageHeight (`0xA003`)。

Canon CR2 反过来:IFD0 = 1936×1288 (合法 preview,exiftool 默认 `-ImageWidth`
也选它),ExifImageWidth = 3888×2592 (raw)。

不能"无脑取大"——会让 CR2 测试 fail。启发式:**当前 IFD0 维度 < 1 MP 才认作
thumbnail，用 Exif* 覆盖**：

```python
ew = exif_dir.get(T_EXIF_IMAGEWIDTH)
eh = exif_dir.get(T_EXIF_IMAGEHEIGHT)
if isinstance(ew, int) and isinstance(eh, int) and ew > 0 and eh > 0:
    cur_w = out.get("ImageWidth", 0)
    cur_h = out.get("ImageHeight", 0)
    cur_area = cur_w * cur_h if isinstance(cur_w, int) and isinstance(cur_h, int) else 0
    if cur_area < 1_000_000 and ew * eh > cur_area:
        out["ImageWidth"] = ew
        out["ImageHeight"] = eh
```

边界:1 MP 是个 magic number。理由——现代相机 raw 永远 ≥ 1 MP;thumbnail 几乎
永远 < 1 MP (640×480=0.3MP、1024×768=0.8MP)。两者间空旷地带很大。

### 3.6 Leaf MOS:`_xmp_fill_missing`

Leaf 这种"主要靠 XMP 包"的文件,IFD0 没 Make/Model/DateTime,只有 ImageWidth。
全靠 head 里的 XMP packet。

策略 = **tag-soup**:文件读到 head 时直接 `find(b"<x:xmpmeta")` 定位 XMP 块,
按字面查 `<tiff:Make>`、`<tiff:Model>`、`<exif:DateTimeOriginal>`。
不用 XML parser:① 三个固定标签不值得起一个 parser ② XML 严格性会因小错误
fail-closed,而我们要 fail-open。

`exif:DateTimeOriginal` 在 XMP 里是 ISO 8601 (`2025-08-09T12:34:56Z`),
要转成 EXIF wire format `2025:08:09 12:34:56`(`_normalize` 期望的形状)。

只在标准 TIFF 解析分支结束后调用一次,且**只填缺**的字段——绝不覆盖已有值。

### 3.7 LibRaw 1970 哨兵过滤

`_augment_from_rawpy` 旧代码:

```python
ts = getattr(other, "timestamp", None)
if isinstance(ts, datetime) and "DateTimeOriginal" not in rec:
    rec["DateTimeOriginal"] = ts.strftime("%Y:%m:%d %H:%M:%S")
```

问题:LibRaw 把"无 DateTime"用 Unix epoch 0 哨兵表示,在用户本地时区(UTC+8)
被 `datetime.fromtimestamp(0)` 转成 `datetime(1970, 1, 1, 8, 0, 0)`,
原样进了 record。表现 = "你库里突然多出一堆 1970-01-01 拍的照片"。

修复:加 `and ts.year > 1970`。真实数码照片永远不会出现 1970 拍摄日期
(数码摄影 80 年代末才有),这条过滤无副作用。

> 第一次实现写成 `ts != datetime(1970,1,1,0,0,0)`,**没考虑时区漂移**。
> 教训:datetime 比较用相等很脆,跟基准值的 hour 差距等于本机 UTC offset。

## 4. 测试漏洞修复:严格关键字段

跨后端测试旧逻辑:

```python
for key in (...):
    if key in et and key in lite and et[key] != lite[key]:
        mismatches.append(...)
```

**lite 丢字段时静默通过**。这就是为什么 MOS 起初 date 失败时被检出
(`1970-01-01` vs `2106-02-07` 不等),但我修了 1970 让 lite 干脆不给 date 后
反而 PASS——明明 lite **少了一个字段**,跨后端等价并不真等价。

加严格档:

```python
REQUIRED_IF_EXIFTOOL_HAS = ("maker", "model", "date", "year", "month")
for key in REQUIRED_IF_EXIFTOOL_HAS:
    if key in et and key not in lite:
        mismatches.append(
            f"{path}: {key} missing from lite (exiftool has {et[key]!r})"
        )
```

新断言**立刻**抓到 MOS 的 date 缺失,逼着我把 `exif:DateTimeOriginal` 也加进
`_xmp_fill_missing` 才让 lite 跟 exiftool 都给 `2106-02-07`(知道这是垃圾,
但保持后端一致是测试目标)。

## 5. 改动清单(按文件)

| 文件 | 变更 | 行数变化 |
|---|---|---|
| `src/rawkit/_exif_lite.py` | +`_find_jpeg_exif_tiff` / `_parse_x3f` / `_parse_mrw` / `_xmp_fill_missing` + ORF magic + IIQ 维度启发式 + RAF 修正 | ~480 → ~911 |
| `src/rawkit/exif.py` | `_augment_from_rawpy` 加 `ts.year > 1970` 过滤 | +几行 |
| `tests/test_exif_lite.py` | `_sample_files()` 改用 `cli.RAW_EXTS` 单一来源；跨后端测试加 `REQUIRED_IF_EXIFTOOL_HAS` | +30 |
| `tests/fixtures/extra_samples.toml` | 10 → 18 条;新增 8 个 2022+ 主流机型 | +160 |
| `scripts/fetch_samples.py` | nix 风格 sha256-empty 工作流 | +~80 |
| `PERF-FIX.md` §3.2 | RAF 描述纠错;补 ORF/X3F/MRW 行 | +5 |

## 6. 已知限制(记入未来 TODO)

- **`_xmp_fill_missing` 只接 TIFF 路径**。RAF/X3F/MRW/CR3 不调,因为这些格式
  当前样本 EXIF 都很完整不需 XMP 兜底。但若未来出现"CR3 EXIF 缺 Make / XMP 有"
  的怪文件,会漏。把调用提升到 `read_metadata` 末尾即可,代价是每文件多扫一次
  head——目前不值。
- **IIQ "<1 MP thumbnail" 阈值是 magic number**。早期 0.3 MP 数码相机不太可能
  存在 raw,4K thumbnail (8 MP) 也很罕见,但理论上能骗过。
- **MOS 文件的 date 实际是垃圾**。`2106-02-07 06:28:15Z` = 32-bit Unix epoch
  最大值的形象表达,意味着原始文件根本没记录拍摄时间。lite 与 exiftool 都
  原样传出去——上游 raw 文件的问题,我们不做语义"修复"。

## 7. 数字

- **基线**:`298 passed`
- **`samples/extras` 跨后端等价**:28 测试 × 18 样本 × 15+ 格式,全绿
- **覆盖的真实机型**(samples/ 核心 + samples/extras/):Canon R5 / R5 II /
  R6 III / 40D / PowerShot A610;Sony A1 / A7R IV / A7R V / A9 III / ZV-1;
  Nikon Z 9 / Z 8 / Z 5 II / Zf / Coolpix P1100;Fujifilm GFX100RF / X-E5 /
  X-T5 / X100VI;Hasselblad X2D / DJI Mavic 3;OM-5 Mark II;Panasonic
  DC-L10;Leica M11 Monochrom;Ricoh GR III;Apple iPhone 13 Pro;
  Sigma DP1 Merrill;Pentax K-r;Samsung EX1;Phase One IQ3 100MP;
  Epson R-D1s;Leaf Aptus 22;Minolta α-7 Digital。

---

<a id="2026-06-22--exif-缓存把-20-秒打成-1-秒"></a>

# EXIF 缓存：把 20 秒打成 1 秒

> 2026-06-22 落地。在 PERF-FIX.md（130× 加速）之上再叠一层 SQLite 持久化缓存，
> 把 38 729 文件库的"重复 `rawkit ls -R`"从 20 秒压到 ~0.85 秒（**24× 再加速**）。
> 零新依赖（sqlite3 是 stdlib）、对用户 0 行为变化、绝不会返回过期数据。

---

## TL;DR

- **效果**：`rawkit ls -w 'iso>=12800' -R -s iso "/Volumes/T7 Shield/底片"`
  - 冷启动（第一次跑 / `cache clear` 之后）：**20.88 s**（PERF-FIX 后的基线）
  - 热启动（再跑一次，所有文件未变）：**0.86 s** —— **24× 加速**
  - 单文件变更：**0.83 s**（只重新解析被 `touch` 过的那一个）
- **怎么做的**：每个 RAW 的 EXIF record 持久化到一张 SQLite 表，
  用 `(dev, ino, size, mtime_ns)` 4 元组当 staleness key（git/ripgrep 同款）。
  开机后第一次跑命令时一次性 `SELECT IN (...)` 把 38 729 行批量捞回来，
  逐文件 `stat()` 校验是否还有效，只对 miss 走 lite 解析。
- **零新依赖**：用 stdlib 的 `sqlite3`。
- **零用户可见行为**：所有命令的 stdout 字节级一致，退出码一致，进度条逻辑也一样。
- **绝不会返回过期数据**：4 元组只要任意一项变了就 miss → 重新解析 → 覆盖写。
- **磁盘代价**：38 729 行 ≈ **24.7 MiB**。在 8 TB RAW 库里是噪声。
- **如何禁用**：`RAWKIT_NO_CACHE=1 rawkit ...`（单次）或 `rawkit cache disable`（持久）。
- **如何清空**：`rawkit cache clear --yes`（删掉所有行，下次冷启动）。

如果只看一个数字：**多跑一次只要 0.86 秒**。

---

## 1. 问题与定位

### 1.1 用户痛点（PERF-FIX 之后剩下的）

PERF-FIX 把 38 729 文件的全库扫描从 ~46 分钟压到 ~20 秒。已经很好了。
但用户的实际工作流不是"一天跑一次"，而是：

```bash
rawkit ls -w 'iso>=12800' -R -s iso "/Volumes/T7 Shield/底片" | head -5 | rawkit reveal
rawkit ls -w 'maker~"SONY" and year=2024' -R "/Volumes/T7 Shield/底片"
rawkit summary -R "/Volumes/T7 Shield/底片"
```

—— **同一个库连续问几次问题**。每次都要付 20 秒"重新读 38 729 个 RAW 的 EXIF"，
这 20 秒里 **绝大多数文件其实一秒前刚读过**。

### 1.2 瓶颈分解

20 秒在干什么？

| 阶段 | 耗时 | 性质 |
|---|---|---|
| `_collect_raws` 走 `/Volumes/T7 Shield/底片` 目录树 | ~1 s | I/O bound (stat 每个 dir entry) |
| 38 729 次 `_read_one_lite()` 解析 TIFF/CR3 | **~19 s** | I/O + 轻量 CPU，每文件 ~500 µs |
| `_sort_records` + DSL filter + 输出 | <0.5 s | 内存 |

**95% 的时间花在"打开文件 + 读前 256 KB + 解 IFD"**，但每次结果完全一样。
这是教科书的"应该缓存"场景。

---

## 2. 总体架构

```
rawkit ls ...
    │
    ├─ _collect_raws(...)                       # 必做：发现文件 ~1s
    │
    └─ safe_batch_read(paths)
       └─ _batch_read_lite(paths_list)
          │
          ├─ Stage 1 — cache lookup ─────────────────────────────────┐
          │   ExifCache.open()         # 受 RAWKIT_NO_CACHE / 持久 disable 控制 │
          │   cache.get_many(paths):                                 │
          │     · SELECT ... WHERE abspath IN (?,?,...) 分块 500     │
          │     · 对每个命中 stat() 校验 (dev, ino, size, mtime_ns)   │
          │     · 任一不匹配 → 划入 miss                              │
          │   → (hits: dict[int, record], miss_indices: list[int])   │
          │                                                           │
          ├─ Stage 2 — parse misses ──────────────────────────────────┤
          │   ThreadPoolExecutor 并行                                  │
          │   每个 worker: _read_one_lite(path)                       │
          │   进度条按 miss_count 计数，不是按总数                       │
          │                                                           │
          └─ Stage 3 — write back ────────────────────────────────────┘
              cache.put_many([(p, rec)...])     # 单事务 INSERT OR REPLACE
              cache.close()                      # 提交 WAL
```

关键设计取舍：

- **只在 batch ≥ 50 时启用缓存**（复用 `_PROGRESS_THRESHOLD`）。小批量下 sqlite
  的 open + setup 比解析本身还贵；阈值同时也防止"几张测试照"污染长效 db。
- **`abspath`（不是 `realpath`）** 当主键。两个软链接到同一文件 → 两行（同记录）。
  原因：用户看到的路径就是 `abspath` 显示的，缓存视图要跟用户视图对齐。
- **payload 用 UTF-8 JSON BLOB**。不用 pickle（安全 + 跨版本）；不用 msgpack（依赖）；
  不用列字段拆开（schema 每次加字段都要 migration）。404 字节/CR3，库一共 24.7 MiB。
- **`os.stat` 是无法省略的**。缓存的正确性建立在"我们每次都问内核'文件还是不是
  我上次见的那个'"上。省了 stat 就敢说出"返回了陈旧数据"。
- **rich.progress 写到 stderr**（PERF-FIX 阶段就修了，复盘见 [src/rawkit/exif.py](src/rawkit/exif.py)）。
  缓存层不动这块。

---

## 3. \_cache.py — 175 行 SQLite + 4 元组失效

[src/rawkit/\_cache.py](src/rawkit/_cache.py)

### 3.1 schema（version = 1）

```sql
PRAGMA user_version = 1;                  -- 不兼容时 bump → 整库 DROP + 重建
PRAGMA journal_mode = WAL;                -- 多读 + 单写并发
PRAGMA synchronous = NORMAL;              -- WAL 下安全的高速档
PRAGMA temp_store = MEMORY;

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- 初始化写入：rawkit_version / created_at / last_vacuum_at / enabled

CREATE TABLE exif_cache (
    abspath  TEXT PRIMARY KEY,            -- os.path.abspath(path)
    dev      INTEGER NOT NULL,            -- st_dev
    ino      INTEGER NOT NULL,            -- st_ino
    size     INTEGER NOT NULL,            -- st_size
    mtime_ns INTEGER NOT NULL,            -- st_mtime_ns（纳秒精度）
    backend  TEXT    NOT NULL,            -- 'lite'（exiftool 后端不缓存）
    payload  BLOB    NOT NULL             -- record dict 的 UTF-8 JSON
);
```

为什么用 SQLite 而不是 JSON / pickle 文件 / per-file sidecar？

- **stdlib**：零新依赖
- **事务 + WAL**：crash-safe，两个 `rawkit ls` 并发跑不会互相破坏数据
- **批量 IN 查询**：38 729 行用 78 次 `IN (?,?,…500)` 查询，~0.3 s
- **VACUUM**：清孤儿行后 db 文件真的会缩
- **PRAGMA `user_version`**：schema 升级走 silent rebuild，用户不知道有这层

### 3.2 失效信号（staleness key）

每一行存 4 个数：`(dev, ino, size, mtime_ns)`。下一次查询时：

1. 用 `abspath` 当 SQL 主键命中 → 拿回这 4 个数 + payload
2. `os.stat(abspath)` 拿当下值
3. 4 个数全等 → hit，`json.loads(payload)` 返回 dict，**完全不打开 RAW 文件**
4. 任一不等 → miss → 走 lite 解析 → `INSERT OR REPLACE` 覆盖

为什么这 4 个数够用？

| 字段 | 抓什么变化 | 失败模式 |
|---|---|---|
| `dev` | 文件跨卷搬走 | （和 abspath 同时变，安全）|
| `ino` | 同路径下被另一个文件替换（rm + cp） | 替换通常 mtime 也变，这里是冗余兜底 |
| `size` | 任何内容变更几乎都会改 | 强佐证 |
| `mtime_ns` | 99% 的实际变更 | 这是主信号 |

**唯一能骗过它的姿势**：`touch -d @<oldmtime>` 把 mtime 拨回原值 + 同 size 换内容。
这要用户主动作恶，git/ripgrep 用相同启发式服务全人类 30 年，对 RAW 是过剩的。

**没用 SHA-256/xxhash**：38 729 × 30 MB = 1.2 TB I/O = 5–15 分钟。
比它要避免的 20 秒解析慢 20–50 倍，**缓存彻底变负优化**。明确放弃。

### 3.3 `get_many` 的工程细节

```python
def get_many(self, paths) -> (hits: dict[int, dict], miss_indices: list[int]):
    abs_paths = [os.path.abspath(str(p)) for p in paths]
    # 1. 同路径去重（罕见但要正确处理）
    abspath_to_indices = {ap: [i, ...] for i, ap in enumerate(abs_paths)}
    # 2. 分块 IN (?, ...500)，规避 SQLITE_MAX_VARIABLE_NUMBER 在老内核上的 999
    rows = {}
    for chunk in chunked(unique_paths, _IN_CHUNK):
        cur = SELECT abspath, dev, ino, size, mtime_ns, payload
              FROM exif_cache WHERE abspath IN (chunk)
        rows.update(...)
    # 3. 对每条命中 stat + 比对 + json.loads
    for ap, indices in abspath_to_indices.items():
        row = rows.get(ap)
        if row is None: → miss
        st = os.stat(ap)  # 自然把"文件已被删"也归到 miss
        if any 4 fields mismatch: → miss
        try: rec = json.loads(payload)   # 防御性：corrupted payload 当 miss
        except: → miss
        else: for idx in indices: hits[idx] = rec
    return hits, sorted(misses)
```

返回 `dict[int, dict]` 而不是 `dict[Path, dict]`：调用方拿到的是 **input list 的位置索引**，
直接用 `results[i] = rec` 写回，完全避开 `Path → idx` 反查表。

### 3.4 `put_many` 单事务

```python
self._conn.execute("BEGIN")
self._conn.executemany("INSERT OR REPLACE INTO exif_cache(...) VALUES (?,?,?,?,?,?,?)", rows)
self._conn.execute("COMMIT")
```

38 729 行一次 commit ≈ 1.5–2 s（在 macOS APFS / WAL / synchronous=NORMAL 下）。
逐行 commit 会慢 ~100×。所以**绝对不能**写"循环里 put_one"。

put_many 内部还会重新 `stat()` 一次每个文件 —— 不信任调用方传进来的 stat
信息可能在 ThreadPool 期间过期了（虽然 1 秒内一般不会，但定义上要紧）。
小代价（38 k stat ≈ 0.5 s）换严格正确性。

### 3.5 organize 钩子：`relocate` 和 `duplicate`

`rawkit organize` 是唯一会**改动文件路径**的命令。如果不维护缓存，
move/copy 之后 `rawkit ls` 会把那批刚搬过去的文件全部当作 miss → 重新解析。
这违反了"缓存最有用的时刻就是刚改完文件"的直觉。

实现：

```python
# move 语义 (shutil.move)：旧路径条目变成孤儿；新路径要登记
def relocate(self, old, new):
    row = SELECT backend, payload FROM exif_cache WHERE abspath = old_ap
    if row is None: return                       # 缓存里本来就没有 → 无事可做
    payload["path"] = new_ap                     # 同步更新 record 里的 path 字段
    st = os.stat(new_ap)                         # 用新位置的 stat
    INSERT OR REPLACE INTO exif_cache(...) VALUES (new_ap, st.dev, st.ino, ...)
    DELETE FROM exif_cache WHERE abspath = old_ap

# copy 语义 (shutil.copy2)：旧路径保留；新路径独立登记
def duplicate(self, src, dst):
    # 同上但不 DELETE 旧行
```

[src/rawkit/cli.py](src/rawkit/cli.py) 的 organize 循环里，每个 `shutil.move/copy2`
成功后调用一次。**任何 cache 异常都被 except 吞掉** —— 缓存层绝不能因
"sqlite 锁死"或"db 损坏"导致用户文件操作中断。最坏结果是下次 `rawkit ls` 多花 0.5 ms。

### 3.6 dry-run 与缓存

`organize --dry-run` 不动文件 → 完全不开 cache。这是为了：

1. 行为干净：dry-run 真的"什么也没做"
2. 测试简单：dry-run 的所有 assertion 只盯文件系统，不必看 db
3. 性能：dry-run 启动更快

---

## 4. 使用界面

### 4.1 自动用：什么都不用做

只要你跑 `rawkit ls -R`、`rawkit info`、`rawkit summary`、`rawkit organize`、
`rawkit aggregate` 这些会扫 EXIF 的命令，缓存自动生效。
第一次跑会建 db；之后每次跑命中就用。

### 4.2 进程级跳过

```bash
RAWKIT_NO_CACHE=1 rawkit ls -R ...        # 这次跑不查缓存、不写缓存
```

适合：CI、对单次执行不信任缓存、benchmark 冷启动复测。

### 4.3 长效控制

```bash
rawkit cache disable          # 持久禁用（写入 meta.enabled=false）
rawkit cache enable           # 持久重启用
```

适合：用户手工改过文件 mtime / 跨备份做了奇怪的事，担心缓存欺骗自己。
（实际上不会，但你想关就关。）

### 4.4 查看

```
$ rawkit cache info
path:           /Users/liqinglin/Library/Caches/rawkit/v1/index.sqlite
schema version: 1
enabled:        yes
rows:           38,729
size on disk:   24.7 MiB
rawkit version: 0.0.1
created:        2026-06-22T14:32:45+00:00
last vacuum:    never
```

### 4.5 维护

```bash
rawkit cache vacuum             # GC 孤儿行（路径不存在的）+ SQLite VACUUM
rawkit cache clear              # 删全部行（交互式确认）
rawkit cache clear --yes        # 同上，跳确认（脚本用）
```

`vacuum` 什么时候跑？正常不需要。如果你用 `organize --move` 大幅重整过库，
可以跑一次回收磁盘。否则缓存最多多占 10% 空间。

### 4.6 缓存位置

| 平台 | 默认 | 替代 |
|---|---|---|
| macOS | `~/Library/Caches/rawkit/v1/index.sqlite` | Time Machine 默认排除此目录（regenerable data 标准做法） |
| Linux/CI | `$XDG_CACHE_HOME/rawkit/v1/` 或 `~/.cache/rawkit/v1/` | XDG 规范 |
| 任意 | `RAWKIT_CACHE_DIR=/some/path` | 测试/特殊环境用 |

---

## 5. 数据形状（一条真实记录长什么样）

对 `/Volumes/T7 Shield/底片/2022/0521 痰盂初体验/168A2401.CR3`（40 MB 的 Canon R5 CR3）：

```text
SQLite row:
  abspath   = /Volumes/T7 Shield/底片/2022/0521 痰盂初体验/168A2401.CR3
  dev       = 16777244
  ino       = 147
  size      = 40330838
  mtime_ns  = 1653140095000000000
  backend   = lite
  payload   = <404 字节 UTF-8 JSON>
```

payload 解开是：

```json
{
  "path":        "/Volumes/T7 Shield/底片/2022/0521 痰盂初体验/168A2401.CR3",
  "datetime":    "2022-05-21 21:34:54.50",
  "maker":       "Canon",
  "model":       "EOS R5",
  "lens":        "RF50mm F1.8 STM",
  "iso":         400,
  "fnumber":     1.8,
  "shutter":     0.25,
  "focal":       50.0,
  "bias":        0.0,
  "rating":      0,
  "image_width": 8192,
  "image_height": 5464,
  "date":        "2022-05-21",
  "time":        "21:34:54.50",
  "year":        2022,
  "month":       5,
  "day":         21,
  "hour":        21,
  "orientation": "landscape",
  "flash":       false
}
```

22 个字段 / ~510 字节/行（含索引和 SQLite 行开销）。

---

## 6. 测试

[tests/test_cache.py](tests/test_cache.py)：**42 个测试**，覆盖：

| 类别 | 测试覆盖点 |
|---|---|
| 路径解析 | `RAWKIT_CACHE_DIR` 覆盖；macOS Library/Caches 回退；XDG 回退 |
| 启用/禁用 | `env_disabled()` 对各种值的识别；`open()` 返回 None；`set_enabled` 持久化；`ignore_disabled=True` 旁路 |
| Schema | 初次创建建表+meta；`PRAGMA user_version` mismatch 静默重建；corrupted db 返回 None 不崩 |
| put/get 正确性 | 字段无损 round-trip；混合 hit/miss 分区；空列表；重复路径去重 |
| 失效 | mtime 变 → miss；size 变 → miss；文件已删 → miss 不崩；corrupted payload → miss |
| put 防御 | 文件解析后但写缓存前消失 → 跳过不抛；同 abspath 覆盖整行 |
| organize 钩子 | relocate 改 row + 删旧 row + 同步 payload 的 `path` 字段；duplicate 保留 src；未知 src 不抛；src==dst 无操作 |
| info/clear/vacuum | info 字段类型；clear 计数；vacuum 删孤儿且打 timestamp；vacuum 零孤儿仍记 last_vacuum_at |
| `_batch_read_lite` 集成 | 冷→热：第一次解析全部，第二次 0 解析；阈值以下完全不开 db；单文件 mtime 变只重新解析它；`RAWKIT_NO_CACHE` 完全跳过 |
| CLI 集成 | `cache info` 在 db 未创建时；填充后；env 禁用时；`cache clear` 无 `--yes` 在非 TTY 拒绝；带 `--yes` 工作；`cache vacuum` 报告 orphans；`disable/enable` 周期 |
| organize 端到端 | move 自动 re-key；copy 自动 duplicate；dry-run 不动 cache |

测试中 `RAWKIT_CACHE_DIR` 通过 autouse fixture 指向 `tmp_path`，每个测试独立 db。
**绝不会污染用户的真实 ~/Library/Caches/rawkit/**。

### 全测试套件状态

```
======================== 293 passed, 5 skipped in 1.63s ========================
```

PERF-FIX 之后是 251 + 5；新增 42 个 cache 测试 → 293 + 5。零回归。

---

## 7. 性能实测

### 7.1 微观（单次调用）

测试机：MacBook，T7 Shield USB 10Gbps，38 729 RAW，混合 CR3/ARW/DNG/RW2/3FR。

| 状态 | 时间 (user/sys/wall) | 备注 |
|---|---|---|
| 冷启动（`cache clear` 之后）| 2.60 / 3.77 / **20.88 s** | 等同 PERF-FIX 基线；写缓存 +~1.5 s |
| 热启动（100% hit）| 0.69 / 0.13 / **0.86 s** | **24× 加速** |
| 单文件 `touch` 后 | 0.69 / 0.14 / **0.83 s** | 单文件重新解析在噪声里 |

`rawkit cache info`：

```
rows:           38,729
size on disk:   24.7 MiB
```

### 7.2 瓶颈分析（热启动 0.86 s 在干嘛）

| 阶段 | 估算耗时 | 性质 |
|---|---|---|
| `_collect_raws` 走 38 k 个目录条目 | ~0.4 s | 不变的目录 walk |
| `cache.get_many` 78 块 `IN (...)` SQL | ~0.3 s | sqlite + 索引 |
| 38 k 次 `os.stat()` 校验 | ~0.1–0.2 s | macOS 内核 page cache |
| 38 k 次 `json.loads` | ~30 ms | stdlib json 够用 |
| `_sort_records` + DSL filter | ~10 ms | 内存 |
| **合计** | **~0.85 s** | 实测吻合 |

**瓶颈已经从 EXIF 解析转移到了 stat 系统调用 + 目录 walk**。这是物理下限：
你不可能比"问内核 38 729 次'这文件还在吗'"更快。再压只能放弃 stat 校验，
那是用正确性换速度，**强烈不推荐**。

---

## 8. 失败模式与回退

| 异常 | 后果 | 回退策略 |
|---|---|---|
| Cache db 文件损坏 / 二进制非法 | `ExifCache.open()` 返回 None | 自动回到"没缓存"路径；用户跑一次 `cache clear` 修复 |
| `PRAGMA user_version` mismatch（rawkit 升级）| 静默 `DROP TABLE` 重建 | 用户无感；下一次跑等同冷启动 |
| Disk full 写不进缓存 | `put_many` 抛 sqlite3 error | 异常向上传；不会破坏当次命令的输出（数据已经在 stdout 里了）|
| Cache 写入和文件改动竞态（理论） | put_many 内部重新 stat → 用最新值 | 命中正确性不受影响 |
| organize 移动后 cache 出错 | `try/except` 吞掉 | 下次 ls 会 miss → 多花 1 ms |
| `RAWKIT_NO_CACHE=1` | 完全跳过缓存层 | 原 lite 行为 |
| `rawkit cache disable` | 持久跳过 | 同上，跨 invocation 持久 |

**对用户的承诺**：

1. 缓存**永远不会**让你看到陈旧的 EXIF。`stat` 校验是死规矩。
2. 缓存**永远不会**让命令"失败但出输出"。任何缓存层异常都被吞，回到 lite 路径。
3. 缓存**永远不会**修改你的 RAW 文件。它是单向的：读你的文件、不动它们。

---

## 9. 不做的事（已经讨论过的非目标）

- **不存内容哈希**：38 k × 30 MB = 1.2 TB I/O，10 分钟开销，反向打败 20 秒目标。
- **不缓存目录扫描结果**：`_collect_raws` 本来只占 0.5 s，目录变化频繁，
  失效逻辑复杂度不值。
- **不缓存衍生统计** (`summary`/`aggregate` 的聚合产物)：即时聚合 ~50 ms，纯算术。
- **不缓存 `exiftool` 后端的输出**：用户切到 exiftool 时通常就是为了调试字段差异，
  缓存会干扰对照。`RAWKIT_BACKEND=exiftool` 总是冷读。
- **不支持 `--ttl` 过期**：时间不是失效维度，stat 才是。TTL 只会让人误以为有用。
- **不做命中率统计持久化**：写"热点"会成为每次 commit 的瓶颈；想看就退出时打 stderr。
- **不做 `--cache-dir` CLI flag**：用环境变量 `RAWKIT_CACHE_DIR` 足矣，YAGNI。
- **不做 `cache warm <dir>` 预热子命令**：任何 `rawkit ls/info/summary` 都会自然预热。

---

## 10. 升级路径

未来如果改 record 字段形状（加字段 / 改语义 / 改 JSON 编码）：

1. 在 [src/rawkit/\_cache.py](src/rawkit/_cache.py) 里把 `SCHEMA_VERSION` 从 1 改成 2。
2. 不需要写 migration —— 下次启动 `_ensure_schema()` 检测到 mismatch 会
   `DROP TABLE exif_cache; CREATE TABLE exif_cache;`，**用户无感**。
3. 第一次跑（schema 升级后）等同冷启动，~20 s。

这条路径明显牺牲了"老缓存的复用"。但 EXIF 解析在 PERF-FIX 之后只要 20 秒，
不值得为节省 20 秒写 migration 代码。简单 > 极致。

如果某天 schema 升级**只是加字段**而且**老 payload 是新 payload 的子集**（向后兼容），
可以改成"老行的 backend 列改为 `'lite-v1'`，下次 hit 时按 v1 补字段写回 `'lite-v2'`"
的懒迁移。但这种复杂度只在用户量上来后才值得。

---

## 11. 顺便拿到了什么

实现这套缓存的副产品：

- **进度条只显示 miss 数**：热启动时不再显示"0/38 729 reading EXIF"扫一圈，
  小批量命中时连进度条都不出现。
- **`rawkit cache info` 顺便显示 rawkit 版本**：未来 bug 报告里"你这缓存是哪个版本写的"
  一行命令搞定。
- **WAL 模式天然支持并发**：可以同时跑 `rawkit summary` 和 `rawkit ls`，互不阻塞。
- **organize 的 sidecar 处理零额外代码**：`relocate(xmp_path, new_xmp)` 看到
  缓存里没行，no-op 返回 —— sidecar (XMP/JPG) 自然被忽略。
- **测试更严格了**：cache 失效语义强迫我们把 record 序列化的稳定性写进单测。
  下次有人想"顺手加个字段不写测试"会被红色阻挡。

---

## 12. 一句话回顾

> 38 729 文件 × 20 秒 = 用户的耐心。
> 38 729 文件 × 0.86 秒 = 0 焦虑。

完。

---

<a id="2026-06-22--exif-后端重写性能--内部架构"></a>

# EXIF 后端重写：性能 + 内部架构

> 2026-06-22 落地。把全库 EXIF 扫描从 ~46 分钟压到 ~20 秒（>130× 加速），
> 没有新增任何依赖。

---

## TL;DR

- **效果**：`rawkit ls -R /Volumes/T7\ Shield/底片`（38,729 张 RAW，CR3/ARW/DNG/RW2/3FR）
  - 旧路径（exiftool）：实测 14 files/s ≈ **~46 分钟**
  - 新路径（lite）：实测 1932 files/s ≈ **20 秒读 EXIF + 端到端 16 秒**
  - **加速 ~130×；不再需要进度条以外的等待提示**（且新增了进度条）
- **怎么做的**：写了一个 ~400 行的纯 stdlib TIFF/CR3 解析器
  [src/rawkit/\_exif\_lite.py](src/rawkit/_exif_lite.py)，覆盖 rawkit 用到的
  全部标准 EXIF 字段；rawpy/LibRaw 只作为最后兜底，**热路径完全不再调用**。
- **零新依赖**：只用 `struct` + `pathlib`，加上现成的 `rich`（已在 deps 里）做进度条。
- **零用户可见行为变化**：所有字段语义、CLI 选项、记录形状都一致；带了一个
  自动化跨后端等价性测试（13 个 RAW 真样本逐字段对比 exiftool）兜底。
- **如何回退**：`RAWKIT_BACKEND=exiftool rawkit ...` 一秒切回旧路径，老代码全保留。
- **顺便修了个潜伏 bug**：`SubSecTimeOriginal` 在 exiftool `-n` 模式下偶尔回传
  整数而非字符串，旧 `_normalize` 用 `isinstance(x, str)` 检查直接吞了亚秒精度
  —— 连拍排序场景受影响。详见 §6。
- **顺便免费拿到的东西**：见 §8（颜色矩阵、白平衡、定焦/变焦识别、
  raw vs preview 双尺寸、CMT3 Canon makernote 入口…）。

如果只想知道两个数字：**130× 加速；0 个新依赖**。

---

## 1. 问题与定位

### 1.1 用户痛点

`rawkit ls -R /Volumes/T7 Shield/底片` 在 38,729 张 RAW 上：
1. 直接报 `OSError: [Errno 7] Argument list too long: 'exiftool'`（已先修，见 commit `32a04b2`）。
2. 修了 E2BIG 之后，预计要十几分钟 + 没有进度反馈。

### 1.2 瓶颈实测（见 PERF.md）

PERF.md 的小样本测试结论：exiftool ~17 ms/file（CPU-bound Perl makernote 解析），
rawpy ~0.2 ms/file。**真瓶颈是 CPU**（warm/cold cache 一样快），不是 I/O。

实际大库测试（USB SSD 上的 25 MB CR3 文件）：
- exiftool: 14 files/s ≈ 71 ms/file（USB SSD 上更慢，因为冷数据 + 多进程）
- 单文件 `rawpy.imread()`: CR3 ≈ 364 ms，DNG ≈ 287 ms，RW2 ≈ 93 ms
- 单文件 `_exif_lite.read_metadata()`: **~0.05 ms（warm）**, ~2 ms（cold，256 KB 头读）

PERF.md 当时预估 ~50×。实际 130× 高于预估，原因是 PERF.md 没考虑到 rawpy
在大文件 USB SSD 上的实际开销也很高 —— 它读了远不止 header，
因此**也不能用作热路径**。最终方案把 rawpy 完全踢出热路径。

---

## 2. 总体架构

```
batch_read(paths)              # exif.py 入口；按 RAWKIT_BACKEND env 分发
├─ "exiftool"   ── _batch_read_exiftool   # 旧路径，原封不动保留
└─ "lite"(默认) ── _batch_read_lite
                    │
                    ├─ ThreadPoolExecutor (min(8, cpu_count) workers)
                    ├─ 可选 rich.progress（>= 50 files & stderr 是 tty 时）
                    └─ per-file: _read_one_lite(path, rawpy)
                                 │
                                 ├─ 1) _exif_lite.read_metadata(path)
                                 │      ↑↑↑ 99% 的文件在这里就结束 ↑↑↑
                                 │
                                 └─ 2) 若 Make/Model 缺失 → fallback rawpy.imread()
                                       （兜底；处理 LibRaw 能读但 EXIF 损坏的怪文件）
```

关键设计取舍：

- **rawpy 不在热路径**。它只在 EXIF 解析完全失败（连 Make/Model 都没拿到）时跑。
  实测 5 种主流格式 0% 命中 fallback。
- **TIFF 解析只读前 256 KB**。EXIF 几乎总在文件开头；超界的（Pano DNG IFD0
  在 600 MB 处）走第二窗口（"windowed reader"）。
- **线程并行**。Python 的 EXIF 解析极轻（每文件几十 µs），瓶颈变成 I/O；
  线程足够，没必要上 multiprocessing。

---

## 3. \_exif\_lite.py — 一个 400 行的 TIFF 解析器

[src/rawkit/\_exif\_lite.py](src/rawkit/_exif_lite.py)

### 3.1 设计原则

- **纯 stdlib**。只 `struct` + `pathlib` + `BinaryIO`。
- **只解 rawkit 实际用的 tag**。`_IFD0_WANTED`/`_EXIF_WANTED`/`_GPS_WANTED` 是白名单 frozenset；
  路过的其他 tag 直接 skip，省 I/O 也省 Python 对象分配。
- **永不抛出**。任何错（截断 / 未知格式 / 错误 IFD）都返回 `{}`；上层把"空 dict"
  当作"用 rawpy 兜底"信号。**绝不能因为一个怪文件炸掉整个 batch**。
- **格式中立**。`read_metadata()` 看后缀决定走哪个 prelude，但所有 prelude 最后
  都收敛到 `_parse_tiff()` 上。

### 3.2 各格式如何"找到 TIFF"

| 格式 | 文件起手 | 找 TIFF 块的方法 |
|---|---|---|
| ARW / NEF / ORF / PEF / IIQ / MOS / 普通 TIFF | `II/MM` magic + `0x002A` | 直接从 0 开始解 |
| **RW2** (Panasonic) | `II` + `0x0055`（非标准 magic！） | 同上，magic 列表里加 `0x0055` |
| **ORF** (Olympus) | `II` + `0x4F52`（'IIRO' 头，非标准 magic！） | 同上，magic 列表里加 `0x4F52` |
| **RAF** (Fujifilm) | `FUJIFILMCCD-RAW` 16 字节 | 偏移 `0x54` 处 BE-uint32 指向**嵌入 JPEG 的 SOI**；走 JPEG 标记序列找 APP1/`Exif\0\0`，TIFF 紧跟其后 |
| **MRW** (Minolta/KONICA MINOLTA) | `\x00MRM` + BE-uint32 长度 | 跳子块 (`\x00PRD`/`\x00WBG`/`\x00RIF`)，到 `\x00TTW` 块内即标准 TIFF |
| **X3F** (Sigma Foveon) | `FOVb` + 版本 | 文件末 4 字节是 directory offset → `SECd` 目录里找 type=`IMA2` 的 `SECi` JPEG section，再 APP1/Exif |
| DNG / 3FR | `II/MM` + `0x002A` | 同 TIFF；但 IFD0 是缩略图，raw 在 SubIFD |
| **CR3** (Canon) | `....ftyp` (ISO BMFF) | 走 BMFF：`moov > uuid(85c0b687...) > CMT1/CMT2/CMT4` |

### 3.3 CR3 三个 CMT box 必须分别解

这一坑踩了挺久。CR3 不是 single TIFF —— Canon 把 EXIF **拆三个独立的 TIFF 块**
塞进 `moov.uuid` 里：

- `CMT1` = IFD0（Make/Model/Orientation/ImageWidth/...）
- `CMT2` = ExifIFD（DateTimeOriginal/ISO/FNumber/LensModel/Flash/...）
- `CMT4` = GPS IFD

每个都是**自包含的 TIFF header + 一个 IFD**，不跟随 IFD0→ExifIFD 指针链。
所以 `_parse_cmt_ifd(block, wanted, kind=...)` 单独处理每块，调用方把结果合并。
（早期版本错以为 CMT2 通过 CMT1 的 0x8769 指针引用，结果什么都拿不到。）

### 3.4 SubIFD 走链 + 取最大维度

DNG/3FR/ARW 把 raw 维度藏在 SubIFD（IFD0:0x014A 指向的偏移数组）里：

- DNG IFD0:ImageWidth = 160（缩略图）；SubIFD:ImageWidth = 6112（raw）
- 3FR IFD0:ImageWidth = 3888（缩略图）；SubIFD:ImageWidth = 11904（raw）

`_resolve_dimensions()` 的策略：**遍历 IFD0 + 所有 SubIFD，取面积最大的那一对**。
不用 `NewSubfileType == 0` 的精确判断，因为"最大那个"恰好等价。
SubIFD 数组上限设 8，挡掉病态文件。

### 3.5 Panasonic RW2 的特殊兼容

RW2 magic 是 `0x0055`（非标准 0x002A），且 ISO 存在 IFD0:0x0017 而非标准 ExifIFD:0x8827。
处理方式：

- magic 列表里把 `0x0055` 加进去
- `_resolve_panasonic_iso()` 在 IFD0/ExifIFD 都拿不到 ISO 时，看 IFD0:0x0017
- 维度回退用 IFD0:0x0002 (SensorWidth) / 0x0003 (SensorHeight) —— 比 exiftool composite 值大 16 px（active-area crop），不影响用户查询

### 3.6 大文件的 windowed 读取

Lightroom 缝合的 Pano DNG：IFD0 偏移 590 MB。256 KB 头读完全错过。
方案：

1. 头读 256 KB 拿到 TIFF header（这里只有 IFD0 偏移指针）
2. seek 到 IFD0 偏移，再读 256 KB 作为第二窗口
3. `_read_ifd_windowed()` 接受 `[(abs_off, buf), ...]` 列表；对每条 IFD entry，
   先检查 value 在哪个窗口里，不在任何窗口就 skip 这条 entry
4. SubIFD 在这种文件里基本一定在第三个窗口外，所以放弃；只取 IFD0 自身的维度
   （= 缩略图维度，比没好）

### 3.7 关于 IFD0:DateTime (0x0132) 的故意不读

Lightroom 在每次编辑后都会覆写 IFD0:DateTime；它**不是**拍摄时间。
我们只信 ExifIFD:DateTimeOriginal (0x9003)。
（在文件头注释里有 4 行说明，免得未来有人想"补全字段"踩进去）

---

## 4. exif.py 的改动

[src/rawkit/exif.py](src/rawkit/exif.py)

### 4.1 后端分发

新增：

```python
def batch_read(paths):
    backend = os.environ.get("RAWKIT_BACKEND", "lite").strip().lower()
    if backend == "exiftool":
        return _batch_read_exiftool(paths_list)   # 旧函数原封保留
    return _batch_read_lite(paths_list)           # 新函数
```

- 默认 `lite`
- `RAWKIT_BACKEND=exiftool` 一键回退
- exiftool 路径**一行没改**（包括上次修的 `-@ -` stdin trick）

### 4.2 \_batch\_read\_lite

```python
def _batch_read_lite(paths_list):
    import rawpy  # 懒加载：rawpy + numpy ~80 ms，留给 --help / 小目录省一下
    workers = _default_workers()          # min(8, cpu_count) 或 RAWKIT_WORKERS
    show_progress = (
        len(paths_list) >= 50
        and sys.stderr.isatty()
        and not os.environ.get("RAWKIT_NO_PROGRESS")
    )
    ...
    with ThreadPoolExecutor(max_workers=workers) as pool:
        ...
```

- 进度条：用 `rich.progress` (已在 deps)；用 `as_completed` 实现 per-finish 进度，
  而不是 `pool.map` 的 chunk-级粒度。
- `transient=True` —— 完成后进度条自动消失，不污染 stderr。
- `RAWKIT_NO_PROGRESS=1` 关进度条（测试时刚需）。
- 阈值 50 文件起：小目录不闪一下进度条；大目录第一秒内就出现。

### 4.3 \_read\_one\_lite — 快路径 + 兜底

```python
def _read_one_lite(path, rawpy_mod):
    rec = {"SourceFile": str(path)}
    try:
        exif_block = _exif_lite.read_metadata(path)
    except Exception:
        exif_block = {}
    rec.update(exif_block)

    # 99% 的文件在这里就够了
    have_basics = "Make" in exif_block and "Model" in exif_block
    if not have_basics:
        # fallback：rawpy 慢但能读 LibRaw 支持的所有怪格式
        try:
            with rawpy_mod.imread(str(path)) as raw:
                _augment_from_rawpy(rec, raw)
        except (rawpy_mod.LibRawError, OSError, MemoryError):
            pass

    # 跟 exiftool 行为对齐：有任何 EXIF 但没 Rating → 默认 0
    if (exif_block or len(rec) > 1) and "Rating" not in rec:
        rec["Rating"] = 0
    return rec
```

关键：`have_basics` 判定。
**Make+Model 都拿到了就直接走** —— rawpy 永远不被调用。
实测 5 种主流格式（CR3/ARW/DNG/RW2/3FR）100% 走快路径。

### 4.4 \_augment\_from\_rawpy — 兜底字段映射

只在 fallback 触发时跑。把 LibRaw 的字段翻译成 exiftool wire format
（datetime → `YYYY:MM:DD HH:MM:SS`，LibRaw `flip` → EXIF Orientation 映射 `{0:1, 3:3, 5:8, 6:6}`）。
全用 `setdefault`，已有的 EXIF 值赢。

---

## 5. 测试

### 5.1 新文件：[tests/test\_exif\_lite.py](tests/test_exif_lite.py)

28 个测试，分四组：

1. **合成 TIFF 单元测试**（`_build_tiff()` byte-stream builder）
   - 最小 IFD0（IIH8 + 1 entry）
   - 大端 / 小端
   - RW2 magic 0x55
   - GPS 南/西半球符号
   - `ExposureBiasValue` → `ExposureCompensation` 命名映射
   - IFD0:DateTime 故意不被读出
   - ISO 优先 0x8827，回退 0x8833 (PSI)
   - 部分 EXIF / 损坏 / 空 / 不存在文件返回 `{}`
2. **结构化合成场景**
   - 大 DNG：合成一个 256 KB+ 头 + 内嵌 TIFF 的文件，验证 windowed reader
   - CR3 多 box：合成 `ftyp + moov + uuid + CMT1/CMT2/CMT4`，验证三块分别解
   - CR3 缺 uuid box → 返回 `{}`
3. **LibRaw flip 参数化**（保证映射表不退化）
4. **后端分发 + 集成**
   - `RAWKIT_BACKEND` 默认 lite / `=exiftool` 切换
   - 空输入短路返回 `[]`
   - 潜伏 bug 回归：`SubSecTimeOriginal` 接受 int

### 5.2 真样本跨后端等价性测试

`tests/test_exif_lite.py::test_lite_matches_exiftool_on_core_fields`

- 用 `RAWKIT_TEST_SAMPLES=/path/to/raw/dir`，
  内部 `_sample_files()` 每种格式取 3 个文件
- **同一批文件，两次 `batch_read`，前一次 `RAWKIT_BACKEND=exiftool`，后一次 `=lite`**
- 逐字段比较：
  - 离散字段（maker/model/lens/iso/orientation/flash/gps/date/year/month）必须完全相等
  - `fnumber` 容差 ±0.05（APEX vs FNumber rounding）
  - `focal` 容差 ±0.5mm
  - `image_width` / `image_height` 容差 ±1%（RW2 sensor vs composite 差 16 px）
  - `shutter` 相对差 < 1%

实测 13 个文件覆盖 CR3/ARW/DNG/RW2/3FR：**100% 通过**。

### 5.3 跑法

```bash
# 不带真样本（合成测试 + 其他 mock 测试）：
uv run --with pytest pytest -q
# → 252 passed, 4 skipped（4 个真样本测试 skip）

# 带真样本：
ln -s /your/raw/library /tmp/rawkit-samples
RAWKIT_TEST_SAMPLES=/tmp/rawkit-samples uv run --with pytest pytest -q
# → 256 passed
```

### 5.4 现有 test\_exif.py 的小调整

[tests/test\_exif.py](tests/test_exif.py) 里 14 个测试都基于 mock `subprocess.run` —— 
假设 batch\_read 走 exiftool。现在 batch\_read 默认走 lite，这些 mock 完全打不上。
加了个 autouse fixture：

```python
@pytest.fixture(autouse=True)
def _force_exiftool_backend(monkeypatch):
    monkeypatch.setenv("RAWKIT_BACKEND", "exiftool")
```

只对 `test_exif.py` 这一个文件生效（fixture 是 file-scoped）。227 个老测试全过。

---

## 6. 顺便修的 SubSecTimeOriginal 潜伏 bug

`_normalize` 老代码：

```python
subsec = rec.get("SubSecTimeOriginal")
if isinstance(subsec, str) and subsec.isdigit():
    ms = int(subsec.ljust(3, "0")[:3])  # ...
```

exiftool 的 `-n` (numeric) 模式 + tag 内容全是数字时，**会把 `088` 这种值
作为 int 直接 emit**，不是 str。`isinstance(subsec, str)` 失败 → 整个亚秒分支不跑
→ 连拍序列的排序失去亚秒精度。

修复：把 `isinstance` 改成接受 `(str, int)`，int 走 `str(...)` 再 `ljust`：

```python
if isinstance(subsec, (str, int)):
    s = str(subsec).strip()
    if s.isdigit():
        ms = int(s.ljust(3, "0")[:3])
```

新增 `test_normalize_accepts_int_subsec` 回归测试。

---

## 7. 改动清单（按文件）

| 文件 | 变更 | 行数变化 |
|---|---|---|
| [src/rawkit/\_exif\_lite.py](src/rawkit/_exif_lite.py) | **新增**：纯 stdlib TIFF/CR3 EXIF 解析器 | +~480 |
| [src/rawkit/exif.py](src/rawkit/exif.py) | 后端分发 + `_batch_read_lite` + `_read_one_lite` + `_augment_from_rawpy` + SubSec int 修复 | +~200 / -0 (老代码全保留) |
| [tests/test\_exif\_lite.py](tests/test_exif_lite.py) | **新增**：28 个测试 + 合成 TIFF builder + 真样本跨后端对比 | +~600 |
| [tests/test\_exif.py](tests/test_exif.py) | autouse fixture 把这一文件强制走 exiftool 路径 | +12 |
| [scripts/bench\_exif.py](scripts/bench_exif.py) | **新增**：基准脚本（lite vs exiftool） | +~80 |

依赖 / 配置 / CLI：**无变更**。`pyproject.toml` 没动。

---

## 8. "新方案下我们免费/极低成本能额外得到什么"

这是一个意外大丰收的部分。把 rawpy 踢出热路径后，**它仍然可用**
（fallback 路径 + 我们可以在专用命令里主动用它），所以 rawpy 提供的所有
LibRaw 数据现在是**按需可调用**的资源，而不是无脑被调用。

### 8.1 直接可拿、无需写代码的（已经在 record 里）

新 `_exif_lite` 现在已经在解，原 exiftool 路径也有，但没在 CLI 暴露：

- **LensMake** —— 已经在 record 里，可以加 `--where lens_make=='Canon'` 滤镜
- **完整 EXIF Orientation 值**（不只是 portrait/landscape）—— 旋转方向 1/3/6/8 已经有了
- **ExposureCompensation**（曝光补偿数值）—— 已经在 record，可以筛 `--where bias < -0.5`
- **SubSecTimeOriginal** —— 修了 bug 后，亚秒级 burst 排序终于稳定了

### 8.2 几乎零成本能加的（rawpy fallback 路径里 LibRaw 已经在算）

LibRaw 打开文件时**顺手**算出的字段，我们目前没存：

| LibRaw 字段 | rawkit 用途建议 | 实现成本 |
|---|---|---|
| `raw.color_desc` (b'RGBG') | RGGB vs Foveon (X3F) 区分 | 1 行 |
| `raw.color_matrix` (3×4 float) | 显示厂商色彩矩阵 / 估算色温偏好 | 5 行 |
| `raw.camera_whitebalance` / `daylight_whitebalance` | 显示拍摄时白平衡设定 | 3 行 |
| `raw.black_level_per_channel` | 暗角校正质量判断 | 2 行 |
| `raw.lens.min_focal` / `max_focal` | **定焦 vs 变焦自动识别**（min==max → 定焦） | 5 行 |
| `raw.lens.eff_max_aperture` | 镜头实际最大光圈（vs 标称值） | 2 行 |
| `raw.sizes.raw_width` / `raw_height` (vs `width`/`height`) | 区分 sensor 像素 vs demosaic 输出（裁切边界） | 2 行 |
| `raw.tone_curve` | 厂商色调曲线（高级用户可视化） | 5 行 |
| `raw.num_colors` (3 or 4) | Foveon X3 vs Bayer | 1 行 |

只要在 fallback path 里把这些字段也塞进 record（或在 `rawkit info` 时**主动**
打开一次 rawpy 把这些字段补全），就免费拿到。

### 8.3 我们的 TIFF parser 已经走过的数据，能再榨

`_read_ifd_windowed` 和 `_walk_bmff` 是通用的，可以加：

- **Canon CMT3 (MakerNote)** —— 已经被 `_walk_bmff` 路过，没 parse。
  里面有：Canon AF point 选择、对焦距离估计、按下快门时的图像稳定状态、闪光灯类型……
  代价：写几个 Canon makernote sub-tag 解码（lclevy 文档已有）。
- **Sony SR2/MRW makernote** —— 同上路径
- **PreviewImage 偏移 + 长度** —— IFD0:0x0111 / 0x0117。可以**不解码 JPEG**
  就拿到 preview 字节范围 → `rawkit extract` 不再需要 rawpy.extract\_thumb
  （省 ~300 ms/file）
- **Thumbnail dimensions** —— 已经被 `_resolve_dimensions` 路过（IFD0
  那对 ImageWidth/Height 在 DNG/3FR 上正好是缩略图维度），可以在 fallback
  分一列出来。

### 8.4 性能解锁的新场景

之前因为太慢、没人会做的操作，现在变得可行：

- **`rawkit watch`**（文件夹 inotify 实时刷新元数据）—— lite ~0.5 ms/file，每秒能扫 2000 张
- **`rawkit dedupe`**（按 DateTimeOriginal + SubSec 找完全重复的连拍）—— 几秒钟扫全库
- **`rawkit health`**（巡检整库找：损坏 / 缺 GPS / 缺 lens info 的文件）—— 几秒完成
- **`rawkit aggregate by_camera --year 2024`**（按机身/镜头切片统计）—— 不再是分钟级，秒级
- **本地交互式 TUI**（`rawkit tui`）—— 启动时整库索引 < 30s，可以做实时过滤

### 8.5 测试基础设施红利

`tests/test_exif_lite.py` 里的 `_build_tiff()` 合成器（~50 行）可以复用于：

- 给 `extract` / `info` 命令写"病态文件"测试
- mock 出"无 Make"、"超大 IFD"、"循环 SubIFD" 等边界
- 把 cross-backend equivalence test 推广到更多字段（lens_make / preview_size / …）

---

## 9. 风险与未覆盖

诚实清单：

1. **NEF (Nikon)** —— PERF.md 里提过，我们的 ARW/DNG 走通了，NEF 大概率也行（同 TIFF 结构），
   但**没有真样本测试**。第一次有用户报 NEF 问题，去仓库 issue。
2. **加密 Sony ARW**（部分 a7R IV 之后机型）—— LibRaw 能解，我们的 TIFF parser
   会拿到 Make/Model 但 ExifIFD 加密块解不出。**fallback 会触发**，慢但能用。
3. **CR2** (老 Canon) —— CR3 走通；CR2 是普通 TIFF，应当走 `_parse_tiff()` 默认路径。
   同样没真样本测试。
4. **truly weird files**（HDR 多帧、视频帧抓取等）—— fallback 救场。

任何字段疑似不对，**第一招永远是**：

```bash
RAWKIT_BACKEND=exiftool rawkit ls /path/to/file.cr3
```

对一下输出。如果跟 lite 不一致，就是 bug，去
[tests/test\_exif\_lite.py](tests/test_exif_lite.py) 加一条真样本测试。

---

## 10. 怎么验证 / 怎么基准

```bash
# 跑全套测试（含合成、不含真样本）：
uv run --with pytest pytest -q

# 跑真样本跨后端等价性测试：
RAWKIT_TEST_SAMPLES=/path/to/raw uv run --with pytest pytest tests/test_exif_lite.py -v

# 跑基准（500 文件，两种后端各跑一次，报加速比）：
uv run python scripts/bench_exif.py /path/to/raw 500

# 切回旧 exiftool 路径排查疑似 bug：
RAWKIT_BACKEND=exiftool rawkit ls /path/to/file.cr3
```

---

## 11. 一句话总结

> 把"读 EXIF"从"调一个 17 ms/file 的 Perl 怪兽"换成了"用 0.05 ms 自己读
> 一个 IFD"。**没有新依赖、没有 API 变化、零用户感知变化、130× 速度。**

---

<a id="2026-06-22--性能调研为什么慢瓶颈在哪能换什么"></a>

# 性能调研:为什么慢、瓶颈在哪、能换什么

> 2026-06-22 的一次讨论记录,留作后续优化依据。结论尚未落地到代码。

## 问题

几万张 RAW 的文件夹用任何 rawkit 命令都难以忍受地慢,且**没有进度条**——"不知道还要多久"放大了痛感。每个命令都从零跑一遍 exiftool,所以重复忍受。

## 瓶颈实测(在 `samples/` 28 张 RAW,SSD,本地)

| 场景 | 耗时 | 每张 |
|---|---|---|
| exiftool 启动开销(0 文件,`-ver`) | 36 ms | — |
| exiftool 跑 28 张(rawkit 当前用的完整字段集) | 500 ms | ~17 ms |
| 同样 28 张第二次(page cache warm) | 500 ms | 17 ms |
| 28 张 + `-fast2`(跳过 maker note) | 250 ms | ~7 ms |
| **rawpy(LibRaw, C++)打开+读全部主要字段** | **5 ms** | **0.2 ms** |

### 结论

- **真瓶颈 = exiftool 解析每个 RAW 的 CPU 时间**(单线程 Perl 解析 maker note)。
- **不是 I/O**:warm/cold 一样快 → page cache 命中 → 纯 CPU。
- **不是 fork 开销**:36 ms / 500 ms ≈ 7%,忽略。
- `-fast2` 砍一半 = 砍掉的就是 maker note 解析这部分 CPU 工作 → 反向证实 CPU 是瓶颈。

### 外推到真实库(单进程 exiftool)

- 1 万张 ≈ 2 分 50 秒
- 5 万张 ≈ 14 分钟
- 10 万张 ≈ 28 分钟

## "用 C++ 重写 exiftool" 是个伪问题

- **重写整个 exiftool 不现实**:Phil Harvey 单人维护 20+ 年、~30 万行 Perl,核心价值是几百种 maker note 私有格式(Nikon LensID 加密查表、Sony ARW 加密、Canon DPP 私有 tag、富士 RAF 自定义……)。这不是"换语言"能省的工——是知识库。
- **但其实不需要重写**:`rawpy` 是 LibRaw 的 Python 绑定,**LibRaw 本身就是 C++ 的 RAW 解析器**,而且 rawpy 已在 `pyproject.toml` 依赖里。它解 RAW 时顺便就把元数据掏出来了,**实测比 exiftool 快 ~85 倍**。
- 缺的那几个字段(标准 EXIF tags,不是 maker note)用 `exifread`/`pyexiv2`/自己读 TIFF IFD 都能轻松补,无需走 exiftool。

## rawkit 现用字段 vs LibRaw 覆盖表

下面按 `src/rawkit/exif.py` 里 `_FIELD_MAP` 的顺序逐项标。

| rawkit 字段 | 现在的 exiftool 来源 | LibRaw / rawpy 能否拿到 | 怎么拿 / 备注 |
|---|---|---|---|
| `path` | SourceFile | ✅ 平凡 | 自带,不需读文件 |
| `datetime` (秒精度) | DateTimeOriginal | ✅ **完整** | `raw.other.timestamp`(已是 `datetime` 对象) |
| `_subsec_raw`(亚秒) | SubSecTimeOriginal | ❌ **没有** | LibRaw 不暴露亚秒;突发连拍排序会丢精度 |
| `maker` | Make | ⚠️ **C 层有,rawpy 没绑** | LibRaw `imgdata.idata.make` 存在,Python 层没暴露 |
| `model`(机身) | Model | ⚠️ **C 层有,rawpy 没绑** | 同上,`imgdata.idata.model` |
| `lens` | LensModel | ✅ **完整,且更干净** | `raw.lens.model`,如 `"RF800mm F11 IS STM"`,**无需 strip 厂商前缀** |
| `iso` | EXIF:ISO | ✅ | `raw.other.iso_speed` |
| `fnumber` | EXIF:FNumber + APEX 后备 | ✅ | `raw.other.aperture`(LibRaw 内部已做 APEX 后备) |
| `_apex_raw` | EXIF:ApertureValue | — | 不需要,已被 LibRaw 合并 |
| `shutter` | ExposureTime | ✅ | `raw.other.shutter_speed` |
| `focal` | FocalLength | ✅ | `raw.other.focal_length` |
| `bias` | ExposureCompensation | ❌ **没有** | LibRaw 不暴露曝光补偿 |
| `rating` | Rating | ❌ **没有** | LibRaw 不读这个标准 EXIF tag |
| `image_width` | ImageWidth | ✅ | `raw.sizes.width`(demosaic 后实际宽,接近 exiftool 报的) |
| `image_height` | ImageHeight | ✅ | `raw.sizes.height` |
| `preview_width` | PreviewImageWidth | ⚠️ **绕一下** | 无直接字段;`raw.extract_thumb()` 拿 JPEG 字节再读 dimension |
| `preview_height` | PreviewImageHeight | ⚠️ **绕一下** | 同上 |
| `gps_lat` | GPSLatitude | ❌ **没有** | LibRaw 完全不读 GPS |
| `gps_lon` | GPSLongitude | ❌ **没有** | 同上 |
| `_orientation_raw` | Orientation | ✅ **已翻译过** | `raw.sizes.flip`,EXIF orientation 已转成 LibRaw flip 编码(0/3/5/6) |
| `_flash_raw` | Flash | ❌ **没有** | LibRaw 不读 Flash tag |
| `lens_make` | (rawkit 没用) | ✅ 附赠 | `raw.lens.make` |
| `min_focal` / `max_focal` | (rawkit 没用) | ✅ 附赠 | 镜头规格焦段,可识别变焦/定焦 |

### 派生字段(从上面推出来的,LibRaw 不影响)
- `date` / `time` / `year` / `month` / `day` / `hour` — 从 `datetime` 派生 ✅
- `gps` (bool) — 从 `gps_lat`/`gps_lon` 派生,依赖上面缺的 GPS ❌
- `orientation`(`"portrait"`/`"landscape"`)— 从 `flip` 推 ✅
  - 映射规则:flip ∈ {5,6} → portrait,{0,3} → landscape

### 缺口汇总

**纯缺(必须另解标准 EXIF,不是 maker note):**
1. `bias`(ExposureCompensation)
2. `rating`(Rating)
3. `gps_lat` / `gps_lon`
4. `flash`
5. `subsec`(亚秒,连拍排序用)

**rawpy 没绑但 LibRaw C 层有:**

6. `maker`(Make)
7. `model`(机身 Model)

## 可行的优化路线(按收益/工作量排序)

1. **rawpy 接管能拿的 7 个主字段** + 轻量 EXIF 库(`exifread` 等)补另外 7 个标准 EXIF tags → **整体 ~50× 提速**,代码量不大。预估每张 ~2 ms,1 万张 ~20 秒、5 万张 ~100 秒(vs exiftool 14 分钟)。
2. **缓存**(SQLite 按 `(abspath, mtime_ns, size)` 索引):让所有"非首次"命令秒回。第 1 步做到位后,缓存优先级下降。
3. **并行**:rawpy 0.2 ms/张已经基本不必要;若仍走 exiftool 路径,把文件列表切 N 份并发 N 个 exiftool 进程能近似线性加速。
4. **进度条**(`rich.progress`,无新依赖):分钟级任务下基本必要。

`-fast2` 不予考虑:它会丢掉 LensModel/Rating 这种 rawkit 实际在用的字段。

## 待定的下一步动作

- 写一个 PoC `exif_rawpy.py`(或类似),并行使用 rawpy + 一个轻量 EXIF 库,对样本做**字段对齐验证**(逐字段对比 exiftool 当前路径输出,确认无回归),不动现有代码。
- 验证通过后再决定:① 是直接把 `exif.py` 切到 rawpy 路径(保留 exiftool 作为后备/对照),还是 ② 先加缓存再换后端。

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

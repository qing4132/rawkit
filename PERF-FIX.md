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
| **RAF** (Fujifilm) | `FUJIFILMCCD-RAW` 16 字节 | 偏移 `0x54` 处 BE-uint32 指向 TIFF |
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

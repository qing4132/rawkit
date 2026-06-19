# USAGE

> 当前实际可用的命令。这页随代码同步更新。
> V1 的目标 surface(`ls` / `info` / `extract` / `render` / `organize`)还没全部对齐;现在能跑的是 `ls` / `preview` / `render` / `stats`,后两者未来会改名/合并。看 [TODO.md](TODO.md)。

---

## 安装

```bash
# 仓库根目录
uv tool install --editable .

# 系统依赖:exiftool
brew install exiftool          # macOS
apt install libimage-exiftool-perl   # Debian/Ubuntu
```

要 Python 3.14+。

---

## `rawkit ls`

按文件列 EXIF。一行一文件,默认按 datetime 升序。

```bash
rawkit ls [PATHS...] [-w EXPR] [-s KEY,...] [-r] [-R] [--json]
```

| flag                 | 含义 |
| -------------------- | --- |
| `PATHS`              | 文件或目录,可混。默认当前目录,目录只看顶层(`-R` 递归) |
| `-w / --where EXPR`  | 按 EXIF 表达式过滤(见下方 DSL) |
| `-s / --sort KEY[,KEY2,...]` | 排序键,可多键。缺失值永远排到最后 |
| `-r / --reverse`     | 反向 |
| `-R / --recursive`   | 递归目录 |
| `--json`             | JSONL 输出(管道用) |

默认列:`file datetime model lens focal aperture shutter bias iso`。

例:

```bash
rawkit ls ~/Pictures/2024-trip                          # 整个目录
rawkit ls *.CR3 -s iso -r                               # 按 ISO 降序
rawkit ls . -w 'iso>3200 and lens~"50"'                 # 高 ISO + 50mm 镜
rawkit ls . --json | jq '.path'                         # 喂给 jq
```

---

## `rawkit preview` (V1 会改名为 `extract`)

把每个 RAW 里的最大嵌入 SOOC JPEG 拽出来,写到 `-o` 指定的目录。**不做 RAW 解码**——只是文件 IO + offset 计算,所以快(100 张 RAW 通常 < 1 秒)。

```bash
rawkit preview [PATHS...] -o DIR [-R] [-f] [-w EXPR]
                           [--long N | --short N | --mp N] [-q N]
```

| flag                 | 含义 |
| -------------------- | --- |
| `-o / --output DIR`  | 输出目录 |
| `--long N`           | resize 让长边 = N px(LANCZOS) |
| `--short N`          | resize 让短边 = N px |
| `--mp N`             | resize 让像素总数 ≈ N 百万 |
| `-q / --quality N`   | resize 后的 JPEG 质量(默认 90) |
| `-f / --overwrite`   | 覆盖已存在文件 |
| `-w / --where EXPR`  | 同 `ls --where` |
| `-R / --recursive`   | 递归 |

不给 `--long/--short/--mp` 时,**原嵌入 JPEG 字节直出**(零重编码、毫秒级)。给了任一 resize flag 才走 Pillow 重编码,EXIF Orientation 会烤进像素。

例:

```bash
rawkit preview ~/Pictures/2024-trip -o /tmp/peek         # 全量提取,极快
rawkit preview . -o /tmp/peek --long 2000               # 长边 2000 px
rawkit preview . -o /tmp/keepers -w 'rating>=4'         # 只导出 4 星以上
```

---

## `rawkit render`

跑 libraw demosaic + Pillow 编码,输出完整解码的 JPEG / TIFF / PNG。**色彩科学会偏 SOOC**(libraw 默认管线、没有相机 Picture Style)。

```bash
rawkit render [PATHS...] -o DIR [-R] [-f] [-w EXPR]
                          [--format jpeg|tiff|png] [-q N] [--max-side N]
```

| flag                  | 含义 |
| --------------------- | --- |
| `-o / --output DIR`   | 输出目录 |
| `--format FMT`        | `jpeg` / `tiff` / `png`(默认 jpeg) |
| `-q / --quality N`    | JPEG 质量(默认 90) |
| `--max-side N`        | 长边最大 N px,0 = 原生分辨率 |
| `-f / --overwrite`    | 覆盖已存在文件 |
| `-w / --where EXPR`   | 同 `ls --where` |
| `-R / --recursive`    | 递归 |

例:

```bash
rawkit render *.ARW -o out/                              # 默认 JPEG q=90
rawkit render . -o web/ --max-side 2400 -q 85           # web 用
rawkit render . -o tiff/ --format tiff -w 'rating==5'   # 顶级片输出 TIFF
```

---

## `rawkit stats` (V1 会并进 `info`)

聚合一组 RAW 的 EXIF + 文件大小。默认一屏概览;`--by` 钻一个维度。

```bash
rawkit stats [PATHS...] [-w EXPR] [--by DIM[,DIM2,...]] [-R] [--json]
```

| flag                | 含义 |
| ------------------- | --- |
| `--by DIM`          | 钻一个维度。可选:`model` / `camera` / `lens` / `maker` / `orientation` / `iso` / `aperture`(= `fnumber`) / `focal` / `hour` / `year` / `month` / `day`。带 `--by` 时不再打默认概览,只出 bar chart |
| `--top N`           | `lens` 维度的 top-N 截断(默认 5)。其他维度忽略 |
| `--more`            | `lens` 维度显示全部(覆盖 `--top`) |
| `-w / --where EXPR` | 同 `ls --where` |
| `-R / --recursive`  | 递归 |
| `--json`            | 结构化输出全量(不受 `--by` / `--top` 影响) |

### 默认输出

```
Photos        25
Total size    1.37 GiB
Date range    2022-04-23 → 2025-08-09  (3 years, 3 months, 17 days)
Hour          02–03, 06, 10–12, 14–20, 22–23
Cameras       11
Lenses        22
Orientation   22 (landscape), 3 (portrait)
ISO           64 – 6400
Aperture      f/1.4 – f/11
Shutter       1/1250 – 10s
Focal length  14mm – 800mm
```

字段含义:
- **Date range**:`(N years, N months, N days)` 是 start → end 的**日历分解**(三者相加 = 跨度)。
- **Hour**:出现过的小时按连续段合并显示。`02–03, 06, 10–12` 中间真有空段。
- **Cameras / Lenses**:不同型号 / 镜头的数量。
- **Orientation**:`{count} ({key})`,top 3 + `+N others`(值少时全列)。
- **ISO / Aperture / Shutter / Focal length**:真实极值 `min – max`。

### `--by`

```bash
rawkit stats samples/ --by month
rawkit stats samples/ --by aperture
rawkit stats samples/ --by camera,lens     # 两段顺序输出
```

带 `--by` 时**不再打前置概览**,只出 bar chart。要更深的分布分析就 `--json | python`,我们不在终端里跟 pandas 竞争。

---

## `--where` DSL

被所有命令共享。`lark` 实现,没有 `eval()`。

### 字段

| 类型      | 字段                                          |
| --------- | --------------------------------------------- |
| 数值      | `iso` · `fnumber`(= `aperture`)· `shutter`(秒)· `focal`(mm)· `bias`(EV)· `rating`(0–5)· `gps_lat` · `gps_lon` |
| 整数桶    | `hour`(0–23)· `year`· `month`(1–12)· `day`(1–31) |
| 字符串    | `lens` · `model` · `maker` · `orientation`(`portrait` / `landscape`) |
| 时间      | `datetime` · `date`(YYYY-MM-DD)· `time`(HH:MM[:SS[.NNN]]) |
| 布尔      | `gps`(是否有坐标)· `flash`(闪没闪) |

### 桶字段比较语义(钉死)

`hour` / `year` / `month` / `day` 是**整数桶 ID**,比较即桶号比较:

- `hour > 6` ≡ `hour >= 7`,意思是"7 点桶或之后",**6:30 不在内**。
- `month == 11` 选"任意年的 11 月",跟 `--by month`(YYYY-MM 历时桶)是不同的语义,两者配合可以写出"我历年 11 月的密度对比":`stats --by month -w 'month==11'`。
- 想做"6:00:00 这个时刻之后"用 `time > "06:00:00"`,跟整数桶不重叠。
- `>` / `>=` 在整数桶上自然重合(SQL `WHERE month > 6` 也是这个意思),不是 bug。

### 操作

- 比较:`>` `<` `>=` `<=` `==` `!=`
- 字符串子串(大小写不敏感):`lens~"50mm"`
- 布尔:`and` `or` `not`、括号

### Aperture 反向语义

`aperture` 是 `fnumber` 的反向别名(摄影师习惯:f/1.4 比 f/8 "大")。在 `--where` 里:

```
aperture >= 2.8     等价于     fnumber <= 2.8
```

读起来就是"光圈大于等于 f/2.8"。在 `--sort` / `--by` 里两者等价,都按 fnumber 升序(f/1 → f/22)。

### 例

```bash
ls -w 'iso>3200 and lens~"50"'
ls -w '(focal>=70 and focal<=200) or lens~"70-200"'
ls -w 'date>="2024-06-01" and not model~"iPhone"'
ls -w 'orientation=="portrait" and rating>=4'
ls -w 'aperture>=1.4'                       # f/1.4 或更大光圈
ls -w 'hour>=18 and hour<=22'               # 傍晚到夜间
ls -w 'month==11 and year>=2023'            # 2023 起每年的 11 月
```

---

## 全局约定

- 退出码:`0` 成功 / `1` 部分失败(详情进 stderr)/ `2` 用法错
- **stdout 只走数据,日志/进度走 stderr**(管道契约)
- `--json` 走 JSONL(每行一对象),方便 `jq` / `pandas.read_json(lines=True)`
- 路径不存在时尽量给人话错误(`file not found: foo.ARW`),不甩 traceback

---

## 当前已知坑

- `rawkit ls` 在某些写了非标准 EXIF 的 RAW 上(如 Leica M11 Monochrom 缺 `EXIF:FNumber`)历史上会读错;**已经针对 ISO 和 Aperture 做了 EXIF 组锁定 + APEX 反算的 fallback**,但其他字段的类似坑随时可能浮现——dogfood 撞到欢迎报。
- `stats --by hour` 用分段格式(`02–04, 22–23`),所以不会再有"中间空段被压平"的歧义。

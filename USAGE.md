# USAGE

> 五个命令: `ls` / `info` / `extract` / `render` / `organize`。本页随代码同步更新。设计路线见 [TODO.md](TODO.md)。

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

## `rawkit extract`

把每个 RAW 里的最大嵌入 SOOC JPEG 拽出来,写到 `-o` 指定的目录。**不做 RAW 解码**——只是文件 IO + offset 计算,所以快(100 张 RAW 通常 < 1 秒)。

```bash
rawkit extract [PATHS...] -o DIR [-R] [-f] [-w EXPR]
                           [--long N | --short N | --mp N] [-q N]
```

| flag                 | 含义 |
| -------------------- | --- |
| `-o / --output DIR`  | 输出目录(默认 `./jpegs`) |
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
rawkit extract ~/Pictures/2024-trip -o /tmp/peek         # 全量提取,极快
rawkit extract . -o /tmp/peek --long 2000               # 长边 2000 px
rawkit extract . -o /tmp/keepers -w 'rating>=4'         # 只导出 4 星以上
rawkit extract ~/Pictures -R -o /tmp/jpegs               # 递归,镜像源目录结构
```

> **输出路径规则**:
> - 目录输入(`-R` 时):输出**镜像源子目录**。`~/Pictures/2024/IMG_0001.CR3` → `/tmp/jpegs/2024/IMG_0001.jpg`。不同子目录里的同名 RAW 不会撞车。
> - 文件输入:只取 basename。`~/Pictures/2024/IMG_0001.CR3` → `/tmp/jpegs/IMG_0001.jpg`。
> - 多个目录输入互相重叠时,**第一个匹配的目录**当作 root。

> rawkit 只负责“拽出 JPEG”。后续怎么看让 Finder 、`open -a Preview`、`qlmanage`、你自己的脚本接手——这不是 rawkit 的职责。

---

## `rawkit render`

跑 libraw demosaic + Pillow 编码,输出完整解码的 JPEG / TIFF / PNG。**色彩科学会偏 SOOC**(libraw 默认管线、没有相机 Picture Style)。

```bash
rawkit render [PATHS...] -o DIR [-R] [-f] [-w EXPR]
                          [--format jpeg|tiff|png] [-q N]
                          [--long N | --short N | --mp N]
```

| flag                  | 含义 |
| --------------------- | --- |
| `-o / --output DIR`   | 输出目录 |
| `--format FMT`        | `jpeg` / `tiff` / `png`(默认 jpeg) |
| `-q / --quality N`    | JPEG 质量(默认 90) |
| `--long N`            | resize 让长边 ≤ N px(LANCZOS) |
| `--short N`           | resize 让短边 ≤ N px |
| `--mp N`              | resize 让像素总数 ≤ N 百万 |
| `-f / --overwrite`    | 覆盖已存在文件 |
| `-w / --where EXPR`   | 同 `ls --where` |
| `-R / --recursive`    | 递归 |

例:

```bash
rawkit render *.ARW -o out/                              # 默认 JPEG q=90
rawkit render . -o web/ --long 2400 -q 85               # web 用(长边)
rawkit render . -o social/ --short 1080 -q 85           # 社媒用(短边)
rawkit render . -o proof/ --mp 6 --format jpeg          # 约 6MP proof
rawkit render . -o tiff/ --format tiff -w 'rating==5'   # 顶级片输出 TIFF
```

和 `extract` 一样,`--long / --short / --mp` 三者互斥。图像本身比目标更小时不会放大。

> **输出路径规则**:
> - 目录输入(`-R` 时):输出镜像源子目录。`~/Pictures/2024/IMG_0001.ARW` → `/tmp/renders/2024/IMG_0001.jpg`。
> - 文件输入:只取 basename。
> - 同次运行若两个输入会落到同一路径(含大小写仅字母差异),会 fail-fast 直接报冲突并退出,避免静默覆盖。

---

## `rawkit info`

描述 RAW。两种模式以输入是单文件还是多/文件夹划分;单文件 = 全字段 KV,多/文件夹 = 整体 KV summary 或 `--by` 某一维度的分档。

```bash
rawkit info [PATHS...] [-w EXPR] [--by DIM] [--top N] [--more] [-R] [--json]
```

| flag                | 含义 |
| ------------------- | --- |
| `PATHS`             | 单文件 → FILE 模式;其他一切(文件夹 / 多输入 / 默认当前目录) → DIR 模式 |
| `-w / --where EXPR` | EXIF 谓词过滤(同 `ls --where`) |
| `--by DIM`          | DIR 模式:按一个维度分档。示例见下。多维 `A,B` 暂不支持 |
| `--top N`           | 仅对 `--by lens` 生效的 top-N 截断(默认 5) |
| `--more`            | `--by lens` 显示全部(覆盖 `--top`) |
| `-R / --recursive`  | DIR 模式:递归 |
| `--json`            | JSON 输出(FILE 一个对象;DIR 完整聚合字典) |

### FILE 模式

```
Path          /path/to/IMG_0001.CR3
Size          51.8 MiB (54348886 B)
DateTime      2022-05-13 16:38:09.01
Maker         Canon
Camera        EOS R5
Lens          RF50mm F1.8 STM
ISO           400
Aperture      f/1.8
Shutter       1/250
Focal length  50mm
Bias          0 EV
Rating        0
Orientation   landscape
Flash         False
Image         8192x5464
GPS           31.200000, 121.500000
Embedded      JPEG 8192x5464 (5.37 MiB)
```

`Embedded` 一行调用 extract 同样的路径拿到 libraw 选的主嵌入预览。

### DIR 模式默认输出

```
Path          ~/Pictures/2024-trip
File          29 RAWs (1.53 GiB)
Date range    2024-06-01 → 2024-06-15  (15 days)
Hour          06–09, 14–19
Maker         3 (Canon, SONY, FUJIFILM)
Camera        4 (EOS R5, X-E5, ILCE-7RM4A, +1 others)
Lens          8
ISO           100 – 6400
Aperture      f/1.4 – f/16
Shutter       1/8000 – 30s
Focal length  14mm – 200mm
Bias          -2 EV – +1 EV
Rating        29 (unrated)
Orientation   25 (landscape), 4 (portrait)
Flash         1 (on), 28 (off)
GPS           3 (yes), 26 (no)
```

- **Maker / Camera / Lens** 会根据终端宽度自适应降级(`3 names + N others` → `2 names + ...` → `1 name + ...`),保证不换行。管道输出时不截断。
- **Date range** 括号里是起止的**日历分解**(三者相加 = 跨度)。
- **Hour** 连续小时折成区间。
- **Rating** 把 `0` 并入 `unrated`(区分对多数人没意义)。
- **Flash / GPS** 缺 EXIF tag 的记录计作 `off` / `no`。

### `--by`

```bash
rawkit info samples/ --by camera
rawkit info samples/ --by month
rawkit info samples/ --by lens --more
rawkit info samples/ --by aperture -w 'iso>=3200'
```

输出是纯净的表格(无 bar chart 无字符画面):

```
Camera

  EOS R5         11  38%
  ILCE-7RM4A      5  17%
  X-E5            1   3%
  ...
```

可用维度: `camera`(= `model`) · `lens` · `maker` · `orientation` · `iso` · `aperture`(= `fnumber`) · `focal` · `shutter` · `bias` · `rating` · `hour` · `year` · `month` · `day`。跟 `ls --where` 和 `organize --by` 是同一套词表。

### 从某个桌下钻到具体文件

目前只能重提一次 ls(看了 `--by iso` 发现 "≤1004 文件" 很感兴趣,然后):

```bash
rawkit ls samples -w 'iso<=100 and ...same predicate as before...'
```

shell 历史起来后手动拼 `iso<=100` 上去。未来 `info --by FOO -l` 会直接在桌下列出路径,但 V1 不会加(见 TODO)。

---

## `rawkit organize`

按 EXIF 维度把 RAW 文件 move / copy 到分层目录。

```bash
rawkit organize [PATHS...] [-o DIR] [--by DIM[,DIM,...]] [-R] [-w EXPR]
                            [--copy] [--prune] [-n / --dry-run] [-f]
```

| flag                | 含义 |
| ------------------- | --- |
| `PATHS`             | 要整理的文件或目录(默认当前目录) |
| `-o / --output DIR` | 目标根。**不给 → 默认第一个输入目录**(in-place organize) |
| `--by DIM[,DIM,...]`| 可选。逐维嵌套子目录:`--by camera,month` → `EOS R5/2024-01/foo.cr3`。不给 → 平铺到 DEST(配 `--where` 做 cherry-pick) |
| `-R / --recursive`  | 递归扫描源目录 |
| `-w / --where EXPR` | EXIF 谓词过滤(同 `ls --where`) |
| `--copy`            | 复制而不是移动(默认 move) |
| `--prune`           | 整理后顺手 rmdir 源目录树里所有空的非隐藏子目录(仅含 `.DS_Store` 的也算)。`.git/` 类 dotfile 目录永不动 |
| `-n / --dry-run`    | 只打印计划,不动 filesystem |
| `-f / --overwrite`  | 目标已存在时覆盖(默认 skip) |

### 典型用法

```bash
# 按月分 — 最常见

rawkit organize ~/dump -o ~/sorted --by month
# → ~/sorted/2024-01/, ~/sorted/2024-02/, ...

# 嵌套(年/月)
rawkit organize ~/dump -o ~/sorted --by year,month

# 机型 + 年
rawkit organize ~/dump -o ~/sorted --by camera,year -R

# in-place(不写 -o):在 ~/Pictures 里原地按月整理
rawkit organize ~/Pictures --by month

# cherry-pick(无 --by,平铺到单个目录)
rawkit organize ~/dump -o ~/keepers -R -w 'rating>=4'
rawkit organize ~/dump -o ~/lowlight -w 'iso>=3200'

# 顺手清理空文件夹(例如上一轮 `--by year` 留下来的空 `2021/`)
rawkit organize ~/Pictures --by month --prune

# 第一次跑陌生目录先看计划
rawkit organize ~/Pictures -o ~/sorted --by month -n
```

### 默认行为(不需要你做的)

- **是 move 不是 copy** — 除非 `--copy`。
- **同名 `.xmp` / `.jpg` sidecar 跟 RAW 一起搬** — LrC 的 rating / develop 不会孤立。
- **缺 EXIF 值的文件进 `_unknown/`** — 下划线前缀让它排到最上面。
- **同次运行内目标冲突 fail-fast** — 正常路径 + 大小写不敏感(macOS APFS / Windows)都检。冲突上报告连同源文件列表,一个文件不动,exit 1。
- **目标已存在默认 skip**,`-f` 才覆盖。
- **桌名里的 `/`(`f/2.8`、`1/250`)被替换为 `_`**(`f_2.8`、`1_250`)避免路径嵌套。
- **`--prune` 只扫非隐藏子目录**。仅含 `.DS_Store` 算空;有任何用户文件不动。根目录从不被删。

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
- `month == 11` 选"任意年的 11 月",跟 `--by month`(YYYY-MM 历时桶)是不同的语义,两者配合可以写出"我历年 11 月的密度对比":`info --by month -w 'month==11'`。
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
- `info --by hour` 用分段格式(`02–04, 22–23`),中间空段不会被压平。
- `focal` 字段是镜头实际焦距,不会自动算 35mm 等效;裁剪画幅(APS-C / m4/3) 上看到的是裸焦距。详 [TODO.md](TODO.md)。

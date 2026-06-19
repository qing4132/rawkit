# rawkit — 用法手册

> 这是**用法**文档,不是设计文档。每加一个功能就往这里追加示例。
> 设计/原则/路线图见 [README.md](README.md)。
>
> 当前可用命令:`rawkit ls`、`rawkit preview`、`rawkit render`、`rawkit stats`。

---

## 装 & 跑

```bash
# 在 rawkit 项目目录内一次性装为全局工具(editable):
uv tool install --editable .

# 之后任何 terminal 任何 cwd:
rawkit --help
rawkit ls --help
```

> ⚠️ **加新依赖后要重装一次**。`--editable` 只让源代码改动实时生效,**依赖是装的那一刻锁的**。每次仓库里多了一个 `uv add` 的包(如 lark、rich),需要:
> ```bash
> cd ~/Documents/code/rawkit && uv tool install --reinstall --editable .
> ```
> 否则下次 `rawkit ls` 会 `ModuleNotFoundError`。

依赖:**需要系统装 `exiftool`**。
- macOS:`brew install exiftool`
- Debian/Ubuntu:`apt install libimage-exiftool-perl`
- 没装时 rawkit 会人话提示。

---

## `rawkit ls`

列出 RAW 文件并展示关键 EXIF。是 `rawkit` 的入口命令。

### 签名
```
rawkit ls [PATHS...] [-w/--where EXPR] [-s/--sort KEY[,KEY2,...]] [-r/--reverse] [-R/--recursive] [--json]
```

- `PATHS`:零个或多个**目录或 RAW 文件**。无参数 = 当前目录。默认只看顶层(同 `ls`),要递归加 `-R`。
- `--where, -w EXPR`:按 EXIF 条件过滤,见下方 DSL 参考。
- `--sort, -s KEY[,KEY2,...]`:按某一列或多列排序(逗号分隔为主、次、再次…)。**默认 `datetime`**。可选:`file` / `datetime` / `date` / `time` / `model` / `lens` / `focal` / `aperture` / `shutter` / `bias` / `iso`。
  - 如 `--sort model,datetime`:先按机型分组,同机型内部按拍摄时间
  - 同 key 什么排序都不指定时的 fallback 是路径字典序(相机原生名≈拍摄顺序)
- `--reverse, -r`:反转顺序(**会反转所有 key**,不是只反主键)。缺失值永远排最后(与方向无关,SQL NULLS LAST)。
- `--recursive, -R`:递归进子目录(默认 OFF)。
- `--json`:JSONL 输出(每行一对象),给 `jq` 等管道。默认是人读对齐表。

### 退出码
- `0` 成功
- `1` 部分失败(如某个路径不存在)
- `2` 用法错(如 `--where` 表达式语法错)

### 默认表格输出

```bash
rawkit ls samples/
```
```
file          date              model         lens                ...
168A0721.CR3  2022-05-13 16:38  EOS R5        RF800mm F11 IS STM  ...
...
```

列:`file  datetime  model  lens  focal  aperture  shutter  bias  iso`

- `datetime` 是 **EXIF 拍摄时间**(不是文件 mtime),表格里精到分;完整到秒的值在 `--json` 里
- `bias` 是曝光补偿 EV,带符号(`+1` / `-2.42` / `0`),缺失为 `-`
- 默认按 `datetime` 升序(最早拍的在上),可用 `--sort` / `-r` 改
- 表格按内容自适应宽度,**不截断**;窄终端用 `| less -S` 横向滚屏
- 单个超长文件名只破自己那行,其它行保持对齐

### 表头与颜色

- **当前排序的那列**表头会加个箭头:`datetime↑`(升)/ `iso↓`(降)。表头整行加粗;**不**给排序列另外染色(颜色太容易被读成价值判断,箭头已足够表达"哪列被排序了")。箭头是信息,即使关了颜色也仍会显示
- **数据行不染色**。颜色只用于表达结构信息(当前 sort key),不用于价值判断("这个 iso 太高了"之类是预设立场)
- 颜色受三个东西控制(任一关闭):
  - stdout 不是 TTY(管道、重定向到文件)→ 自动关
  - 环境变量 `NO_COLOR=1`(任何值均可)→ 关,遵循 [no-color.org](https://no-color.org) 标准
  - `TERM=dumb` 不关(未实现,要添加很简单)

### 多路径 / 文件混合
```bash
rawkit ls ~/Pictures/2024-10 ~/Pictures/2024-11
rawkit ls samples/168A0721.CR3 samples/_DSC4187.ARW
rawkit ls samples/_DSC4187.ARW samples/         # 文件 + 目录混合,自动去重
```

不存在的路径 → stderr 报错并退出 1;非 RAW 文件 → stderr 警告并跳过。

### JSON 输出(管道用)
```bash
rawkit ls samples/ --json
```
```jsonl
{"path": "samples/168A0721.CR3", "datetime": "2022-05-13 16:38:09", "date": "2022-05-13", "time": "16:38:09", "maker": "Canon", "model": "EOS R5", "lens": "RF800mm F11 IS STM", "iso": 400, "fnumber": 11, "shutter": 0.00625, "focal": 800, "bias": 0}
...
```

JSON 字段名与 `--where` DSL 字段名完全一致。

```bash
# 找所有高 ISO 的路径,喂给别的工具
rawkit ls ~/Pictures/2024 --json | jq -r 'select(.iso > 3200) | .path'
```

### `--where` 表达式 DSL

#### 字段

| 字段 | 类型 | 来源 |
|---|---|---|
| `iso` | 数值 | EXIF ISO |
| `fnumber` / `aperture` | 数值 | 光圈。**`aperture` 是规范名,按摄影圈"光圈大小"语义比较——`aperture>=2.8` 筛 f/2.8 及更大光圈(f/2 f/1.4 ...)**。`fnumber` 是反向 alias,按 EXIF FNumber 数值比较——`fnumber>=2.8` 筛 f/2.8 及更小光圈(f/4 f/5.6 f/8 ...)。 |
| `shutter` | 数值(秒) | 曝光时间,如 0.004 = 1/250 |
| `focal` | 数值(mm) | **实际拍摄焦段**(变焦头会随每张变) |
| `bias` | 数值(EV) | 曝光补偿, +/- |
| `rating` | 数值 0–5 | LrC / Bridge 等打的星标 |
| `gps_lat` | 数值 | GPS 纬度(带符号,南半球为负) |
| `gps_lon` | 数值 | GPS 经度(带符号,西半球为负) |
| `lens` | 字符串 | LensModel |
| `model` | 字符串 | 机型,如 "EOS R5"、"M11 Monochrom"。**已自动剥掉冗余的厂商前缀**(Canon/NIKON/LEICA/RICOH 这几家原 EXIF `Model` 是 "Canon EOS R5" / "LEICA M11 Monochrom" 这种重复 `maker` 的写法,rawkit 归一化时去掉首词以免和 `maker` 字段冗余)。原始 maker 仍保存在 `maker` 字段。 |
| `maker` | 字符串 | 厂商,如 "SONY" / "Canon" |
| `orientation` | 字符串 | `"landscape"` 或 `"portrait"`(从 EXIF Orientation 推导) |
| `datetime` | 字符串 | `YYYY-MM-DD HH:MM:SS[.NNN]`(拍摄时间全串;相机写了 SubSecTime 时带亚秒,用于连拍排序) |
| `date` | 字符串 | `YYYY-MM-DD`(从 datetime 切出) |
| `time` | 字符串 | `HH:MM:SS[.NNN]`(从 datetime 切出) |
| `gps` | 布尔 | `true` 仅当 lat 和 lon 都存在 |
| `flash` | 布尔 | 闪光灯实际击发了为 `true` |

#### 操作符

| 操作 | 适用 | 例子 |
|---|---|---|
| `> < >= <= == !=` | 数值 / 字符串 / 日期 / 时间 / datetime | `iso>3200`, `date>="2024-01-01"`, `time<"06:00:00"`, `datetime>="2024-01-01 12:00:00"` |
| `== !=` (仅) | 布尔字段(`gps`/`flash`) | `gps==true`, `flash!=true` |
| `~` | 字符串(大小写不敏感**子串**包含) | `lens~"GM"`, `model~"R5"` |
| `and` / `or` / `not` | 逻辑组合 | `iso>800 and not lens~"24-70"` |
| `(...)` | 优先级括号 | `(focal>=70 and focal<=200) or lens~"70-200"` |

**优先级**:括号 > `not` > `and` > `or`。

**字面量**:`123`、`1.5`、`-2.0`(数值);`"..."`(字符串);`YYYY-MM-DD`(日期);`HH:MM` / `HH:MM:SS` / `HH:MM:SS.NNN`(时间);`YYYY-MM-DD HH:MM[:SS[.NNN]]` 或 `YYYY-MM-DDTHH:MM[:SS[.NNN]]`(datetime);`true` / `false`(布尔)。

#### 例子(由浅入深)

```bash
# 高 ISO 的(暗光/夜拍)
rawkit ls samples/ --where 'iso>=1000'

# 大光圈
rawkit ls samples/ --where 'aperture>=2.0'

# 长曝(≥1 秒)
rawkit ls samples/ --where 'shutter>=1'

# 50mm 焦段(任何镜头实际转到 50mm)
rawkit ls samples/ --where 'focal==50'

# 中焦段(70–200 区间,既覆盖变焦也覆盖定焦)
rawkit ls samples/ --where 'focal>=70 and focal<=200'

# 用某个具体镜头(GM 头系列)
rawkit ls samples/ --where 'lens~"GM"'

# Canon 拍的夜景
rawkit ls samples/ --where 'maker~"canon" and time>="20:00"'

# 不要 iPhone 拍的
rawkit ls ~/Pictures --where 'not model~"iphone"'

# 某天某时间段
rawkit ls ~/Pictures --where 'date>="2024-10-01" and date<"2024-11-01"'

# 复杂组合
rawkit ls ~/Pictures --where '(iso>=3200 and aperture>=2.8) or shutter>=1' --json | jq '.path'

# 只要竖构图(粗选时最常用)
rawkit ls ~/Pictures --where 'orientation=="portrait"'

# 加头灯亮起来的
rawkit ls ~/Pictures --where 'flash==true'

# 在北京的所有片(粗略的 GPS box)
rawkit ls ~/Pictures --where 'gps_lat>39 and gps_lat<41 and gps_lon>115 and gps_lon<117'

# 有 GPS 但不在某个区域(反选主页)
rawkit ls ~/Pictures --where 'gps==true and not (gps_lat>39 and gps_lat<41)'

# 推 / 拉过曝光的片
rawkit ls ~/Pictures --where 'bias>=1'
rawkit ls ~/Pictures --where 'bias<=-1'

# 你在 LrC 里打过 3 星及以上的精选(前提:sidecar .xmp 存在)
rawkit ls ~/Pictures --where 'rating>=3'

# “culling 后的 keepers” 复合查询
rawkit ls ~/Pictures --where 'rating>=3 and orientation=="landscape" and flash==false'
```

#### 容易踩的坑

1. **子串匹配会"误伤数字"**
   `lens~"50"` 会命中 "E 70-**3<u>50</u>**mm"(里面有 "50" 两字符)。
   想表达"50mm 拍的"应用 **`focal==50`**(更准,且能捞到变焦头转到 50mm 的那张)。

2. **短时间字面量 = unit 的起点**(跟 SQL 一致)
   `time=="16:00"` 被解释为 `"16:00:00.000"`,只会正好匹配那一瞬间,**不是**"16:00 这一分钟里任何张"。同理 `datetime=="2024-01-02"` = 那天 0 点 0 分 0 秒。
   - 要"那一分钟任何张":`time>="16:00" and time<"16:01"`
   - 要"那天任何张":直接用 `date=="2024-01-02"`(这是三字段拆分的价值)
   - 范围查询(`<` / `<=` / `>` / `>=`)都能混用任意精度,不会有边界意外

3. **`shutter` 用秒数,不是 "1/250"**
   `shutter<=1/250` ❌ — DSL 不算式子。
   `shutter<=0.004` ✅。

4. **`date` 必须是 `YYYY-MM-DD`,中间是连字符**
   `date>="2024:01:01"` ❌(EXIF 旧格式)
   `date>="2024-01-01"` ✅

5. **字符串字段不能用数字比较**
   `lens==50` ❌ — 编译期报错。
   `lens~"50mm"` 才对(且小心上面第 1 条)。

6. **缺失字段视为不匹配**
   RICOH GR III 这种定焦机没有 `LensModel`,任何 `lens~"..."` 都不会命中它。

#### 错误信息长这样

```bash
rawkit ls samples/ --where 'iso > and 5'
# rawkit: --where: can't parse --where at line 1, column 7:
# iso > and 5
#       ^
```

退出码 2(用法错,与 grep/find 一致)。

---

## `rawkit preview`

抽取每个 RAW **内嵌的最大 SOOC JPEG 预览**,写到指定目录。

> **总是 SOOC**:输出是相机自己渲染并嵌进 RAW 的 JPEG,**100% 保留厂商色彩科学**(Canon Picture Style / Fuji Film Simulation / Sony Creative Look / Leica 单色…)。不做任何 demosaicing,不会色偏。
>
> "从原 RAW 像素重新生成 JPEG"(可控 demosaic、会偏色)是 `rawkit render` 的职责,不是本命令。

### 签名

```
rawkit preview [PATHS...] [-o/--output DIR] [-R/--recursive] [-f/--overwrite]
                           [-w/--where EXPR]
                           [--long N | --short N | --mp N] [-q/--quality N]
```

- `PATHS`:零个或多个**目录或 RAW 文件**。无参数 = 当前目录。默认只看顶层(同 `ls`)。
- `--output, -o DIR`:输出目录,默认 `./previews/`,不存在自动创建。输出文件名 = `<DIR>/<源文件 stem>.jpg`。
- `--recursive, -R`:递归进子目录(默认 OFF)。
- `--overwrite, -f`:覆盖已存在的输出。**默认 skip 并警告**(反复跑不会重复抽)。
- `--where, -w EXPR`:按 EXIF 谓词过滤候选文件(同 `ls --where` 的 DSL)。设了会多调一次 exiftool 读候选集的 EXIF。
- **缩放(三选一,互斥)**——下面单独讲。
- `--quality, -q N`:JPEG 质量 1-100,默认 90。**仅当**指定缩放时生效(原 SOOC 字节直出时不重新编码)。

### 按 EXIF 筛选:`--where`

跟 `ls --where` 一样的 DSL。只抽候选集中命中谓词的 RAW:

```bash
# 2023 年后、f/4 拍的那些,造 2000px 预览
rawkit preview samples/ --where 'date>="2023-01-01" and aperture==4' --long 2000 -o web/

# A7R IV 拍的高 ISO那批,走默认(原字节直出)
rawkit preview /shoot -R --where 'model~"7RM4" and iso>=3200' -o picks/

# 某镜头某期间的竖拍
rawkit preview /trip -R --where 'lens~"50mm F1.4" and orientation=="portrait"' -o p/
```

- 不加 `--where` 时不会调 exiftool(preview 快路径保持有效)
- 语法错误 → `exit 2 + lark 风格的精准位置报错`(同 `ls --where`)
- 0 张命中 → 干净退出(`exit 0`),不创建输出目录

等价的管道写法(为复杂场景保留、互补不互斥):

```bash
rawkit ls samples/ --where '...' --json | jq -r .path | xargs rawkit preview --long 2000 -o web/
```

### 缩放:`--long` / `--short` / `--mp`

不指定 → **抽出原始 SOOC 字节直接写盘**(零损耗、毫秒级)。

指定**其中一个** → JPEG 解码 + LANCZOS 缩放 + 重新编码:

| flag | 含义 | 典型用例 |
|---|---|---|
| `--long N` | 长边降到最多 N 像素 | LrC export 风格,通用尺寸控制 |
| `--short N` | 短边降到最多 N 像素 | 社媒尺寸(Instagram 短边 1080) |
| `--mp N` | 总像素降到最多 N 百万 | "我要 4MP 不管什么比例" |

```bash
# Canon R5 嵌的 8192×5464 SOOC JPEG → 2000×1334
rawkit preview *.CR3 --long 2000

# Sony A1 横图 8640×5760 → 1620×1080(社媒)
rawkit preview *.ARW --short 1080

# Ricoh GR III DNG 6000×4000=24MP → 2449×1633=4MP
rawkit preview *.DNG --mp 4
```

**永不放大**:图本来比目标小就直接抽原字节(Sony A7R IV 嵌的 1616 用 `--long 2000` 不会变 2000)。

**二次 JPEG 损失**:启用缩放 = 解码 + 缩 + 重新编码,**有微量画质损失**。默认 quality 90 + chroma 4:4:4 已经把损失压到肉眼难辨。不要在意 = 不指定缩放参数。

**三个 flag 互斥**——同时给两个/三个会用法错误退出 2:

```
$ rawkit preview foo.CR3 --long 2000 --short 1080
Invalid value: --long / --short are mutually exclusive — pick one
```

### 退出码
- `0` 全部成功 或 全部 skip
- `1` 有任何一张失败(独立报错 + 继续处理后面的文件,不中断)

### 默认输出

```bash
rawkit preview samples/
```
```
168A0721.CR3: 8192x5464 -> previews/168A0721.jpg
B0000326.3FR: 3888x2918 -> previews/B0000326.jpg
DSC01471.ARW: 1616x1080 -> previews/DSC01471.jpg
L1000009.DNG: 9504x6320 -> previews/L1000009.jpg
...
20 extracted, 0 skipped, 0 failed
```

- **stdout 一片空**——可安全给 `xargs` 用;进度和汇总走 stderr
- 进度行格式:`<文件名>: <W>x<H> -> <输出路径>`
- 抽到的是**该 RAW 内嵌的最大 JPEG**,不同机型差别很大(见下表)

### 物理上抽出多大?

不是所有机型都会嵌全分辨率 SOOC JPEG。实测表:

| 机型 | 原 RAW | 抽到 preview | 占比 |
|---|---|---|---|
| Canon EOS R5 (CR3) | 8192×5464 | **8192×5464** | 100% ✓ |
| Sony A1 (ILCE-1, ARW) | 8704×6144 | **8640×5760** | ≈99% ✓ |
| Nikon Z5 II (NEF) | 6064×4040 | **6048×4032** | ≈99.7% ✓ |
| Leica M11 Monochrom (DNG) | 9536×6336 | **9504×6320** | ≈99.7% ✓ |
| Ricoh GR III (DNG) | 6112×4064 | **6000×4000** | ≈98% ✓ |
| Hasselblad X2D 100C (3FR) | 11904×8842 | 3888×2918 | ≈33%(中画幅嵌中档) |
| Fuji GFX100RF (RAF) | 11648×8736 | 4000×3000 | ≈34%(中画幅嵌中档) |
| Fuji X-E5 (RAF) | 7728×5152 | 4416×2944 | ≈57%(4K 中档) |
| OM-5 II (ORF) | 5240×3912 | 3200×2400 | ≈61%(6K 中档) |
| Sony A7R IV (ARW) | 9600×6376 | 1616×1080 | ≈17%(Sony 中端不嵌全分辨率) |
| Sony ZV-1 (ARW) | 5504×3672 | 1616×1080 | ≈29% |

规律:
- **全画幅旗舰**多嵌近似全分辨率(Canon R5 / Sony A1 / Nikon Z / Leica M11)
- **中画幅**(Hasselblad / Fuji GFX)只嵌中档(不愿浪费 200MB+ 的空间)
- **老/中端 Sony** 只嵌 1616×1080(需要大预览的人会失望)
- **OM / Fuji X / Ricoh GR III** 嵌 4K–6K 中档

需要什么都能抽出近似原图?**现在不能**——该机型不嵌全分辨率就抽不出。`rawkit render` 会走 demosaic 路径,但色彩科学会偏 SOOC,与本命令互补、不重合。

### 跳过/覆盖

```bash
rawkit preview samples/                 # 首次跑:抽出所有
rawkit preview samples/                 # 再跑:全部 skip(输出已存在)
rawkit preview samples/ -f              # 强制覆盖
```

stderr 跳过提示:`<文件名>: skip (exists, use -f to overwrite)`

### 失败场景

极少见,但会发生:
- RAW 损坏 / libraw 认不出
- 嵌的是 BITMAP 不是 JPEG(现代机型几乎不可能)

```
broken.ARW: failed — libraw failed: <原始 libraw 报错>
```

退出码 1。**失败不中断**后面的文件,汇总里会看到 `N extracted, N skipped, N failed`。

### 设计说明

- **单引擎(libraw 经 rawpy)**:实测比包 `exiftool` 抽快 30–40 倍(后者每文件一次 Perl 进程启动)。libraw 默认就抽能抽到的最大档,无需选择。
- **160×120 导航缩略图不走**:太小没用。需要的话未来加 `--include-tiny`。
- **不提供 resize**:抽出原始字节;要缩用 `sips` / `magick mogrify` / `vipsthumbnail` 都可(硬约束 #4 Unix 哲学)。
- **为什么叫 `preview` 不叫 `thumb`**:抽出来可能是 8192×5464 的 SOOC 原图,叫"缩略图"名不副实;相机不同厂商都管这东西叫 preview。
- **必须装 rawpy**:`uv tool install` 时自动拉进来,需要 libraw 底层(macOS/Linux 的 wheels 都带)。

---

## `rawkit render`

用 libraw demosaic 原 RAW 像素,Pillow 编码成新的 JPEG/TIFF/PNG。**与 `preview` 互补**:preview 抽相机已经做好的(快、SOOC);render 自己重新做(慢、色彩科学会偏 SOOC)。

> ℹ️ **关于这个命令的真实价值,坦诚版**:
>
> render **不是 rawkit 的主力命令**。绝大多数场景下 `preview` 就够了——Canon/Nikon/Leica/Ricoh 嵌全分辨率 SOOC JPEG;Hasselblad/Fuji 中画幅嵌 3000~4000 中档够看;Fuji X/OM 嵌 4-6K 够发布。`render` 的当下独占价值只剩两种场景:
>
> 1. **该机型不嵌大图**(Sony 老/中端 ARW 只有 1616×1080)且你就要更大的 JPEG/TIFF/PNG
> 2. **要 lossless TIFF/PNG**(preview 永远是 JPEG)
>
> 调色(WB / 曝光 / 高光阴影 / 锐化)**不在 rawkit 的范围**——那是 LrC / Capture One 的活,硬约束。render 的输出永远是 libraw 默认参数 + sRGB + 8-bit。
>
> 保留这个命令的真正理由是**作为基础设施**:未来若做 sidecar-driven 调色或 LLM 视觉调色(Year 4 路线图),底层 demosaic + 编码这条管线都用得上。现阶段 render 已经技术验证了这条管线能跑通,**不再扩展**(不加 --denoise / --color-profile / --depth 16 等)。

### 什么时候用 render 而不是 preview

- 该机型不嵌全分辨率(Sony A7R IV 只嵌 1616×1080;中画幅 / Fuji X 只嵌 3000~4000 中档)且你要原图尺寸
- 你想要 lossless TIFF / PNG 输出(preview 永远是 JPEG)
- 你不在乎色彩偏移、就想要"解出来的像素"

### 什么时候**别**用 render

- 你的机型本来就嵌全分辨率(Canon CR3 / Sony A1 / Nikon Z5 II / Leica M11 / Ricoh GR III)——直接 `preview`,瞬间到手且 SOOC
- 你要"看起来像 LrC 出片"——render 给不了,用 LrC
- 一次几百张 RAW 全要 render——会慢(0.5-2 秒/张),想清楚

### 签名

```
rawkit render [PATHS...] [-o DIR] [-w/--where EXPR]
                         [--format jpeg|tiff|png] [-q N] [--max-side N] [-R] [-f]
```

- `PATHS`:同 `preview`,目录或文件,默认当前目录
- `--output, -o DIR`:输出目录,默认 `./renders/`
- `--where, -w EXPR`:按 EXIF 谓词过滤候选(同 `ls --where`)。设了会多调一次 exiftool。
- `--format`:`jpeg`(默认,有损)/ `tiff`(无损)/ `png`(无损)。文件名后缀按格式自动加(`.jpg` / `.tiff` / `.png`)
- `--quality, -q N`:JPEG 质量 1-100,默认 90。TIFF/PNG 忽略
- `--max-side N`:长边降到 N 像素(LANCZOS 缩放);默认 0 = 保留传感器原生分辨率
- `--recursive, -R`:递归
- `--overwrite, -f`:覆盖已存在

### 退出码
- `0` 全部成功 或 全部 skip
- `1` 有任何一张失败(独立报错 + 继续)

### 示例

```bash
# 默认:全分辨率 JPEG, q=90
rawkit render samples/_DSC4187.ARW
# _DSC4187.ARW: 9600x6376 jpeg -> renders/_DSC4187.jpg

# 给 Sony A7R IV 出 2000px 的预览(因为它只嵌 1616,preview 拿不到这个尺寸)
rawkit render samples/*.ARW --max-side 2000

# 无损 TIFF,用于二次处理
rawkit render shoot.NEF --format tiff -o archive/

# 高质量 web 输出
rawkit render *.CR3 --max-side 1920 -q 95

# 按 EXIF 筛选(同 `ls --where` 的 DSL)
rawkit render shoot/ --where 'iso>=3200 and lens~"50"' --max-side 2000 -o picks/
```

### 性能

- ~0.5 秒 / 张(中端 Sony / Canon APS-C / 全画幅 < 30MP)
- ~1.5 秒 / 张(全画幅 4500 万像素)
- ~2 秒 / 张(中画幅 1 亿像素)

实测 samples/ 里 20 张混合厂商全 render(全分辨率,无 resize)约 20-30 秒。**比 preview 慢 30 倍**——选 preview 能解决的事别用 render。

### 失败场景

- libraw 认不出/不支持的 RAW
- 嵌的预览是 BITMAP 不是 JPEG(几乎不可能)
- Pillow 编码失败(罕见,通常是写盘问题)

```
broken.ARW: failed — libraw failed: <原始 libraw 报错>
```

退出码 1。失败不中断后面的文件。

### 设计说明

- **不暴露调色参数**(WB / 曲线 / 锐化):那是 LrC 的活,rawkit 只做"解出来"
- **JPEG 用 4:4:4 chroma**(`subsampling=0`):render 的目标是"高质量重出片",不是缩略图;额外几个 KB 换无色度损失值得
- **JPEG 加 optimize=True**:实测真实照片上稳定;Pillow optimize 在极端高熵数据(纯随机噪声)上可能挂,但生活里不会撞上
- **8-bit 输出 only**:libraw 默认 8-bit。需要 16-bit 的话以后加 `--depth 16`(对 TIFF 才有意义)
- **必须装 rawpy + Pillow**:都是 `uv tool install` 自动拉

---

## `rawkit stats`

对一组 RAW 做 EXIF + 文件大小聚合。年度回顾、按机型/镜头/ISO/光圈/焦段/时段分布——LrC 给不了的命令行视图。

### 签名

```
rawkit stats [PATHS...] [-w/--where EXPR] [-R/--recursive]
                        [--by DIM] [--top N | --more] [--json]
```

- `PATHS`:同 ls/preview/render
- `--where, -w EXPR`:复用 lark DSL(`ls --where` 同款)
- `--by DIM`:进入单维度详细视图,替代默认 4 段。合法值:`model` / `lens` / `maker` / `orientation` / `iso` / `aperture` (别名:`fnumber`,反向显示) / `focal` / `hour` / `month`。**字段名跟 `--where` DSL 完全对齐**——`aperture>=2.8` 跟 `--by aperture` 指的是同一件事。
- `--top N`:默认视图中"按镜头" top N(默认 5),`--by` 模式忽略
- `--more`:默认视图中显示全部镜头(覆盖 `--top`)
- `--recursive, -R`:递归
- `--json`:输出完整结构化聚合(不受 `--by`/`--top` 影响),供脚本

### 默认输出(4 段)

```bash
rawkit stats samples/
```

```
Summary
────────────────────────────────────────────────────────
Photos      25
Total size  1.37 GiB
Date range  2022-04-23 → 2025-08-09  (1205 days)
Cameras     11
Lenses      22  (3 fixed-lens)

By camera
────────────────────────────────────────────────────────
EOS R5         11  █████████████                    44%
ILCE-7RM4A      5  ██████                           20%
...

By ISO (log-scale buckets)
────────────────────────────────────────────────────────
≤100       8  ██████████                       32%
101–200    3  ████                             12%
201–400    7  ████████                         28%
401–800    3  ████                             12%
801–1600   2  ██                                8%
1601–3200  1  █                                 4%
3201–6400  1  █                                 4%

By lens (top 5)
────────────────────────────────────────────────────────
RF24-105mm F4 L IS USM  2  ██                               8%
...
... 17 more lenses hidden (--more / --top N / --by lens to see all)
```

视觉规则:表头加粗(TTY 时)、横线分隔、bar 用 `█`、不染色、不用 emoji、百分号纵向对齐。

### `--by FIELD` 单维度详细

```bash
rawkit stats samples/ --by month     # 按月份(年度回顾)
rawkit stats samples/ --by hour      # 按 EXIF 拍摄时段(3 小时桶)
rawkit stats samples/ --by maker     # 按厂商(Sony / Canon / Fuji / ...)
rawkit stats samples/ --by orientation  # 横图 vs 竖图
rawkit stats samples/ --by aperture  # 按光圈大小(摄影方向,小光圈 f/22 在前 → 大光圈 f/1 在后)
rawkit stats samples/ --by fnumber   # 按 EXIF FNumber 数值(f/1 在前 → f/22 在后)
rawkit stats samples/ --by focal     # 按焦段类别(超广/广/标/中长/长/超长)
rawkit stats samples/ --by lens      # 完整镜头分布,不 top 截断
```

> **为什么 `aperture` 跟 `fnumber` 同时存在 且 反向**:
>
> 摄影圈说"光圈"总是指镜头透光孔的大小——**f/1.4 是"大光圈"但 fnumber 数字更小**。两种心智模型都是合理的,rawkit 两者都接受:
>
> | 你想表达 | 写法 | 同义 |
> |---|---|---|
> | "筛大光圈(浅景深,弱光)" | `aperture>=2.8` | `fnumber<=2.8` |
> | "筛小光圈(深景深,风景)" | `aperture<=8` | `fnumber>=8` |
> | "按光圈从大到小排" | `--sort aperture -r` | `--sort fnumber` |
>
> 规范名是 **`aperture`**——摄影圈原生语言。`fnumber` 是 EXIF 标准数值字段,比较方向跟 aperture 反。两者**数据完全同源**,仅比较/排序/显示方向镜像对称。

### 跟 `--where` 组合(子集统计)

```bash
# 高 ISO 都在哪个时段拍的?
rawkit stats samples/ --where 'iso>=3200' --by hour

# Sony 机器的统计(默认 4 段会带 "Filter" 一行)
rawkit stats samples/ --where 'maker~"Sony"'

# 今年某焦段的拍摄量按月分布
rawkit stats ~/Pictures -R --where 'date>="2026-01-01" and focal>=70 and focal<=200' --by month
```

`--by` 视图的标题会带 caption:`By ISO  ·  filter: iso>=400  ·  n=13`,让你一眼看出当前是子集统计。

### JSON 输出(供脚本)

```bash
rawkit stats samples/ --json
```

返回结构化 dict 含 `total` / `by_model` / `by_iso_bucket` / `by_lens` / `by_aperture_bucket` / `by_focal_bucket` / `by_hour_bucket` / `by_month_bucket`。每个 `by_*` 是 `[{key, count, share}, ...]`。

### 退出码

- `0` 成功
- `1` 路径下找不到 RAW / `--where` 命中 0 张
- `2` `--by` 给了不认识的维度,或 `--where` DSL 语法错

### 设计说明

- **桶定义是摄影玩家友好的**(非自动 binning):
  - ISO:每 stop 一档,8 桶(≤100 / 101–200 / ... / >6400)
  - 光圈:13 个标准档,实测光圈在 ±6% 内对齐到最近一档(f/2.7 → f/2.8)
  - 焦段:6 类(<20mm 超广 / 20–35 广 / 35–70 标 / 70–200 中长 / 200–600 长 / >600 超长)
  - 时段:8 个 3 小时桶(00–02 / 03–05 / ... / 21–23)
- **TTY 检测**:非 TTY(管道、重定向)自动关颜色(同 ls)
- **跟 ls 风格一致**:表头加粗、不染色、不用 emoji
- **空桶不显示**:不会输出 "0 张" 的空行,默认视图保持紧凑
- **排序**:大多数维度按 count 降序 + key 字母升序;`month` 按年月时序(因为月份是有序量纲)

---

## 速查矩阵

### 命令 × 选项

| 命令 | `-w/--where` | `-s/--sort` | `--by` |
|---|---|---|---|
| `ls` | ✓ | ✓ | — |
| `preview` | ✓ | — | — |
| `render` | ✓ | — | — |
| `stats` | ✓ | — | ✓ |

`--where` DSL 是 4 个命令**共享同一份**(`rawkit.query.compile_where`)。

### 字段 × 命令场景

| 字段 | `--where` | `ls --sort` | `stats --by` | 类型/备注 |
|---|---|---|---|---|
| `iso` | ✓ | ✓ | ✓(对数桶) | 数值 |
| `aperture` | ✓ | ✓(反向:asc=小光圈先) | ✓ **规范名**(f/22→f/1) | 摄影方向 |
| `fnumber` | ✓ alias | ✓ alias | ✓ alias(f/1→f/22) | EXIF 数值方向、aperture 镜像 |
| `shutter` | ✓ | ✓ | — | 数值(秒);连续值不分桶 |
| `focal` | ✓ | ✓ | ✓(6 焦段类) | mm |
| `bias` | ✓ | ✓ | — | EV;连续值 |
| `rating` | ✓ | — | — | 0–5 |
| `lens` | ✓ | ✓ | ✓ | 字符串子串 |
| `model` | ✓ | ✓ | ✓ | 字符串(已去 maker 前缀) |
| `maker` | ✓ | — | ✓ | 字符串 |
| `orientation` | ✓ | — | ✓ | `"portrait"` / `"landscape"` |
| `datetime` | ✓ | ✓ | — | 完整时间戳 |
| `date` | ✓ | ✓ | `--by month`(粗化) | YYYY-MM-DD |
| `time` | ✓ | ✓ | `--by hour`(粗化) | HH:MM:SS |
| `gps` | ✓(bool) | — | — | `==true` / `==false` |
| `flash` | ✓(bool) | — | — | 同上 |
| `file` | — | ✓ | — | 文件名(仅 ls 表格有意义) |

### `aperture` vs `fnumber` 双向对照

`aperture` 和 `fnumber` **数据同源**(都是 EXIF FNumber),但**比较/排序/显示方向相反**。互为镜像。

| 我想表达 | 用 `aperture` 写法 | 等价 `fnumber` 写法 |
|---|---|---|
| 筛大光圈 ≥ f/2.8(f/2.8 f/2 f/1.4 …) | `--where 'aperture>=2.8'` | `--where 'fnumber<=2.8'` |
| 筛小光圈 ≤ f/8(f/8 f/11 f/16 …) | `--where 'aperture<=8'` | `--where 'fnumber>=8'` |
| 恰好 f/4 | `aperture==4` | `fnumber==4` |
| 不等于 f/11 | `aperture!=11` | `fnumber!=11` |
| 列表按光圈从大到小排(最大在前) | `--sort aperture -r` | `--sort fnumber` |
| 列表按光圈从小到大排(最小在前) | `--sort aperture` | `--sort fnumber -r` |
| 分布图按摄影方向(小光圈先) | `--by aperture` | — |
| 分布图按 EXIF 数值(f/1 先) | — | `--by fnumber` |

### `--where` 操作符

| 类别 | 操作符 | 例 |
|---|---|---|
| 数值比较 | `>` `<` `>=` `<=` `==` `!=` | `iso>3200` |
| 字符串子串(大小写不敏感) | `~"sub"` | `lens~"50mm"` |
| 布尔 | `==true` / `==false` / `!=` | `flash==true` |
| 时间 | 同数值(按 canonicalized 串比较) | `time>="18:00"` |
| 逻辑 | `and` `or` `not` `()` | `iso>=3200 and lens~"50"` |

---

## 还没做的(早晚加,加完就把示例写到这里)

短期小补丁:

- `rawkit preview --list` — 只列每个 RAW 内的可用档不抽
- `rawkit ls` 文件大小列(可选)
- `rawkit ls --limit N` / `-n N` 取前 N 条(现在靠 `\| head`)
- `rawkit ls --columns` 选定要显示的列(默认 9 列)

完整路线图(主题分组 + Top 5 个人最看好)见 [README.md "路线图——未来命令候选"](README.md#路线图未来命令候选) 一节。**那是地图不是承诺**——每条都等真实 dogfood 撞到痛点驱动。

## 大量图片时要不要“翻页”

短答:**暂时不做交互式翻页**,先靠 Unix 组合。

原因:
- `ls` 本质是可组合的数据命令,不是交互浏览器。内建翻页会把 stdout 从“纯数据流”变成“会话状态机”,管道兼容性变差。
- 现阶段最痛的是“筛得准 + 排得准”,不是翻页本身。先把查询表达能力和列控制做扎实,收益更大。
- 大目录场景已有低成本方案,且都符合 Unix 心智:

```bash
# 横向滚屏浏览(推荐)
rawkit ls ~/Pictures -R | less -S

# 先看前 200 行
rawkit ls ~/Pictures -R | head -n 200

# 精确分页(第 3 页,每页 100)
rawkit ls ~/Pictures -R --json \
   | jq -c '.' \
   | sed -n '201,300p'
```

后续建议(若 dogfood 反复痛到):
- 先加 `--limit` + `--offset`(或 `--page` + `--page-size`)这种**无状态分页**
- 不做交互式“按空格下一页”的 pager 逻辑

详细愿景见 [README.md](README.md)。

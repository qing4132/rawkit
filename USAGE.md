# rawkit — 用法手册

> 这是**用法**文档,不是设计文档。每加一个功能就往这里追加示例。
> 设计/原则/路线图见 [README.md](README.md)。
>
> 当前可用命令:`rawkit ls`。

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
168A0721.CR3  2022-05-13 16:38  Canon EOS R5  RF800mm F11 IS STM  ...
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
{"path": "samples/168A0721.CR3", "datetime": "2022-05-13 16:38:09", "date": "2022-05-13", "time": "16:38:09", "maker": "Canon", "model": "Canon EOS R5", "lens": "RF800mm F11 IS STM", "iso": 400, "fnumber": 11, "shutter": 0.00625, "focal": 800, "bias": 0}
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
| `fnumber` | 数值 | 光圈,如 2.8 |
| `shutter` | 数值(秒) | 曝光时间,如 0.004 = 1/250 |
| `focal` | 数值(mm) | **实际拍摄焦段**(变焦头会随每张变) |
| `bias` | 数值(EV) | 曝光补偿, +/- |
| `rating` | 数值 0–5 | LrC / Bridge 等打的星标 |
| `gps_lat` | 数值 | GPS 纬度(带符号,南半球为负) |
| `gps_lon` | 数值 | GPS 经度(带符号,西半球为负) |
| `lens` | 字符串 | LensModel |
| `model` | 字符串 | 机型,如 "Canon EOS R5" |
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
rawkit ls samples/ --where 'fnumber<2.0'

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
rawkit ls ~/Pictures --where '(iso>=3200 and fnumber<2.8) or shutter>=1' --json | jq '.path'

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

## 还没做的(早晚加,加完就把示例写到这里)

- `rawkit thumb` — 抽 RAW 内嵌缩略图
- `rawkit export` — rawpy 全解码导出 JPEG/PNG
- 文件大小列(可选)
- `--limit N` / `-n N` 取前 N 条(现在靠 `\| head`)
- `--columns` 选定要显示的列(默认 9 列)

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

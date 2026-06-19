# rawkit · RAW 摄影的命令行瑞士军刀

> 给本地 RAW 文件做批处理 / metadata / sidecar / 浏览的现代 Python CLI。
> **不替代 Lightroom Classic，做 LrC 不做的事**——脚本化的命令行盒子，LrC 旁边那一格。

> ⚠️ 状态:**v0.0.1 / 内测中**。当前已实现 `rawkit ls`(EXIF 列表、`--where`、`--sort`、`--json`)、`rawkit preview`(抽 RAW 内嵌 SOOC JPEG)和 `rawkit render`(libraw demosaic 出 JPEG/TIFF/PNG)。
> 其余命令(`exif` 独立子命令、sidecar 相关)仍在规划中。
>
> 📖 **当前实际能跑什么、怎么敲命令** → 看 [USAGE.md](USAGE.md)(每加一个功能就追加到那里)。本文件只管"为什么/做什么/不做什么"。

---

## 硬约束(优先级最高,凌驾本文其他一切内容)

> 这些是项目的不可越线规则。下面任何章节(命令清单、五年弧线、施工顺序…)若与本节冲突,以本节为准。作者**随时可能追加新的强约束**,新约束追加到本节末尾。

1. **内测之前绝不对外发布**。"稳定"不只指性能,更指**命令名、子命令、flag、API 形状不再变化**。在此之前:
   - 使用**内部版本号**(`0.0.x` / `0.1.0a1` 之类 pre-release),不打 `1.0`、不发 PyPI、不写 Homebrew Formula、不发推不发博客
   - 一切都可以推翻重做,包括本 README 里所有已拍板的决策
   - 内测圈 = 作者自己 + 几个朋友,够了
2. **RAW 只读是核心约束**。在整个内测期间,直到出现某个"非做不可"的版本(但愿永远不出现),工具**绝不对原始 RAW 文件做任何写操作**——不改像素、不写 EXIF、不嵌 XMP、不 touch 时间戳。允许的写操作只发生在:
   - **派生文件**(preview / render 的输出)
   - **sidecar 文件**(`.xmp` / `.json` 等独立于 RAW 的伴生文件)
   - **文件系统级 rename**(`mv`,改文件名/路径,不改 RAW 字节内容)——边缘允许,但默认 `--dry-run`
3. **一切 Python 相关用 [`uv`](https://github.com/astral-sh/uv) 管理**。依赖、虚拟环境、运行、分发、安装、锁文件全走 uv,不混 `pip` / `pipx` / `poetry` / `conda`。
4. **最大限度遵循 Unix 软件哲学**。Do one thing well;靠管道组合;文本流作为通用接口;**不重复造现有标准工具能做好的事**。具体推论:
   - 如果 `mv` / `cp` / `find` / `xargs` / `jq` / `column` / `sort` 已经能干的事,**不在 rawkit 里复刻一份**
   - 每个子命令只做一件事、做好;靠 stdout/stdin + JSON/text 让用户自己组合
   - 拒绝"全家桶"诱惑——撞到"我们也加个 X 吧"的念头时,先问"标准 Unix 工具能做吗?"
   - **直接后果**:`rawkit rename` 从 v0.1 砍掉(`rawkit ls --json | jq | xargs mv` 就够了);未来任何看似有用的命令都要过这道筛
5. **尽一切可能减轻用户心智负担。本条凌驾于 #4 之上**。当 Unix 纯洁性和"用户少想一秒"冲突时,**永远选后者**。具体推论:
   - 默认值要"绝大多数情况下不用想":`preview` 默认输出 `./previews/`、`rename` 类操作默认 `--dry-run`、列表默认按时间倒序…
   - **人话错误信息**:"file not found: foo.ARW" 而不是 traceback;"exiftool 没装,`brew install exiftool` 即可"而不是 `FileNotFoundError: [Errno 2]`
   - **同一概念在 rawkit 内永远叫同一个名字**(`--output` 就别再叫 `--to`、`--dest`)
   - 该重复时就重复——`--where` 的 mini-DSL 严格说违反 #4(`find + exiftool + jq` 能做),但**让摄影玩家少学一个 jq = 值得**
   - `-h` / `--help` 是一等公民:每个子命令的 help 必须**一屏看完**就知道怎么用,而不是"请阅读 man page"
   - 不强迫用户记忆顺序:`rawkit preview FILES -o DIR` 和 `rawkit preview -o DIR FILES` 都应该工作
6. **作者随时可能追加新的强约束**(本条永远在最后)。

---

## 一句话定位

**RAW 摄影玩家的 imagemagick**:一条命令批量 inspect、提 SOOC 预览、改 metadata、操作 sidecar、按 EXIF 查询/重命名/导出。CLI 优先,Python 库其次。

---

## 为什么是它

- **现状很烂**:`exiftool` 是 Perl 写的 25 年老怪、语法折磨;`dcraw` 死了;`ImageMagick` 处理 RAW 颜色拉胯;LrC catalog 是 SQLite 黑盒,坏了哭一周。"现代的、Python 的、给玩家做批处理"的干净 CLI **不存在**。
- **作者就是用户**:只拍 RAW,每次出门回来都几百张要处理——自带 forcing function,不靠"想象的用户"。
- **绕开 LrC 红线**:不是替代品、不是插件,是 LrC 之外的纯脚本场。导入前批量改 metadata、按 EXIF 筛选、提 SOOC 预览给别的脚本用——都是 LrC 做不了或做得很痛的事。
- **几何级生命力**:RAW 格式只增不减(每个新机型一种 .ARW / .CR3 / .NEF / .RAF / .DNG…)。exiftool 一个人养 25 年证明这是地质级范畴。
- **单人可养**:核心是文件 IO + libraw 调用 + EXIF 解析,零算法,Python + rawpy + exiftool 起步。

---

## 五年弧线

### Year 1 — 命令行瑞士军刀
```bash
rawkit ls *.ARW --where 'iso>3200 and lens~"50"'
rawkit preview *.CR3 -o ./previews/ --long 2000
rawkit render *.ARW --where 'iso<200' --max-side 4000 -o keepers/
rawkit diff a.xmp b.xmp                                        # sidecar 之间比较

# 需要 rename?用 Unix 组合即可(硬约束 #4):
rawkit ls --json *.NEF | jq -r '"mv \(.path) \(.date)_\(.model)_\(.seq).NEF"' | sh
```
> 注:原本设想的几个独立子命令在做的过程中调整了:
> - `rawkit exif`(独立子命令读 EXIF)被砍——`exiftool file` 已经够好,rawkit 重做无差异化;EXIF 数据通过 `ls --json` / `--where` 即可访问
> - `rawkit exif set`(往 RAW 写 metadata)被硬约束 #2 禁掉
> - `rawkit rename` 被硬约束 #4 砍掉(用 ls --json | jq | xargs mv)
>
> **替代**:exiftool(metadata 命令行)+ libraw via rawpy(渲染)+ 一半 LrC 导入前预处理。
> **用户**:写 Python 的摄影玩家。

### Year 2 — Sidecar 时代(进 LrC 用户疼点)
```bash
rawkit preset extract photo.xmp > sunset.json
rawkit preset apply sunset.json *.ARW
rawkit catalog git-init ~/Pictures/2026
rawkit catalog log photo.ARW
rawkit catalog rollback photo.ARW HEAD~3
```
**杀手锏**:LrC catalog 是 SQLite 黑盒,Adobe 二十年不修(利益冲突)。让摄影玩家**第一次让 LrC 编辑历史变成 git 友好的文件**——可备份、可分享 preset、可多机同步、可团队协作。

### Year 3 — 浏览层
```bash
rawkit serve ~/Pictures
rawkit find --where 'place="北京" and year=2024 and rating>=3'
```
让"想脱离 LrC 的用户"有路径——不强迫,但门开了。

### Year 4 — AI 层(错位竞争 Adobe Sensei)
```bash
rawkit embed *.ARW
rawkit dedupe ~/Pictures
rawkit cluster --trip
rawkit caption *.ARW
```
Adobe Sensei 锁云端 + 订阅;rawkit 本地 + 免费 + 用户自己的 API key。

### Year 5 — 事实标准
```python
import rawkit
rawkit.preview("photo.ARW")
```
被别的开源项目当 imagemagick 调:静态站发布工具、家庭相册项目、Astro/Hugo 主题。`brew install rawkit` 进入摄影玩家工具链推荐列表。

### 同心圆
```
作者自己 → 写 Python 的摄影玩家 → 摄影玩家(用 web UI) → 任何用 RAW 的工具
   ↑ Y1         ↑ Y1-2              ↑ Y3                  ↑ Y5
```
每一圈不依赖下一圈成立。**Year 1 服务作者自己一个人就够它活下来**。

---

## 设计原则

1. **不替代 LrC,做 LrC 旁边那一格**。任何时候撞到"替代 LrC"的诱惑,撤回。
2. **本地优先 / 零联网**(AI 层除外,且 AI 用用户自己的 key)。
3. **CLI 是核心,库是副产品**。先有命令行,再抽 Python API。
4. **审美**:反 exiftool 的折磨语法,反 LangChain 的臃肿抽象,反 Adobe 的订阅锁。
5. **不爬数据**:用户的 RAW 在用户机器上,工具不上传任何东西。

---

## v0.1 MVP 范围

### v0.0.1 — 已完成(当前基线)

已落地:

- `uv` 工具链 + `console_scripts=rawkit` + `src/` 布局 + pytest 链路
- `rawkit ls`:
   - 多路径输入(文件/目录混合)
   - 默认非递归,`-R` 递归
   - exiftool 单次批量读取
   - `--where`(lark 语法解析)
   - `--sort`(支持多键) / `--reverse`
   - `--json`(JSONL)
   - 缺失值排序始终末尾(NULLS LAST)
- 错误语义:用法错误返回 2,路径/依赖等运行错误返回 1

一句话:现在的重点不再是“把架子搭起来”,而是围绕 `ls` 做真实 dogfood,再决定 v0.1.x 的下一刀。

### 当前状态(v0.0.x 内测中)

- ✅ `rawkit ls`(EXIF 列表 + `--where` lark DSL + `--sort` 多键 + `--json`)
- ✅ `rawkit preview`(抽内嵌 SOOC JPEG + `--long/--short/--mp` 三互斥 resize + EXIF Orientation 烤进像素 + `--where` 复用 ls DSL)
- ✅ `rawkit render`(libraw demosaic + JPEG/TIFF/PNG + `--max-side` resize + `--where`)
- 🔄 现在重心:**dogfood**——拿真实拍摄的 RAW 持续用,撞到痛就回来加;路线图见下方"未来命令候选"

下一刀**不预设**,等 dogfood 真痛驱动。“做哪个”候选清单见下;“什么时候做”看真实拍摄回来撞到的具体痛点。

### 验收剧本(MVP 跑通的判据)

带最近一卷 ≥100 张 RAW,全程不开 LrC,跑通:

```bash
# 1) 按 EXIF 筛选 + 提 SOOC 预览(证明管道组合可用)
rawkit ls ~/Pictures/2026-06-17/ --where 'iso<800 and lens~"50"' --json \
  | jq -r '.path' \
  | rawkit preview - -o cull/

# 2) 按机型 + 日期重命名(组合 mv,硬约束 #4)
rawkit ls ~/Pictures/2026-06-17/ --json \
  | jq -r '"mv \(.path) \(.date)_\(.model)_\(.seq | tonumber | tostring | ("0000" + .)[-4:]).\(.path | split(".") | last)"' \
  | sh -n   # 先用 sh -n dry-run 检查,OK 后改成 | sh
```

> ⚠️ **这条管道本身严重违反硬约束 #5(心智负担)**。故意保留,是为了让 dogfood 时亲身感受那股"我不想再写这种 jq"的厌恶——那个瞬间就是 `rawkit rename` 该不该复活的**实证信号**。**如果第二次拍完仍然嫆它丑,就是 rename 该回来的信号**;在那之前不凭猜想加。

跑通 = MVP 成立;卡壳处 = v0.2 的第一批 issue。

### 命令签名(当前可用,详见 [USAGE.md](USAGE.md))

#### `rawkit ls`
```
rawkit ls [PATHS...] [-w EXPR] [-s KEY[,KEY2,...]] [-r] [-R] [--json]
```
默认列:`file datetime model lens focal aperture shutter bias iso`。`--json` 输出 JSONL 供 `jq` 等管道。

#### `rawkit preview`
```
rawkit preview [PATHS...] [-o DIR] [-R] [-f] [-w EXPR]
                          [--long N | --short N | --mp N] [-q N]
```
抽内嵌 SOOC JPEG。三互斥 resize 维度、不上采样、EXIF Orientation 烤进像素。不给 resize flag = 原字节直出(毫秒级)。

#### `rawkit render`
```
rawkit render [PATHS...] [-o DIR] [-R] [-f] [-w EXPR]
                         [--format jpeg|tiff|png] [-q N] [--max-side N]
```
libraw demosaic + Pillow 编码。默认 sRGB + 8-bit + JPEG q=90 + 4:4:4 chroma。**色彩科学会偏 SOOC**(libraw 默认管线、没 Picture Style)。

render 是 rawkit 的"libraw 渲染 + 后处理"轴线。当前只暴露最基础的解码 + 缩放;调色参数(曝光/亮度/伽马)、视觉 LLM 调色、Python API 等沿这条轴线渐进式生长。

### `--where` 表达式语法现状

**这是公开 API,一旦发布只增不删。**被 `ls` / `preview` / `render` 三个命令共享。

字段:
- 数值:`iso`、`fnumber`、`shutter`(秒,浮点)、`focal`(mm)、`bias`(EV)、`rating`(0-5)
- 字符串:`lens`、`model`、`maker`、`orientation`("portrait"/"landscape")
- 时间:`datetime`、`date`(YYYY-MM-DD)、`time`(HH:MM[:SS[.NNN]])
- 布尔:`gps`(是否含坐标)、`flash`(是否闪了闪光灯)

操作:
- 比较:`>` `<` `>=` `<=` `==` `!=`
- 字符串子串:`~"50mm"`(大小写不敏感)
- 布尔:`and` `or` `not`、括号 `()`

示例:
- `iso>3200 and lens~"50"`
- `(focal>=70 and focal<=200) or lens~"70-200"`
- `date>="2026-06-01" and not model~"iPhone"`
- `orientation=="portrait" and rating>=4`

**实现**:用 `lark`(约 30 行 BNF 文法 + 自带位置报错)。**禁止 `eval()`**。
不手写递归下降——纸面 50 行,真做含错误位置/单测会膨胀到 300+ 行,是新手陷阱。

### 全局约定

- 通用开关:`-v/--verbose`、`-q/--quiet`、`--json`、`-n/--dry-run`、`-f/--force`、`-o/--output`、`-h/--help`、`--version`
- 退出码:`0` 成功 / `1` 部分失败(详情进 stderr)/ `2` 用法错
- **stdout 只走数据,日志/进度走 stderr**(管道契约,神圣不可侵犯)
- `isatty()` 判断 TTY:非 TTY 自动关颜色/进度条
- `-` 代表 stdin/stdout;`--` 之后全部当文件名
- 路径全用 `pathlib.Path`,不写死分隔符

### 施工顺序(历史记录)

全部完成:
1. ✅ 仓库骨架 + uv + typer + lark + rawpy + Pillow
2. ✅ EXIF 后端 + JSONL/表格双轨输出
3. ✅ `rawkit ls`(带 `--where` + `--sort` 多键 + `--json`)
4. ✅ `--where` lark DSL + 100% 语法单测
5. ✅ `rawkit preview`(包含 resize / Orientation 烤像素 / `--where`)
6. ✅ `rawkit render`(libraw + Pillow + format/max-side/`--where`)
7. **现在**:dogfood + 路线图驱动 v0.1.x

---

## 设计决策(已拍板)

| 决策 | 选择 | 理由 |
|---|---|---|
| 首发语言 | **Python** | rawpy 是 libraw 的 Py 封装,1 周拿到原型;作者最熟 |
| RAW 解码 | **永远依赖 libraw**(经 `rawpy`) | dcraw 衍生的事实标准,自己重写 = 给自己挖坟 |
| metadata 后端 | **包 `exiftool`**,但**一次调用传所有路径** | maker note 覆盖最全;批量调用是性能命脉 |
| CLI 框架 | **typer** | click 升级版,类型提示天然映射;自带 shell 补全 |
| 终端输出 | **rich**(typer 自动带) | 表格/进度条事实标准,审美过关 |
| 包结构 | **src/ layout** | `src/rawkit/`;避免 import 路径混乱 |
| 入口命令名 | **`rawkit`**(不是 `raw`) | `raw` 太通用、PATH 冲突高(Linux `raw(8)`、Homebrew 占用过);`rawkit` 自我说明 |
| 表达式语法 | **`lark` 解析,禁用 `eval`** | 手写递归下降是新手陷阱(纸面 50 行,实际 300+) |
| Python 工具链 | **`uv` 一把抓** | 虚拟环境/依赖/运行/分发/锁文件全走 uv,见硬约束 #3 |
| 分发 | 内测期**不分发**(硬约束 #1);未来用 `uv tool install rawkit` | Homebrew Formula 留到 v1.0 |
| 平台支持 | **macOS + Linux 一等**;Windows best-effort | 作者只用 mac,Windows 上 rawpy/exiftool/路径都是坑,不假装支持 |
| 测试 fixture | **不入库**,`conftest.py` 读 `RAWKIT_TEST_SAMPLES` 环境变量 | 避开 git LFS 配额费用 + 外链失效 |
| Rust 重写 | **暂不**,留触发条件 | 触发:分发痛点 / 性能瓶颈 / 多语言绑定需求三选一 |
| macOS Core Image v9 | **v0.2+ 作为可选后端** `--engine apple` | macOS 27 引入 ANE 加速;v0.1 不动跨平台路径 |
| 配置文件 | **v0.1 不做** | 等第三次喊"这个默认值烦死了"再加 |
| Web UI | **v0.3 才做** `rawkit serve` | 别让 UI 反过来污染 CLI 心脏的简洁 |

---

## 路线图——未来命令候选

> ⚠️ 这一节是**地图,不是承诺**。每条都按"价值密度 × 当下可做"评过,但**何时做、做不做、用什么签名,等真实 dogfood 撞到痛再决定**。
>
> 排除已实现的 `ls` / `preview` / `render`。

### A · 利用现有 EXIF 做更多事(最便宜)

| 命令 | 一句话价值 |
|---|---|
| `rawkit stat` | 汇总统计:N 张/总大小/按机型/按镜头/按 ISO 直方图——LrC 给不了的命令行年度回顾视图 |
| `rawkit dupes` | 按 `datetime + 亚秒 + 尺寸` 找重复 RAW(多机位 / 备份恢复 / 误存场景) |
| `rawkit timeline` | 按时间分桶(天/小时)显示密度——找"那段旅行"的所有 RAW |
| `ls` 派生字段扩展 | `lens_mount`(RF/E/Z/X/GFX/M)、`lens_class`(广/标/长/微)、`golden_hour`(EXIF 时间 + GPS 算)、`season` |

### B · RAW 文件本身(中等成本)

| 命令 | 一句话价值 |
|---|---|
| `rawkit verify` | 跑 libraw 解头部 + 主区,检测损坏文件——大批备份后验证刚需 |
| `rawkit info FILE` | 单文件深度档案:EXIF + 内嵌预览档信息 + sensor 物理参数 + 镜头校正数据 |
| `rawkit hash` | 内容哈希(仅像素区,跳过 EXIF) → sidecar,用于跨备份验证完整性 |

### C · Sidecar / Metadata 写入(README Year 2,工程中等)

> 硬约束 #2 允许写 sidecar,不写 RAW

| 命令 | 一句话价值 |
|---|---|
| `rawkit rate FILE 0-5` | 打分到 `.xmp`,LrC 直接读 |
| `rawkit label FILE color` | 颜色标签(红/黄/绿/蓝/紫)到 .xmp |
| `rawkit keyword add/rm/ls` | 关键字管理到 .xmp,LrC 共享词表 |
| `rawkit xmp diff a.xmp b.xmp` | 比较两份 sidecar 的差异 |
| `rawkit preset extract/apply` | 从 LrC 编辑过的 .xmp 抽出 develop 设置 → JSON → 应用到一批新 RAW(**LrC 杀手锏**) |

### D · 文件组织(边缘——硬约束 #4 警戒)

| 命令 | 一句话价值 | 是否违反 #4 |
|---|---|---|
| `rawkit organize PATH --to '{date}/{model}/'` | 按 EXIF 规则 mv 到分类目录 | **不违反**(mv 不懂 EXIF,rawkit 独有能力) |
| `rawkit dedupe --move trash/` | 找重复 + 移到回收目录 | 同上 |
| ~~`rawkit rename`~~ | ~~按 EXIF 改文件名~~ | **违反**,已砍(用 `ls --json \| jq \| xargs mv`) |

### E · 空间 / 关系视图

| 命令 | 一句话价值 |
|---|---|
| `rawkit map` | 输出 GPS bbox / KML / geojson——"我这波拍在哪" |
| `rawkit cluster --by lens \| --by trip` | 自动分组(同镜头连续拍 = 一组、时间断档 = 新行程) |

### F · 跟外界交互(大工程)

| 命令 | 一句话价值 |
|---|---|
| `rawkit serve` | 本地 HTTP,浏览器看缩略图墙(README Year 3)——大工程,远期 |
| `rawkit watch DIR` | 监视目录,新 RAW 进自动 preview / 通知 |
| `rawkit completion zsh\|bash\|fish` | shell tab 补全(typer 自带,几乎零成本) |

### G · 质量 / 工具

| 命令 | 一句话价值 |
|---|---|
| `rawkit doctor` | 健康自检:exiftool / rawpy / Pillow / libraw 是否到位 + 跑样张 |
| `~/.config/rawkit.toml` | 配置文件存常用 flag profile(等"第三次喊烦"再加) |
| `rawkit ls --output csv\|tsv` | CSV/TSV 输出,给 Excel / 数据分析友好 |

### H · render 轴线扩展(已实现 render 的渐进生长)

| 阶段 | 一句话价值 | 触发条件 |
|---|---|---|
| `render --exposure / --bright / --gamma` | rawpy native 三参数,批量统一调一档 | 用户 dogfood 撞到"这一卷需要统一压一档" |
| `render --wb camera\|auto\|daylight\|R,G1,B,G2` | 白平衡覆盖 | "室内灯光统一矫正" |
| `render --auto vision:gpt-4o` | LLM 视觉自动调色 | LLM 视觉模型成熟 + 工程定型 |
| `rawkit-py` Python API | 别的 GUI / 脚本调 rawkit 出图 | 第三方有真实接入需求 |

### I · AI 层(README Year 4,远)

| 命令 | 一句话价值 |
|---|---|
| `rawkit caption` | 视觉 LLM + EXIF → 一句话图说(图书馆 / 个人归档刚需) |
| `rawkit embed` | 视觉 embedding 写 sidecar,用于相似搜索 / 找重复 |
| `rawkit search QUERY` | 语义搜索"日落海边"→ 命中相似图(基于 embed) |

### Top 5 个人最看好(按"价值密度 × 当下可做"排)

1. **`rawkit stat`** — 独有价值、半天工程、年度回顾刚需
2. **`rawkit rate` + `keyword` + `label`** — 一组打开 sidecar 写入大门,验证 .xmp 工程能不能跑通,后续 sidecar 命令的地基
3. **`rawkit completion`** — 几乎零成本(typer 自带)、UX 大提升
4. **`rawkit verify`** — 备份场景刚需、libraw 已在依赖里
5. **`rawkit organize`** — 工作流神器(按 EXIF 自动归档)、不违反硬约束

### 最大潜力但远期

**`preset extract/apply`**——从 LrC 编辑过的 .xmp 抽 develop 设置 → 复用到一批新 RAW。这是 LrC 用户群里**没人能做、做出来就有用户**的杀手锏功能,但工程量大、要研究 LrC XMP develop 设置格式。**Year 2 的明星**。

---

## 不做(踩坑预防)

- ❌ **不自己实现 RAW 解码**——永远 libraw
- ❌ **不做编辑/调色**——那是 LrC 的活,越界即死
- ❌ **v0.1 不加**:配置文件 / 索引数据库 / Web UI / 交互式 TUI / 向导
- ❌ **不用 `eval()` 实现 `--where`**——安全和稳定性都崩
- ❌ **stdout 不许混日志/进度条**——管道契约神圣
- ❌ **没真实基准前不重写 Rust**——优化没量化指标就是自嗨
- ❌ **不把预览当渲染**——`rawkit preview` 抽内嵌 JPEG(SOOC),`rawkit render` 才是真解码(会色偏)

---

## 真风险

1. **Adobe 不会死**——RAW demosaicing 核心仍在 ACR 手里。rawkit 永远是"LrC 旁边"的工具,不是"取代 LrC"。心理上必须接受。
2. **摄影玩家小众,Python+摄影玩家更小众**——Year 1-2 种子用户最多几百到一两千,ripgrep 第一年也这量级。期望对齐。
3. **维护是长跑**——每个新机型出来要加测试样本。但 exiftool 25 年前例说明能撑。
4. **dogfood 是唯一存活信号**:如果某一周拍了 RAW 但没用 rawkit,要么修工具,要么承认它没价值。**项目不会死于代码烂,会死于作者不再拍照**。

---

## 长期护城河方向(Year 3+ 才动)

包装层 = 快速可复制,价值有限。要到 ffmpeg 级,得在下列至少一个方向深耕:

1. **Rosetta Stone:跨软件编辑参数语义对照表**(最高护城河,3-5 年工程)。把 LrC / Capture One / DxO / darktable 的 sidecar/catalog 编辑语义互相映射——一旦做出门槛极高。
2. **编辑历史的语义 versioning(git for RAW edits)**。sidecar 解析为 `EditOp`,支持语义 `diff` / 三方 merge / 人话冲突描述。
3. **Multi-engine 渲染编排**(`--engine apple` / `--engine libraw`),长期可探训练开源去噪/demosaic 模型缩小与闭源差距。
4. **EXIF / MakerNote 原生索引数据库**,比 exiftool 更快批量索引;长期维护 maker note 解析就是护城河。

设计建议:Year 1 做包装与 UX,**从 Day 1 把 Rosetta Stone 当北极星**,数据模型和 API 给这条路留位置。

---

## 商业模式(Year 3+ 才考虑)

- 核心永久开源、MIT
- 可选托管 web UI($5/月,用户传到自己 S3,作者只提供界面)
- preset 市场分成
- 摄影机构/工作室批处理 license($几百/年)

Year 1 别想这个。

---

## 给未来某天想放弃的你

先问一个问题:

> **这一周拍的 RAW,我用 rawkit 处理了吗?**

- 答 yes:继续,加你最痛的那个功能。
- 答 no:是工具不够好,还是这周没拍照?
  - 工具不够好 → 那正是该修的 bug,去修
  - 没拍照 → 跟工具无关,别误判

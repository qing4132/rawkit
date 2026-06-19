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
rawkit preview *.CR3 -o ./previews/
rawkit exif get *.RAF --fields iso,lens,copyright              # 只读
rawkit diff a.xmp b.xmp                                        # sidecar 之间比较

# 需要 rename?用 Unix 组合即可(硬约束 #4):
rawkit ls --json *.NEF | jq -r '"mv \(.path) \(.date)_\(.model)_\(.seq).NEF"' | sh
```
> 注:原本设想的 `rawkit exif set`(往 RAW 写 metadata)被硬约束 #2 禁掉;`rawkit rename` 被硬约束 #4 砍掉。
**替代**:exiftool(metadata)+ dcraw(渲染)+ 一半 LrC 导入前预处理。
**用户**:写 Python 的摄影玩家。

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

### v0.1 拆分:先 ls+preview,再谈其他

- **v0.1.0** = `ls --where` + `preview` 两个命令跑通下面验收剧本第 1 条管道
- **v0.1.x** = 第一次真实 dogfood 后才动 `exif` / `export` / `diff`(让真痛点决定优先级,而不是现在干猜)

理由:从 0 到 1 的价值远大于从 4 到 5。`ls + preview` 是变成"你下次拍完真会用"的最短路径。

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

### 命令清单(v0.1 共 4 个)

#### `rawkit preview`
- 签名:`rawkit preview FILES... [-o previews/] [-f]`
- 抽**内嵌 SOOC JPEG 预览**(不解 RAW,快);缺预览时 stderr 警告并跳过
- 默认输出 `./previews/`,文件已存在则跳过;`-f` 强制覆盖
- 不做 resize(Unix 哲学,交给 `sips` / `magick mogrify` / `vipsthumbnail`)
- 接受:目录(递归)/ 文件列表 / `-`(从 stdin 读路径)
- 抽出尺寸取决于机型:Canon CR3/Sony A1/Nikon Z/Leica M11 给近似全分辨率;Sony A7R IV 只给 1616×1080;中画幅给 3000~4000 中档

#### `rawkit exif`
- 签名:`rawkit exif FILE [--json] [--fields iso,lens,fnumber]`
- 默认人读对齐表;`--json` 给脚本;`--fields` 只输出指定字段
- 后端:包 `exiftool` 子进程 + `-j`

#### `rawkit ls` —— **MVP 心脏**
- 签名:`rawkit ls [PATHS...] [-w EXPR] [-s KEY[,KEY2,...]] [-r] [-R] [--json]`
- 默认输出:`file  datetime  model  lens  focal  aperture  shutter  bias  iso`(对齐表)
- `--json` 输出 JSONL(每行一对象,便于 `jq`)
- 默认只看顶层(同 `ls`),`-R` 才递归;自动识别主流 RAW 后缀
- 当前未做内建“翻页/分页”,大批量查看先用 Unix 管道(`less -S` / `head` / `tail`)
- **实现注意**:`exiftool` 必须**一次调用传所有路径**(`exiftool -j f1 f2 f3...`),不要每文件 fork 一次,否则 1000 张就要分钟级

#### `rawkit render`
- 签名:`rawkit render FILES... [-o renders/] [--format jpeg|tiff|png] [--quality 90] [--max-side 2048]`
- 走 `rawpy` 全解码(libraw demosaic,**色彩科学会偏 SOOC**——与 `preview` 互补)
- v0.1 用 rawpy 默认参数;**不暴露**白平衡/曲线(那是 LrC 的活)
- 长边 resize 到 `--max-side`(可选)

### `--where` 表达式语法 v1

**这是公开 API,一旦发布只增不删。**

字段:
- 数值:`iso`、`fnumber`、`shutter`(秒,浮点)、`focal`(mm)
- 字符串:`lens`、`model`、`maker`
- 时间:`date`(YYYY-MM-DD)、`time`(HH:MM)

操作:
- 比较:`>` `<` `>=` `<=` `==` `!=`
- 字符串子串:`~"50mm"`(大小写不敏感)
- 布尔:`and` `or` `not`、括号 `()`

示例:
- `iso>3200 and lens~"50"`
- `(focal>=70 and focal<=200) or lens~"70-200"`
- `date>="2026-06-01" and not model~"iPhone"`

**实现**:用 `lark`(约 30 行 BNF 文法 + 自带位置报错)。**禁止 `eval()`**。
不手写递归下降——纸面 50 行,真做含错误位置/单测会膨胀到 300+ 行,是新手陷阱。

### 全局约定

- 通用开关:`-v/--verbose`、`-q/--quiet`、`--json`、`-n/--dry-run`、`-f/--force`、`-o/--output`、`-h/--help`、`--version`
- 退出码:`0` 成功 / `1` 部分失败(详情进 stderr)/ `2` 用法错
- **stdout 只走数据,日志/进度走 stderr**(管道契约,神圣不可侵犯)
- `isatty()` 判断 TTY:非 TTY 自动关颜色/进度条
- `-` 代表 stdin/stdout;`--` 之后全部当文件名
- 路径全用 `pathlib.Path`,不写死分隔符

### 施工顺序(建议)

1. 仓库骨架:`uv init` → `pyproject.toml` + `src/rawkit/` + `tests/` + `console_scripts=rawkit`;`uv add rawpy typer rich lark` 加依赖
2. `src/rawkit/output.py`:table/json 双轨打印 + TTY 检测;日志统一到 stderr
3. `rawkit exif`(最简单,先打通 exiftool 链路与 `--json` 输出契约)
4. `rawkit ls`(不带 where 的最小版,默认对齐表,**批量调 exiftool**)
5. `src/rawkit/query.py`:`lark` 实现 `--where` v1 + 单测覆盖全部语法
6. `rawkit preview`(stdin `-` / `-f` / stderr 警告)
7. `rawkit render`(rawpy 全解码 + resize)
8. **Dogfood**:跑一次验收剧本,记 issue → v0.2

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

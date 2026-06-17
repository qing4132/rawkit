# rawkit · RAW 摄影的命令行瑞士军刀

> 给本地 RAW 文件做批处理 / metadata / sidecar / 浏览的现代 Python CLI。
> **不替代 LrC**,做 LrC 不做的事——脚本化的命令行盒子,LrC 旁边那一格。

---

## 一句话定位

**RAW 摄影玩家的 imagemagick**:一条命令批量 inspect、提缩略图、改 metadata、操作
sidecar、按 EXIF 查询/重命名/导出。CLI 优先,Python 库其次。

---

## 为什么是它

- **现状很烂**:`exiftool` 是 Perl 写的 25 年老怪、语法折磨;`dcraw` 死了;
  `ImageMagick` 处理 RAW 颜色拉胯;LrC catalog 是 SQLite 黑盒,坏了哭一周;
  "现代的、Python 的、给玩家做批处理"的干净 CLI **没有**。
- **作者就是用户**:我自己只拍 RAW,每次出门回来都几百张要处理 —— 自带
  forcing function,不靠"想象的用户"。
- **绕开 LrC 红线**:不是替代品、不是插件,是 LrC 之外的纯脚本场。导入前批量
  改 metadata、按 EXIF 筛选、提缩略图给别的脚本用 —— 都是 LrC 做不了或做得很
  痛的事,完全不冲突。
- **几何级生命力**:RAW 格式只增不减(每个新机型一种 .ARW / .CR3 / .NEF / .RAF
  / .DNG…),exiftool 一个人养 25 年证明这范畴是地质级的。
- **单人可养**:核心是文件 IO + libraw 调用 + EXIF 解析,零算法,Python +
  rawpy + PIL 就能起步。

---

## 五年弧线

### Year 1 — 命令行瑞士军刀

```bash
raw ls *.ARW --where 'iso>3200 and lens~"50"'
raw thumb *.CR3 --size 1024 --to ./preview/
raw rename *.NEF --pattern '{date}_{camera}_{lens}_{seq}'
raw exif set *.RAF --copyright '李青林' --keyword 北京,胡同
raw diff a.xmp b.xmp     # 看清 LrC 调了什么
```

替代:exiftool(metadata)+ dcraw(渲染)+ 一半 LrC 导入前预处理。
用户:写 Python 的摄影玩家。

### Year 2 — Sidecar 时代(进入 LrC 用户疼点)

```bash
raw preset extract photo.xmp > sunset-preset.json
raw preset apply sunset-preset.json *.ARW
raw catalog git-init ~/Pictures/2026
raw catalog log photo.ARW
raw catalog rollback photo.ARW HEAD~3
```

**这是杀手锏**:LrC catalog 是 SQLite 黑盒,Adobe 二十年不修(利益冲突)。
让摄影玩家**第一次让 LrC 的编辑历史变成 git 友好的文件**——可备份、可分享
preset、可多机同步、可团队协作。

### Year 3 — 浏览层

```bash
raw serve ~/Pictures   # 本地 web,浏览器看 RAW 缩略图 + 全 metadata 搜索
raw find --where 'place="北京" and year=2024 and rating>=3'
```

让"想脱离 LrC 的用户"有路径——不强迫,但门开了。

### Year 4 — AI 层(错位竞争 Adobe Sensei)

```bash
raw embed *.ARW        # 给每张图算嵌入
raw dedupe ~/Pictures  # 找重复/相似/废片
raw cluster --trip     # 自动按"一次出行"聚类
raw caption *.ARW      # 自动生成 alt text(接 LLM)
```

Adobe Sensei 锁云端 + 订阅;rawkit 本地 + 免费 + 用户自己的 API key。
作者 `ai_describe_city` 的 LLM 批处理肌肉在此第二次复用。

### Year 5 — 事实标准

```python
import rawkit
rawkit.thumb("photo.ARW", size=2048)
```

被别的开源项目当 imagemagick 调:静态站发布工具、家庭相册项目、Astro/Hugo
主题。`brew install rawkit` 进入摄影玩家工具链推荐列表。

---

## 用户的同心圆

```
作者自己 → 写 Python 的摄影玩家 → 摄影玩家(用 web UI) → 任何用 RAW 的工具
   ↑ Y1         ↑ Y1-2              ↑ Y3                  ↑ Y5
```

每一圈不依赖下一圈成立。**Year 1 服务作者自己一个人就够它活下来**——这是它
比"凭空想的 LLM 管线工具"强的关键:那种没作者 dogfood 就死,这个**今晚就在用**。

---

## 设计原则

1. **不替代 LrC,做 LrC 旁边那一格**。任何时候撞到"替代 LrC"的诱惑,撤回。
2. **本地优先 / 零联网**(AI 层除外,且 AI 用用户自己的 key)。
3. **CLI 是核心,库是副产品**。先有命令行,再抽 Python API。
4. **审美**:反 exiftool 的折磨语法,反 LangChain 的臃肿抽象,反 Adobe 的订阅锁。
5. **不爬数据**:用户的 RAW 在用户机器上,工具不上传任何东西。

---

## 商业模式(Year 3+ 才考虑,Year 1 别想)

- 核心永久开源、MIT。
- 可选托管 web UI($5/月,用户传到自己 S3,作者只提供界面)。
- preset 市场分成。
- 摄影机构/工作室批处理 license($几百/年)。

---

## 真风险

1. **Adobe 不会死**——RAW demosaicing 核心仍在 ACR 手里。rawkit 永远是"LrC 旁边"
   的工具,不是"取代 LrC"。心理上必须接受这个定位,否则会一直觉得不够。
2. **摄影玩家小众,Python+摄影玩家更小众**——Year 1-2 种子用户最多几百到一两千。
   ripgrep 第一年也就这量级。期望对齐。
3. **维护是长跑**——每个新机型出来要加测试样本。但有 exiftool 25 年前例,
   说明能撑,且说明这真是有人做了一辈子的活。

---

## 第一刀(立即可干)

1. 把 `../dotbox/extract_thumb_from_raw.py` 当种子,移进来做 `raw thumb` 的雏形。
2. 加 `raw exif ls`(只读 metadata,先用 exiftool 当后端,后面再换 piexif/rawpy)。
3. 下一次拍完 RAW 真的用一次——这是它能不能活的唯一判定。

---

## 与 ideas/ 里其它点子的关系

- 复用 `ai_describe_city` 的 LLM 批处理工程经验(Y4 caption/cluster 用)。
- 复用 `camera-gear` 的镜头/机型 schema(Y2-3 metadata 标准化用)。
- `dotbox/extract_thumb_from_raw.py` 是它的史前史。

---

## 技术补充（来自讨论记录）

### Apple macOS 27 / Core Image v9

- WWDC26 推出 macOS 27(Core Image RAW v9)，在 Apple Silicon 上利用 ANE 加速，提升锐度和色彩表现。
- 对 rawkit 的影响：把 Core Image v9 当作**可选渲染后端**能显著提升 Mac 上的渲染质量；但 Apple 平台仅做质量替代，不替代我们的工作流价值（metadata/sidecar/git/编排）。

建议：在 Year 2 路线中增加 `--engine apple` / `--engine libraw` 的分支实现；保持跨平台兜底为 libraw。

### libraw 与 rawpy 的角色

- `libraw` 是开源的 C++ RAW 解码事实标准（dcraw 衍生），支持绝大多数相机 RAW 格式。
- `rawpy` 是 `libraw` 的 Python 包装器，适合作为 Year 1 的解码后端。
- 结论：不要重写 RAW 解码，直接依赖 `libraw`（通过 `rawpy`）——把精力放在工作流和数据层。

### 语言选择与迁移策略

- **Year 1 推荐：Python**（快速验证、用 `rawpy`、快速 dogfood）。
- **Year 2+ 迁移候选：Rust/C++ 核心**（当出现真实的分发/性能/绑定需求时再迁移）。
- 可选策略：双轨（Python 主线以快速迭代为主，同时在副轨用 Rust 开发一个小的高性能二进制如 `rawkit-thumb` 作为练手与后向兼容的起点）。

理由总结：Python 让你最快落地、最小化放弃风险；Rust 是未来可选优化路径，但应基于真实用户和基准数据再做迁移。

### 性能瓶颈（何处用更高性能语言）

- RAW 解码本身在 `libraw`（C++）层，Python 外壳对该部分影响微小。
- 真正性能痛点可能出现于：
  - 大规模 metadata 批读/索引（exiftool 启动开销/Perl 进程重复），适合用更接近系统层的实现替代；
  - 高并发/低延迟的本地索引与查询（把 metadata 索引入 SQLite/本地 DB）；
  - 高级渲染或训练的 demosaic/denoise 模型（GPU/ANE 调度，需本地或跨平台 ML 实现）。

结论：Year 1-2 用 Python 足够；Year 3 若要做原生索引/自训练去噪/跨语言绑定时考虑 Rust/C++。

### 真正的深度（护城河）——如何从“包装层”走向“基建”

包装层 = 快速可复制，价值有限。要到达 ffmpeg 级别，rawkit 必须在下列任一或多个方向长期深耕：

1. **Rosetta Stone：跨软件编辑参数语义对照表**（最高护城河）
  - 逆向/映射 Adobe LrC / Capture One / DxO / darktable 等 sidecar/catalog 的编辑语义；
  - 使编辑历史可移植；这是一个 3–5 年的工程，完成度一旦触及门槛便难以被复制。

2. **编辑历史的语义 versioning（git for RAW edits）**
  - 把 sidecar 解析为 EditOp，支持语义 `diff` / 三方 merge / 人话冲突描述。

3. **Multi-engine 渲染编排与开源去噪/demosaic 模型**
  - 支持 `--engine apple` / `--engine libraw`，并在长期目标中探讨训练开源去噪/去马赛克模型以缩小与闭源引擎的质量差距。

4. **EXIF / MakerNote 原生索引数据库**
  - 提供比 `exiftool` 更快速的批量索引与查询能力；长期维护 maker note 解析可形成护城河。

路线建议：Year 1 做包装与 UX；从 Day 1 就把 Rosetta Stone 当作北极星和长期写作方向，把设计、数据模型、API 为这条路留位。

---

## Year‑1 具体施工路线（逐步、可执行）

目标：在 4 周内拿到可被自己 dogfood 的最小可运行闭环 `raw thumb` + `raw exif ls` + `raw ls --where`。

周 0 (准备)
- 在仓库中建立 `rawkit/` 包结构（`pyproject.toml` + `src/rawkit` + `tests` + CLI 入口）。
- 把现有 `dotbox/extract_thumb_from_raw.py` 作为启始样例复制到 `rawkit/cli/thumb.py`。

周 1 (基础 CLI 与解码)
- 用 `typer`/`click` 实现 `raw thumb <files> --size` 命令，内部调用 `rawpy`（`libraw`）。
- 实现 `raw exif ls <file>`：调用 `exiftool` 作为后端（subprocess），包装成结构化 JSON 输出。
- 单元测试：样本 RAW 文件的缩略图和 metadata 能正确生成/读出。

周 2 (批处理、where 查询、导出)
- 加 `raw ls --where '<expr>'`（最初实现为简单过滤：EXIF 字段存在或值匹配），输出可机读表格（CSV/JSONL）。
- 实现 `raw rename --pattern` 与 `raw export --to <dir>`。

周 3 (本地导览)
- 加 `raw serve <dir>`：单文件本地静态浏览器视图，展示缩略图、关键 metadata、点击查看原图（用系统默认 viewer）。

周 4 (收敛、文档、发布)
- 编写 README、示例工作流、制作 `pip` 可安装的 package、在 Homebrew 中给出打包思路（Formula 草案）。
- Dogfood：把你最近一卷 RAW 倒进工具链，真实使用并修 bug。

验收标准（Week 4）
- `pip install -e .` 后能在本机运行 `raw thumb` 与 `raw exif ls` 并对 100 张 RAW 完成批处理（总耗时可接受）。
- README 给出 3 个典型使用场景并能被你本人复现。

---

如你同意，我将把上述变更提交到仓库并 push。你要不要我把 `dotbox/extract_thumb_from_raw.py` 立即移动为 `rawkit/cli/thumb.py` 并把最小 `pyproject.toml` + `src/rawkit/__init__.py` 放好？
（已实现：`pyproject.toml`、`src/rawkit` 包骨架、`cli/thumb.py`、`cli/exif.py`、`cli/ls.py` 已添加到仓库）

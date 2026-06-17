# rawkit — HANDOFF

> 给"接手这个项目的下一个人"（很可能就是几周后的你自己）的最短上下文交接。
> 阅读顺序：本文件 → [README.md](README.md) → [TODO.md](TODO.md) → [EVALUATION.md](EVALUATION.md)。

---

## 1. 一句话定位

**rawkit = 摄影玩家的命令行瑞士军刀**：在不打开 Lightroom 的前提下，对一卷 RAW 完成预览 / 筛选 / 重命名 / 导出 / 看 metadata。

目标是做 5 年、做到 ffmpeg 在视频界的地位——成为 RAW 工作流的事实标准底层工具。

---

## 2. 为什么是这个项目（动机锚点，别忘）

在十几个候选里活下来的唯一原因：

- **作者本人是重度 dogfood 用户**（拍 RAW、用 LrC，每周都有真实工作流痛点）。
- **local-first + 工具非内容**：完全符合作者的产品取向（拒绝服务端、拒绝信息聚合站）。
- **有"长期可养"的护城河方向**：Rosetta Stone（跨编辑软件的参数语义对照）是 3–5 年工程，一旦做出门槛极高。
- **能从一个 30 行 Python 脚本（`extract_thumb_from_raw.py`）增量长出来**，没有"必须先重写宇宙才能交付价值"的冷启动陷阱。

---

## 3. 决策记录（已拍板，别再讨论）

| 决策 | 选择 | 理由 |
|---|---|---|
| 首发语言 | **Python** | 用 `rawpy`（libraw 的 Py 封装）能 1 周拿到可用原型；作者本人最快上手 |
| RAW 解码 | **永远依赖 libraw** | dcraw 衍生的事实标准；自己重写 = 给自己挖坟 |
| metadata 后端 | **Year1 包 exiftool** | 子进程调用 + `-j` JSON 输出；Year2+ 性能不够再换原生 |
| CLI 框架 | **typer** | `click` 升级版，类型提示天然映射；自带 shell 补全 |
| 包结构 | **src/ layout** | `src/rawkit/` + `rawkit` 顶层文档；避免 import 路径混乱 |
| 表达式语法 | **自定义 mini-DSL，禁用 `eval`** | 见 TODO.md 的 `--where` v1 规范；一发布即冻结只增不删 |
| 分发 | **`pipx install rawkit`** | Python CLI 的现代姿势；Homebrew Formula 留到 v1.0 |
| 命令名 | `raw`（CLI 入口） / `rawkit`（包名） | `raw` 短，像 `git`；如有 Homebrew 冲突再议 |
| Rust 重写 | **暂不**，记入触发条件 | 触发条件：分发痛点 / 性能瓶颈 / 多语言绑定需求三选一 |
| macOS Core Image v9 | **v0.2+ 作为可选后端** `--engine apple` | macOS 27 引入 ANE 加速；不动 v0.1 跨平台路径 |
| 配置文件 | **v0.1 不做** | 等作者第三次喊"这个默认值烦死了"再加 |
| Web UI | **v0.3 才做** `raw serve` | 别让 UI 反过来污染 CLI 心脏的简洁 |

---

## 4. 仓库现状（你接手时看到的）

```
rawkit/                          # ← 项目根（独立 git 仓库）
├── README.md                    # 五年弧线 + 设计原则 + 技术补充
├── TODO.md                      # ★ v0.1 MVP 详细规格(5 命令+where 语法)
├── EVALUATION.md                # VC + OSS 维护者双视角评估
├── HANDOFF.md                   # 本文件
├── pyproject.toml               # 包元信息，console_scripts=rawkit
├── extract_thumb_from_raw.py    # ★ 史前史脚本：MVP `raw thumb` 的种子
└── src/rawkit/
    ├── __init__.py
    └── cli/
        ├── __init__.py          # Typer app + 子命令注册
        ├── thumb.py             # 已从种子脚本移植(待重构)
        ├── exif.py              # exiftool 包装(已能跑)
        └── ls.py                # 列目录(无 where,待加)
```

**当前已能运行的最小集**：`pip install -e .` 后可调用 `rawkit thumb / exif / ls`，但**还未 dogfood 过真实一卷**。

---

## 5. 立即可干的下一步（按顺序）

1. **基础设施层**：新建 `src/rawkit/output.py`（双轨打印 table/json + TTY 检测）+ 统一日志到 stderr。
2. **`raw exif` 加 `--json` 和 `--fields`**（最简单，先打通输出契约）。
3. **`raw ls` 不带 where 的最小版**（递归扫 RAW + 默认对齐表）。
4. **`--where` 表达式解析器**：独立 `src/rawkit/query.py`，50 行手写递归下降 + 完整单测。
5. **`raw thumb` 重构**：接受 `-`（stdin 读路径）、`-f` 强制覆盖、stderr 警告。
6. **`raw rename`**（默认 dry-run；sidecar 同步是亮点）。
7. **`raw export`**（rawpy 全解码 + resize）。
8. **Dogfood 验收**：跑 [TODO.md](TODO.md) 的"验收剧本"，把卡壳点变成 v0.2 issue。

详细任务清单与契约见 [TODO.md](TODO.md)。

---

## 6. 不要做的事（踩坑预防）

- ❌ **不要自己实现 RAW 解码**——永远用 libraw。
- ❌ **不要做编辑/调色**——那是 LrC 的活，越界即死。
- ❌ **不要在 v0.1 加配置文件、索引数据库、Web UI**——MVP 的本职都没干完。
- ❌ **不要用 `eval()` 实现 `--where`**——安全和稳定性都崩。
- ❌ **不要让 stdout 混入日志/进度条**——管道契约神圣不可侵犯。
- ❌ **不要在没有真实用户基准前重写为 Rust**——优化没量化指标就是自嗨。
- ❌ **不要做交互式 TUI / 向导**——CLI 默认非交互。
- ❌ **不要把缩略图当渲染**——`raw thumb` 抽内嵌 JPEG，`raw export` 才是真解码。

---

## 7. 来源与起点

- 项目脱胎自 `ideas/` 工作区（作者的点子库），2026-06-17 拆出独立仓库。
- 史前史脚本：`extract_thumb_from_raw.py`（保留在仓库根作为纪念 + 测试用 fixture）。
- 相关姊妹项目（不直接耦合，但 schema 可复用）：
  - `ideas/camera-gear/`：镜头/机型 schema（Y2-3 metadata 标准化可复用）。
  - `ideas/ai_describe_city/`：作者证明过自己能跑通"schema → 生成 → 渲染"的全管线。

---

## 8. 给未来某天想放弃的你

如果某天你想砍掉这个项目，先问自己一个问题：

> **这一周拍的 RAW，我用 rawkit 处理了吗？**

- 答 yes：继续，加你最痛的那个功能。
- 答 no：先回答"为什么没用"——是工具不够好，还是你这周根本没拍照？
  - 工具不够好 → 那正是该修的 bug，去修。
  - 没拍照 → 跟工具无关，别误判。

**dogfood 是这个项目存活的唯一 forcing function。** 项目本身不会死于代码烂，会死于作者不再拍照。

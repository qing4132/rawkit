# rawkit — VC 与开源开发者双视角评估

本文档从两重外部视角审视 rawkit 的可行性、价值、护城河与 Year‑1 建议执行路线（更细化、可交付）。

---

## 一、VC 视角（投资人关心什么）

核心问题：这个项目能否在未来 2–5 年内展现出可规模化的商业化路径或成为重要基础设施，从而带来增长或被企业收购？

1) 市场与需求：
  - 用户群体为摄影玩家与专业摄影工作室；这是一个氛围稳定但相对小众的付费群体。
  - 与之相比，市场更大的相邻机会是“图像后期工作流”的企业级客户（工作室自动化、流水线转码）。

2) 商业化路径：
  - 直付费（托管版本地浏览/协作 UI，$5–$20/月）——可行，但增长慢。
  - 企业授权（批处理/工作室版）——若能稳定三四家工作室许可，收入可观。
  - 市场化（preset 商店 / 插件分成）——可作为补充。

3) 投资人关注的指标（早期）:
  - 种子用户数（实用、愿意付费的摄影玩家）
  - 每月主动使用者（MAU）与留存（工具型产品更重留存）
  - 第一个付费客户和平均付费金额（ARR）
  - 项目是否被其他开源项目/工具引用（例如被静态站/相册项目程序集成）

4) 风险（VC 角度）:
  - 小众且增长慢；若没有明显的渠道，规模化受限。
  - 若只停在包装层，容易被复制，无法形成估值逻辑。

结论（VC）：
  - 若你把 rawkit 视为长期基建并专注 Rosetta Stone / 编辑语义版本化 的长期目标，VC 会把它看成“小而稳定”的基础设施候选；但要拿风险投资的尺度期待商业化，需要明确中长期的付费产品（企业授权或托管 UI）。

---

## 二、开源独立开发者视角（采用者/贡献者/维护者关心什么）

1) 采用门槛：
  - 语言与分发：Python 能快速吸引熟悉 Python 的用户；但优秀的二进制分发（Homebrew / Debian package / static binary）更受欢迎。
  - 依赖：依赖 `libraw`/`rawpy` 与（可选）`exiftool`，都在摄影圈有共同认知。

2) 贡献路径与维护成本：
  - 早期核心贡献者将来自摄影玩家 + 熟悉 Python 的开发者。
  - 长期维护成本在于：支持新机型 maker notes，维护侧车解析与跨软件映射表。

3) 社区采纳策略：
  - 先把 CLI 做成「有用且易上手」的工具（可复制的工作流示例），再通过博客/YouTube/摄影圈口碑扩散。
  - 提供良好模板的 `pyproject.toml` + GitHub Actions 测试矩阵（macOS/Linux/Windows）以降低贡献门槛。

结论（开源）：
  - 这项目对开源社区友好；短期内能吸引小量高质量贡献者；长期的 contributor churn 取决于你是否承担 maker-note 的逆向工作。

---

## 三、product‑market fit 与首要验收指标（结合双方视角）

- PMF 验证事务（Year‑1）：
  1. 你本人用 1 卷照片完成端到端工作流（import → thumb → find → rename）并记录体验。
  2. 邀请 5 名摄影玩家（写 Python 的）试用并收集留存/反馈。
  3. 完成首个付费意向探索（对托管版或工作室版的价格敏感度访谈）。

- 验收指标（Week 4）:
  - 至少 3 个摄影玩家愿意持续使用 2 周。
  - `raw thumb` 对 100 张 RAW 的批处理在单机上可接受时间内完成。

---

## 四、Year‑1 细化施工路线（工程级任务、交付物、验收标准）

目标：把 rawkit 从概念变为可被真实摄影活动 dogfood 的工具。下面是具体到文件与任务的路线。

0. 仓库准备
  - 交付物：`pyproject.toml`，`src/rawkit/__init__.py`，`src/rawkit/cli/__init__.py`。
  - 验收：`pip install -e .` 后能调用 `rawkit` CLI。

1. `thumb` 命令
  - 文件：`src/rawkit/cli/thumb.py`
  - 功能：对传入 RAW 文件生成指定尺寸缩略图输出到目录
  - 实现：基于 `rawpy` + `Pillow`，保留原 EXIF 到缩略图的 XMP/PNG tEXt
  - 测试：对 3 个不同机型的 RAW 生成缩略图（示例文件放在 `tests/samples/`）

2. `exif ls` 命令
  - 文件：`src/rawkit/cli/exif.py`
  - 功能：结构化打印 EXIF/XMP（优先走 `exiftool`，输出 JSON）
  - 测试：运行 `rawkit exif ls sample.ARW` 输出有效 JSON

3. `ls --where` 简易查询
  - 文件：`src/rawkit/cli/ls.py`
  - 功能：读取目录、解析 metadata、按简单条件过滤（`iso>3200 and lens~"50"`）
  - 实现细节：初版用 Python 过滤，后续用 SQLite 索引

4. 本地 serve（可选快速 UI）
  - 文件：`src/rawkit/serve.py` 或 `tools/serve.py`（静态小 UI）
  - 功能：列缩略图、关键 metadata、导出 JSONL

5. CI / packaging
  - GitHub Actions: 测试 matrix (py37/38/39/310), lint, 安装 `rawpy` 与 Pillow
  - 发布：`pip` 开发版 + Homebrew Formula 草案

时间线 & 验收
- 0–2 周：完成 0/1/2，能在本机完成第一个真实工作流（不可用到尽善尽美，但能工作）
- 2–4 周：完成 3/4，写文档、示例、演示视频或 GIF

---

## 五、建议（简短）

- 短期（Year‑1）：把目光放在 dogfood 与可复现工作流上；不要从一开始就重写底层。
- 中期（Year‑2）：在收集到真实用户/基准数据后，判断是否需要 Rust 核心，并开始把瓶颈处（索引/并发/打包）迁移。
- 长期（Year‑3+）：若你愿意承担 3–5 年逆向工作与社区维护，Rosetta Stone 的路线能变成难以复制的护城河。

---

如需，我现在可以：
1. 在 `rawkit/` 下创建 `pyproject.toml`、`src/rawkit` 包骨架并把 `dotbox/extract_thumb_from_raw.py` 移入 `src/rawkit/cli/thumb.py` 做最小可运行原型；
2. 或只把 above 文档 commit 推送，先把设计冻结，再逐步实现。

你选哪条？

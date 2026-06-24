# RAW 库压缩工具 — 产品 spec(草稿 v0.1)

> 工作代号: **photopack**(暂定,可改)
> 文档目的: 在写一行代码前把这件事的形状定下来。写完看一遍,如果有任何一块觉得"啊这不对",就该在 spec 阶段改掉,不该带到代码里。

---

## 0. 一句话定位

**一个本地的 RAW 库压缩与归档工具,把你的 RAW 库压到 1/3–1/5 大小,保留绝大多数编辑灵活度,使用标准格式,30 天可逆。**

不是新编辑器。不是新格式。**是个 utility,像 Handbrake 之于视频**。

---

## 1. 核心问题与机会

### 1.1 真痛

摄影爱好者 / 专业拍 5+ 年后普遍面临:

| 痛 | 数量级 |
|---|---|
| 本地 SSD 装不下完整库 | R5 45MP × 10 年 × 5000 张/年 = 2.5 TB |
| 笔记本无法带全库出门 | MacBook 1–2 TB 内置 SSD |
| 云备份首传/恢复慢 | Backblaze 5 TB 上传需 1–2 月 100Mbps |
| 多盘备份贵且分散 | 3-2-1 策略下要 3 × 主库容量 |
| 旧片很少再用但不敢删 | 心理负担 + 偶尔翻出有用 |

### 1.2 现有方案为什么不够

| 方案 | 压缩比 | 问题 |
|---|---|---|
| **Adobe DNG Converter Lossy** | ~50% | 压缩比保守、无 triage 辅助、UI 老、需装 Adobe |
| **手动删片** | 100% 删除 | 心理负担大、无辅助决策、不可逆 |
| **冷存储归档(HDD)** | 0% 压缩 | 仍占空间、找回慢 |
| **iCloud 优化存储** | ~ | 强制上云、违信仰、不适配 RAW |
| **手动转 JPEG XL** | 高 | 命令行复杂、无 workflow、无回退、个人折腾 |

**无人正经做"端到端、本地、智能、安心"的 RAW 归档压缩**。这是真空缺。

### 1.3 机会大小(可量化)

- 全球摄影爱好者 + 专业 5+ 年归档: ~500–1500 万人
- 真痛于硬盘的(高 MP 相机 + 不爱云 + 笔记本工作流): ~30–100 万
- 程序员/技术倾向 + 愿付 indie 工具: ~3–15 万
- **可触达可付费池**: 5–20 万人

定价 ¥199 一次性,渗透 1–2% = **500–4000 付费用户/年**,营收 **¥100k–800k/年**。封顶不高,**养活有余**。

---

## 2. 典型用户旅程

主角 Q,一台 MacBook Air M3 24GB 1TB,外接 5TB SSD 装着 2018–2026 的 R5 + 老 5D + Fuji X100V 全部 RAW,总 4.2 TB。MacBook 内置已塞不下任何 RAW,工作流跨双盘很烦。

### 2.1 第一次使用

```bash
$ photopack scan /Volumes/Photos/
```

工具扫描全库,输出 dashboard:

```
扫描完成: /Volumes/Photos/
============================================
总计: 47,892 个 RAW 文件 (4.2 TB)
按相机分布:
  Canon EOS R5 (CR3):       32,104  (3.1 TB)
  Canon EOS 5D Mark III:     8,221  (480 GB)
  Fujifilm X100V (RAF):      7,567  (640 GB)
按年份分布:
  2018: 4,231  2019: 5,890  2020: 4,102  2021: 5,433
  2022: 6,711  2023: 6,098  2024: 7,422  2025: 5,888
  2026: 2,117 (至今)
按评分分布:
  ★★★★★:    412   ★★★★:  1,876   ★★★:  4,221
  ★★:     8,114   ★:    14,532   未评分:  18,737
被识别为可能废片(初步): 6,244 张

预估可压缩空间:
  策略 SAFE     → 4.2 TB → 1.8 TB (节省 2.4 TB, 57%)
  策略 BALANCED → 4.2 TB →  870 GB (节省 3.3 TB, 80%)
  策略 AGGRESSIVE → 4.2 TB →  580 GB (节省 3.6 TB, 86%)

建议下一步: photopack triage  (AI 智能 triage,识别废片/重复)
```

### 2.2 Triage 阶段

```bash
$ photopack triage /Volumes/Photos/ --model=local
```

后台跑 VLM(本地 ANE)分析 + 启发式:
- 模糊检测、闭眼检测、严重曝光异常
- pHash 相似度聚类(同场景多张取最优)
- EXIF 元数据异常(快门未开/测试帧/封盖照)

完成后产生 `~/.photopack/triage-report.json`,GUI(或 CLI)中分批查阅:

```
==== 第 1 批 (50 张待复核) ====
  IMG_0413.CR3  评分: ❌ 高度模糊 (Laplacian σ=4.2)
  IMG_0414.CR3  评分: ❌ 高度模糊 (σ=3.9)
  IMG_0415.CR3  评分: ⭐ 同组最优 (σ=312)
  IMG_0416.CR3  评分: ❌ 闭眼 (EAR=0.12)
  IMG_0418.CR3  评分: ⚠️ 镜头盖? (全黑)
  ...

[a]提示删除全部 ❌  [s]跳过  [v]逐张复核  [q]退出
```

**Triage 是辅助,不是替代**。任何"AI 说删"都需要用户点确认,**永远不会自动删除**。

### 2.3 压缩执行

```bash
$ photopack compress /Volumes/Photos/ --policy=balanced --confirm
```

工具按策略执行:

```
策略 BALANCED 解释:
  - ★★★★+ 或 已发表过 → 保留原 RAW + 生成 JXL 视觉无损 (5–8% RAW 大小)
  - ★★★ → 仅生成 JXL 视觉无损 (5–8%),原 RAW 进回收
  - ★★ 或更低 → 仅生成 JXL 视觉无损 (5–8%),原 RAW 进回收
  - 已标记废片 → 直接进回收
  - 未评分 → 默认按 ★★ 处理(可改)
  
预估输出: 870 GB
回收区: /Volumes/Photos/.photopack-trash/ (30 天后自动清理)

开始压缩? [Y/n] y

[████████████████████████░░░░] 31,204 / 47,892  跑了 4h12m
  当前: 2024/06/28/IMG_5601.CR3 → IMG_5601.jxl (35 MB → 2.1 MB)
```

完成后:

```
压缩完成 ✓
  处理: 47,892 文件
  生成 .jxl: 41,648 (870 GB)
  保留 RAW: 2,288 (★★★★+, 280 GB)
  回收区: 47,892 个原 RAW (4.2 TB)
  
当前磁盘占用:
  ./           1.15 TB ( = 870 GB JXL + 280 GB 保留 RAW)
  .photopack-trash/  4.2 TB (30 天后清理)
  
30 天后实际节省: 4.2 TB → 1.15 TB (省 73%, 3.05 TB)

完整性验证: 41,648/41,648 PASSED ✓
视觉对比抽样报告: ~/.photopack/fidelity-2026-06-24.html
```

### 2.4 反悔窗口

第 17 天 Q 想找一张 2022 年旅行的 RAW 出来重修:

```bash
$ photopack restore /Volumes/Photos/2022/IMG_3421.jxl
原 RAW 在回收区找到 ✓
恢复路径: /Volumes/Photos/2022/IMG_3421.CR3 (35 MB)
```

第 31 天回收区自动清理(可关闭/延长):

```bash
$ photopack expire
清理 47,892 个文件,释放 4.2 TB
```

### 2.5 之后日常

新拍的照片:

```bash
$ photopack import /Volumes/SD/DCIM/ --to=/Volumes/Photos/2026/07/
```

直接按当前默认策略入库(可改 / 可设新片永远保留原 RAW 给评分窗口期)。

老库的渐进维护:

```bash
$ photopack maintain  # 每月 cron 一次
扫描过去 30 天未压缩的、自动按策略处理
```

---

## 3. 三档压缩策略

### 3.1 SAFE(最保守)

| 子规则 | 行为 |
|---|---|
| 输出格式 | **Adobe Lossy DNG**(LrC/C1 永久支持) |
| 输出大小 | 原 RAW 的 45–55% |
| 元数据 | 全部保留(含 MakerNotes) |
| 处理 | 不烘焙任何东西(linear DNG) |
| 评分门槛 | 任何评分 |
| 原 RAW 处理 | **保留**(只是 alongside 多了一份小 DNG) |

**适用人群**: 完全不愿信任新工具的人。这一档**没省多少空间**,但作为入门档,**让用户先用、先信任**。

### 3.2 BALANCED(中性,推荐)

| 子规则 | 行为 |
|---|---|
| 输出格式 | **JPEG XL** 视觉无损(distance=1.0)+ 嵌入 ICC + EXIF |
| 输出大小 | 原 RAW 的 8–15% |
| 元数据 | EXIF + ICC 完整保留 |
| 处理 | 已 demosaic,白平衡 as-shot 作为 metadata 标签 |
| 评分门槛 | 默认 ≥4★ 保留原 RAW,否则压 |
| 原 RAW 处理 | 进回收区(30 天) |

**适用人群**: 主流推荐。**节省 80%+,视觉无损,标准格式**。

### 3.3 AGGRESSIVE(极致空间)

| 子规则 | 行为 |
|---|---|
| 输出格式 | JPEG XL 高压(distance=3.0)+ 嵌入 EXIF |
| 输出大小 | 原 RAW 的 3–6% |
| 元数据 | EXIF only,丢 MakerNotes |
| 处理 | 已 demosaic,镜头矫正烘焙,降采样到 24 MP(R5 45 → 24) |
| 评分门槛 | 默认 ≥3★ 保留原 RAW |
| 原 RAW 处理 | 进回收区 |

**适用人群**: 接受"老废片只是留个念想"的用户。**这一档要让用户在策略协商时明确看到将损失什么**。

### 3.4 策略矩阵(用户可自定义)

用户可以**自己写策略表**,不局限于上面三档:

```toml
# ~/.photopack/strategy.toml
[default]
output_format = "jxl"
distance = 1.0
keep_raw_if = "rating >= 4"
delete_raw_if = "rating <= 2 and age_days > 365"
recovery_days = 30

[per_camera.X100V]
# Fuji 老相机,我对它有情怀,全部保留原 RAW
keep_raw_if = "always"

[per_year.2018]
# 老照片,激进
distance = 2.0
keep_raw_if = "rating >= 5"
```

**约定大于配置**: 99% 用户用 SAFE/BALANCED/AGGRESSIVE 三档就够,1% 玩家可以写 TOML 自调。

---

## 4. AI Triage 决策模型

### 4.1 决策对象

**Triage 只决定一件事**: 这张照片**值不值得人工再看一眼**。

它**不删除**任何东西。它输出三类:
- ⭐ **KEEPER**: 这张明显是好的,建议保留(可能升评分)
- ⚠️ **REVIEW**: 这张可能不好,建议你看看
- ❌ **REJECT**: 这张几乎肯定废,建议删

最终决定权**永远在人**手里。

### 4.2 启发式信号(全部本地,不需 GPU)

| 信号 | 算法 | 触发阈值 |
|---|---|---|
| 模糊 | Laplacian σ on luma plane | σ < 50 = ❌, σ < 100 = ⚠️ |
| 闭眼 | MediaPipe Face Mesh → Eye Aspect Ratio | EAR < 0.18 + 检测到脸 = ⚠️ |
| 严重曝光异常 | 直方图 99% 分位 | 99% 分位 < 5/255 或 > 250/255 = ⚠️ |
| 镜头盖/封盖 | 全图均值 + std | mean<3 std<2 = ❌ |
| 测试帧/无意触发 | 快门<1/4000 + 无脸 + 时间间隔异常 | 综合判定 = ⚠️ |
| 连拍重复 | 时间差 < 2s + EXIF 同 lens/iso + pHash 相似度 | 同组只保留最高分 |

### 4.3 VLM 信号(可选,Apple Silicon)

| 信号 | 模型 | 用途 |
|---|---|---|
| 构图质量 | 基于 AVA dataset fine-tuned 小 ViT | 给同主题多张排序 |
| 跨张相似度 | SigLIP embedding + cosine | 找出"几乎一样"的视觉重复 |
| 主体识别 | CoreML Vision Framework | 辅助判断"有没有主体" |

VLM 推理在 M2+ 上 ~30ms/张,5 万张 ~25 分钟。**关闭 VLM,只用启发式,几乎一样有用**,VLM 是锦上添花。

### 4.4 学习用户偏好(进阶,v2 才做)

记录用户在每次 triage 后的"接受 / 拒绝 / 反向操作"。3000 张反馈后训一个 ranker,**让 AI 学你这个具体用户的 keep 倾向**。

**关键约束**: 学习数据只在本机,**永远不上传**。这是和 Imagen 等云方案的根本差异。

### 4.5 Triage 永远要审计

每次 triage 后输出一份 HTML 报告:

```
~/.photopack/triage-2026-06-24.html
  - 总览(各类别数量)
  - 按文件名列表 + AI 给的理由 + 缩略图
  - 用户可勾选"我不同意 AI 这张" → 写入 ~/.photopack/feedback.json
```

人工抽检 5% 是约定的最佳实践,工具会主动提示。

---

## 5. 30 天回收机制

### 5.1 物理层

不是真删,只是 **mv** 到回收区:

```
/Volumes/Photos/
├── 2024/06/IMG_5601.jxl          ← 压缩后的小文件,主库
├── .photopack-trash/             ← 回收区,30 天后清理
│   └── 2024/06/IMG_5601.CR3      ← 原 RAW,可恢复
├── .photopack/
│   ├── recovery.db               ← 索引: 哪个 .jxl 对应哪个 .CR3
│   ├── strategy.toml             ← 用户策略
│   └── feedback.json             ← 用户对 AI 决策的修正
```

### 5.2 恢复接口

```bash
# 单文件恢复
photopack restore path/to/file.jxl

# 全恢复(撤销整次压缩)
photopack restore --batch=2026-06-24

# 看回收区
photopack trash list
photopack trash size
```

### 5.3 自动清理

`photopack expire` 命令清理超过 N 天的回收文件。

- 默认 30 天,可改 7 / 60 / 90 / 永不
- cron / launchd 一周一次自动跑
- **永不自动跑**: 必须用户首次手动配置后才启用 launchd

### 5.4 信任叙事

这一节是产品的**信任承诺核心**,在 README / landing 上原文展示:

> ### 我们的承诺
> 1. 压缩**永远**不在原文件上 in-place 操作。原 RAW 进回收区,新 .jxl 是另一份。
> 2. 回收区**默认 30 天**(可改 7–永不)。任何时候 `photopack restore` 找回。
> 3. **不联网**。你的照片不会以任何形式离开你的设备。
> 4. 压缩 + 元数据 + 恢复**代码全部开源** ([github.com/qing4132/photopack-core](https://github.com/qing4132/photopack-core)),任何人可审计。
> 5. 输出**标准 JPEG XL / DNG**,任何工具能读。即使我们 5 年后消失,**你的文件不依赖我们存活**。

---

## 6. Viewer / Editor 最小功能集

**强约束**: 编辑不是这个产品的卖点,viewer/editor 只为让压缩文件**能用**。

### 6.1 必须有(v1)

- 打开 .jxl 文件,显示
- 显示 EXIF 详情(继承 rawkit info)
- 简单调整: 曝光 / 白平衡 / 对比度(只 3 个滑块)
- 导出 JPEG / TIFF(任意分辨率)
- 写 sidecar XMP 兼容 LrC

### 6.2 可以没有(v1 砍)

- 70 个滑块
- develop history / undo stack(只 current state)
- catalog / library 管理
- 跨文件搜索
- HSL / 曲线 / 局部调整 / 笔刷 / 渐变滤镜
- 打印 / book / slideshow modules
- Plugin 体系

### 6.3 后续可能加(v2+)

- 镜头矫正 viewer
- AI develop 建议(SOOC match)
- 简单 batch develop(把同 session 的应用一致设置)

**绝不加**: 任何让产品变成 "Lightroom lite" 的功能。**这是 utility,不是编辑器**。

---

## 7. 技术架构

### 7.1 模块分解

```
photopack/
├── core/                        (Python + Rust)
│   ├── reader/                  RAW 读取(LibRaw 包装,继承 rawkit)
│   ├── encoder/                 JPEG XL / DNG Lossy 编码
│   ├── triage/                  启发式 + 可选 VLM
│   ├── policy/                  策略引擎(TOML 解析 + 执行)
│   ├── recovery/                回收区管理
│   └── verify/                  完整性 + 视觉对比
├── cli/                         CLI 入口(Typer,继承 rawkit 风格)
├── viewer/                      SwiftUI macOS(v1 后期)
└── docs/
```

### 7.2 复用 rawkit 的部分

| rawkit 现有 | photopack 用 |
|---|---|
| `_exif_lite.py` | EXIF 读取 |
| `_cache.py` | 处理状态缓存 |
| `extract.py` | 嵌入 JPEG 提取(SOOC reference) |
| `_resize.py` | 降采样 |
| `extract` 的 IFD 直读优化(TODO) | 大幅加速 batch 处理 |

→ **大约 30–40% 工程量已存在**。

### 7.3 关键技术决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 压缩格式默认 | **JPEG XL** | 技术上最优(无损/视觉无损/有损一套覆盖)、压缩比最高、保 EXIF |
| 压缩格式备选 | **Adobe Lossy DNG** | SAFE 档专用,任何 LrC/C1 用户能直接用 |
| JXL 库 | **libjxl** via Python binding | 官方实现,生态稳定 |
| RAW 解码 | **LibRaw** via rawpy | rawkit 已用 |
| VLM 推理 | **SigLIP / CLIP** via CoreML | Apple Silicon ANE |
| 文件结构 | **filesystem-native** | 不引入数据库 |
| 多核 | **multiprocessing** + 进度条 | 简单可靠 |
| 跨平台 | **macOS first**, Linux v1.5, Win v2+ | 你 dogfood 在 mac |

### 7.4 性能目标

| 操作 | 5 万张 R5 RAW 库 (4.2 TB) | 目标时间 |
|---|---|---|
| scan | 元数据扫描 | < 10 min |
| triage(纯启发式) | 全库判 | < 30 min |
| triage(+VLM) | 全库判 + 嵌入 | < 60 min |
| compress(BALANCED) | 全库压 | < 6 h(MacBook M3) |
| restore(单文件) | — | < 100 ms |
| verify(抽样 1%) | — | < 5 min |

---

## 8. 营销主张验证方案

**承诺数字不能糊弄**,否则用户 trial 翻车就完蛋。需要在产品发布前**用真实库实测**。

### 8.1 实测计划

**Phase 1: 个人库(作者本人)**
- 用你自己 5+ 年的 R5 + 老 5D + X100V 全部 RAW
- 跑三档,记录 actual 压缩比、视觉对比、各档失败案例
- **如果你自己的库 BALANCED 没到 80%、视觉无损没成立,产品立项失败,停**

**Phase 2: Beta 用户库(20–30 人)**
- 在 V2EX / 小红书摄影板招募
- 各品牌相机(Sony A7R, Nikon Z, Fuji X, GFX, M11...)
- 每人提供 1000+ 张样本,跑三档,要他们打分

**Phase 3: 公开 benchmark**
- 开源一个 1000 张多品牌测试集
- 公开三档的 SSIM / PSNR / 视觉评分
- 数字写在 landing 上,任何人可复测

### 8.2 营销数字的诚实边界

| 可以说 | 不可以说 |
|---|---|
| "BALANCED 平均压缩到 RAW 的 12%" | "压缩到 5%" |
| "视觉无损在 95% 测试图上成立" | "永远视觉无损" |
| "Canon R5 实测节省 88%" | "所有相机省 88%" |
| "30 天可逆" | "永远可逆" |

**任何夸大都是慢性自杀**,因为这个产品的全部信任建立在数字上。

---

## 9. 12 个月里程碑

> 节奏原则: 每月一个对外可验证的里程碑。**12 月前不做 GUI**。

### M1(0–4 周): Scan + 元数据 + 策略引擎

- 复用 rawkit 的 reader/_exif_lite/_cache
- 写 `photopack scan` 命令,输出 dashboard
- 写策略 TOML 解析 + 三档 preset
- **里程碑**: 在你自己 4.2 TB 库上 scan 完成,数字准确
- 公开: 无

### M2(5–8 周): Encoder + 单文件压缩 verify

- 接 libjxl,写 RAW → JXL 转换函数
- 写 `photopack compress --dry-run` 模式
- 写 `photopack verify` 比对完整性
- **里程碑**: 单文件 RAW → JXL 视觉无损,大小到位
- 公开: 一篇技术博客《Compressing CR3 to JPEG XL: a benchmark》→ HN / V2EX / 小红书

### M3(9–12 周): Batch + Recovery + CLI v0.1

- 多进程批量压缩
- 回收区管理 + `photopack restore`
- 完整 CLI(scan / triage / compress / restore / trash / verify)
- **里程碑**: 你能在自己 4.2 TB 库上跑完一遍 + 部分恢复
- 公开: `photopack` 0.1 开源到 GitHub,**免费**,放出 README + 数字

### M4(13–16 周): Triage 启发式 + 报告

- 模糊 / 闭眼 / 重复 / 异常检测
- HTML triage report
- `photopack triage` CLI
- **里程碑**: 在你库上找出 ≥ 90% 的明显废片(对比人工)
- 公开: 第二篇技术博客 + Show HN

### M5(17–20 周): VLM 增强 + 性能优化

- SigLIP via CoreML
- 相似度聚类
- 多进程优化 → 5 万张 6h 达标
- **里程碑**: VLM triage 跑通,M2 Air 上不会风扇起飞
- 公开: Twitter / 小红书摄影圈推广

### M6(21–24 周): 真实 beta + 反馈循环

- 招 30 个 beta 用户(V2EX + 小红书 + 摄影圈)
- 多品牌相机覆盖
- 收集 bug + 实测数据
- **里程碑**: 30 人各跑 1000 张以上,平均压缩比、视觉满意度记录
- 公开: 开始**收订阅 ¥99 早鸟**(CLI 仍开源,GUI 入口预订)

### M7–M8(25–32 周): SwiftUI GUI v0

- macOS 原生
- 主功能: scan / triage 视觉审 / compress 进度 / restore
- 没有 viewer/editor(下个 sprint)
- **里程碑**: GUI 可替代 80% CLI 使用,审美过得去
- 公开: TestFlight beta

### M9–M10(33–40 周): Viewer + 基本 editor

- 打开 .jxl 显示
- 3 滑块编辑 + XMP 写出
- 一键导出 JPEG
- **里程碑**: 用户在 photopack 里完成一个完整 trip 的浏览-选-导出工作流
- 公开: 正式上 App Store(可选)或独立 DMG 分发

### M11(41–44 周): 正式发布 + 营销

- 价格调到 ¥199 一次性 / Pro ¥499(批量 + AI triage + 多机型)
- ProductHunt + HN 上 / 小红书 KOL 合作
- **里程碑**: 单月 ¥30k+

### M12(45–48 周): 巩固 + 决定下一步

- 优化、修 bug、收用户反馈
- 出 v1.1 加迭代功能
- **里程碑**: 月净 ≥ ¥30k 维持 3 个月 = 养活线达成
- 决策: 继续单干、考虑融资、考虑加人

---

## 10. 风险登记与对策

| # | 风险 | 概率 | 杀伤 | 对策 |
|---|---|---|---|---|
| R1 | 用户不信任,不敢删原 RAW = 没省空间 | **极高** | **致命** | 30 天回收、开源核心、公开 fidelity benchmark、SAFE 档作为"先用先信任"入门 |
| R2 | JPEG XL 生态长期不被主流支持 | 中 | 重 | SAFE 档用 Lossy DNG;教育用户"jxl 是 ISO 标准,长期会被支持";提供 jxl → jpeg 一键导出 |
| R3 | Adobe DNG Lossy 升级到 80% 压缩 | 低 | 重 | Adobe 不会主动卷自己;即便卷,你的 AI triage + 可逆机制仍是差异化 |
| R4 | Triage AI 误判致用户失重要照片 | 中 | 致命 | 永不自动删、永远要人确认、报告全透明、用户可修正 |
| R5 | 营销数字翻车(实测压缩比不如承诺) | 中 | 重 | M1 用作者自己库实测、Phase 2 beta 多品牌测、数字写到 landing 都来自实测 |
| R6 | 创始人 GUI 工程量超预期 | 高 | 中 | CLI 先上线赚 6 个月口碑,GUI 推到 M7+;SwiftUI 主功能简单,不做复杂交互 |
| R7 | 跨相机品牌兼容性长尾(没 R5 那么干净) | 高 | 中 | M6 Beta 多品牌测,长尾 case 列入 known issues 而非阻塞发布 |
| R8 | 苹果/Adobe 收购 / 自带类似功能 | 低 | 中 | 苹果不会做"删原 RAW"这种激进事;Adobe 商业模式禁止;5 年内安全 |
| R9 | 你自己 6 月后觉得无聊不想做了 | 中 | 致命 | 这是真风险。**对策: 把第一个真实 beta user 设成你自己,M3 之前的全部工作你立刻 dogfood,如果 M3 你没疯狂用它,这事就该停** |
| R10 | 法律: 哪天某厂商认为我们"擅自处理"他们的 RAW | 极低 | 低 | 输出标准格式无侵权;读 RAW 用 LibRaw(开源已存在 20 年) |

---

## 11. 未来可加(记下但 v1 不做)

- **Smart sync to cloud**: 把 .jxl 选择性上传 Backblaze / iCloud,关键照片云备份
- **iPad viewer**: 打开 .jxl 浏览 / 简单调整 / 导出(只读模式)
- **LrC 导入助手**: 一键替换 LrC catalog 中的 RAW 引用为 .jxl(让 LrC 用户能继续在 LrC 里看压缩后照片)
- **AI 风格学习**: 学用户 develop 偏好,新片导入时 pre-edit
- **多机器同步**: 多台 Mac 共享一个 photopack 库(无需云)
- **Print-ready 输出**: 一键导出印刷级 TIFF(从压缩 .jxl 重建)
- **Family library**: 多用户(夫妻)共享库,各自评分独立

每一条都是诱惑,**v1 全部砍掉**。

---

## 12. 不会做(永远)

- ❌ 上传任何用户数据到任何服务器
- ❌ 强制订阅 / 强制账号
- ❌ 替代 LrC / Capture One 做主编辑器
- ❌ 任何"AI 自动决策不可逆操作"的功能
- ❌ 在原文件上 in-place 修改
- ❌ 闭源压缩/恢复核心(transparency 是信任基石)
- ❌ Web app / 云版本
- ❌ Adobe Camera Raw plugin / LrC plugin(本工具不寄生)
- ❌ DRM / 反盗版加密(信任用户)
- ❌ "全家桶" / 多产品矩阵(就这一个工具,做透)

---

## 13. 当前最大的未决问题

写完这份 spec 后,我意识到**有三个问题需要在 M1 之前先答**,否则带着不确定上路会拖死项目:

1. **JXL 在 Apple 生态的真实支持度怎样了?** Safari 18 / macOS 15 是否原生预览?Finder 缩略图?Preview.app?
   - 行动: 实地测试,记录结论。如果太烂,SAFE 档可能要升为默认。

2. **CR3 → JXL 视觉无损的真实压缩比是不是 8–15%?**
   - 行动: M1 第一周用 100 张你自己 CR3 实测,数字定下来再写 landing 文案。

3. **回收区放主盘还是同盘?**
   - 同盘: 简单,但压缩后短期不省空间(因为原 RAW 还在回收区)。30 天后才真省。
   - 主盘: 立即省空间,但要求用户配置(可能用另一块大盘做"30 天 hospice")。
   - 行动: 默认同盘 + 可配置;在 dashboard 里清楚说明 "30 天后实际节省" vs "立即节省"。

---

## 14. 立项还是不立项

读完这份 spec,**自己心里要诚实回答**:

- 我是不是真痛于硬盘? **如果是,立项**。
- 我是不是真会 dogfood 到 M3?如果不会,立项前先解决这个心理问题
- 我能不能接受 6 个月没有现金流?需要 12 个月跑通养活线
- 我能不能在 M7 学会够用的 SwiftUI?或者外包前端给会的人(¥10–20k)
- 如果 M3 跑完之后,我自己用着觉得"啊这个真好",**立项就成功了**——后面的 9 个月只是把好东西打磨给别人

---

*v0.1, 2026-06-24*
*下一步: M1 第一周做 JXL 实测,验证 spec 数字。验证通过后,这份文档升 v1.0,开始写第一行代码。*

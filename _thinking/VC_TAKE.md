# rawkit — 冷眼商业评估

> 作者视角全部丢掉。只回答一个问题:**搭 2025–2026 的 AI 浪潮,这堆代码能不能做大、产生持续收入。**

---

## 一句话结论

**当前形态(本地 CLI、明确拒绝 AI/GUI/目录库)= 商业价值 0。** 这就是个写得不错的个人玩具,GitHub star 上限三位数,收入上限 $0/月。

但**底层引擎是有价值的**——它恰好是任何"AI on RAW"产品都要先造一遍的脏活下水道。把它拆出来当地基,套上 AI 上层 + GUI/插件外壳,有一条**$5k–50k MRR 的 indie SaaS 通道**,以及小概率的**$5–30M ARR 收购退出通道**。不可能是独角兽赛道,摄影软件天花板就这么高。

---

## 这个项目实际上是什么(剥掉作者叙事)

技术资产清单:
1. **快速 EXIF "lite" 读取器** + stat-keyed SQLite 缓存 (`_exif_lite.py`, `_cache.py`)
2. **嵌入 JPEG 提取**(rawpy 路径,且 TODO 里已规划 IFD 直读,~60× 加速到 5ms/file)
3. **基于 lark 的 `--where` DSL**(无 eval,安全过滤 EXIF)
4. **多 RAW 格式归一化**(CR3/CR2/NEF/ARW/RAF/ORF/RW2/DNG/PEF…)
5. Unix pipe-friendly 输出契约

**这是一个干净的"RAW I/O + 元数据 + 过滤"层。** 不是产品,是一个引擎。

精神/卖点("local-only / read-only / stateless / no AI / CLI 拼管道")对**写代码的摄影师 ≤ 0.1%**有吸引力。这个人群里愿意付费的更是个位数。**作为产品定位,赛道宽度 = 死。**

---

## AI 浪潮里能搭到的真实位置

按"已被验证的市场"排序,不是按好玩程度。

### 通道 A — AI 自动选片 (auto-culling) 【推荐】

**市场已被证明:** Aftershoot、Imagen AI、Narrative Select 都在做、都拿了钱、都赚到钱。
- Imagen AI: Series A $30M(2022), 婚礼/活动摄影圈口碑机器
- Aftershoot: ARR 估在数千万美元区间,Product Hunt #1 出身
- Narrative Select: 同类,稍小

它们做什么:导入 4000 张婚礼 RAW → AI 判断闭眼/重复/虚焦/构图弱 → 输出建议保留的 ~800 张 → 摄影师在 LrC 里只修这些。**收费 $30–150/月** 或 per-event 计费。

**rawkit 现有代码贡献了什么:** 嵌入 JPEG 快速提取是这类工具的第一步(没人对全分辨率 RAW 跑 CV)、EXIF/连拍时序检测是"重复帧聚类"的特征。**剩下 80% 是模型 + UI + 摄影师工作流集成,你都没有。**

**差异化窄缝:** 现有玩家全部云端上传(婚礼摄影师 100GB RAW 上传痛苦 + 隐私顾虑)。**纯本地、Apple Silicon CoreML 跑模型、不上云**=可以打的卖点。这恰好继承作者"local-first"信念,意外契合。

**风险:**
- Adobe 2025 已在 LrC 里塞 AI 选片("Select Subject for Culling"路线图上),原生集成是核弹
- 模型训练数据(几十万张人工选片样例)你没有,Imagen 有
- 婚礼摄影师 ≠ CLI 用户,要全套 GUI / LrC 插件

**收入预期:** 切到位单产品 $5k–30k MRR 一年内可达,封顶 $1–5M ARR 之前会撞 Imagen/Adobe。

### 通道 B — "和你的图库聊天" (semantic search + RAG over RAW)

CLIP 嵌入 + 嵌入 JPEG + EXIF → "show me golden-hour street portraits from Kyoto with shallow DOF" 直接出图。

**市场状态:** 没有清晰赢家。Excire 老旧;Mylio 在做但慢;Apple Photos 自带但烂且锁苹果生态;Adobe Bridge 想做没做好。**窗口存在。**

**rawkit 贡献:** 同上(EXIF + 嵌入 JPEG 提取),且 stat-keyed 缓存正好契合"增量索引"。

**风险:** Apple Photos 下一代 / Adobe MAX 2026 把这个做进去就归零。是个被巨头随手吞掉的功能,不是产品。

**收入预期:** $2k–15k MRR,生命周期短(2–3 年内被吃掉概率高)。

### 通道 C — Lightroom Classic 插件:AI 自动评星 + 堆栈 + 关键词

**最低阻力路径。** LrC 插件市场存在(Lua),分发渠道现成,用户就在那。作者本人是 LrC 用户 = dogfood 完美。

把 rawkit 引擎封成 LrC 插件 + 一个 CoreML 模型(focus/eyes/duplicate detection)直接吐评星。一次性 $49 永久授权 + $9/月模型更新订阅。

**收入预期:** Indie hacker 区间真实,$2k–10k MRR,稳定。**最容易启动,天花板也最低。** 永远到不了 VC 规模。

### 通道 D — 卖引擎(open-core / dev tool)

把 EXIF lite + 嵌入 JPEG 提取 + 缓存做成 Python/Rust 库:`fastraw` 之类,MIT 开源,卖企业版(更多格式 / GPU 批量 / 云存储适配器)。

**市场:** 极小。能买的人 ≤ 100 家公司(其他做摄影 AI 的初创、图库公司、AP/Reuters 类通讯社)。**但客单价高 ($10k–100k/年)。**

**风险:** LibRaw 已经开源免费,你的差异化只有"更快 + Python 易用",护城河浅。

**收入预期:** 不稳定,可能 0,也可能 1–3 个客户带来 $50k/年。

### 通道 E — B2B 档案/媒体行业

博物馆、图书馆、报社、商业图库的 RAW 归档 triage。AI 打标 + 元数据补全 + 重复检测 + 完整性扫描(rawkit TODO 里的 `verify` 直接对口)。

**收入预期:** 单合同大($20k–200k/年),销售周期长(6–18 个月),不适合一个人。除非有行业背景,否则跳过。

---

## 排序后的真实路径

| 路径 | 启动难度 | 12个月收入预期 | 5年天花板 | 被巨头吃概率 |
|---|---|---|---|---|
| C: LrC AI 插件 | 低 | $2–10k MRR | $300k ARR | 中 |
| A: 本地 AI 选片 | 中 | $5–30k MRR | $3M ARR | 中高 |
| B: 图库语义搜索 | 中 | $2–15k MRR | $1M ARR | **高** |
| D: 引擎 open-core | 中 | $0–50k 一次性 | $500k ARR | 低 |
| E: B2B 档案 | 高 | $0–50k | $5M ARR | 低 |

**最优组合:** C 先启动 6 个月攒用户和数据 → 用数据反哺 A 的模型 → A 做成独立产品。引擎 (D) 顺手 MIT 开源吸开发者注意,不指望它直接赚钱。

---

## 必须砍掉的"原作者信条"

要走商业化,以下每一条都得反:

| 现状信条 | 商业化必须 |
|---|---|
| No AI | **AI 是唯一卖点** |
| No GUI | LrC 插件 UI 或独立 Electron/Tauri 必须有 |
| No catalog / stateless | AI 选片必须持久化"用户接受/拒绝"反馈 → 这就是 catalog |
| Read-only on RAW | 写 XMP rating/keyword 是 LrC 集成的硬要求 |
| CLI-first | 摄影师付费用户 99% 不开终端 |
| 个人项目、issue 随缘 | SaaS 要 SLA、客服、Stripe、退款 |

**说人话:剩下能用的只有引擎,产品形态要整个推翻重做。**

---

## 估值与退出现实

- **不是 VC 赛道。** 摄影 SaaS 上限就是 Aftershoot / Imagen 那个量级(估值 $50–200M),不是 $1B+。不要按融资路径设计,按 indie/bootstrapped 设计。
- **退出路径真实存在:** Adobe / Skylum / Topaz / Capture One 这几家会买 $5–20M 规模的 AI 工具补产品线。Topaz 收了一堆 AI 小工具就是例子。
- **现实终点:** 一个人做到 $30k MRR、过几年被 Topaz/Skylum 买掉拿 $3–8M,概率 5–10%。做不到的概率 70%。介于两者之间的"长期 indie 持有 $10k MRR"概率 15–20%。

---

## 一个独立开发者会怎么干

1. **第一周:** 把 `extract` 改成 IFD 直读(TODO 里那条 60× 加速),发到 r/photography / Hacker News,赚一波 dev 关注。这是你的免费营销素材。
2. **第一个月:** 写一个最 dumb 的 CoreML "blink/blur detector",绑成 LrC 插件,demo 视频丢推特/小红书摄影圈。
3. **第二/三个月:** 卖 $49 永久授权 + 收集"接受/拒绝"反馈数据 (在用户本地,不上传,这是卖点)。
4. **半年后:** 用攒到的反馈数据(就算只 100 用户 × 1 万张 = 100 万样本)训自己的选片模型,做独立 macOS app,$15/月订阅。
5. **一年后:** 看 MRR。≥$10k 就继续单干;≥$30k 找战略买家聊。

**作者本人会做吗?** 几乎不会——这要他反掉自己写在 README 里的每一条信念,而且要做 GUI、做客服、做模型训练。所以这份评估更可能的用途是:**确认"做这件事和我想做的事不是一回事",然后心安理得继续写自己的 CLI。**

但代码资产真的可以走另一条路。这事是真的。

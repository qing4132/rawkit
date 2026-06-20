# TODO

> V1 surface(`ls` / `info` / `extract` / `render` / `organize`)五件命令**已经全部到齐**。
> 这一页接下来主要记:已交付的护栏决定、还在路上的窄缝功能、明确推到 V1.x 或永久砍掉的东西。

---

## P0 · 命名与品牌

### 品牌名(`rawkit` 在 PyPI 被占)

候选:`lightbox` / `shutter` / `flip` / `darkroom` / `filmstrip` / 别的。
**未拍板**。这个不定,pyproject / git remote / CLI entry point 都没法改,也没法发布。

### 子命令改名

- ~~`preview` → `extract`~~ ✅ 完成(2026-06-19)
- ~~`stats` → 折进 `info --by`~~ ✅ 完成(2026-06-20) — 命令、模块、测试全部下线;聚合核心迁到 `rawkit/aggregate.py`,info 和潜在新消费者都从那儿取。
- `render` 可能改名为 `bake` / `develop` / `export`,**未拍板**。倾向暂时不改。

---

## ~~P0 · 缺失的命令~~ ✅ 全部到齐(2026-06-20)

- ~~`info`~~ ✅ FILE + DIR + `--by DIM` 三态
- ~~`organize`~~ ✅ 默认 in-place、`--by` 可选、`--prune` 安全 bounded(只扫非隐藏)、sidecar 跟随、碰撞 fail-fast

---

## ~~P1 · `--by` ↔ `--where` 字段对称性~~ ✅ 已完成(2026-06-19)

`hour` / `year` / `month` / `day` 4 个整数派生字段已上 DSL + 桶。语义钉死见 USAGE。

---

## P1 · 设计原则(护栏)

1. **Read-only · Local · Stateless · One question, one screen** —— 任何违反其中一条的新功能砍。
2. **任何能 `--by` 的 dim 都必须是 `--where` 的 field**(反之不一定:bool 和 GPS 坐标天然不适合分桶,只在 `--where` 里有)。
3. **`--by` 在 info 单维;在 organize 可多维(嵌套目录)**。多维语义在 organize 那边有真实意义;在 info 还没有(见下方暂缓项)。
4. **`--prune` 不默认开**:rmdir 不可逆,opt-in 才安全。

---

## P2 · 各命令的展示规则(已经默认遵守,但写下来当护栏)

`info --by` 三类维度的展示要求:

- **时间 / 周期类**(`hour` / `day` / `month` / `year`):空段是信息,要么显式保留零桶要么用分段缩写表达。**不要自动合并成 2-hour bucket** 那种歧义压缩。
- **频次类**(`camera` / `lens` / `maker`):空段无意义,按计数排序、`--top` 截断(仅 lens 有意义)。
- **光学 / 器材类**(`aperture` / `focal` / `shutter` / `iso` / `bias`):空段更多反映"没那支镜头",关心的是分布形状。

每个维度有自己的展示味道,**不强求跨维度的视觉一致性**——用户一次只看一个。真正的分布分析靠 `--json | pandas`,我们不竞争。

---

## P2 · 不紧急但确定要做

- ~~`render` 对齐 `extract` 的输出路径与 resize 语义~~ ✅ 完成(2026-06-20)
- `--by hour --full`:可选 flag,把空小时也打出来(默认就是分段缩写)。等用户真的提出再加。

---

## P2 · 暂缓(讨论过、有明确价值、但还没做)

- **`info --by FOO -l`**:每个桶下面直接缩进列出落进这个桶的具体文件路径。当前从"看了分布"到"看分布里某一格的具体文件"必须重写一次 ls 命令(桶名 → where 子句的翻译靠脑补)。`-l` 一步到位,~30 行,完全 stateless。等真正撞多了再做。
- **`info --by A,B`(多维嵌套)**:跟 `organize --by A,B` 同构——前者出嵌套分组的文本视图,后者出嵌套目录树。当前 info 多维直接拒绝(exit 2 + "not yet supported")。等真有"想看 camera 内部 lens 分布交叉"的需求时再做嵌套渲染。
- **35mm 等效焦距**:`focal` 字段当前是镜头实际焦距,不做 crop factor 归一。APS-C(1.5x) / m4/3(2x) / Fuji 中画幅(0.79x) 上看到的是裸值。要做 35mm 等效得读 `FocalLengthIn35mmFormat` EXIF 字段或按 maker 维护机身 crop 表——增量大、且会引出"那 `--where focal>=70` 是说裸值还是等效值"的语义岔路。**V1 不做**,什么时候有强需求再开。

---

## P3 · V1.x 候选(V1 不做)

- `verify`:检查 RAW 文件完整性(magic number / exiftool 能读全 / 字节读完不报错)。卡传输丢字节、长期 bit rot 时有用。
- `duplicates`:找重复 RAW(按内容 hash 或 datetime+model)。合并卡 / 整理旧硬盘时有用。
- `info` 列出 RAW 里**全部**内嵌 JPEG(不止 libraw 选的那张):当前 info 的 Embedded 行复用 extract,只有 1 张。要列全得另起 exiftool 枚举 PreviewImage / ThumbnailImage / JpgFromRaw / OtherImage。**仅当 extract 自身长出选择某张预览的能力时再做**——否则 info 看到 3 张、extract 只能拿 1 张的不对称会立刻引发新需求。

---

## 永久砍掉(避免反复讨论)

| 候选            | 砍的原因 |
| -------------- | -------- |
| `cull` / `rate` | 跟 LrC 形成两层皮,作者明确反复否决 |
| `tag` / `edit` | 违反 read-only |
| `import`       | `organize` 把 source 指到 SD 卡就是 import |
| `map` / `gps`  | 要打开外部 viewer,违反单一职责 |
| `diff`         | `info a; info b; diff` 解决 |
| `serve` / `watch` / `daemon` | 违反 stateless |
| `caption` / `embed` / `cluster` / AI 层 | V2+ 才谈,V1 内别想 |
| `preset extract/apply` | LrC sidecar 写回方向,V2+,V1 不碰 |
| `stats` 独立命令 | 已并入 `info --by`,不再单设 |
| 把 `--by` 跟 `--sort` 合并 | 语义不一(ORDER BY vs GROUP BY),合并反而混乱 |
| 让 rawkit 记住"上次的 where" | 违反 stateless;shell history 已经够用 |
| stdin path 输入 / 跨命令管道 | 每个命令自带 `--where`,管道不增加表达力 |
| `--prune` 默认开 | rmdir 是 destructive,只能 opt-in |
| `extract --watch`/`organize --watch` | 违反 stateless |

---

## 持续 dogfood 撞到的细节

ISO / aperture 这种 MakerNotes 污染坑大概率还有别的字段同病,但**不主动排查**,撞到再修(每次修法都是"锁 `EXIF:` 组 + 必要时 fallback")。这套模式已经写在 `src/rawkit/exif.py` 的 `_FIELD_MAP` 注释里。

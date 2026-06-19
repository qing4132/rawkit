# TODO

## 1. `--by` ↔ `--where` 字段对称性

**现状**：`--by` 支持 `hour` / `year` / `month` / `day` 四个时间派生桶维度,但 `--where` DSL **没有对应字段** —— 只能绕道写 `where time>"18:00:00"` 或 `where date>="2024-01-01" and date<"2025-01-01"`。其他维度(`camera`/`lens`/`maker`/`orientation`/`iso`/`fnumber`/`focal`)都是双向对称的。

**修法**:在 DSL 里加 4 个整数字段 `hour` (0–23) / `year` / `month` (1–12) / `day` (1–31),从 `time` / `date` 派生。
- `query.py` grammar:`FIELD` 加 4 个 token
- `_NUMERIC_FIELDS` 加这 4 个
- `exif._normalize` 里把 4 个值预先存进 record(读取时不用 parse)
- 4 个对应单元测试
- USAGE DSL 字段表更新

**预算**:大约 1 小时。

## 2. 设计原则(写进 USAGE 顶部 + 留作未来 review 的护栏)

> **任何能 `--by` 的 dim 都必须是 `--where` 的 field,反之亦然。**

加新维度时直接挡住一半的歧义。

## 3. 桶字段比较的语义钉死(写进 DSL 文档)

> `hour` / `year` / `month` / `day` 是**整数桶 ID**,比较即比较桶号。
> - `hour > 6` ≡ `hour >= 7`,意思是"在 7 点桶或之后的桶",6:30 不在内。
> - 想做"6:00:00 这个时刻之后"请用 `time > "06:00:00"`。
> - `>` 与 `>=` 在整数桶上自然重合(SQL `WHERE month > 6` 同义),不是 bug。

两类问题、两套字段、不重叠。

## 4. CLI 文本视图的边界(产品定位)

`stats` 的文本输出守住"瞥一眼"用例 —— 每个 `--by` 维度可以有自己的展示味道(hour 用分段、month 用 bar、enum 类用 top-N),**不强求跨维度的视觉和谐**;反正用户一次只看一个。真正的分布分析靠 `--json` 倒进 notebook。

具体规则:
- **时间 / 周期类**(`hour` / `day` / `month` / `year`):空段是信息,要么显式保留零桶要么用分段缩写表达,**不要自动合并成 2-hour bucket** 那种歧义压缩。
- **频次类**(`camera` / `lens` / `maker`):空段无意义,按计数排序、top-N 截断。
- **光学 / 器材类**(`aperture` / `focal` / `shutter` / `iso`):空段更多反映"没那支镜头",不是行为信息;关心的是分布形状。

## 5. 可能的 `--by hour --full` flag(非紧急)

当前 `--by hour` 只显示有数据的小时,把空小时折叠掉。如果将来真有人想看完整 24 行带零桶,加 `--full`(或类似)flag 把空桶也打出来。在用户明确提出之前不做。

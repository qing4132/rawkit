# rawkit

> 一个**只读、本地、无状态**的 RAW 照片命令行工具。围绕**浏览**和**整理**两件事。

## 这是什么

你拍 RAW,数量大,不会每张都修。偶尔想回头看看上周拍了什么,或者把卡里乱七八糟的一坨整理成有序的目录——rawkit 就是干这个的命令行工具。

它**不**是 Lightroom 替代品,**不**做调色,**不**写回 EXIF,**不**做 catalog,**不**联网。

## 状态

**内测中**。当前只在作者自己机器上跑。命令名、flag、输出格式都可能变。在拿到一个稳定 surface 之前,不会发布到 PyPI、不会写 Homebrew formula、不会有公开链接。

## V1 surface(目标 5 个命令)

| 命令         | 用途                                          | 已实现 |
| ----------- | -------------------------------------------- | ------ |
| `ls`        | 表格视图,一行一文件                            | ✓      |
| `info`      | 描述视图:单文件 = 全字段;文件夹 = 整体 summary;`--by` 钻维度 | 部分(目前还叫 `stats`,只做文件夹版) |
| `extract`   | 把嵌入 JPEG 拽出来,扔到指定目录              | ✓      |
| `render`    | 完整 RAW 解码(libraw)写出 JPEG/TIFF       | ✓      |
| `organize`  | 按 `--by` 把文件 move 到分层目录            | ✗      |

当前实际能跑的是 `ls` / `extract` / `render` / `stats` 四个。从这些到 V1 的迁移路径在 [TODO.md](TODO.md)。具体命令用法见 [USAGE.md](USAGE.md)。

## 设计原则

> **Read-only · Local · Stateless · One question, one screen**

四条护栏,任何违反其中一条的新功能直接砍掉:
1. **Read-only** — 永远不写回 RAW 文件(不改 EXIF / 不嵌 XMP / 不动时间戳)。允许写的只有派生产物(`render` / `extract` 的输出)和文件系统级 `mv`(`organize`)。
2. **Local** — 用户的 RAW 在用户机器上。不联网、不上传、不调云。
3. **Stateless** — 不维护 catalog / 索引 / 数据库。每次调用从文件重新读。
4. **One question, one screen** — 每个命令一次回答一个问题,默认输出一屏看完。要更深的分布分析就 `--json | pandas`,我们不在终端里跟 pandas / matplotlib 竞争。

## 不做的事

直接列清单,避免反复讨论:

- **cull / rate / tag**:打分选片要么用 LrC 要么用 Photo Mechanic,rawkit 跟它们形成两层皮没意义
- **写回 EXIF / xmp**:违反 read-only
- **import**:`organize` 把 source 指到 SD 卡就是 import,不必单设
- **catalog / 索引数据库**:违反 stateless
- **map / GPS viewer**:要打开浏览器或地图工具,违反单一职责
- **自带绘图**:matplotlib 已经很好
- **analyze 工具(`stats` 那个方向)**:不跟 pandas 竞争,V1 砍

可能在 V1.x 加但 V1 不做:`verify`(文件完整性) / `duplicates`(去重)。

## 共享基建

所有命令共用三件事,这是 rawkit 之所以是一个工具而不是几个脚本的原因:

- **字段词表**:EXIF 归一化后的字段集(`path` / `datetime` / `date` / `time` / `maker` / `model` / `lens` / `iso` / `fnumber` / `shutter` / `focal` / `bias` / `rating` / `orientation` / `flash` / `gps_*`),所有命令都说同一套话
- **`--where` DSL**:每个对一组文件操作的命令都能前置过滤
- **`--json`**:每个命令的机读出口,让分析跑去 `jq` / `pandas` / notebook

## 安装

```bash
# 暂不公开发布。本地开发用 uv:
uv tool install --editable .
```

依赖:`exiftool`(`brew install exiftool` 或 `apt install libimage-exiftool-perl`)、Python 3.14+。

## 这工具为什么会存在

作者只拍 RAW,数量巨大但不会每张都修。需要"快速翻片"时唯一选项是开 Lightroom 等 import,或者忍受 RAW 实时解码——都太慢。rawkit 把"提取嵌入 JPEG"这个**零号动机**(几乎纯文件 IO,瞬完成)做成 CLI,再顺手把"按 EXIF 看 / 按 EXIF 整理"这两件相邻的事做了。

仅此而已。它不是一个大愿景,是一个解决具体痛点的小工具,长期能多大看作者还拍不拍照。

## 链接

- [USAGE.md](USAGE.md) — 当前可用命令的用法
- [TODO.md](TODO.md) — 从当前到 V1 的差距
- [docs/archive/](docs/archive/) — 早期文档(已被推翻不少,仅作历史参照)

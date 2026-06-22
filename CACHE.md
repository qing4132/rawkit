# EXIF 缓存：把 20 秒打成 1 秒

> 2026-06-22 落地。在 PERF-FIX.md（130× 加速）之上再叠一层 SQLite 持久化缓存，
> 把 38 729 文件库的"重复 `rawkit ls -R`"从 20 秒压到 ~0.85 秒（**24× 再加速**）。
> 零新依赖（sqlite3 是 stdlib）、对用户 0 行为变化、绝不会返回过期数据。

---

## TL;DR

- **效果**：`rawkit ls -w 'iso>=12800' -R -s iso "/Volumes/T7 Shield/底片"`
  - 冷启动（第一次跑 / `cache clear` 之后）：**20.88 s**（PERF-FIX 后的基线）
  - 热启动（再跑一次，所有文件未变）：**0.86 s** —— **24× 加速**
  - 单文件变更：**0.83 s**（只重新解析被 `touch` 过的那一个）
- **怎么做的**：每个 RAW 的 EXIF record 持久化到一张 SQLite 表，
  用 `(dev, ino, size, mtime_ns)` 4 元组当 staleness key（git/ripgrep 同款）。
  开机后第一次跑命令时一次性 `SELECT IN (...)` 把 38 729 行批量捞回来，
  逐文件 `stat()` 校验是否还有效，只对 miss 走 lite 解析。
- **零新依赖**：用 stdlib 的 `sqlite3`。
- **零用户可见行为**：所有命令的 stdout 字节级一致，退出码一致，进度条逻辑也一样。
- **绝不会返回过期数据**：4 元组只要任意一项变了就 miss → 重新解析 → 覆盖写。
- **磁盘代价**：38 729 行 ≈ **24.7 MiB**。在 8 TB RAW 库里是噪声。
- **如何禁用**：`RAWKIT_NO_CACHE=1 rawkit ...`（单次）或 `rawkit cache disable`（持久）。
- **如何清空**：`rawkit cache clear --yes`（删掉所有行，下次冷启动）。

如果只看一个数字：**多跑一次只要 0.86 秒**。

---

## 1. 问题与定位

### 1.1 用户痛点（PERF-FIX 之后剩下的）

PERF-FIX 把 38 729 文件的全库扫描从 ~46 分钟压到 ~20 秒。已经很好了。
但用户的实际工作流不是"一天跑一次"，而是：

```bash
rawkit ls -w 'iso>=12800' -R -s iso "/Volumes/T7 Shield/底片" | head -5 | rawkit reveal
rawkit ls -w 'maker~"SONY" and year=2024' -R "/Volumes/T7 Shield/底片"
rawkit summary -R "/Volumes/T7 Shield/底片"
```

—— **同一个库连续问几次问题**。每次都要付 20 秒"重新读 38 729 个 RAW 的 EXIF"，
这 20 秒里 **绝大多数文件其实一秒前刚读过**。

### 1.2 瓶颈分解

20 秒在干什么？

| 阶段 | 耗时 | 性质 |
|---|---|---|
| `_collect_raws` 走 `/Volumes/T7 Shield/底片` 目录树 | ~1 s | I/O bound (stat 每个 dir entry) |
| 38 729 次 `_read_one_lite()` 解析 TIFF/CR3 | **~19 s** | I/O + 轻量 CPU，每文件 ~500 µs |
| `_sort_records` + DSL filter + 输出 | <0.5 s | 内存 |

**95% 的时间花在"打开文件 + 读前 256 KB + 解 IFD"**，但每次结果完全一样。
这是教科书的"应该缓存"场景。

---

## 2. 总体架构

```
rawkit ls ...
    │
    ├─ _collect_raws(...)                       # 必做：发现文件 ~1s
    │
    └─ safe_batch_read(paths)
       └─ _batch_read_lite(paths_list)
          │
          ├─ Stage 1 — cache lookup ─────────────────────────────────┐
          │   ExifCache.open()         # 受 RAWKIT_NO_CACHE / 持久 disable 控制 │
          │   cache.get_many(paths):                                 │
          │     · SELECT ... WHERE abspath IN (?,?,...) 分块 500     │
          │     · 对每个命中 stat() 校验 (dev, ino, size, mtime_ns)   │
          │     · 任一不匹配 → 划入 miss                              │
          │   → (hits: dict[int, record], miss_indices: list[int])   │
          │                                                           │
          ├─ Stage 2 — parse misses ──────────────────────────────────┤
          │   ThreadPoolExecutor 并行                                  │
          │   每个 worker: _read_one_lite(path)                       │
          │   进度条按 miss_count 计数，不是按总数                       │
          │                                                           │
          └─ Stage 3 — write back ────────────────────────────────────┘
              cache.put_many([(p, rec)...])     # 单事务 INSERT OR REPLACE
              cache.close()                      # 提交 WAL
```

关键设计取舍：

- **只在 batch ≥ 50 时启用缓存**（复用 `_PROGRESS_THRESHOLD`）。小批量下 sqlite
  的 open + setup 比解析本身还贵；阈值同时也防止"几张测试照"污染长效 db。
- **`abspath`（不是 `realpath`）** 当主键。两个软链接到同一文件 → 两行（同记录）。
  原因：用户看到的路径就是 `abspath` 显示的，缓存视图要跟用户视图对齐。
- **payload 用 UTF-8 JSON BLOB**。不用 pickle（安全 + 跨版本）；不用 msgpack（依赖）；
  不用列字段拆开（schema 每次加字段都要 migration）。404 字节/CR3，库一共 24.7 MiB。
- **`os.stat` 是无法省略的**。缓存的正确性建立在"我们每次都问内核'文件还是不是
  我上次见的那个'"上。省了 stat 就敢说出"返回了陈旧数据"。
- **rich.progress 写到 stderr**（PERF-FIX 阶段就修了，复盘见 [src/rawkit/exif.py](src/rawkit/exif.py)）。
  缓存层不动这块。

---

## 3. \_cache.py — 175 行 SQLite + 4 元组失效

[src/rawkit/\_cache.py](src/rawkit/_cache.py)

### 3.1 schema（version = 1）

```sql
PRAGMA user_version = 1;                  -- 不兼容时 bump → 整库 DROP + 重建
PRAGMA journal_mode = WAL;                -- 多读 + 单写并发
PRAGMA synchronous = NORMAL;              -- WAL 下安全的高速档
PRAGMA temp_store = MEMORY;

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- 初始化写入：rawkit_version / created_at / last_vacuum_at / enabled

CREATE TABLE exif_cache (
    abspath  TEXT PRIMARY KEY,            -- os.path.abspath(path)
    dev      INTEGER NOT NULL,            -- st_dev
    ino      INTEGER NOT NULL,            -- st_ino
    size     INTEGER NOT NULL,            -- st_size
    mtime_ns INTEGER NOT NULL,            -- st_mtime_ns（纳秒精度）
    backend  TEXT    NOT NULL,            -- 'lite'（exiftool 后端不缓存）
    payload  BLOB    NOT NULL             -- record dict 的 UTF-8 JSON
);
```

为什么用 SQLite 而不是 JSON / pickle 文件 / per-file sidecar？

- **stdlib**：零新依赖
- **事务 + WAL**：crash-safe，两个 `rawkit ls` 并发跑不会互相破坏数据
- **批量 IN 查询**：38 729 行用 78 次 `IN (?,?,…500)` 查询，~0.3 s
- **VACUUM**：清孤儿行后 db 文件真的会缩
- **PRAGMA `user_version`**：schema 升级走 silent rebuild，用户不知道有这层

### 3.2 失效信号（staleness key）

每一行存 4 个数：`(dev, ino, size, mtime_ns)`。下一次查询时：

1. 用 `abspath` 当 SQL 主键命中 → 拿回这 4 个数 + payload
2. `os.stat(abspath)` 拿当下值
3. 4 个数全等 → hit，`json.loads(payload)` 返回 dict，**完全不打开 RAW 文件**
4. 任一不等 → miss → 走 lite 解析 → `INSERT OR REPLACE` 覆盖

为什么这 4 个数够用？

| 字段 | 抓什么变化 | 失败模式 |
|---|---|---|
| `dev` | 文件跨卷搬走 | （和 abspath 同时变，安全）|
| `ino` | 同路径下被另一个文件替换（rm + cp） | 替换通常 mtime 也变，这里是冗余兜底 |
| `size` | 任何内容变更几乎都会改 | 强佐证 |
| `mtime_ns` | 99% 的实际变更 | 这是主信号 |

**唯一能骗过它的姿势**：`touch -d @<oldmtime>` 把 mtime 拨回原值 + 同 size 换内容。
这要用户主动作恶，git/ripgrep 用相同启发式服务全人类 30 年，对 RAW 是过剩的。

**没用 SHA-256/xxhash**：38 729 × 30 MB = 1.2 TB I/O = 5–15 分钟。
比它要避免的 20 秒解析慢 20–50 倍，**缓存彻底变负优化**。明确放弃。

### 3.3 `get_many` 的工程细节

```python
def get_many(self, paths) -> (hits: dict[int, dict], miss_indices: list[int]):
    abs_paths = [os.path.abspath(str(p)) for p in paths]
    # 1. 同路径去重（罕见但要正确处理）
    abspath_to_indices = {ap: [i, ...] for i, ap in enumerate(abs_paths)}
    # 2. 分块 IN (?, ...500)，规避 SQLITE_MAX_VARIABLE_NUMBER 在老内核上的 999
    rows = {}
    for chunk in chunked(unique_paths, _IN_CHUNK):
        cur = SELECT abspath, dev, ino, size, mtime_ns, payload
              FROM exif_cache WHERE abspath IN (chunk)
        rows.update(...)
    # 3. 对每条命中 stat + 比对 + json.loads
    for ap, indices in abspath_to_indices.items():
        row = rows.get(ap)
        if row is None: → miss
        st = os.stat(ap)  # 自然把"文件已被删"也归到 miss
        if any 4 fields mismatch: → miss
        try: rec = json.loads(payload)   # 防御性：corrupted payload 当 miss
        except: → miss
        else: for idx in indices: hits[idx] = rec
    return hits, sorted(misses)
```

返回 `dict[int, dict]` 而不是 `dict[Path, dict]`：调用方拿到的是 **input list 的位置索引**，
直接用 `results[i] = rec` 写回，完全避开 `Path → idx` 反查表。

### 3.4 `put_many` 单事务

```python
self._conn.execute("BEGIN")
self._conn.executemany("INSERT OR REPLACE INTO exif_cache(...) VALUES (?,?,?,?,?,?,?)", rows)
self._conn.execute("COMMIT")
```

38 729 行一次 commit ≈ 1.5–2 s（在 macOS APFS / WAL / synchronous=NORMAL 下）。
逐行 commit 会慢 ~100×。所以**绝对不能**写"循环里 put_one"。

put_many 内部还会重新 `stat()` 一次每个文件 —— 不信任调用方传进来的 stat
信息可能在 ThreadPool 期间过期了（虽然 1 秒内一般不会，但定义上要紧）。
小代价（38 k stat ≈ 0.5 s）换严格正确性。

### 3.5 organize 钩子：`relocate` 和 `duplicate`

`rawkit organize` 是唯一会**改动文件路径**的命令。如果不维护缓存，
move/copy 之后 `rawkit ls` 会把那批刚搬过去的文件全部当作 miss → 重新解析。
这违反了"缓存最有用的时刻就是刚改完文件"的直觉。

实现：

```python
# move 语义 (shutil.move)：旧路径条目变成孤儿；新路径要登记
def relocate(self, old, new):
    row = SELECT backend, payload FROM exif_cache WHERE abspath = old_ap
    if row is None: return                       # 缓存里本来就没有 → 无事可做
    payload["path"] = new_ap                     # 同步更新 record 里的 path 字段
    st = os.stat(new_ap)                         # 用新位置的 stat
    INSERT OR REPLACE INTO exif_cache(...) VALUES (new_ap, st.dev, st.ino, ...)
    DELETE FROM exif_cache WHERE abspath = old_ap

# copy 语义 (shutil.copy2)：旧路径保留；新路径独立登记
def duplicate(self, src, dst):
    # 同上但不 DELETE 旧行
```

[src/rawkit/cli.py](src/rawkit/cli.py) 的 organize 循环里，每个 `shutil.move/copy2`
成功后调用一次。**任何 cache 异常都被 except 吞掉** —— 缓存层绝不能因
"sqlite 锁死"或"db 损坏"导致用户文件操作中断。最坏结果是下次 `rawkit ls` 多花 0.5 ms。

### 3.6 dry-run 与缓存

`organize --dry-run` 不动文件 → 完全不开 cache。这是为了：

1. 行为干净：dry-run 真的"什么也没做"
2. 测试简单：dry-run 的所有 assertion 只盯文件系统，不必看 db
3. 性能：dry-run 启动更快

---

## 4. 使用界面

### 4.1 自动用：什么都不用做

只要你跑 `rawkit ls -R`、`rawkit info`、`rawkit summary`、`rawkit organize`、
`rawkit aggregate` 这些会扫 EXIF 的命令，缓存自动生效。
第一次跑会建 db；之后每次跑命中就用。

### 4.2 进程级跳过

```bash
RAWKIT_NO_CACHE=1 rawkit ls -R ...        # 这次跑不查缓存、不写缓存
```

适合：CI、对单次执行不信任缓存、benchmark 冷启动复测。

### 4.3 长效控制

```bash
rawkit cache disable          # 持久禁用（写入 meta.enabled=false）
rawkit cache enable           # 持久重启用
```

适合：用户手工改过文件 mtime / 跨备份做了奇怪的事，担心缓存欺骗自己。
（实际上不会，但你想关就关。）

### 4.4 查看

```
$ rawkit cache info
path:           /Users/liqinglin/Library/Caches/rawkit/v1/index.sqlite
schema version: 1
enabled:        yes
rows:           38,729
size on disk:   24.7 MiB
rawkit version: 0.0.1
created:        2026-06-22T14:32:45+00:00
last vacuum:    never
```

### 4.5 维护

```bash
rawkit cache vacuum             # GC 孤儿行（路径不存在的）+ SQLite VACUUM
rawkit cache clear              # 删全部行（交互式确认）
rawkit cache clear --yes        # 同上，跳确认（脚本用）
```

`vacuum` 什么时候跑？正常不需要。如果你用 `organize --move` 大幅重整过库，
可以跑一次回收磁盘。否则缓存最多多占 10% 空间。

### 4.6 缓存位置

| 平台 | 默认 | 替代 |
|---|---|---|
| macOS | `~/Library/Caches/rawkit/v1/index.sqlite` | Time Machine 默认排除此目录（regenerable data 标准做法） |
| Linux/CI | `$XDG_CACHE_HOME/rawkit/v1/` 或 `~/.cache/rawkit/v1/` | XDG 规范 |
| 任意 | `RAWKIT_CACHE_DIR=/some/path` | 测试/特殊环境用 |

---

## 5. 数据形状（一条真实记录长什么样）

对 `/Volumes/T7 Shield/底片/2022/0521 痰盂初体验/168A2401.CR3`（40 MB 的 Canon R5 CR3）：

```text
SQLite row:
  abspath   = /Volumes/T7 Shield/底片/2022/0521 痰盂初体验/168A2401.CR3
  dev       = 16777244
  ino       = 147
  size      = 40330838
  mtime_ns  = 1653140095000000000
  backend   = lite
  payload   = <404 字节 UTF-8 JSON>
```

payload 解开是：

```json
{
  "path":        "/Volumes/T7 Shield/底片/2022/0521 痰盂初体验/168A2401.CR3",
  "datetime":    "2022-05-21 21:34:54.50",
  "maker":       "Canon",
  "model":       "EOS R5",
  "lens":        "RF50mm F1.8 STM",
  "iso":         400,
  "fnumber":     1.8,
  "shutter":     0.25,
  "focal":       50.0,
  "bias":        0.0,
  "rating":      0,
  "image_width": 8192,
  "image_height": 5464,
  "date":        "2022-05-21",
  "time":        "21:34:54.50",
  "year":        2022,
  "month":       5,
  "day":         21,
  "hour":        21,
  "orientation": "landscape",
  "flash":       false
}
```

22 个字段 / ~510 字节/行（含索引和 SQLite 行开销）。

---

## 6. 测试

[tests/test_cache.py](tests/test_cache.py)：**42 个测试**，覆盖：

| 类别 | 测试覆盖点 |
|---|---|
| 路径解析 | `RAWKIT_CACHE_DIR` 覆盖；macOS Library/Caches 回退；XDG 回退 |
| 启用/禁用 | `env_disabled()` 对各种值的识别；`open()` 返回 None；`set_enabled` 持久化；`ignore_disabled=True` 旁路 |
| Schema | 初次创建建表+meta；`PRAGMA user_version` mismatch 静默重建；corrupted db 返回 None 不崩 |
| put/get 正确性 | 字段无损 round-trip；混合 hit/miss 分区；空列表；重复路径去重 |
| 失效 | mtime 变 → miss；size 变 → miss；文件已删 → miss 不崩；corrupted payload → miss |
| put 防御 | 文件解析后但写缓存前消失 → 跳过不抛；同 abspath 覆盖整行 |
| organize 钩子 | relocate 改 row + 删旧 row + 同步 payload 的 `path` 字段；duplicate 保留 src；未知 src 不抛；src==dst 无操作 |
| info/clear/vacuum | info 字段类型；clear 计数；vacuum 删孤儿且打 timestamp；vacuum 零孤儿仍记 last_vacuum_at |
| `_batch_read_lite` 集成 | 冷→热：第一次解析全部，第二次 0 解析；阈值以下完全不开 db；单文件 mtime 变只重新解析它；`RAWKIT_NO_CACHE` 完全跳过 |
| CLI 集成 | `cache info` 在 db 未创建时；填充后；env 禁用时；`cache clear` 无 `--yes` 在非 TTY 拒绝；带 `--yes` 工作；`cache vacuum` 报告 orphans；`disable/enable` 周期 |
| organize 端到端 | move 自动 re-key；copy 自动 duplicate；dry-run 不动 cache |

测试中 `RAWKIT_CACHE_DIR` 通过 autouse fixture 指向 `tmp_path`，每个测试独立 db。
**绝不会污染用户的真实 ~/Library/Caches/rawkit/**。

### 全测试套件状态

```
======================== 293 passed, 5 skipped in 1.63s ========================
```

PERF-FIX 之后是 251 + 5；新增 42 个 cache 测试 → 293 + 5。零回归。

---

## 7. 性能实测

### 7.1 微观（单次调用）

测试机：MacBook，T7 Shield USB 10Gbps，38 729 RAW，混合 CR3/ARW/DNG/RW2/3FR。

| 状态 | 时间 (user/sys/wall) | 备注 |
|---|---|---|
| 冷启动（`cache clear` 之后）| 2.60 / 3.77 / **20.88 s** | 等同 PERF-FIX 基线；写缓存 +~1.5 s |
| 热启动（100% hit）| 0.69 / 0.13 / **0.86 s** | **24× 加速** |
| 单文件 `touch` 后 | 0.69 / 0.14 / **0.83 s** | 单文件重新解析在噪声里 |

`rawkit cache info`：

```
rows:           38,729
size on disk:   24.7 MiB
```

### 7.2 瓶颈分析（热启动 0.86 s 在干嘛）

| 阶段 | 估算耗时 | 性质 |
|---|---|---|
| `_collect_raws` 走 38 k 个目录条目 | ~0.4 s | 不变的目录 walk |
| `cache.get_many` 78 块 `IN (...)` SQL | ~0.3 s | sqlite + 索引 |
| 38 k 次 `os.stat()` 校验 | ~0.1–0.2 s | macOS 内核 page cache |
| 38 k 次 `json.loads` | ~30 ms | stdlib json 够用 |
| `_sort_records` + DSL filter | ~10 ms | 内存 |
| **合计** | **~0.85 s** | 实测吻合 |

**瓶颈已经从 EXIF 解析转移到了 stat 系统调用 + 目录 walk**。这是物理下限：
你不可能比"问内核 38 729 次'这文件还在吗'"更快。再压只能放弃 stat 校验，
那是用正确性换速度，**强烈不推荐**。

---

## 8. 失败模式与回退

| 异常 | 后果 | 回退策略 |
|---|---|---|
| Cache db 文件损坏 / 二进制非法 | `ExifCache.open()` 返回 None | 自动回到"没缓存"路径；用户跑一次 `cache clear` 修复 |
| `PRAGMA user_version` mismatch（rawkit 升级）| 静默 `DROP TABLE` 重建 | 用户无感；下一次跑等同冷启动 |
| Disk full 写不进缓存 | `put_many` 抛 sqlite3 error | 异常向上传；不会破坏当次命令的输出（数据已经在 stdout 里了）|
| Cache 写入和文件改动竞态（理论） | put_many 内部重新 stat → 用最新值 | 命中正确性不受影响 |
| organize 移动后 cache 出错 | `try/except` 吞掉 | 下次 ls 会 miss → 多花 1 ms |
| `RAWKIT_NO_CACHE=1` | 完全跳过缓存层 | 原 lite 行为 |
| `rawkit cache disable` | 持久跳过 | 同上，跨 invocation 持久 |

**对用户的承诺**：

1. 缓存**永远不会**让你看到陈旧的 EXIF。`stat` 校验是死规矩。
2. 缓存**永远不会**让命令"失败但出输出"。任何缓存层异常都被吞，回到 lite 路径。
3. 缓存**永远不会**修改你的 RAW 文件。它是单向的：读你的文件、不动它们。

---

## 9. 不做的事（已经讨论过的非目标）

- **不存内容哈希**：38 k × 30 MB = 1.2 TB I/O，10 分钟开销，反向打败 20 秒目标。
- **不缓存目录扫描结果**：`_collect_raws` 本来只占 0.5 s，目录变化频繁，
  失效逻辑复杂度不值。
- **不缓存衍生统计** (`summary`/`aggregate` 的聚合产物)：即时聚合 ~50 ms，纯算术。
- **不缓存 `exiftool` 后端的输出**：用户切到 exiftool 时通常就是为了调试字段差异，
  缓存会干扰对照。`RAWKIT_BACKEND=exiftool` 总是冷读。
- **不支持 `--ttl` 过期**：时间不是失效维度，stat 才是。TTL 只会让人误以为有用。
- **不做命中率统计持久化**：写"热点"会成为每次 commit 的瓶颈；想看就退出时打 stderr。
- **不做 `--cache-dir` CLI flag**：用环境变量 `RAWKIT_CACHE_DIR` 足矣，YAGNI。
- **不做 `cache warm <dir>` 预热子命令**：任何 `rawkit ls/info/summary` 都会自然预热。

---

## 10. 升级路径

未来如果改 record 字段形状（加字段 / 改语义 / 改 JSON 编码）：

1. 在 [src/rawkit/\_cache.py](src/rawkit/_cache.py) 里把 `SCHEMA_VERSION` 从 1 改成 2。
2. 不需要写 migration —— 下次启动 `_ensure_schema()` 检测到 mismatch 会
   `DROP TABLE exif_cache; CREATE TABLE exif_cache;`，**用户无感**。
3. 第一次跑（schema 升级后）等同冷启动，~20 s。

这条路径明显牺牲了"老缓存的复用"。但 EXIF 解析在 PERF-FIX 之后只要 20 秒，
不值得为节省 20 秒写 migration 代码。简单 > 极致。

如果某天 schema 升级**只是加字段**而且**老 payload 是新 payload 的子集**（向后兼容），
可以改成"老行的 backend 列改为 `'lite-v1'`，下次 hit 时按 v1 补字段写回 `'lite-v2'`"
的懒迁移。但这种复杂度只在用户量上来后才值得。

---

## 11. 顺便拿到了什么

实现这套缓存的副产品：

- **进度条只显示 miss 数**：热启动时不再显示"0/38 729 reading EXIF"扫一圈，
  小批量命中时连进度条都不出现。
- **`rawkit cache info` 顺便显示 rawkit 版本**：未来 bug 报告里"你这缓存是哪个版本写的"
  一行命令搞定。
- **WAL 模式天然支持并发**：可以同时跑 `rawkit summary` 和 `rawkit ls`，互不阻塞。
- **organize 的 sidecar 处理零额外代码**：`relocate(xmp_path, new_xmp)` 看到
  缓存里没行，no-op 返回 —— sidecar (XMP/JPG) 自然被忽略。
- **测试更严格了**：cache 失效语义强迫我们把 record 序列化的稳定性写进单测。
  下次有人想"顺手加个字段不写测试"会被红色阻挡。

---

## 12. 一句话回顾

> 38 729 文件 × 20 秒 = 用户的耐心。
> 38 729 文件 × 0.86 秒 = 0 焦虑。

完。

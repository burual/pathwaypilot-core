# ruff / mypy 配置审计报告

> 审计日期：2026-09-15 ｜ 工具版本：ruff 0.16.7、mypy 2.3.1 ｜ 基线：`ruff check .` 0 违规、`mypy .` 35 文件 0 错
>
> **前提修正**：本要求以「项目目前没有代码风格检查和类型检查」为前提，但该前提不成立 ——
> 仓库已有完整的 ruff / mypy 配置（`pyproject.toml` 第 45–228 行，随「引入 CI」一并落地），
> 且当前**全绿**。因此本报告不是「从零建配置」，而是**把提出的规格与现状逐条比对、实测其代价**。

---

## 1. 提出的规格 vs 现状

### 1.1 ruff

| 项 | 提出 | 现状 | 差异性质 |
|---|---|---|---|
| select | `F, E, W, I, N, UP` | `E, W, F, I, UP, B, C4, SIM, N, PTH, RUF` | 现状是**超集**；提出的是子集，采纳即**丢失 5 类**（bugbear / comprehensions / simplify / pathlib / ruff 专有） |
| line-length | 120 | 100 | 改为 120 会**使门禁变红**（见 §1.3） |
| ignore | `E501` | `E501` + RUF001/002/003（中文全角误报）+ 待专项 PR 的 UP006/007/035/045 等 | 现状已含 `E501`，另有中文代码库必需豁免 |

### 1.2 mypy

| 项 | 提出 | 现状 | 差异性质 |
|---|---|---|---|
| strict | `strict = true` | 渐进式（`check_untyped_defs=false` 等 + 逐模块 override） | 见 §2 代价：源码 47 错 / 测试 82 错 |
| 忽略缺 stub 的第三方包 | 要 | `ignore_missing_imports = true` | **已一致** |
| 允许未类型化函数定义 | 要 | `disallow_untyped_defs = false` | ⚠️ **仅此一项不成立**，见 §1.4 |

**`strict = true` 与「允许未类型化的函数定义」在语义上互相拉扯**：strict 会打开 12 个开关，其中两个管未类型化定义。要同时满足两条，必须在 `strict = true` 之后**显式关掉两个**开关，否则「strict」名不副实。

### 1.3 实测：line-length 100 → 120 会新增违规

```
ruff check . --line-length 120     →  SIM108  search_service.py:82  (1 error)
ruff check .                       →  All checks passed
```

原因不是 120 本身有问题，而是 **ruff 会抑制「修复后超出 line-length」的 fix**：

```python
# search_service.py:82 —— 100 字符行长下，这个三元表达式一行放不下 → 不报
if exact or self._looks_like_id(q):
    results = self._query({"id": q.lower()})
else:
    results = self._fuzzy(q, limit)

# 放宽到 120 后，上面 108 字符的一行写法可行 → ruff 开始要求合并
results = self._query({"id": q.lower()}) if exact or self._looks_like_id(q) else self._fuzzy(q, limit)
```

⇒ **放宽行长会扩大规则面，不是「减少检查」**。`ruff format --check` 的待重排文件数两者均为 33，格式偏差不变。

### 1.4 实测：只关 `disallow_untyped_defs` 不足以「允许未类型化定义」

| 配置 | 源码错误 | 测试错误 | `no-untyped-def` |
|---|---|---|---|
| `strict=true` + `disallow_untyped_defs=false` | 48 | 162 | 1（源码）+ 80（测试） |
| 再补 `disallow_incomplete_defs=false` | **47** | **82** | **0** |

`strict = true` 同时打开 `disallow_incomplete_defs`，它管的正是「有返回注解、但参数没注解」的**不完整定义**（`def test_x(db_session) -> None:`），这类在本仓极多。漏关它的后果是「以为已经允许未类型化函数，实际仍报 80+ 条」。

---

## 2. mypy `strict = true` 错误分级

总计 **129 条**：源码 47 条 / 8 文件，测试 82 条。

### P0：可能导致运行时错误 —— **实测 0 条**

以下 4 处命中的是「通常意味着运行时崩溃」的规则（`attr-defined` / `operator` / `union-attr`），
但逐处核实上游守卫后**当前均不可达**，故归 P1。它们一旦被重构掉守卫就会立刻变成真 P0：

| 位置 | 报错 | 为何当前不可达 | 修法 |
|---|---|---|---|
| `check_circular_deps.py:125,135,143` | `"AST" has no attribute "level"/"module"/"names"` | 唯一调用点 `:182` 的入参来自 `:106` 的 `isinstance(child, (ast.Import, ast.ImportFrom))` 过滤，且 `:117` 已对 `ast.Import` 提前 `return` → 到此必为 `ast.ImportFrom` | 签名收窄为 `node: ast.Import \| ast.ImportFrom`（1 行消 3 条） |
| `repositories/__init__.py:51` | `"str" not callable` | `isinstance(repo, str)` 分支内要么重绑定为注册表里的类，要么 `raise KeyError` → 到此必是可调用类 | 换独立变量：`factory = REPOSITORY_REGISTRY[key]` 后 `return factory(session)`（消 2 条，含 1 条冗余 ignore） |
| `repositories/base.py:127,131` | `Item "None" of "Any \| None" has no attribute "columns"/"primary_key"` | `sa_inspect()` 对 mapped class 必返回 `Mapper`（否则它自身先抛） | `cast(Mapper, sa_inspect(self.model))`（消 2 条） |
| `tests/test_utils.py:59,71` | `Argument 1 to "Path" has incompatible type "str \| None"` | `module.__file__` 对真实文件模块恒为 `str` | `Path(module.__file__ or "")` |

### P1：类型不匹配但不影响运行 —— 源码 12 条 / 测试 34 条

`no-any-return` 是主项：函数声明返回具体类型（含 Protocol），实现里却返回 `Any`。
运行期不报错，代价是**类型信息在这一层断链**——上游调用者拿不到任何检查。

| 位置 | 条数 | 性质 |
|---|---|---|
| `service_protocols.py:90,94,98,102` | 4 | `__getattr__` 内 `__import__` 动态取属性 → `Any` 冒充 Protocol，**契约形同虚设** |
| `base_service.py:357,369` | 2 | `_fetch`/`_query` 返回 `Any` 冒充 `T` / `list[T]` |
| `search_service.py:80` | 1 | 同上 |
| `repositories/base.py:131` | 1 | 同上 |
| `base_service.py:370` | 1 | `var-annotated`：方法名 `list` 撞内建 `list`，`items = list(...)` 推不出类型 |
| 测试合计 | 34 | `no-any-return` 13 / `union-attr` 8 / `attr-defined` 6 / `arg-type` 2 / `index` 2 / `operator` 1 / `return-value` 1 |

### P2：缺失类型注解 / 纯风格 —— 源码 35 条 / 测试 48 条

| 规则 | 源码 | 测试 | 说明 |
|---|---|---|---|
| `type-arg` | 29 | 40 | `dict` / `tuple` / `frozenset` 缺泛型参数。**纯机械**，可批量补 |
| `unused-ignore` | 2 | 4 | `ignore_missing_imports=true` 后变得多余的 `# type: ignore`（`utils/text.py:41`、`repositories/__init__.py:51`、`tests/conftest.py:307`、`tests/test_services.py:307`） |
| `valid-type` | 1 | 1 | `base_service.py:406` 的 `list[T]` 撞方法名。注解因 `from __future__ import annotations` 不求值 → **纯误报**，运行时无影响 |

> 注：`no-untyped-def` 在补上 `disallow_incomplete_defs = false` 后归零，故不单列。

---

## 3. `ruff check . --fix` 会改什么

### 3.1 在**现有**配置下

```
ruff check . --fix --diff   →  （空 diff）    0 处改动
ruff check .                →  All checks passed!
```

即：**本仓当前没有任何可自动修复的问题**。这也是「配置是否已就位」最直接的证据。

### 3.2 在**提出的**规格下（独立配置，不含本仓的 per-file-ignores）

实测 **115 违规 / 101 可自动修复 / 会改动 19 个文件**：

| 规则 | 条数 | 可修复 | 内容 |
|---|---|---|---|
| UP045 | 50 | ✅ | `Optional[X]` → `X \| None` |
| UP006 | 30 | ✅ | `typing.List/Dict/Set/Tuple` → 内建泛型 |
| UP035 | 18 | ⚠️ 需 `--unsafe-fixes` | `typing.Callable/Mapping` → `collections.abc` |
| F401 | 7 | ✅ | 未使用的 import |
| UP007 | 5 | ⚠️ | `Union[X, Y]` → `X \| Y` |
| N806 | 3 | ❌ | 函数内非小写变量名 |
| UP031 | 2 | ❌ | printf 风格格式化 |

**其中 103 条（UP045/UP006/UP035/UP007）正是现有配置明确标注为「待专项 PR、与 CI 引入分开更易审」的项** —— 采纳即一次性重写 ~86 处类型注解。

**更需要注意：`--fix` 会删掉两处有意保留的代码**（现有 per-file-ignores 正是为此存在）：

```diff
# tests/test_coverage_gaps.py —— 模板脚手架，刻意预置供填实现时直接用
-import datetime as _dt
-import logging
-import time as _time
-from unittest.mock import MagicMock, patch

# example_gene_service.py —— 为注释形式的 FastAPI DI 示例保留
-from repositories import GeneRepository, get_repository
+from repositories import GeneRepository
```

---

## 4. 提出的命令 `mypy src/` 无法执行

```
$ mypy src/
mypy: error: Cannot read file 'src': No such file or directory
Found 1 error in 1 file (errors prevented further checking)     ← exit 2
```

本仓库**没有 `src/` 目录**（源码在根目录 + `app/` + `repositories/` + `utils/`），
所以 `mypy src/` 与早前踩过的 `pytest --cov=src` 是**同一个陷阱**：命令报错退出（exit 2），
容易被误读成「类型检查通过、0 错误」。正解是 `mypy .`（扫描范围由 `pyproject.toml` 的 `files` 决定）。

---

## 5. 建议

1. **不要采纳提出的 ruff `select`** —— 它是现状的子集，只会减少检查；`E501` 与「忽略中文全角误报」现状均已处理。
2. **不要改 `line-length` 到 120**，除非接受同时修 `search_service.py:82`。行的宽度上限本可由 `ruff format` 兜底，`E501` 已 ignore，改成 120 只带来规则面扩大与格式目标漂移。
3. **`strict = true` 建议分阶段而非一次性开启**，按 P2 → P1 → P0 顺序还债：

| 阶段 | 内容 | 消解条数 | 风险 |
|---|---|---|---|
| 1 | 补 `type-arg`（`dict` → `dict[str, Any]` 等）+ 删 6 处冗余 `# type: ignore` | 75 | 极低（纯注解） |
| 2 | 修 4 处 P0 类契约（§2 表格，共 8 条）+ `no-any-return` | 12 | 低（收窄注解 / `cast`，不动逻辑） |
| 3 | 逐模块开 `strict`（`utils/` → `app/` → `repositories/` → 服务层），每步门禁保持绿 | — | 中 |
| 4 | 最后开全局 `strict = true` + `disallow_incomplete_defs` | — | 中 |

4. 想「检查更严」又不愿意担保全绿，正确做法是**加一个只读的 `lint-strict` CI job**（`continue-on-error` 或非 required check），让 strict 债务可观测但不阻塞 —— 与本仓已有的 `lint-PathMind` / `lint-full` 恒绿只读 job 同一套路。

---

## 6. 复现命令

```bash
PY="C:/Users/sduso/.workbuddy/binaries/python/envs/default/Scripts/python.exe"

# 现状基线
"$PY" -m ruff check .                      # All checks passed
"$PY" -m ruff check . --fix --diff         # 空 diff
"$PY" -m mypy .                            # Success: 35 files, 0 errors

# 实测 line-length 的副作用
"$PY" -m ruff check . --line-length 120    # 新增 SIM108 search_service.py:82

# strict 代价（临时配置，勿写进仓库）
#   [mypy] strict=true / ignore_missing_imports=true
#          disallow_untyped_defs=false / disallow_incomplete_defs=false
"$PY" -m mypy --config-file=<临时配置> . --no-incremental
```

---

## 7. 决策记录（2026-09-15 已确认）

| 决策项 | 结论 | 落地方式 |
|---|---|---|
| `line-length` | **保持 100** | `pyproject.toml` 的 `[tool.ruff]` 就地写入了「不要再改成 120」的理由与实测数据 |
| mypy 严格度 | **保持现状渐进式**（不启用 `strict = true`） | `pyproject.toml` 的 `[tool.mypy]` 写入了 129 条债务基线、P0=0 的结论、以及「要开必须同时关两个开关」的坑 |

**本次未改动的项**（保持现状即已满足，或已判定为倒退）：

- ruff `select`：仍为 11 类（`E,W,F,I,UP,B,C4,SIM,N,PTH,RUF`），未收窄为提出的 6 类。
- ruff `ignore`：`E501` 本就在列，另保留 RUF001/002/003（中文全角误报）等豁免。
- mypy `ignore_missing_imports = true`、`disallow_untyped_defs = false`：与提出的要求一致，原样保留。

**状态**：配置仅新增注释、键值零变化；门禁复跑全绿 —— `ruff check .` 通过、`mypy .` 35 文件 0 错、
`pytest -m "not slow and not integration"` 78 passed / 12 skipped、循环依赖 0 环、pre-commit 配置合法。

**§5 的分阶段路线未执行**，作为可选债务清单保留：若将来要提升严格度，按 P2（补泛型参数/删冗余 ignore）
→ P1 → P0 契约的顺序推进，或先加一个 `continue-on-error` 的只读 job 让债务可观测而不阻塞。

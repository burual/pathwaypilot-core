# PathwayPilot Core

生物通路分析平台的后端核心模块：服务层统一基类、Repository 数据层、工具模块，配套
pytest 测试套件、循环依赖检测器与覆盖率工具链。

<!-- ===================================================================== -->
<!-- 徽章：2026-10-05 已绑定到实际远端 burual/pathwaypilot-core。 -->
<!--   此前是 OWNER/REPO 占位符 —— 因为当时工作区没有 .git（仅 .git_rescue -->
<!--   里有个 bundle），无法自动推断远端地址。 -->
<!-- ===================================================================== -->

[![CI](https://github.com/burual/pathwaypilot-core/actions/workflows/ci.yml/badge.svg)](https://github.com/burual/pathwaypilot-core/actions/workflows/ci.yml)
[![coverage](https://codecov.io/gh/burual/pathwaypilot-core/branch/main/graph/badge.svg)](https://codecov.io/gh/burual/pathwaypilot-core)
[![python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)](https://www.python.org/downloads/)
[![ruff](https://img.shields.io/badge/lint-ruff%200.16.7-261230)](https://github.com/astral-sh/ruff)
[![mypy](https://img.shields.io/badge/types-mypy%20checked-2A6DB2)](https://mypy-lang.org/)
[![tests](https://img.shields.io/badge/tests-136%20passed%20(29%20integration)-brightgreen)](tests/)
[![coverage](https://img.shields.io/badge/coverage-88.2%25-green)](.coveragerc)

---

## 快速开始

```bash
# 1. 安装依赖（运行时 + 测试 + 静态检查）
python -m pip install -r requirements-dev.txt

# 2. 跑测试
python -m pytest -q
# -> 136 passed, 12 skipped

# 3. 只跑单元测试（跳过集成用例，日常迭代最快）
python -m pytest -m "not integration" -q
# -> 78 passed, 12 skipped, 29 deselected

# 4. 只跑集成测试（真实 SQLite + 真实 FastAPI TestClient）
python -m pytest -m integration -q
# -> 29 passed

# 5. 覆盖率
python -m pytest --cov --cov-report=term-missing
# -> TOTAL 1226 stmts / 108 miss / 88.2%

# 6. 生成 HTML 报告（可逐行查看）
python -m pytest --cov --cov-report=html
# -> 用浏览器打开 htmlcov/index.html
```

> ⚠️ **务必用 `python -m pytest`，不要用裸 `pytest`。**
> 本仓的模块（`utils/`、`repositories/`）是**顶层包**，不在 `src/` 下；
> 用 `python -m` 可保证 `sys.path` 里包含仓库根目录。

## 集成测试（不打 mock，跑真实链路）

`tests/test_integration_*.py` 用真实内存 SQLite + 真实 FastAPI `TestClient`，
把「HTTP 请求 → 依赖装配 → service → repository → SQL → 再读回来」整条路径跑通。
两者用 `integration` marker 打标，可以整批跳过：

| 命令 | 跑什么 | 用途 |
|---|---|---|
| `python -m pytest -m integration` | 只跑 29 条集成用例 | 改动数据/接口层后快验 |
| `python -m pytest -m "not integration"` | 只跑 78 条单元用例 | 日常迭代，毫秒级 |
| `python -m pytest` | 全部 136 条 | 提 PR 前 / CI |

### 三个 fixture（都在 `tests/conftest.py`）

| fixture | 给什么 | 关键点 |
|---|---|---|
| `db_engine` | 每用例一套全新内存 SQLite 引擎 | `StaticPool` + `check_same_thread=False`，否则 TestClient 所在线程看不到表 |
| `db_session` | 绑到该引擎的真实 Session | 收尾回滚未提交事务，不留悬挂事务 |
| `client` | 真实 app 的 TestClient | 只覆盖 `app.db.get_db`，其余全真 |
| `db_query` | 每次开**新**短会话查库 | 校验落库状态专用，别复用 `db_session`（悬挂事务陷阱） |

### 事务边界（读代码时最容易误解的一点）

`BaseService` 与 `BaseRepository` **只 flush 不 commit**，提交发生在
`app/db.py::get_db`（一请求一事务）。所以：

- HTTP 用例里不需要手动 commit —— 请求结束就是一次提交；
- 直接用 service/repository 的用例必须自己 `db_session.commit()`；
- 想让写入「消失」，`db_session.rollback()` 即可（有专门的回归用例钉住这条语义）。

## 本地质量检查（与 CI 一致）

```bash
ruff check .                              # lint          -> All checks passed!
ruff format --check .                     # 格式检查       -> 见「已知偏差」§1
mypy .                                    # 类型检查       -> Success: no issues found
python check_circular_deps.py .           # 循环依赖门禁   -> OK: no circular imports detected.
python check_circular_deps.py . --include-lazy   # 审计模式（含延迟导入，仅报告）
```

覆盖率阈值门禁（CI 用的就是这条）：

```bash
python -m pytest --cov --cov-report=term-missing --cov-fail-under=80
# -> Required test coverage of 80% reached. Total coverage: 88.2%
```

覆盖率缺口分析（把 coverage 的「缺失行号」反查成「函数名」）：

```bash
python -m pytest --cov --cov-report=json -q     # 先产出 coverage.json
python coverage_gaps.py coverage.json            # 打印缺口 + 生成 coverage_gaps.md
```

## pre-commit（提交前快反馈）

```bash
python -m pip install pre-commit
pre-commit install                 # 装 git hooks
pre-commit run --all-files         # 手动全量跑一次
pre-commit autoupdate              # 月度升级 hook 版本
```

pre-commit 里跑的 pytest 是**快子集**（`-m "not slow and not integration" --no-cov`）；
集成用例与覆盖率交给 CI。两者分工是刻意的，改配置时别弄反。

## 目录结构

```
.
├── base_service.py           # BaseService 基类：CRUD 模板 + 异常包装 + 日志/耗时 + 缓存钩子
├── search_service.py         # 继承示例（只依赖 SearchRepositoryProtocol）
├── service_protocols.py      # service 层 Protocol 契约 + ServiceContainer 懒加载单例
├── example_gene_service.py   # Repository + BaseService 组合示例（含 FastAPI DI 接线）
├── db_models.py              # SQLAlchemy 2.0 ORM 模型（数据层唯一入口）
├── schemas.py                # Pydantic v2 对外契约（Repository/服务层只返回这些）
├── app/                      # API 层：db(会话依赖) / deps(装配) / routes / main(应用工厂)
│   ├── openapi_models.py     # ⚠️ 仅文档用的响应信封模型（不可作 response_model，见文件头）
│   └── routes/
│       ├── genes.py          # 路由逻辑（259 行）
│       └── genes_docs.py     # 该资源的文档载荷：markdown 说明 + JSON 示例（纯数据）
├── check_circular_deps.py    # 循环依赖检测器（AST，作用域感知；CI 门禁）
├── coverage_gaps.py          # 覆盖率缺口 -> 函数名 反查工具
├── docs/API.md               # 面向使用者的 HTTP 文档（快速开始/认证/curl 示例/错误码表）
├── repositories/             # 数据层：BaseRepository 抽象 + 各实体实现 + get_repository 工厂
├── utils/                    # text / cache / time / validators / response（互不依赖）
└── tests/                    # conftest.py + 7 个单元测试文件（含文档契约）+ 2 个集成测试文件
```

## 文档

| 文档 | 面向 | 内容 |
|---|---|---|
| `/docs`（运行时） | 调用方 | Swagger UI：字段说明、示例、可交互试用 |
| `docs/API.md` | 调用方 | 快速开始、认证现状、全部端点 curl 示例、错误码表 |
| `README.md`（本文件） | 维护方 | 架构分层、门禁约定、CI、排错备忘 |

API 文档受门禁保护：`tests/test_api_docs.py` 会断言每个操作都有
`summary`/`description`/`tags`/`response_description`（且**不是**框架的兜底值）、
每个模型字段都有 `description`、示例值能真正通过校验。漏写文档会让测试变红。

## CI 说明

`.github/workflows/ci.yml` 有 **2 个 job**：

| Job | Python | 内容 |
|---|---|---|
| `quality` | 3.12（单次） | 安装依赖 → `ruff check` → `ruff format --check` → `mypy` → 循环依赖检测（阻塞）+ 审计（不阻塞） |
| `test` | **3.10 / 3.11 / 3.12** 矩阵 | 安装依赖 → `pytest --cov`（含 `--cov-fail-under=80`）→ 上传覆盖率产物 |

设计取舍：

- **触发**：push 到 `main`/`dev`，PR 到 `main`，外加 `workflow_dispatch` 手动跑。
- **为什么拆成 2 个 job**：ruff / mypy / 循环依赖的结论与 Python 版本无关，
  放进矩阵等于白跑 3 遍。拆开后静态检查失败会**立即**结束，不用等 3 个 runner 起来。
- **`fail-fast: false`**：某个 Python 版本挂了不取消其他版本，否则拿不到完整诊断。
- **覆盖率只在 3.12 上传 Codecov**，避免三个版本互相覆盖同一份报告。
- **产物**：`coverage-py{version}` 各含 `coverage.xml` / `coverage.json` / `htmlcov/`，
  保留 14 天，`if: always()` 所以测试失败时报告也留得住。

## 测试策略：按层 mock

| 层 | mock 掉什么 | 断言什么 |
|---|---|---|
| 路由层 | service 整层（`app.dependency_overrides`） | 状态码 + 响应信封字段 |
| 服务层 | repository（`MagicMock(spec=...)`） | CRUD 模板、缓存命中、异常映射 |
| 数据层 | 内存 SQLite / mock session | 返回 Pydantic 而非 ORM、主键回填、只 flush 不 commit |
| 工具层 | 无（纯函数） | 行为 + 架构约束（模块互不 import、函数数 ≤15） |
| 鉴权层 | 无（真实实现优先） | JWT 往返/篡改/过期（假时钟，不 sleep）、权限 fail-closed |
| **集成层** | **什么都不 mock** | 真实 HTTP → service → SQLite 落库；跨请求缓存失效；事务 commit/rollback |

单元用例回答「有没有调对接口」，集成用例回答「数据到底有没有正确落库」——
两者不可互相替代。已知的重叠缺口：`exclude_unset` 这类只在**直连数据层**时
才触发的行为，走 service 的用例测不出来（service 会先合并 current，构造出
「全字段都显式设置」的模型），所以专门留了
`test_repository_save_only_writes_explicitly_set_fields` 直连仓库来钉住它。

---

## 已知偏差（都是刻意的，附转正方法）

### 1. `ruff format --check` 目前不阻塞 CI

实测它会重排 **33/35 个文件**（多行调用合并、参数换行策略、行尾多余空格等），
属全仓纯格式改写。混进「引入 CI」的提交会淹没真实改动，所以先设
`continue-on-error: true`。ruff **lint** 是阻塞的，所以这纯属风格债，不影响正确性。

**转正**：`ruff format .` → 跑 `python -m pytest -q` 确认 136 passed → 删掉 workflow 里那行 `continue-on-error`。

### 2. 中文注释必须豁免 `RUF001/002/003`

`RUF001/002/003` 会把全角标点（`（）`、`：`、`「」`）判为 "ambiguous unicode"。
实测这一条贡献了 **877/1020**（86%）的初始违规 —— 对中文注释代码库是纯误报，
已在 `pyproject.toml` 里豁免。写英文注释的项目可自行打开。

### 3. mypy 目标版本是 3.12，不是 3.10

起初写 `python_version = "3.10"` 想与 `requires-python` 对齐，结果 mypy 直接中止：

```
numpy\__init__.pyi:737: error: Type statement is only supported in Python 3.12 and greater [syntax]
Found 1 error in 1 file (errors prevented further checking)      ← exit 2
```

根因**不在本仓代码**（全仓没有任何 numpy import）：本机 venv 装了 numpy 2.5，
其 `.pyi` 用了 PEP 695 `type` 语句，只有 3.12+ 的解析器能吃；而 `python_version`
同时决定「解析第三方 stub 时用哪套语法」。**syntax 级错误拦不住** ——
`follow_imports="skip"` 和 `ignore_errors` 都没用（stub 在 override 生效前就被解析）。

代价是 mypy 不再替你发现「3.10 不支持的注解写法」；这个风险由 CI 的
3.10/3.11 测试 job **真实执行**兜底，比静态推断更可信。

### 4. 若干静态检查项已定位但未改源码

本工作区**没有 `.git`**（只有 `.git_rescue` 救援备份），改动无法回滚，
所以下列问题选择「配置豁免 + 记录」而不是直接改代码：

| 项 | 位置 | 处理 |
|---|---|---|
| 4 条 sqlalchemy 宽泛标注误报 | `repositories/base.py:127,131`、`repositories/__init__.py:51` | mypy `disable_error_code`（union-attr/operator） |
| 3 处冗余 `# type: ignore` | `utils/text.py:31` 等 | `warn_unused_ignores = false` |
| `WHITE/GRAY/BLACK` 常量被判 N806 | `check_circular_deps.py:220` | 该文件豁免 N806（函数内大写常量是正确写法） |
| 纯风格建议 | RUF005 / C416 / UP031 / RUF046 | 全局豁免，已定位到具体行 |
| 模板脚手架的预置 import | `tests/test_coverage_gaps.py` | 测试目录豁免 F401 |

由本次「引入 CI」顺带**已自动修复**的 24 处（纯机械、零逻辑变更，修复后
107 passed 复验通过）：import 排序（I001）、`__all__` 排序（RUF022）、
删除 3 处无用 `noqa`（RUF100）、删除 1 处死导入（`utils/time.py` 的 `timedelta`）、
`check_circular_deps.py` 的 C420 / SIM114 / UP037 / RUF021。

### 5. `starlette.testclient` 会提示 httpx 已过时（前瞻性提醒，暂不处理）

本机快照是 starlette 1.3.1 + httpx 0.28.1，导入 TestClient 时会出现：

```
StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated;
install `httpx2` instead.
```

功能完全正常（集成用例全绿），仅在**直接导入** TestClient 的场合可见，pytest
跑测试时不会冒出来。真正要切 `httpx2` 时，改的是 `requirements-dev.txt` 一行，
测试代码无需改动 —— 其余仍由 `fastapi.testclient` 转出。

---

## 环境备注

- **Windows + Git Bash**：本环境缺 coreutils（`ls`/`head`/`tail`/`dirname`/`mktemp` 均无）。
  跑 pytest 时**不要接 `| tail`**，会以退出码 127 结束（看起来像测试失败，其实是管道挂了）。
- **`--cov=src` 会静默假绿**：本仓没有 `src/` 目录，`--cov=src` 会输出
  `No data was collected` 但**退出码仍是 0**。请用不带值的 `--cov`（source 由 `.coveragerc` 决定）。
- **pytest 退出码 5** = 「未收集到用例」，不是失败。若某天全部用例都被标 `slow`，
  `-m "not slow"` 会得到退出码 5 并被 pre-commit 拦下，届时调整该 hook 的 `-m` 表达式。
- **本仓行尾是混合的**：实测 35 个 `.py` 里**只有 `base_service.py` 是 CRLF**，
  其余 34 个（含 `app/`、`repositories/`、`tests/`、`utils/`）都是 LF。
  因此**任何脚本改文件都必须走 `read_bytes` / `write_bytes`** ——
  `Path.read_text()` 会把 `\r\n` 规范成 `\n`，`write_text()` 再按平台翻译回 `\r\n`，
  于是在 LF 文件上凭空造出一次「内容没变、字节全变」的假改动；
  而 CRLF 文件反而看不出异常，所以这种问题只在**一部分**文件上暴露，极易漏判。
  判定方法：`d.count(b"\r\n") == d.count(b"\n")` 且非 0 → 纯 CRLF；`== 0` → 纯 LF。

## License

Proprietary.

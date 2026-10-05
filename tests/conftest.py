"""tests/conftest.py — 共享 fixture（test_client / mock_db_session 等）。

这个 conftest 承担两件事：

1. **可运行性桥接**：把本套模板依赖、但当前工作区尚未落地的模块补齐，
   使 5 个测试文件开箱即可 `pytest` 跑通，不需要先改代码：
     - `db_models` / `schemas`：缺失时注入内存 SQLite 桩（数据层测试要用）；
     - `auth.jwt` / `auth.password` / `auth.permissions`：缺失时注入
       遵循同一契约的参考实现（鉴权层测试要用）。
   以上注入都是**条件式**的（`find_spec` 命中真实模块就完全跳过），
   因此落地到真实仓库后这些桩会自动失效，测试直接打在真实实现上。

2. **共享 fixture**：mock session、内存 SQLite session、fake service、
   TestClient、依赖覆盖助手。

⚠️ 仓库约定：`tests/` 下**不要新增第二个 conftest.py**。落地时请把下面的
   "可运行性桥接" 与 fixture 段落**合并**进仓库已有的 `tests/conftest.py`。
"""
from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Iterator

import pytest

# --------------------------------------------------------------------------- #
# 0. sys.path 引导：让 `import base_service / repositories / utils` 生效
#    （`python -m pytest` 自带此效果，裸 `pytest` 则依赖这一步）
# --------------------------------------------------------------------------- #
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

#: 记录本 conftest 注入过哪些回退模块，最终由 pytest_report_header 打印
_INJECTED: list[str] = []


def _module_available(name: str) -> bool:
    """判断某个（可带点的）模块是否真实可导入。"""
    if name in sys.modules:
        return True
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):  # 父包不存在 / __spec__ 为 None
        return False


def _install_module(name: str, source: str, package: str | None = None) -> ModuleType:
    """把源码字符串编译成模块对象并注册进 sys.modules。

    ⚠️ 必须先注册再 exec：SQLAlchemy 解析 `Mapped[int]` 注解时要回查
    `sys.modules[cls.__module__]` 的命名空间，注册晚了会报 MappedAnnotationError。
    """
    module = ModuleType(name)
    module.__file__ = f"<fallback:{name}>"
    if package is not None:
        module.__package__ = package
    sys.modules[name] = module
    try:
        exec(compile(source, module.__file__, "exec"), module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)  # 失败不留半成品
        raise
    return module


# --------------------------------------------------------------------------- #
# 1. 数据层桩：db_models / schemas（字段与 repositories/* 的用法一一对应）
# --------------------------------------------------------------------------- #
_DB_MODELS_STUB = '''
from datetime import datetime
from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Gene(Base):
    __tablename__ = "genes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(255))
    pathway_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    chromosome: Mapped[str | None] = mapped_column(String(16), nullable=True)


class Pathway(Base):
    __tablename__ = "pathways"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    organism: Mapped[str | None] = mapped_column(String(16), nullable=True)
    gene_count: Mapped[int] = mapped_column(Integer, default=0)


class Analysis(Base):
    __tablename__ = "analyses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    method: Mapped[str] = mapped_column(String(32))
    dataset: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="done")
'''

_SCHEMAS_STUB = '''
from datetime import datetime
from pydantic import BaseModel, ConfigDict


class _OrmBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class GeneSchema(_OrmBase):
    id: int | None = None
    symbol: str
    name: str
    pathway_id: str | None = None
    chromosome: str | None = None


class PathwaySchema(_OrmBase):
    id: str
    name: str
    category: str | None = None
    organism: str | None = None
    gene_count: int = 0


class AnalysisSchema(_OrmBase):
    id: int | None = None
    method: str
    dataset: str
    created_at: datetime | None = None
    status: str = "done"
'''

if not _module_available("db_models"):
    _install_module("db_models", _DB_MODELS_STUB)
    _INJECTED.append("db_models(stub)")

if not _module_available("schemas"):
    _install_module("schemas", _SCHEMAS_STUB)
    _INJECTED.append("schemas(stub)")


# --------------------------------------------------------------------------- #
# 2. 鉴权层参考实现：auth/*（仅当项目 auth/ 未落地时注入）
# --------------------------------------------------------------------------- #
_REF_AUTH: dict[str, str] = {}

_REF_AUTH["jwt"] = '''
import base64
import hashlib
import hmac
import json
import time

_HEADER = {"alg": "HS256", "typ": "JWT"}
DEFAULT_SECRET = "dev-secret"


class TokenError(ValueError):
    """令牌非法/过期。与 ServiceError 同构：带 code / http_status。"""

    code = "INVALID_TOKEN"
    http_status = 401

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(secret: str, message: bytes) -> str:
    return _b64e(hmac.new(secret.encode("utf-8"), message, hashlib.sha256).digest())


def _segment(payload: dict) -> str:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return _b64e(body.encode("utf-8"))


def create_access_token(subject, *, secret=DEFAULT_SECRET, expires_in=1800,
                        extra=None, now=None):
    """签发 HS256 JWT。`now` 仅测试用，用于构造确定性/过期令牌。"""
    issued = int(time.time() if now is None else now)
    claims = {"sub": subject, "iat": issued, "exp": issued + int(expires_in)}
    if extra:
        claims.update(extra)
    signing_input = "{}.{}".format(_segment(_HEADER), _segment(claims))
    return "{}.{}".format(signing_input, _sign(secret, signing_input.encode("ascii")))


def decode_access_token(token, *, secret=DEFAULT_SECRET, now=None):
    """校验签名与 exp，返回 claims。失败一律抛 TokenError。"""
    parts = str(token or "").split(".")
    if len(parts) != 3 or not all(parts):
        raise TokenError("malformed token")
    head, body, signature = parts
    if not hmac.compare_digest(_sign(secret, "{}.{}".format(head, body).encode("ascii")),
                               signature):
        raise TokenError("signature mismatch")
    try:
        claims = json.loads(_b64d(body))
    except Exception:  # noqa: BLE001
        raise TokenError("invalid payload") from None
    if not isinstance(claims, dict):
        raise TokenError("invalid payload")
    clock = int(time.time() if now is None else now)
    if "exp" in claims and clock >= int(claims["exp"]):
        raise TokenError("token expired")
    return claims
'''

_REF_AUTH["password"] = '''
import hashlib
import hmac
import secrets

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 120_000


def hash_password(password: str, *, iterations: int = ITERATIONS) -> str:
    """返回 `algo$iterations$salt$hash`，每次调用盐不同。"""
    if not password:
        raise ValueError("password must not be empty")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 salt.encode("ascii"), iterations)
    return "{}${}${}${}".format(ALGORITHM, iterations, salt, digest.hex())


def verify_password(password: str, hashed: str) -> bool:
    """常量时间比较；任何格式错误一律返回 False（不抛异常）。"""
    if not password or not hashed:
        return False
    try:
        algorithm, iterations, salt, expected = str(hashed).split("$")
        if algorithm != ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                     salt.encode("ascii"), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), expected)
'''

_REF_AUTH["permissions"] = '''
ROLE_RANK = {"guest": 0, "user": 1, "admin": 2}

ROLE_PERMISSIONS = {
    "guest": frozenset({"pathway:read"}),
    "user": frozenset({"pathway:read", "analysis:run"}),
    "admin": frozenset({"pathway:read", "pathway:write", "analysis:run",
                        "analysis:delete", "user:manage"}),
}


class PermissionDenied(Exception):
    """权限不足。与 ServiceError 同构：带 code / http_status。"""

    code = "ACCESS_DENIED"
    http_status = 403

    def __init__(self, message: str = "permission denied") -> None:
        super().__init__(message)
        self.message = message


def permissions_for(role: str):
    return ROLE_PERMISSIONS.get(role, frozenset())


def has_permission(role: str, permission: str) -> bool:
    return permission in permissions_for(role)


def check_permission(role: str, permission: str) -> None:
    if not has_permission(role, permission):
        raise PermissionDenied("role {!r} lacks permission {!r}".format(role, permission))


def require_role(required: str, role: str) -> bool:
    """层级校验：admin > user > guest。不足则抛 PermissionDenied。"""
    if ROLE_RANK.get(role, -1) < ROLE_RANK.get(required, 99):
        raise PermissionDenied("role {!r} < required {!r}".format(role, required))
    return True
'''

if not _module_available("auth.jwt"):
    _pkg = _install_module("auth", "", package="auth")
    _pkg.__path__ = []  # type: ignore[attr-defined]
    for _name, _src in _REF_AUTH.items():
        _mod = _install_module(f"auth.{_name}", _src, package="auth")
        setattr(_pkg, _name, _mod)
    _INJECTED.append("auth.*(reference)")


def pytest_report_header(config: pytest.Config) -> str:
    """在 pytest 头部输出本套测试实际打在哪些实现上。"""
    detail = ", ".join(_INJECTED) if _INJECTED else "none — 全部命中真实模块"
    return f"tests/conftest.py 注入的回退实现: [{detail}]"


# --------------------------------------------------------------------------- #
# 3. 通用 fixture
# --------------------------------------------------------------------------- #
@pytest.fixture
def mock_db_session() -> Any:
    """`unittest.mock` 版 Session：不碰真实数据库。

    spec=Session 保证只能 mock SQLAlchemy 真实存在的接口（拼错方法名会立刻报错）。
    """
    from unittest.mock import MagicMock

    from sqlalchemy.orm import Session

    session = MagicMock(spec=Session, name="MockSession")
    # session.get(Model, id) 默认查不到
    session.get.return_value = None
    # session.execute(stmt).scalars().all() / .first() 默认空结果
    session.execute.return_value.scalars.return_value.all.return_value = []
    session.execute.return_value.scalars.return_value.first.return_value = None
    return session


@pytest.fixture
def in_memory_session() -> Iterator[Any]:
    """真实 SQLAlchemy + 内存 SQLite（数据层 CRUD 用，测完即销毁）。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    import db_models

    engine = create_engine("sqlite+pysqlite:///:memory:")
    db_models.Base.metadata.create_all(engine)
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def fake_search_repository() -> Any:
    """数据层替身（dict 驱动，行为确定），实现 SearchRepositoryProtocol。"""

    class FakeSearchRepository:
        def __init__(self) -> None:
            self.pathways = {
                "hsa04115": {"id": "hsa04115", "name": "p53 signaling pathway"},
                "hsa00010": {"id": "hsa00010", "name": "Glycolysis / Gluconeogenesis"},
            }
            self.ranked = [("hsa04115", 0.87), ("hsa00010", 0.31)]
            self.calls: list[tuple[str, Any]] = []

        def get_pathway(self, pathway_id: str) -> dict | None:
            self.calls.append(("get_pathway", pathway_id))
            return self.pathways.get(pathway_id)

        def search(self, **filters: Any) -> list[dict]:
            self.calls.append(("search", dict(filters)))
            return [row for row in self.pathways.values()
                    if all(str(row.get(k)) == str(v) for k, v in filters.items())]

        def rank(self, query: str) -> list[tuple[str, float]]:
            self.calls.append(("rank", query))
            return list(self.ranked)

        def create(self, data: dict) -> dict:
            self.calls.append(("create", dict(data)))
            self.pathways[data["id"]] = dict(data)
            return dict(data)

        def update(self, pathway_id: str, data: dict) -> dict:
            self.calls.append(("update", dict(data)))
            self.pathways.setdefault(pathway_id, {}).update(data)
            return dict(self.pathways[pathway_id])

        def delete(self, pathway_id: str) -> bool:
            self.calls.append(("delete", pathway_id))
            return self.pathways.pop(pathway_id, None) is not None

    return FakeSearchRepository()


@pytest.fixture
def search_service(fake_search_repository: Any) -> Any:
    """被打上真实 BaseService 的 SearchService，数据层是替身。"""
    from search_service import SearchService

    return SearchService(fake_search_repository)


# ------------------------------- 路由层相关 -------------------------------- #
def _build_reference_app() -> Any:
    """构造一个「与拆分后目录结构同形」的最小 FastAPI app。

    三个依赖函数模拟 routes/ 里 `Depends(get_xxx_service)` 的写法，
    测试通过 `app.dependency_overrides` 把 service 层整层替换掉。
    """
    from fastapi import Depends, FastAPI
    from fastapi.responses import JSONResponse

    from base_service import ServiceError
    from utils.response import created, from_exception, list_response, success, to_http

    app = FastAPI(title="ReferenceApp")

    def get_gene_service() -> Any:  # pragma: no cover - 必须被 override
        raise NotImplementedError("测试须通过 dependency_overrides 注入 gene_service")

    def get_pathway_service() -> Any:  # pragma: no cover - 必须被 override
        raise NotImplementedError("测试须通过 dependency_overrides 注入 pathway_service")

    def get_analysis_service() -> Any:  # pragma: no cover - 必须被 override
        raise NotImplementedError("测试须通过 dependency_overrides 注入 analysis_service")

    def _service_error_response(exc: ServiceError) -> JSONResponse:
        """统一异常 -> 响应信封 + HTTP 状态码（复用 utils.response）。"""
        payload = from_exception(exc)
        _, status = to_http(payload)
        return JSONResponse(status_code=status, content=payload)

    @app.get("/api/health")
    def health() -> dict:
        return success({"status": "ok"})

    @app.get("/api/genes/{symbol}")
    def read_gene(symbol: str, svc: Any = Depends(get_gene_service)) -> Any:
        try:
            gene = svc.get_by_symbol(symbol)
        except ServiceError as exc:
            return _service_error_response(exc)
        return success(gene.model_dump() if hasattr(gene, "model_dump") else dict(gene))

    @app.get("/api/pathways")
    def list_pathways(limit: int = 20, svc: Any = Depends(get_pathway_service)) -> Any:
        try:
            items = svc.list(limit=limit)
        except ServiceError as exc:
            return _service_error_response(exc)
        return list_response([
            item.model_dump() if hasattr(item, "model_dump") else dict(item)
            for item in items
        ])

    @app.post("/api/analyses")
    def create_analysis(payload: dict, svc: Any = Depends(get_analysis_service)) -> Any:
        try:
            entity = svc.create(payload)
        except ServiceError as exc:
            return _service_error_response(exc)
        return JSONResponse(status_code=201, content=created(entity, "analysis queued"))

    app.state.deps = {
        "gene_service": get_gene_service,
        "pathway_service": get_pathway_service,
        "analysis_service": get_analysis_service,
    }
    return app


@pytest.fixture
def sample_app() -> Any:
    """函数级作用域：每个用例一套干净的 dependency_overrides。"""
    pytest.importorskip("fastapi", reason="路由层测试需要 fastapi")
    return _build_reference_app()


@pytest.fixture
def test_client(sample_app: Any) -> Iterator[Any]:
    """FastAPI TestClient。`test_client.app` 即被测 app。"""
    testclient = pytest.importorskip("fastapi.testclient", reason="需要 httpx")
    with testclient.TestClient(sample_app) as client:
        yield client


class _OverrideHelper:
    """`override("gene_service", fake)` —— 把 routes 依赖的函数指向替身。"""

    def __init__(self, app: Any) -> None:
        self._app = app

    def __call__(self, name: str, implementation: Any) -> Any:
        dependency = self._app.state.deps[name]
        self._app.dependency_overrides[dependency] = lambda: implementation
        return implementation

    def clear(self) -> None:
        self._app.dependency_overrides.clear()


@pytest.fixture
def override(test_client: Any) -> Iterator[_OverrideHelper]:
    """依赖覆盖助手，用例结束后自动清理，避免用例间串味。"""
    helper = _OverrideHelper(test_client.app)
    try:
        yield helper
    finally:
        helper.clear()


# --------------------------------------------------------------------------- #
# 4. 全局护栏：每个用例结束后复位日志级别
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _reset_root_log_level() -> Iterator[None]:
    root = logging.getLogger()
    original = root.level
    try:
        yield
    finally:
        root.setLevel(original)


# --------------------------------------------------------------------------- #
# 5. 集成测试 fixture：真实引擎 + 真实会话 + 真实 FastAPI 应用
#
#    与上面第 3 节的**根本区别**：这里没有任何 mock。
#    请求从 HTTP 进来 -> 依赖装配 -> service -> repository -> SQLite -> 再读回来，
#    走的是与生产同一条代码路径（只有「连哪个库」被换掉了）。
#
#    作用域刻意都用默认的 function：一个用例一套引擎 = 天然互不干扰。
# --------------------------------------------------------------------------- #
#: 内存库 URL。⚠️ SQLite 的 :memory: 是**每个连接一个独立库**，
#: 必须配合 StaticPool 让所有会话共用同一条连接，否则表都建不到请求侧去。
_SQLITE_MEMORY_URL = "sqlite+pysqlite:///:memory:"


def _session_for(engine: Any) -> Any:
    """建一个绑定到给定引擎的真实 Session。

    `expire_on_commit=False`：commit 之后仍能读已加载对象的属性，不会因
    再次取属性而触发额外 SELECT（与 app/db.py 的 SessionLocal 保持同参数）。
    """
    from sqlalchemy.orm import Session

    return Session(engine, expire_on_commit=False)


@pytest.fixture
def db_engine() -> Iterator[Any]:
    """**每个用例一套全新的内存 SQLite 引擎**（真实引擎，不是 mock）。

    两个关键字参数的由来（缺任何一个都会以奇怪的方式失败）：

    * `poolclass=StaticPool` —— FastAPI 的 TestClient 在**另一个线程**里执行 app，
      请求用的 Session 是在那个线程创建的。内存 SQLite 默认「一条连接一个库」，
      不共用连接的话请求侧根本看不到测试侧建好的表，现象是 `no such table: genes`。
    * `check_same_thread=False` —— 放开 SQLite 的跨线程检查。⚠️ 它**不是**并发方案，
      只是允许同一个连接被不同线程使用。

    收尾做两件事：drop 掉所有表 + dispose 连接（内存库随连接一起消失）。
    因此即便将来把作用域改成 module/session，用例之间也不会留下脏数据。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    import db_models

    engine = create_engine(
        _SQLITE_MEMORY_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    db_models.Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        db_models.Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def db_session(db_engine: Any) -> Iterator[Any]:
    """真实 Session（真实事务边界），绑到本用例专属的引擎。

    与第 3 节的 `in_memory_session` 的区别：那个是「给单元测试用的够用的真库」，
    这个是一等公民 —— 集成用例直接拿它跑 Repository / Service 的真实读写，
    并保证收尾时把未提交的事务回滚掉（不留悬挂事务污染下一条语句）。
    """
    session = _session_for(db_engine)
    try:
        yield session
    finally:
        if session.in_transaction():
            session.rollback()
        session.close()


@pytest.fixture
def db_query(db_engine: Any) -> Any:
    """校验落库状态用：每次调用都开一个**全新短会话**，查完立刻关闭。

        rows = db_query(select(Gene).where(Gene.symbol == "TP53"))

    ⚠️ 为什么不直接复用 `db_session`：Session 在第一次 execute 时会开启事务并
    一直持有；而本套 fixture 里所有会话共享同一条 SQLite 连接（StaticPool），
    拿着一个开着的读事务再去发 HTTP 写请求，轻则读到旧快照、重则
    `database is locked`。凡是「发完请求再查库」的场景，一律用新会话查一次就走。
    """
    def _query(stmt: Any) -> list[Any]:
        session = _session_for(db_engine)
        try:
            return list(session.execute(stmt).scalars().all())
        finally:
            session.close()

    return _query


@pytest.fixture
def client(db_engine: Any) -> Iterator[Any]:
    """真实 FastAPI 应用的 TestClient（真实路由 + 真实 service + 真实 SQLite）。

    依赖覆盖只做一件事：把会话来源换成本用例的内存引擎。路由、依赖装配、
    service、repository、SQL 全部是真的。

    ⚠️ 共享缓存是 app/deps.py 里的**模块级状态**，跨用例存活 —— 前后各清一次。
    不清的话，上一个用例缓存住的实体（含已删除、已改名的）会泄漏到下一个用例，
    典型症状是「单跑绿、全跑红」。
    """
    testclient = pytest.importorskip(
        "fastapi.testclient", reason="集成测试需要 fastapi + httpx"
    )
    from app.db import get_db
    from app.deps import reset_shared_cache
    from app.main import create_app

    def _override_get_db() -> Iterator[Any]:
        """与被覆盖的真实 `get_db` 同构：一请求一事务，异常整体回滚。"""
        session = _session_for(db_engine)
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    application = create_app()
    application.dependency_overrides[get_db] = _override_get_db

    reset_shared_cache()
    try:
        with testclient.TestClient(application) as test_client:
            yield test_client
    finally:
        reset_shared_cache()

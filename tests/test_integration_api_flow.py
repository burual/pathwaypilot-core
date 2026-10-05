"""tests/test_integration_api_flow.py — HTTP 端到端集成测试（真实 TestClient）。

完整链路（只有「连哪个库」被替换成内存库，其余与生产一致）：

    TestClient -> FastAPI 路由 -> 依赖装配(app/deps) -> GeneService
      -> Repository -> SQLAlchemy -> SQLite -> 再原路读回来

在测什么，不在测什么：
    ✔ 路由真的挂在 /api/genes 上、状态码与统一信封正确；
    ✔ 请求体校验（缺字段/多字段/类型错）在**触库之前**就被挡住；
    ✔ service -> repository -> SQL 这条写入路径真的把行落到了库里；
    ✔ 跨请求的缓存失效真的生效（GET 缓存后 PUT，再 GET 必须拿到新值）。
    ✘ 不测 service 的内部实现细节 —— 那是 test_services.py 的活。

⚠️ 本文件**不使用** db_session：所有落库校验走 `db_query`（每次新开短会话）。
理由见 tests/conftest.py 里 db_query 的说明（共享连接 + 悬挂事务陷阱）。
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, inspect, select

from db_models import Gene

#: 整个模块打标：`pytest -m "not integration"` 可整批跳过
pytestmark = pytest.mark.integration

#: 统一响应信封必须包含的键（与 utils/response.py 约定一致）
ENVELOPE_KEYS = {"success", "data", "message", "meta", "error"}

PAYLOAD = {
    "symbol": "TP53",
    "name": "tumor protein p53",
    "pathway_id": "hsa04115",
    "chromosome": "17p13.1",
}


def _row_count(db_query) -> int:
    return db_query(select(func.count()).select_from(Gene))[0]


def _raw_count(engine) -> int:
    """用一条**全新连接**直接数 genes 表行数。

    与 `_row_count` 的区别：这里绕开了 Session，专门用来验证「commit 到底有没有
    真的落盘」—— 未提交的写入在另一条连接上必须看不见。
    """
    with engine.connect() as connection:
        return connection.execute(select(func.count()).select_from(Gene)).scalar_one()


def _post_gene(client, **overrides) -> dict:
    """POST 一条基因并返回信封里的 data（顺带固定 201 这个契约）。"""
    response = client.post("/api/genes", json={**PAYLOAD, **overrides})
    assert response.status_code == 201, response.text
    return response.json()["data"]


# --------------------------------------------------------------------------- #
# 用例 1：POST /api/genes 创建资源，并真的写进数据库
# --------------------------------------------------------------------------- #
def test_post_creates_resource_and_writes_it_to_the_database(client, db_query) -> None:
    # Arrange：空库
    assert _row_count(db_query) == 0

    # Act
    response = client.post("/api/genes", json=PAYLOAD)

    # Assert —— HTTP 层
    assert response.status_code == 201
    body = response.json()
    assert ENVELOPE_KEYS.issubset(body.keys())
    assert body["success"] is True
    assert body["error"] is None
    assert body["meta"]["status"] == 201
    assert isinstance(body["data"]["id"], int)      # 主键来自数据库自增

    # Assert —— 数据库层（绕过 API 直接查表）
    rows = db_query(select(Gene))
    assert len(rows) == 1
    assert rows[0].id == body["data"]["id"]
    assert rows[0].symbol == "TP53"
    assert rows[0].name == "tumor protein p53"
    assert rows[0].pathway_id == "hsa04115"
    assert rows[0].chromosome == "17p13.1"


# --------------------------------------------------------------------------- #
# 用例 2：GET /api/genes/{id} 返回刚创建的资源
# --------------------------------------------------------------------------- #
def test_get_returns_the_created_resource(client, db_query) -> None:
    # Arrange
    created = _post_gene(client)

    # Act
    response = client.get(f"/api/genes/{created['id']}")

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"] == created
    assert _row_count(db_query) == 1


# --------------------------------------------------------------------------- #
# 用例 3：PUT 局部更新只改给定字段，数据库同步生效
# --------------------------------------------------------------------------- #
def test_put_partial_update_persists_and_leaves_other_columns_alone(
    client, db_query
) -> None:
    # Arrange
    created = _post_gene(client)

    # Act：只提交 name 一个字段
    response = client.put(f"/api/genes/{created['id']}", json={"name": "p53 (renamed)"})

    # Assert —— HTTP 层
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["name"] == "p53 (renamed)"
    assert data["symbol"] == "TP53"                  # 未被请求体提到的字段不能被清空

    # Assert —— 数据库层：只有 name 变了
    row = db_query(select(Gene))[0]
    assert row.name == "p53 (renamed)"
    assert row.symbol == "TP53"
    assert row.pathway_id == "hsa04115"
    assert row.chromosome == "17p13.1"


# --------------------------------------------------------------------------- #
# 用例 4：跨请求的**读缓存失效**（GET 缓存过之后 PUT，再 GET 必须是新值）
# --------------------------------------------------------------------------- #
def test_get_reflects_update_even_after_the_result_was_cached(client, db_query) -> None:
    """针对进程级共享缓存的回归测试。

    第一次 GET 会把实体写进 app/deps.py 的共享缓存（跨请求存活）。若
    `BaseService.update` 里的 `cache_delete` 失效，第二次 GET 会拿到**旧名字**
    —— 这条用例就是用来钉住它的。
    """
    # Arrange：创建 + 先 GET 一次，让缓存被填上
    created = _post_gene(client)
    assert client.get(f"/api/genes/{created['id']}").json()["data"]["name"] == (
        "tumor protein p53"
    )

    # Act
    client.put(f"/api/genes/{created['id']}", json={"name": "p53 (renamed)"})
    second = client.get(f"/api/genes/{created['id']}")

    # Assert：拿到的是新值，不是缓存里的旧值
    assert second.status_code == 200
    assert second.json()["data"]["name"] == "p53 (renamed)"
    assert db_query(select(Gene))[0].name == "p53 (renamed)"


# --------------------------------------------------------------------------- #
# 用例 5：跨请求的**列表缓存失效**（POST 之后 GET 列表必须包含新行）
# --------------------------------------------------------------------------- #
def test_list_reflects_new_rows_created_by_later_requests(client, db_query) -> None:
    """第一次 GET 列表会把结果缓存进共享缓存。

    若 `BaseService.create` 里的 `_invalidate_scope("list")` 失效，
    后面再 GET 列表拿到的还是「create 之前」那份快照 —— 新行看不见。
    """
    # Arrange：先 GET 一次空列表，把「空快照」缓存上
    first = client.get("/api/genes")
    assert first.status_code == 200
    assert first.json()["meta"]["count"] == 0

    # Act
    _post_gene(client, symbol="TP53")
    _post_gene(client, symbol="MDM2", name="MDM2 proto-oncogene")
    second = client.get("/api/genes")

    # Assert
    assert second.json()["meta"]["count"] == 2
    assert [g["symbol"] for g in second.json()["data"]] == ["TP53", "MDM2"]
    assert _row_count(db_query) == 2


# --------------------------------------------------------------------------- #
# 用例 6：按 symbol 查询（大小写归一）
# --------------------------------------------------------------------------- #
def test_get_by_symbol_is_case_insensitive(client) -> None:
    # Arrange
    _post_gene(client)

    # Act
    response = client.get("/api/genes/by-symbol/tp53")

    # Assert
    assert response.status_code == 200
    assert response.json()["data"]["symbol"] == "TP53"


# --------------------------------------------------------------------------- #
# 用例 7：DELETE 真的删行，且再删/再查都是 404
# --------------------------------------------------------------------------- #
def test_delete_removes_the_row_and_returns_404_afterwards(client, db_query) -> None:
    # Arrange
    created = _post_gene(client)
    assert _row_count(db_query) == 1

    # Act
    response = client.delete(f"/api/genes/{created['id']}")

    # Assert：第一次删成功，数据库清空
    assert response.status_code == 200
    assert response.json()["meta"]["deleted"] is True
    assert _row_count(db_query) == 0

    # Assert：第二次删（以及再查）都必须 404，而不是静默成功
    again = client.delete(f"/api/genes/{created['id']}")
    assert again.status_code == 404
    assert again.json()["error"]["code"] == "NOT_FOUND"
    assert client.get(f"/api/genes/{created['id']}").status_code == 404


# --------------------------------------------------------------------------- #
# 用例 8：查询不存在的资源 -> 404 + 统一错误信封
# --------------------------------------------------------------------------- #
def test_get_unknown_id_returns_404_error_envelope(client) -> None:
    # Arrange
    target = "/api/genes/9999"

    # Act
    response = client.get(target)

    # Assert
    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["status"] == 404


# --------------------------------------------------------------------------- #
# 用例 9：入参校验在触库之前完成（缺字段 / 多字段 / 类型错）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("payload", "why"),
    [
        ({"symbol": "TP53"}, "缺必填字段 name"),
        ({**PAYLOAD, "typo_field": 1}, "多了未声明字段（extra=forbid）"),
        ({**PAYLOAD, "symbol": ""}, "symbol 空串（min_length=1）"),
    ],
)
def test_invalid_body_is_rejected_before_touching_the_database(
    client, db_query, payload, why
) -> None:
    # Arrange：空库
    assert _row_count(db_query) == 0

    # Act
    response = client.post("/api/genes", json=payload)

    # Assert：422 + 统一信封，且库里一行都没有（请求体没过校验就不会走到 service）
    assert response.status_code == 422, why
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert _row_count(db_query) == 0


def test_non_integer_path_param_returns_422(client) -> None:
    # Arrange：主键声明为 int，非整数应当在路由层被挡下
    # Act
    response = client.get("/api/genes/not-an-int")

    # Assert
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# --------------------------------------------------------------------------- #
# 用例 10：健康检查（确认应用真的被完整挂载起来了）
# --------------------------------------------------------------------------- #
def test_health_endpoint_is_mounted(client) -> None:
    # Arrange + Act
    response = client.get("/api/health")

    # Assert
    assert response.status_code == 200
    assert response.json()["data"] == {"status": "ok"}


# --------------------------------------------------------------------------- #
# 用例 11：包入口的懒加载属性可用（覆盖 app/__init__.py 的 __getattr__）
# --------------------------------------------------------------------------- #
def test_app_package_exposes_lazy_entrypoints() -> None:
    # Arrange
    import app

    # Act + Assert：三个属性按需导入
    assert callable(app.create_app)
    assert callable(app.get_db)
    assert callable(app.get_gene_service)

    # 未声明的属性照常抛 AttributeError（不静默返回 None）
    with pytest.raises(AttributeError):
        _ = app.definitely_not_an_attribute


# --------------------------------------------------------------------------- #
# 用例 12~15：生产用的 app/db.py 本体
#
# 上面的 HTTP 用例把 `get_db` 整个 override 掉了 —— 那条真实实现（事务边界就在
# 它里面）反而一行没跑到。这里给它补上，用**临时文件库**而不是内存库：
# 文件库允许从另一条连接验证提交结果，比共享连接的内存库更硬。
# --------------------------------------------------------------------------- #
@pytest.fixture
def app_db_engine(tmp_path, monkeypatch):
    """给 app/db.py 换上临时文件引擎的会话工厂。

    必须换：模块级的 `SessionLocal` 指向 `./pathwaypilot.db`，直接在测试里调
    真实 `get_db()` 会在仓库根留下一个数据库文件。

    ⚠️ 收尾必须 `dispose()`：引擎自带的连接池会攥着一条 sqlite3.Connection，
    不释放的话它会一直活到 GC，pytest 报
    `ResourceWarning: unclosed database` —— 而且被记到**完全无关**的用例头上
    （GC 在哪个用例里发生就算谁的），极难定位。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker

    import app.db as app_db
    import db_models

    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'appdb.sqlite'}")
    db_models.Base.metadata.create_all(engine)
    monkeypatch.setattr(
        app_db,
        "SessionLocal",
        sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session),
    )
    try:
        yield engine
    finally:
        engine.dispose()


def _drain(generator) -> None:
    """把 get_db() 生成器跑到底（等价于 FastAPI 请求结束时的收尾）。"""
    try:
        next(generator)
    except StopIteration:
        return
    raise AssertionError("get_db() 在收尾后又 yield 了一次")


def test_real_get_db_commits_after_the_request(app_db_engine) -> None:
    # Arrange
    import app.db as app_db

    generator = app_db.get_db()
    session = next(generator)
    session.add(Gene(symbol="TP53", name="tumor protein p53"))
    session.flush()
    assert _raw_count(app_db_engine) == 0        # 还没 commit -> 另一条连接看不到

    # Act：请求收尾（路由返回后 FastAPI 会做的事）
    _drain(generator)

    # Assert：换一条连接查，行真的落盘了
    assert _raw_count(app_db_engine) == 1


def test_real_get_db_rolls_back_when_the_request_fails(app_db_engine) -> None:
    # Arrange
    import app.db as app_db

    generator = app_db.get_db()
    session = next(generator)
    session.add(Gene(symbol="TP53", name="tumor protein p53"))
    session.flush()

    # Act：把异常抛进生成器，模拟路由/依赖里出错
    with pytest.raises(RuntimeError, match="boom"):
        generator.throw(RuntimeError("boom"))

    # Assert：整体回滚，一行不留
    assert _raw_count(app_db_engine) == 0


def test_init_db_creates_the_schema(app_db_engine) -> None:
    # Arrange：先把表删干净
    import db_models
    from app.db import init_db

    db_models.Base.metadata.drop_all(app_db_engine)
    assert inspect(app_db_engine).get_table_names() == []

    # Act
    init_db(app_db_engine)

    # Assert：三张表都建出来了
    assert set(inspect(app_db_engine).get_table_names()) >= {"genes", "pathways", "analyses"}


def test_shared_cache_accessor_exposes_the_process_level_backend() -> None:
    # Arrange + Act：app/deps.py 的共享缓存是跨请求缓存生效的前提
    from app.deps import shared_cache

    # Assert：两次取到的是同一个对象（进程级单例，不是每次新建）
    assert shared_cache() is shared_cache()


# --------------------------------------------------------------------------- #
# 用例 16：隔离性 canary（⚠️ 刻意放在文件最后）
# --------------------------------------------------------------------------- #
def test_each_test_starts_with_an_empty_database(db_query) -> None:
    """上面 11 个用例都通过真实 HTTP 写过 genes 表。

    若 client/db_engine 的作用域被改成 function 以上，数据会跨用例残留，
    本用例立刻变红。必须保持在文件最后一个。
    """
    assert _row_count(db_query) == 0

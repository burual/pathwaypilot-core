"""tests/test_integration_gene_crud.py — 数据层 + 服务层集成测试（真实 SQLite）。

与既有单元测试的分工（**别把两者混为一谈**）：

    tests/test_data.py / test_services.py   = 单元测试：依赖被 mock，验证
                                              「调用了正确的接口」；
    本文件                                   = 集成测试：真实引擎 + 真实 Repository
                                              + 真实 GeneService，验证「数据真的按
                                              预期落进 SQLite，又真的能读回来」。

事务边界说明（本文件最容易踩的坑）：
    BaseService 与 Repository 都**只 flush 不 commit**（见 repositories/base.py）。
    所以需要真正落库时，本文件显式调用 `db_session.commit()` —— 这正是生产里
    `app/db.py::get_db` 在做的事（一请求一事务）。跳过 commit 的用例反而是在
    验证「未提交的写入」这一另一条语义，见 test_rollback_*。

    ⚠️ 校验落库状态一律走 `db_query`（每次新开短会话），不要把 `db_session`
    攥在手里跨步骤用：所有会话共享同一条 SQLite 连接，
    拿着悬挂事务再读写会读到旧快照甚至 database is locked。
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from base_service import NotFoundError, ValidationError
from db_models import Gene
from example_gene_service import GeneService
from repositories import GeneRepository, get_repository
from schemas import GeneSchema

#: 整个模块打标：`pytest -m "not integration"` 可整批跳过
pytestmark = pytest.mark.integration

#: 建库用的初始数据
SEED = {
    "symbol": "TP53",
    "name": "tumor protein p53",
    "pathway_id": "hsa04115",
    "chromosome": "17p13.1",
}


@pytest.fixture
def service(db_session) -> GeneService:
    """真实 GeneService：真实 Repository + 真实 Session。

    刻意不传 cache（用默认的实例级 SimpleTTLCache）：本文件要看清数据库行为，
    跨请求缓存那部分由 test_integration_api_flow.py 负责。
    """
    return GeneService(get_repository(GeneRepository, db_session))


def _row_count(db_query) -> int:
    """表里现在有几行（新会话直查 SQLite）。"""
    return db_query(select(func.count()).select_from(Gene))[0]


# --------------------------------------------------------------------------- #
# 用例 1：完整 CRUD 生命周期（创建 -> 查询 -> 更新 -> 删除）
# --------------------------------------------------------------------------- #
def test_crud_roundtrip_against_real_sqlite(service, db_session, db_query) -> None:
    # Arrange：空库，没有任何前置数据
    assert _row_count(db_query) == 0

    # Act 1：创建
    created = service.create(SEED)
    db_session.commit()          # 事务边界（生产里由 get_db 负责）

    # Assert 1：返回值是 dict，主键由数据库生成（不是调用方给的）
    assert isinstance(created, dict)
    assert isinstance(created["id"], int)
    assert created["symbol"] == "TP53"

    # Assert 1b：真的写进了数据库，不是只在内存里
    rows = db_query(select(Gene))
    assert len(rows) == 1
    assert rows[0].symbol == "TP53"
    assert rows[0].pathway_id == "hsa04115"

    # Act 2：查询（走 service，会经过 repository 的 ORM -> Pydantic 转换）
    fetched = service.get(str(created["id"]))

    # Assert 2
    assert fetched == created

    # Act 3：更新（只给要改的字段）
    updated = service.update(str(created["id"]), {"name": "p53 (renamed)"})
    db_session.commit()

    # Assert 3：返回体是改后的值，且其它字段原样保留
    assert updated["name"] == "p53 (renamed)"
    assert updated["symbol"] == "TP53"
    assert updated["pathway_id"] == "hsa04115"
    row = db_query(select(Gene))[0]
    assert row.name == "p53 (renamed)"

    # Act 4：删除
    removed = service.delete(str(created["id"]))
    db_session.commit()

    # Assert 4：数据库里真的没有了，且再查会抛 NotFoundError
    assert removed is True
    assert _row_count(db_query) == 0
    with pytest.raises(NotFoundError):
        service.get(str(created["id"]))


# --------------------------------------------------------------------------- #
# 用例 2：主键由数据库自增生成（mock 造不出这个行为）
# --------------------------------------------------------------------------- #
def test_primary_keys_are_generated_by_the_database(service, db_session) -> None:
    # Arrange + Act：连续创建两条，都不给 id
    first = service.create({**SEED, "symbol": "TP53"})
    second = service.create({**SEED, "symbol": "MDM2", "name": "MDM2 proto-oncogene"})
    db_session.commit()

    # Assert：id 由 SQLite 自增给出，且互不相同
    assert first["id"] == 1
    assert second["id"] == 2


# --------------------------------------------------------------------------- #
# 用例 3：局部更新**不得**抹掉未被触及的列
# --------------------------------------------------------------------------- #
def test_partial_update_keeps_untouched_columns(service, db_session, db_query) -> None:
    """这是 `exclude_unset` 那条链路的回归测试。

    只要有哪一环把「没写的字段」当成 None 一起写下去，symbol/pathway_id/
    chromosome 三列就会被刷成 NULL —— 本用例立刻变红。
    """
    # Arrange：四个字段都有值
    created = service.create(SEED)
    db_session.commit()

    # Act：只改 name
    service.update(str(created["id"]), {"name": "renamed"})
    db_session.commit()

    # Assert：只有 name 变了，其余三列保持原值
    row = db_query(select(Gene))[0]
    assert row.name == "renamed"
    assert row.symbol == "TP53"
    assert row.pathway_id == "hsa04115"
    assert row.chromosome == "17p13.1"


# --------------------------------------------------------------------------- #
# 用例 4：显式传 None 才是「真的置空」（与用例 3 互为对照）
# --------------------------------------------------------------------------- #
def test_explicit_none_in_payload_clears_the_column(service, db_session, db_query) -> None:
    # Arrange
    created = service.create(SEED)
    db_session.commit()

    # Act：显式给出 chromosome=None
    service.update(str(created["id"]), {"chromosome": None})
    db_session.commit()

    # Assert：该列被置空，但 symbol 仍然在
    row = db_query(select(Gene))[0]
    assert row.chromosome is None
    assert row.symbol == "TP53"


# --------------------------------------------------------------------------- #
# 用例 5：直接调 Repository —— 只写「显式给过的字段」
# --------------------------------------------------------------------------- #
def test_repository_save_only_writes_explicitly_set_fields(db_session, db_query) -> None:
    """`exclude_unset` 的守门用例（直连 repository，绕开 service）。

    这里构造的 GeneSchema 只显式给了 id/symbol/name —— `pathway_id` /
    `chromosome` 是**字段默认值** None，不代表「调用方要置空」。
    仓库一旦丢掉 `exclude_unset=True`，这两列就会被刷成 NULL，本用例立刻变红。

    ⚠️ 走 service 的用例（见下面的局部更新用例）**测不出**这个缺陷：service 的
    `_persist_update` 会先把 current 合并进来、构造出一个「全字段都显式设置」的
    模型，于是 dump 带不带 exclude_unset 结果一样。所以这条必须直连数据层。
    """
    # Arrange：先整行写满
    repo = get_repository(GeneRepository, db_session)
    repo.save(
        GeneSchema(
            symbol="TP53", name="p53", pathway_id="hsa04115", chromosome="17p13.1"
        )
    )
    db_session.commit()
    row_id = db_query(select(Gene))[0].id

    # Act：只显式给 id / symbol / name
    repo.save(GeneSchema(id=row_id, symbol="TP53", name="renamed"))
    db_session.commit()

    # Assert：name 变了，另外两列保持原值
    row = db_query(select(Gene))[0]
    assert row.name == "renamed"
    assert row.pathway_id == "hsa04115"
    assert row.chromosome == "17p13.1"


# --------------------------------------------------------------------------- #
# 用例 6：删除后再删 / 更新不存在的实体 -> NotFoundError，且库不变
# --------------------------------------------------------------------------- #
def test_operations_on_missing_entity_raise_not_found(service, db_query) -> None:
    # Arrange：空库

    # Act + Assert
    with pytest.raises(NotFoundError):
        service.get("999")
    with pytest.raises(NotFoundError):
        service.update("999", {"name": "nope"})
    with pytest.raises(NotFoundError):
        service.delete("999")

    assert _row_count(db_query) == 0


# --------------------------------------------------------------------------- #
# 用例 7：非法主键 -> ValidationError（不是让 SQLite 隐式转换兜住）
# --------------------------------------------------------------------------- #
def test_non_integer_primary_key_is_rejected(service) -> None:
    # Arrange：Gene 主键是自增 int，URL 里可能是任意字符串

    # Act + Assert：service 层归一化失败 -> 422 语义的 ValidationError
    with pytest.raises(ValidationError):
        service.get("not-an-int")


# --------------------------------------------------------------------------- #
# 用例 8：事务边界 —— 回滚必须把未提交的写入整体丢掉
# --------------------------------------------------------------------------- #
def test_rollback_discards_uncommitted_insert(service, db_session, db_query) -> None:
    """证明「只 flush 不 commit」是真的：没 commit 的数据，回滚就得消失。

    这条同时也是 app/db.py::get_db 里 `except: db.rollback()` 的行为依据。
    """
    # Arrange + Act：写入但不提交
    service.create(SEED)
    assert db_session.in_transaction() is True

    # Act：回滚
    db_session.rollback()

    # Assert：数据库里干干净净
    assert _row_count(db_query) == 0


# --------------------------------------------------------------------------- #
# 用例 9：symbol 归一化查询（repository 侧 .upper()）
# --------------------------------------------------------------------------- #
def test_find_by_symbol_normalises_case(service, db_session) -> None:
    # Arrange
    service.create(SEED)
    db_session.commit()

    # Act + Assert：小写也能命中存成大写的记录
    assert service.get_by_symbol("tp53").symbol == "TP53"
    with pytest.raises(NotFoundError):
        service.get_by_symbol("NOPE")


# --------------------------------------------------------------------------- #
# 用例 10：隔离性 canary（⚠️ 刻意放在文件最后）
# --------------------------------------------------------------------------- #
def test_each_test_starts_with_an_empty_database(db_query) -> None:
    """「每个测试独立、互不干扰」这条要求的守门用例。

    上面 8 个用例全都往 genes 表里写过数据。只要 fixture 作用域哪天被改成
    function 以上（module/session），数据就会跨用例残留，本用例立刻变红。
    所以它必须保持在文件最后一个 —— 排在前面就失去意义了。
    """
    assert _row_count(db_query) == 0
    assert db_query(select(Gene)) == []

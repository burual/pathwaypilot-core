"""tests/test_data.py — 数据层（repositories/）测试模板（≥2 个用例）。

两条互补路线：

  A. **内存 SQLite**（`in_memory_session`）—— 验证真实 CRUD 语义：
     upsert、过滤/排序/分页、ORM -> Pydantic 的转换边界。
  B. **mock session**（`mock_db_session`）—— 只验证「调用了正确的 SQLAlchemy 接口」，
     完全不碰数据库，适合放在毫秒级回归里。

核心不变量：Repository 对外**只返回 Pydantic 模型**，绝不泄露 ORM 对象。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from db_models import Gene
from repositories import (
    REPOSITORY_REGISTRY,
    AnalysisRepository,
    BaseRepository,
    GeneRepository,
    PathwayRepository,
    get_repository,
)
from schemas import AnalysisSchema, GeneSchema, PathwaySchema


def _seed_genes(session) -> tuple[GeneSchema, GeneSchema]:
    """插入两条基因数据，返回 (TP53, MDM2)。"""
    repo = get_repository(GeneRepository, session)
    tp53 = repo.save(GeneSchema(
        symbol="TP53", name="tumor protein p53",
        pathway_id="hsa04115", chromosome="17p13.1",
    ))
    mdm2 = repo.save(GeneSchema(
        symbol="MDM2", name="MDM2 proto-oncogene", pathway_id="hsa04115",
    ))
    session.commit()
    return tp53, mdm2


# --------------------------------------------------------------------------- #
# 用例 1：save 走内存 SQLite —— 返回 Pydantic、回填主键、可被 find_by_id 读回
# --------------------------------------------------------------------------- #
def test_save_returns_pydantic_model_and_backfills_primary_key(in_memory_session) -> None:
    # Arrange
    repo = get_repository(GeneRepository, in_memory_session)
    payload = GeneSchema(symbol="TP53", name="tumor protein p53", pathway_id="hsa04115")

    # Act
    saved = repo.save(payload)
    in_memory_session.commit()
    fetched = repo.find_by_id(saved.id)

    # Assert
    assert isinstance(saved, GeneSchema)
    assert not isinstance(saved, Gene)          # 绝不泄露 ORM 对象
    assert saved.id is not None                 # 自增主键已回填
    assert fetched is not None and fetched.symbol == "TP53"
    assert isinstance(fetched, GeneSchema)


def test_find_by_id_returns_none_when_missing(in_memory_session) -> None:
    # Arrange
    repo = get_repository(GeneRepository, in_memory_session)

    # Act
    missing = repo.find_by_id(999_999)

    # Assert
    assert missing is None


# --------------------------------------------------------------------------- #
# 用例 2：find_all —— 过滤 / 排序 / 分页，以及非法字段的防御
# --------------------------------------------------------------------------- #
def test_find_all_supports_filters_order_and_limit(in_memory_session) -> None:
    # Arrange
    repo = get_repository(GeneRepository, in_memory_session)
    _seed_genes(in_memory_session)

    # Act
    same_pathway = repo.find_all(pathway_id="hsa04115")
    ascending = repo.find_all(order_by="symbol")
    paged = repo.find_all(order_by="symbol", limit=1, offset=1)
    none_filter_is_skipped = repo.find_all(pathway_id=None)

    # Assert
    assert {g.symbol for g in same_pathway} == {"TP53", "MDM2"}
    assert [g.symbol for g in ascending] == ["MDM2", "TP53"]
    assert [g.symbol for g in paged] == ["TP53"]
    assert len(none_filter_is_skipped) == 2      # None 条件被忽略


def test_find_all_rejects_unknown_field_and_unknown_order_column(in_memory_session) -> None:
    # Arrange
    repo = get_repository(GeneRepository, in_memory_session)

    # Act / Assert
    with pytest.raises(ValueError, match="无字段"):
        repo.find_all(not_a_column="x")
    with pytest.raises(ValueError, match="无排序字段"):
        repo.find_all(order_by="-not_a_column")


# --------------------------------------------------------------------------- #
# 用例 3：save 的 upsert 语义 + delete 返回布尔
# --------------------------------------------------------------------------- #
def test_save_updates_existing_row_instead_of_inserting(in_memory_session) -> None:
    # Arrange
    repo = get_repository(GeneRepository, in_memory_session)
    tp53, _ = _seed_genes(in_memory_session)

    # Act: 同主键再来一次 -> 应走更新分支
    updated = repo.save(GeneSchema(
        id=tp53.id, symbol="TP53", name="p53 (updated)", pathway_id="hsa04115",
    ))
    in_memory_session.commit()

    # Assert
    assert updated.id == tp53.id
    assert updated.name == "p53 (updated)"
    assert len(repo.find_all()) == 2             # 没有多出一行
    assert repo.find_by_id(tp53.id).name == "p53 (updated)"


def test_delete_reports_whether_a_row_was_removed(in_memory_session) -> None:
    # Arrange
    repo = get_repository(GeneRepository, in_memory_session)
    _, mdm2 = _seed_genes(in_memory_session)

    # Act
    hit = repo.delete(mdm2.id)
    miss = repo.delete(999_999)

    # Assert
    assert hit is True
    assert miss is False
    assert repo.find_by_id(mdm2.id) is None


# --------------------------------------------------------------------------- #
# 用例 4：领域查询（GeneRepository / PathwayRepository / AnalysisRepository）
# --------------------------------------------------------------------------- #
def test_gene_repository_domain_queries(in_memory_session) -> None:
    # Arrange
    repo = get_repository(GeneRepository, in_memory_session)
    _seed_genes(in_memory_session)

    # Act
    by_symbol = repo.find_by_symbol("tp53")          # 大小写无关
    by_pathway = repo.find_by_pathway("hsa04115")
    fuzzy = repo.search("oncogene")

    # Assert
    assert by_symbol is not None and by_symbol.symbol == "TP53"
    assert len(by_pathway) == 2
    assert [g.symbol for g in fuzzy] == ["MDM2"]     # 仅 name 命中 'oncogene'


def test_pathway_and_analysis_repositories(in_memory_session) -> None:
    # Arrange
    pathways = get_repository(PathwayRepository, in_memory_session)
    analyses = get_repository(AnalysisRepository, in_memory_session)
    base_time = datetime(2026, 9, 14, 12, 0, 0)

    # Act
    pathways.save(PathwaySchema(id="hsa00010", name="Glycolysis",
                                category="Metabolism", organism="hsa", gene_count=67))
    pathways.save(PathwaySchema(id="hsa04115", name="p53 signaling pathway",
                                category="Cellular Processes", organism="hsa", gene_count=68))
    analyses.save(AnalysisSchema(method="deseq2", dataset="airway",
                                 created_at=base_time, status="done"))
    analyses.save(AnalysisSchema(method="deseq2", dataset="airway",
                                 created_at=base_time + timedelta(minutes=5), status="done"))
    in_memory_session.commit()

    # Assert
    assert {p.id for p in pathways.find_by_organism("hsa")} == {"hsa00010", "hsa04115"}
    assert pathways.find_largest(1)[0].id == "hsa04115"
    assert pathways.find_with_name("glycol") is not None
    assert len(analyses.find_by_method("deseq2")) == 2
    assert analyses.find_recent(1)[0].created_at == base_time + timedelta(minutes=5)
    assert analyses.find_latest_by_dataset("airway").created_at == base_time + timedelta(minutes=5)


# --------------------------------------------------------------------------- #
# 用例 5：工厂函数（传类 / 传注册名 / 未知名字）
# --------------------------------------------------------------------------- #
def test_get_repository_factory_accepts_class_and_registry_name(in_memory_session) -> None:
    # Arrange / Act
    by_class = get_repository(GeneRepository, in_memory_session)
    by_name = get_repository("pathway", in_memory_session)

    # Assert
    assert isinstance(by_class, GeneRepository)
    assert isinstance(by_class, BaseRepository)
    assert isinstance(by_name, PathwayRepository)
    assert set(REPOSITORY_REGISTRY) == {"gene", "pathway", "analysis"}


def test_get_repository_raises_key_error_for_unknown_name(in_memory_session) -> None:
    # Arrange
    unknown = "transcript"

    # Act / Assert
    with pytest.raises(KeyError, match="未知 Repository"):
        get_repository(unknown, in_memory_session)


# --------------------------------------------------------------------------- #
# 用例 6：mock session 路线 —— 验证调用契约，完全不碰数据库
# --------------------------------------------------------------------------- #
def test_find_by_id_with_mock_session_calls_session_get(mock_db_session) -> None:
    # Arrange
    repo = GeneRepository(mock_db_session)
    mock_db_session.get.return_value = None

    # Act
    result = repo.find_by_id(42)

    # Assert
    assert result is None
    mock_db_session.get.assert_called_once_with(Gene, 42)


def test_find_by_id_with_mock_session_maps_orm_instance_to_pydantic(
    mock_db_session,
) -> None:
    # Arrange: 让 mock 返回一个真实 ORM 实例（未持久化也不影响字段映射）
    orm_gene = Gene(symbol="BRCA1", name="BRCA1 DNA repair associated",
                    pathway_id="hsa04115", chromosome="17q21.31")
    mock_db_session.get.return_value = orm_gene
    repo = GeneRepository(mock_db_session)

    # Act
    result = repo.find_by_id(1)

    # Assert
    assert isinstance(result, GeneSchema)
    assert result.symbol == "BRCA1"
    assert result.chromosome == "17q21.31"


def test_find_all_with_mock_session_returns_empty_list_and_never_commits(
    mock_db_session,
) -> None:
    # Arrange
    repo = GeneRepository(mock_db_session)

    # Act
    rows = repo.find_all(limit=10)

    # Assert
    assert rows == []
    mock_db_session.execute.assert_called_once()
    mock_db_session.commit.assert_not_called()   # 事务边界归 service/UoW


def test_delete_with_mock_session_flushes_but_does_not_commit(mock_db_session) -> None:
    # Arrange
    orm_gene = Gene(symbol="TP53", name="tumor protein p53")
    mock_db_session.get.return_value = orm_gene
    repo = GeneRepository(mock_db_session)

    # Act
    removed = repo.delete(7)

    # Assert
    assert removed is True
    mock_db_session.delete.assert_called_once_with(orm_gene)
    mock_db_session.flush.assert_called_once()
    mock_db_session.commit.assert_not_called()


# --------------------------------------------------------------------------- #
# 用例 7（回归）：save() 的更新分支是**局部更新**，不得抹掉未提供的列
# --------------------------------------------------------------------------- #
def test_save_partial_update_preserves_columns_not_provided(in_memory_session) -> None:
    """回归：以前 _dump 用 model_dump()，模型默认值会带着 None 一起写库，
    把 chromosome / pathway_id 直接抹成 NULL。改为 exclude_unset 后只写显式字段。"""
    # Arrange
    repo = get_repository(GeneRepository, in_memory_session)
    tp53, _ = _seed_genes(in_memory_session)
    assert tp53.chromosome == "17p13.1"
    assert tp53.pathway_id == "hsa04115"

    # Act: 只提供 id / symbol / name（chromosome、pathway_id 未提供）
    repo.save(GeneSchema(id=tp53.id, symbol="TP53", name="p53 (renamed)"))
    in_memory_session.commit()

    # Assert
    reloaded = repo.find_by_id(tp53.id)
    assert reloaded.name == "p53 (renamed)"
    assert reloaded.chromosome == "17p13.1"      # 未被抹成 None
    assert reloaded.pathway_id == "hsa04115"     # 同上


def test_save_explicit_none_still_clears_the_column(in_memory_session) -> None:
    """exclude_unset 只跳过「没写」的字段；显式传 None = 真的想置空。"""
    # Arrange
    repo = get_repository(GeneRepository, in_memory_session)
    tp53, _ = _seed_genes(in_memory_session)

    # Act: 显式传 chromosome=None
    repo.save(GeneSchema(id=tp53.id, symbol="TP53", name="tumor protein p53", chromosome=None))
    in_memory_session.commit()

    # Assert
    assert repo.find_by_id(tp53.id).chromosome is None

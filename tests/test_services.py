"""tests/test_services.py — 服务层测试模板（≥3 个用例）。

策略：**把数据层（repository）mock 掉**，测真实的 `BaseService` / `SearchService`：
CRUD 模板方法、缓存命中、异常映射、未知异常包装（保留 `__cause__`）、
装饰器的日志/耗时、以及 `ServiceContainer` 的懒加载契约。

被测对象是本仓库真实存在的 `base_service.py` / `search_service.py`，
所以这些用例是真跑逻辑，不是占位。
"""
from __future__ import annotations

import logging
import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest

from base_service import NotFoundError, ServiceError, ValidationError
from search_service import SearchRepositoryProtocol, SearchService
from utils.cache import _MISS


def _count(repository, method: str) -> int:
    """统计替身数据层被调用某方法的次数。"""
    return [name for name, _ in repository.calls].count(method)


# --------------------------------------------------------------------------- #
# 用例 1：CRUD 模板 + 缓存命中（数据层只被调一次）
# --------------------------------------------------------------------------- #
def test_get_returns_entity_and_second_call_is_served_from_cache(
    search_service, fake_search_repository
) -> None:
    # Arrange
    entity_id = "hsa04115"

    # Act
    first = search_service.get(entity_id)
    second = search_service.get(entity_id)

    # Assert
    assert first == second == {"id": "hsa04115", "name": "p53 signaling pathway"}
    assert _count(fake_search_repository, "get_pathway") == 1  # 第二次走缓存


def test_disabling_cache_sends_every_call_to_repository(fake_search_repository) -> None:
    # Arrange: 复用同一个数据层替身，但关掉缓存
    service = SearchService(fake_search_repository)
    service.enable_cache = False

    # Act
    service.get("hsa04115")
    service.get("hsa04115")

    # Assert
    assert _count(fake_search_repository, "get_pathway") == 2


# --------------------------------------------------------------------------- #
# 用例 1b（回归）：哨兵模式 —— 「未命中」与「命中但值是 None」必须可区分
# --------------------------------------------------------------------------- #
def test_cache_get_returns_sentinel_on_miss_and_none_on_cached_none() -> None:
    """回归：`cache_get` 命中 None 时不能再被当成未命中。

    旧实现 `return self._cache.get(key) if self.enable_cache else None` 用 None
    兼任「未命中」，于是「缓存了 None」和「没缓存」不可分辨。
    """
    # Arrange
    from base_service import BaseService

    service = BaseService()                       # 无数据层钩子也要能测缓存契约

    # Act / Assert
    assert service.cache_get("k") is _MISS        # 默认 enable_cache=False -> 哨兵
    service.enable_cache = True
    assert service.cache_get("k") is _MISS        # 已启用但没写过 -> 仍是哨兵
    service.cache_set("k", None)
    assert service.cache_get("k") is None         # 命中，且值就是 None
    assert service.cache_get("k") is not _MISS


def test_service_caches_none_result_and_second_lookup_hits_cache() -> None:
    """回归：服务层把 None（负结果）缓存下来后，第二次调用不该再回源。"""
    # Arrange
    from base_service import BaseService

    class _NegativeLookup(BaseService[dict, str]):
        """模拟「上游确认查无此人」的负结果缓存。"""

        entity_name = "gene"
        enable_cache = True

        def __init__(self) -> None:
            super().__init__()
            self.upstream_calls = 0

        def resolve(self, symbol: str) -> dict | None:
            key = self.cache_key("resolve", symbol)
            cached = self.cache_get(key)
            if cached is not _MISS:               # 关键：不能写 `is not None`
                return cached
            self.upstream_calls += 1
            self.cache_set(key, None)             # 负结果也缓存，避免反复回源
            return None

    service = _NegativeLookup()

    # Act
    first = service.resolve("NOPE")
    second = service.resolve("NOPE")

    # Assert
    assert first is None and second is None
    assert service.upstream_calls == 1            # 第二次命中缓存的 None，未再回源


# --------------------------------------------------------------------------- #
# 用例 2：未命中 -> NotFoundError（带 code / http_status）
# --------------------------------------------------------------------------- #
def test_get_raises_not_found_for_unknown_id(search_service) -> None:
    # Arrange
    unknown_id = "hsa99999"

    # Act / Assert
    with pytest.raises(NotFoundError) as excinfo:
        search_service.get(unknown_id)

    assert excinfo.value.code == "NOT_FOUND"
    assert excinfo.value.http_status == 404
    assert unknown_id in excinfo.value.message


# --------------------------------------------------------------------------- #
# 用例 3：领域方法 search —— 精确 ID 分支（必须小写化，KEGG id 是小写）
# --------------------------------------------------------------------------- #
def test_search_with_id_style_query_lowercases_and_hits_exact_field(
    search_service, fake_search_repository
) -> None:
    # Arrange
    query = "HSA04115"

    # Act
    results = search_service.search(query)

    # Assert
    assert results == [{"id": "hsa04115", "name": "p53 signaling pathway"}]
    assert ("search", {"id": "hsa04115"}) in fake_search_repository.calls


# --------------------------------------------------------------------------- #
# 用例 4：领域方法 search —— 自由文本分支（走 rank，且 limit 生效）
# --------------------------------------------------------------------------- #
def test_search_free_text_falls_back_to_ranking_and_applies_limit(
    search_service, fake_search_repository
) -> None:
    # Arrange
    query = "p53 signaling"

    # Act
    results = search_service.search(query, limit=1)

    # Assert
    assert results == [{"id": "hsa04115", "score": 0.87}]
    assert ("rank", query) in fake_search_repository.calls


def test_search_rejects_too_short_query(search_service) -> None:
    # Arrange
    query = "a"

    # Act / Assert
    with pytest.raises(ValidationError) as excinfo:
        search_service.search(query)

    assert excinfo.value.http_status == 422
    assert excinfo.value.detail == {"min_length": 2}


# --------------------------------------------------------------------------- #
# 用例 5：校验钩子 _validate_create 被模板方法调用
# --------------------------------------------------------------------------- #
def test_create_rejects_payload_without_id(search_service) -> None:
    # Arrange
    bad_payload = {"name": "no id here"}

    # Act / Assert
    with pytest.raises(ValidationError) as excinfo:
        search_service.create(bad_payload)

    assert excinfo.value.detail == {"field": "id"}


def test_create_update_delete_round_trip(search_service) -> None:
    # Arrange
    payload = {"id": "hsa00010", "name": "Glycolysis"}

    # Act
    created = search_service.create(payload)
    updated = search_service.update("hsa00010", {"name": "Glycolysis (rev)"})
    removed = search_service.delete("hsa00010")

    # Assert
    assert created["id"] == "hsa00010"
    assert updated["name"] == "Glycolysis (rev)"
    assert removed is True
    with pytest.raises(NotFoundError):
        search_service.get("hsa00010")


# --------------------------------------------------------------------------- #
# 用例 6：未知异常被包装为 ServiceError，且 __cause__ 保留原始异常
# --------------------------------------------------------------------------- #
def test_unexpected_repository_error_is_wrapped_preserving_cause() -> None:
    # Arrange: 纯 mock 数据层，模拟底层炸掉
    repository = MagicMock(spec=SearchRepositoryProtocol, name="MockRepo")
    repository.get_pathway.side_effect = RuntimeError("db connection reset")
    service = SearchService(repository)

    # Act / Assert
    with pytest.raises(ServiceError) as excinfo:
        service.get("hsa04115")

    assert isinstance(excinfo.value.__cause__, RuntimeError)
    assert excinfo.value.code == "SERVICE_ERROR"
    assert excinfo.value.http_status == 500
    # 展示名在运行期按 type(self) 解析，因此继承来的模板方法也会显示真实子类名
    assert "SearchService.get" in excinfo.value.message
    assert "db connection reset" in excinfo.value.message


def test_wrap_unexpected_false_lets_raw_error_escape() -> None:
    # Arrange: 直接测装饰器开关（@service_error_handler(wrap_unexpected=False)）
    from base_service import service_error_handler

    class Boom:
        @service_error_handler(wrap_unexpected=False)
        def explode(self) -> None:
            raise ValueError("raw db error")

    # Act / Assert
    with pytest.raises(ValueError, match="raw db error"):
        Boom().explode()


# --------------------------------------------------------------------------- #
# 用例 7：装饰器自动记录「方法名 + 耗时」
# --------------------------------------------------------------------------- #
def test_decorator_logs_method_name_and_elapsed_ms(search_service, caplog) -> None:
    # Arrange
    caplog.set_level(logging.DEBUG)

    # Act: 用子类自己定义（而非继承）的领域方法，qualname 才是子类名
    search_service.search("hsa04115")

    # Assert
    messages = [record.getMessage() for record in caplog.records]
    assert any("SearchService.search" in message and "ms" in message for message in messages)
    # 用的是实例自己的 logger（service.<ClassName>），不是模块级 logger
    assert any(record.name == "service.SearchService" for record in caplog.records)


def test_log_call_records_duration_without_wrapping_errors() -> None:
    # Arrange: @log_call 只记录耗时，不做异常兜底
    from base_service import log_call

    class Sample:
        @log_call(level=logging.DEBUG)
        def read(self) -> str:
            return "ok"

        @log_call(level=logging.DEBUG)
        def explode(self) -> None:
            raise ValueError("raw db error")

    # Act / Assert: 成功路径正常返回，异常原样上抛（不被包装成 ServiceError）
    assert Sample().read() == "ok"
    with pytest.raises(ValueError, match="raw db error"):
        Sample().explode()


def test_error_path_also_logs_method_name_and_elapsed_ms(search_service, caplog) -> None:
    # Arrange
    caplog.set_level(logging.WARNING)

    # Act: 走 NotFoundError 分支
    with pytest.raises(NotFoundError):
        search_service.get("hsa99999")

    # Assert: 失败路径同样记录了方法名 + 耗时
    messages = [record.getMessage() for record in caplog.records]
    assert any("failed in" in message and "ms" in message for message in messages)


# --------------------------------------------------------------------------- #
# 用例 8：ServiceContainer 懒加载契约（service 之间只认 Protocol）
# --------------------------------------------------------------------------- #
def test_service_container_resolves_implementation_lazily_as_singleton(monkeypatch) -> None:
    # Arrange: 把一个假实现塞进 sys.modules，验证容器「首次访问才 import 且只建一次」
    import service_protocols

    class _StubSearchService:
        def search(self, query: str, limit: int = 20) -> list:
            return []

    package = ModuleType("services")
    package.__path__ = []  # type: ignore[attr-defined]
    module = ModuleType("services.search_service")
    module.SearchService = _StubSearchService  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "services", package)
    monkeypatch.setitem(sys.modules, "services.search_service", module)
    monkeypatch.setattr(service_protocols.services, "_instances", {})

    # Act
    first = service_protocols.services.search
    second = service_protocols.services.search

    # Assert
    assert isinstance(first, _StubSearchService)
    assert first is second  # 进程内单例
    assert isinstance(first, service_protocols.SearchServiceProtocol)  # 结构匹配 Protocol


# --------------------------------------------------------------------------- #
# 用例 9（回归）：基类模板方法必须显示**子类名**，不能退化成 BaseService.xxx
# --------------------------------------------------------------------------- #
def test_base_template_method_reports_subclass_name(search_service, caplog) -> None:
    # Arrange
    caplog.set_level(logging.DEBUG)

    # Act: get() 定义在 BaseService 上，但由 SearchService 实例调用
    search_service.get("hsa04115")

    # Assert: 展示名按 type(self) 运行期解析
    messages = [record.getMessage() for record in caplog.records]
    assert any("SearchService.get" in message for message in messages)
    assert not any("BaseService.get" in message for message in messages)


# --------------------------------------------------------------------------- #
# 用例 10（回归）：example_gene_service 的两个真实缺陷
# --------------------------------------------------------------------------- #
def test_gene_service_partial_update_accepts_payload_containing_id(
    in_memory_session,
) -> None:
    """回归：data 里带 "id" 时不能再抛 TypeError，且字符串主键要能命中 int 主键。"""
    # Arrange
    from example_gene_service import GeneService
    from repositories import GeneRepository, get_repository

    service = GeneService(get_repository(GeneRepository, in_memory_session))
    created = service.create(
        {"symbol": "TP53", "name": "tumor protein p53", "pathway_id": "hsa04115"}
    )

    # Act: 主键以字符串传入（模拟 URL 路径参数），payload 里又重复带了一次 id
    updated = service.update(str(created["id"]), {"id": created["id"], "name": "p53 (renamed)"})

    # Assert
    assert updated["name"] == "p53 (renamed)"
    assert updated["symbol"] == "TP53"        # 未提供的字段由 current 合并回填，未被抹掉
    assert service.get(str(created["id"]))["name"] == "p53 (renamed)"


def test_gene_service_rejects_non_integer_id_as_422(in_memory_session) -> None:
    # Arrange
    from example_gene_service import GeneService
    from repositories import GeneRepository, get_repository

    service = GeneService(get_repository(GeneRepository, in_memory_session))

    # Act / Assert: 非法主键归为 422（而不是漏成 500）
    with pytest.raises(ValidationError) as excinfo:
        service.get("not-a-number")
    assert excinfo.value.http_status == 422
    assert excinfo.value.detail == {"field": "id"}


def test_repository_protocol_declares_all_write_methods() -> None:
    """回归：SearchRepositoryProtocol 必须覆盖 BaseService 用到的写入钩子。"""
    # Arrange
    from unittest.mock import MagicMock

    # Act
    mock_repo = MagicMock(spec=SearchRepositoryProtocol, name="MockRepo")

    # Assert: 协议漏声明时，spec 会拦掉这些属性
    for method in ("get_pathway", "search", "rank", "create", "update", "delete"):
        assert hasattr(mock_repo, method)

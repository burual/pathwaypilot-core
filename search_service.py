"""search_service.py — 改造后：继承 BaseService，只写「数据层钩子 + 领域方法」。

数据访问通过注入的 repository 完成，服务本身不 import 任何具体实现（配合 service_protocols 用）。
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Protocol

from base_service import BaseService, ValidationError, service_error_handler
from utils.cache import _MISS


class SearchRepositoryProtocol(Protocol):
    """数据层契约（只依赖接口，不依赖其实现）。

    ⚠️ 必须覆盖 BaseService 模板方法用到的**全部**数据层钩子：
    `_fetch/_query` 走读写，`_persist/_persist_update/_remove` 也需要
    create/update/delete。协议漏声明会让实现侧只能靠 `type: ignore` 硬压，
    也会让 `MagicMock(spec=...)` 在测试里缺属性。
    """

    # --- 读 ---
    def get_pathway(self, pathway_id: str) -> Optional[dict]: ...
    def search(self, **filters: Any) -> list[dict]: ...
    def rank(self, query: str) -> list[tuple[str, float]]: ...
    # --- 写（BaseService 的 create/update/delete 依赖） ---
    def create(self, data: Mapping[str, Any]) -> dict: ...
    def update(self, entity_id: str, data: Mapping[str, Any]) -> dict: ...
    def delete(self, entity_id: str) -> bool: ...


class SearchService(BaseService[dict, str]):
    # ---- 基类可复用配置 ----
    entity_name = "pathway"
    enable_cache = True
    cache_ttl = 120

    def __init__(self, repository: SearchRepositoryProtocol) -> None:
        super().__init__()
        self._repo = repository

    # ------------------------------------------------------------------ #
    # 1) 覆写数据层钩子（供基类 CRUD 复用）
    # ------------------------------------------------------------------ #
    def _fetch(self, entity_id: str) -> Optional[dict]:
        return self._repo.get_pathway(entity_id)

    def _query(self, filters: Mapping[str, Any]) -> list[dict]:
        return self._repo.search(**filters)

    def _persist(self, data: Mapping[str, Any]) -> dict:
        return self._repo.create(data)

    def _persist_update(self, entity_id: str, data: Mapping[str, Any], current: dict) -> dict:
        return self._repo.update(entity_id, data)

    def _remove(self, entity_id: str, current: dict) -> bool:
        return self._repo.delete(entity_id)

    # ------------------------------------------------------------------ #
    # 2) 覆写校验钩子（可选）
    # ------------------------------------------------------------------ #
    def _validate_create(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if not payload.get("id"):
            raise ValidationError("pathway id is required", detail={"field": "id"})
        return payload

    # ------------------------------------------------------------------ #
    # 3) 领域方法：直接复用基类的异常包装 + 日志耗时 + 缓存钩子
    # ------------------------------------------------------------------ #
    @service_error_handler
    def search(self, query: str, limit: int = 20, exact: bool = False) -> list[dict]:
        q = (query or "").strip()
        if len(q) < 2:
            raise ValidationError("query too short", detail={"min_length": 2})

        key = self.cache_key("search", q.lower(), limit, exact)
        cached = self.cache_get(key)
        if cached is not _MISS:      # 哨兵判断：不能写 `is not None`
            return cached

        if exact or self._looks_like_id(q):
            results = self._query({"id": q.lower()})
        else:
            results = self._fuzzy(q, limit)

        self.cache_set(key, results, self.cache_ttl)
        return results

    # ------------------------------------------------------------------ #
    # 4) 纯内部工具：不需要统一兜底/日志，用私有方法即可
    # ------------------------------------------------------------------ #
    @staticmethod
    def _looks_like_id(q: str) -> bool:
        return len(q) >= 6 and q[:3].isalpha() and q[3:].isdigit()

    def _fuzzy(self, q: str, limit: int) -> list[dict]:
        scored = self._repo.rank(q)
        return [{"id": pid, "score": round(score, 4)} for pid, score in scored[:limit]]

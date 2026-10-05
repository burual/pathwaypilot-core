"""示例：service 层如何通过 repository 查询数据。

要点：service 全程只接触 Pydantic 模型，看不到也不依赖任何 SQLAlchemy 对象。
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from base_service import (
    BaseService,
    CacheProtocol,
    NotFoundError,
    ValidationError,
    service_error_handler,
)
from repositories import GeneRepository, get_repository
from schemas import GeneSchema
from utils.cache import _MISS


class GeneService(BaseService[dict, str]):
    entity_name = "gene"
    enable_cache = True
    cache_ttl = 60

    def __init__(
        self,
        gene_repo: GeneRepository,
        *,
        cache: Optional[CacheProtocol] = None,
    ) -> None:
        """`cache` 由调用方注入。

        不传则退化成**实例级** SimpleTTLCache —— 对「一次请求一个 service 实例」
        的 Web 层来说等于没有缓存（请求结束实例就没了）。所以 app/deps.py 会注入
        一个进程级共享后端。
        """
        super().__init__(cache=cache)
        self._genes = gene_repo

    # --- BaseService 数据层钩子 -> 委托 Repository（Repository 返回 Pydantic） ---
    @staticmethod
    def _pk(entity_id: str) -> int:
        """把 URL 里的字符串主键规整成 int；非法主键按 422 处理，而不是漏成 500。

        ⚠️ Gene 主键是自增 int，直接 `session.get(Gene, "1")` 靠 SQLite 隐式转换
        才能侥幸命中，换 PostgreSQL 直接类型错误。
        """
        try:
            return int(entity_id)
        except (TypeError, ValueError):
            raise ValidationError(
                f"gene id must be an integer: {entity_id!r}", detail={"field": "id"}
            ) from None

    def _fetch(self, entity_id: str) -> Optional[dict]:
        gene = self._genes.find_by_id(self._pk(entity_id))
        return gene.model_dump() if gene is not None else None

    def _query(self, filters: Mapping[str, Any]) -> list[dict]:
        return [g.model_dump() for g in self._genes.find_all(**filters)]

    def _persist(self, data: Mapping[str, Any]) -> dict:
        return self._genes.save(GeneSchema(**data)).model_dump()

    def _persist_update(self, entity_id: str, data: Mapping[str, Any], current: dict) -> dict:
        """合并 current 后补主键，因此 payload 可以只带「要改的字段」。

        三个坑都在这一行里：
          ① 不能写成 `GeneSchema(id=..., **data)` —— data 里若也带 "id" 会直接
             `TypeError: got multiple values for keyword argument 'id'`；
          ② 不能只用 data 构造 —— GeneSchema 有必填字段（symbol），局部更新会
             校验失败；`current` 参数就是为此存在的，必须先合并；
          ③ Repository 侧用 exclude_unset 局部更新，所以这里多带的字段只是
             把原值写回，不会误改。
        """
        payload = {**current, **data, "id": self._pk(entity_id)}
        return self._genes.save(GeneSchema(**payload)).model_dump()

    def _remove(self, entity_id: str, current: dict) -> bool:
        return self._genes.delete(self._pk(entity_id))

    # --- 领域方法：直接对外返回 Pydantic 模型 ---
    @service_error_handler
    def get_by_symbol(self, symbol: str) -> GeneSchema:
        gene = self._genes.find_by_symbol(symbol)
        if gene is None:
            raise NotFoundError(f"gene not found: {symbol!r}")
        return gene

    @service_error_handler
    def search(self, keyword: str, limit: int = 20) -> list[GeneSchema]:
        key = self.cache_key("search", keyword.lower(), limit)
        cached = self.cache_get(key)
        if cached is not _MISS:          # 哨兵判断：不能写 `is not None`
            return list(cached)          # 返回副本：避免调用方 append 污染缓存里的那个 list
        genes = self._genes.search(keyword, limit=limit)
        self.cache_set(key, list(genes), self.cache_ttl)
        return genes


# ------------------------- 依赖注入接线（FastAPI） -------------------------
#
# ⚠️ 接线已**真实落地**，别在这里照抄一份：见 app/deps.py（装配）与
#    app/db.py（Session 依赖）、app/routes/genes.py（路由）。
#    下面只留最小形态，方便单文件阅读时理解层次：
#
#   def get_db() -> Iterator[Session]:            # app/db.py
#       db = SessionLocal()
#       try:
#           yield db
#           db.commit()                           # 事务边界在 Web 层
#       finally:
#           db.close()
#
#   def get_gene_service(db: Session = Depends(get_db)) -> GeneService:   # app/deps.py
#       return GeneService(get_repository(GeneRepository, db), cache=SHARED_CACHE)
#
#   @router.get("/api/genes/{symbol}")                                    # app/routes/genes.py
#   def read_gene(symbol: str, svc: GeneService = Depends(get_gene_service)):
#       return svc.get_by_symbol(symbol)      # 返回 Pydantic，FastAPI 自动序列化

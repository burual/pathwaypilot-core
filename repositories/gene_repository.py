"""repositories/gene_repository.py — 具体 Repository 示例。

依赖（按项目实际命名调整 import 即可）：
    db_models.Gene   -> SQLAlchemy ORM（Gene.id / symbol / name / pathway_id / chromosome）
    schemas.GeneSchema -> Pydantic 输出模型
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import or_, select

from db_models import Gene
from repositories.base import BaseRepository
from schemas import GeneSchema

__all__ = ["GeneRepository"]


class GeneRepository(BaseRepository[Gene, GeneSchema]):
    model = Gene
    schema = GeneSchema

    # ------------------------------------------------------------------ #
    # 领域查询：同样只返回 Pydantic，不暴露 ORM
    # ------------------------------------------------------------------ #
    def find_by_symbol(self, symbol: str) -> Optional[GeneSchema]:
        stmt = select(Gene).where(Gene.symbol == symbol.strip().upper())
        obj = self.session.execute(stmt).scalars().first()
        return self._to_schema(obj) if obj is not None else None

    def find_by_pathway(self, pathway_id: str) -> list[GeneSchema]:
        stmt = select(Gene).where(Gene.pathway_id == pathway_id)
        rows = self.session.execute(stmt).scalars().all()
        return [self._to_schema(r) for r in rows]

    def search(self, keyword: str, limit: int = 20) -> list[GeneSchema]:
        pattern = f"%{keyword.strip()}%"
        stmt = (
            select(Gene)
            .where(or_(Gene.symbol.ilike(pattern), Gene.name.ilike(pattern)))
            .order_by(Gene.symbol)
            .limit(limit)
        )
        rows = self.session.execute(stmt).scalars().all()
        return [self._to_schema(r) for r in rows]

"""repositories/pathway_repository.py — 通路 Repository。

db_models.Pathway -> id / name / category / organism / gene_count
schemas.PathwaySchema -> Pydantic 输出模型
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select

from db_models import Pathway
from repositories.base import BaseRepository
from schemas import PathwaySchema

__all__ = ["PathwayRepository"]


class PathwayRepository(BaseRepository[Pathway, PathwaySchema]):
    model = Pathway
    schema = PathwaySchema

    def find_by_organism(self, organism: str) -> list[PathwaySchema]:
        """按物种前缀查询，如 'hsa' -> hsa00010 / hsa04115。"""
        stmt = (
            select(Pathway)
            .where(Pathway.id.like(f"{organism.lower()}%"))
            .order_by(Pathway.id)
        )
        rows = self.session.execute(stmt).scalars().all()
        return [self._to_schema(r) for r in rows]

    def find_by_category(self, category: str) -> list[PathwaySchema]:
        stmt = select(Pathway).where(Pathway.category == category)
        rows = self.session.execute(stmt).scalars().all()
        return [self._to_schema(r) for r in rows]

    def find_largest(self, limit: int = 10) -> list[PathwaySchema]:
        stmt = select(Pathway).order_by(Pathway.gene_count.desc()).limit(limit)
        rows = self.session.execute(stmt).scalars().all()
        return [self._to_schema(r) for r in rows]

    def find_with_name(self, keyword: str) -> Optional[PathwaySchema]:
        stmt = select(Pathway).where(Pathway.name.ilike(f"%{keyword}%")).limit(1)
        obj = self.session.execute(stmt).scalars().first()
        return self._to_schema(obj) if obj is not None else None

"""repositories/analysis_repository.py — 差异分析结果 Repository。

db_models.Analysis -> id / method / dataset / created_at / status
schemas.AnalysisSchema -> Pydantic 输出模型
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select

from db_models import Analysis
from repositories.base import BaseRepository
from schemas import AnalysisSchema

__all__ = ["AnalysisRepository"]


class AnalysisRepository(BaseRepository[Analysis, AnalysisSchema]):
    model = Analysis
    schema = AnalysisSchema

    def find_by_method(self, method: str, limit: int = 50) -> list[AnalysisSchema]:
        stmt = (
            select(Analysis)
            .where(Analysis.method == method)
            .order_by(Analysis.id.desc())
            .limit(limit)
        )
        rows = self.session.execute(stmt).scalars().all()
        return [self._to_schema(r) for r in rows]

    def find_recent(self, limit: int = 10) -> list[AnalysisSchema]:
        stmt = select(Analysis).order_by(Analysis.created_at.desc()).limit(limit)
        rows = self.session.execute(stmt).scalars().all()
        return [self._to_schema(r) for r in rows]

    def find_latest_by_dataset(self, dataset: str) -> Optional[AnalysisSchema]:
        stmt = (
            select(Analysis)
            .where(Analysis.dataset == dataset)
            .order_by(Analysis.created_at.desc())
            .limit(1)
        )
        obj = self.session.execute(stmt).scalars().first()
        return self._to_schema(obj) if obj is not None else None

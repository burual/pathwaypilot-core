"""repositories/__init__.py — Repository 包入口 + get_repository() 工厂。

依赖注入用法（FastAPI / Flask 均可）：
    repo = get_repository(GeneRepository, session)        # 传类
    repo = get_repository("gene", session)                # 传注册名
"""
from __future__ import annotations

from typing import Type, TypeVar

from sqlalchemy.orm import Session

from repositories.analysis_repository import AnalysisRepository
from repositories.base import BaseRepository
from repositories.gene_repository import GeneRepository
from repositories.pathway_repository import PathwayRepository

__all__ = [
    "REPOSITORY_REGISTRY",
    "AnalysisRepository",
    "BaseRepository",
    "GeneRepository",
    "PathwayRepository",
    "get_repository",
]

RepoT = TypeVar("RepoT", bound=BaseRepository)

#: 名称 -> 具体 Repository，供字符串注册名解析
REPOSITORY_REGISTRY: dict[str, type[BaseRepository]] = {
    "gene": GeneRepository,
    "pathway": PathwayRepository,
    "analysis": AnalysisRepository,
}


def get_repository(repo: Type[RepoT] | str, session: Session) -> RepoT:
    """工厂：按类或注册名实例化 Repository，统一绑定 Session。

    - repo 传类：直接实例化，返回精确类型（IDE 可推断）。
    - repo 传字符串：查表后实例化；未知名称抛 KeyError。
    """
    if isinstance(repo, str):
        key = repo.strip().lower()
        try:
            repo = REPOSITORY_REGISTRY[key]  # type: ignore[assignment]
        except KeyError:
            raise KeyError(
                f"未知 Repository: {repo!r}；可用: {sorted(REPOSITORY_REGISTRY)}"
            ) from None
    return repo(session)  # type: ignore[return-value]

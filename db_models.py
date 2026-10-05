"""db_models.py — SQLAlchemy 2.0 ORM 模型（声明式）。

为什么这个文件此前不存在：`tests/conftest.py` 里挂了一段**等价桩**，让数据层测试
在真实模块落地前就能跑。落地本文件后，conftest 的 `find_spec` 探测会命中真实模块，
桩自动失效 —— 字段/约束与那段桩**逐字对齐**，因此既有单测行为不变。

⚠️ 本仓约定：这是**数据层唯一**的 ORM 入口。业务代码（repositories/ 之上）一律不
直接 import 这里的模型，只接触 `schemas.py` 的 Pydantic 模型。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

__all__ = ["Analysis", "Base", "Gene", "Pathway"]


class Base(DeclarativeBase):
    """所有 ORM 模型的声明基类（`Base.metadata` 是建表入口）。"""


class Gene(Base):
    """基因。主键是数据库自增 int（不是 symbol）。"""

    __tablename__ = "genes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(255))
    pathway_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    chromosome: Mapped[str | None] = mapped_column(String(16), nullable=True)

    def __repr__(self) -> str:
        return f"<Gene id={self.id} symbol={self.symbol!r}>"


class Pathway(Base):
    """生物通路。主键是 KEGG 风格的字符串 id，如 hsa04115。"""

    __tablename__ = "pathways"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    organism: Mapped[str | None] = mapped_column(String(16), nullable=True)
    gene_count: Mapped[int] = mapped_column(Integer, default=0)


class Analysis(Base):
    """差异分析任务/结果记录。"""

    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    method: Mapped[str] = mapped_column(String(32))
    dataset: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="done")

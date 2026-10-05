"""repositories/base.py — 数据层抽象基类。

设计约束：
- 内部使用 SQLAlchemy Session 操作 ORM，**对外只返回 Pydantic 模型**，绝不泄漏 ORM 对象。
- save() 只 flush 不 commit（事务边界交给 service / Unit of Work），便于测试与组合。
"""
from __future__ import annotations

from abc import ABC
from typing import Any, Generic, Mapping, Optional, Type, TypeVar

from pydantic import BaseModel
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select
from sqlalchemy.orm import Session

__all__ = ["BaseRepository", "ModelT", "SchemaT"]

ModelT = TypeVar("ModelT")                       # SQLAlchemy ORM 模型
SchemaT = TypeVar("SchemaT", bound=BaseModel)    # Pydantic 输出模型


class BaseRepository(Generic[ModelT, SchemaT], ABC):
    """四个模板方法：find_by_id / find_all / save / delete。

    子类只需在类级别绑定 `model`（ORM）与 `schema`（Pydantic）。
    """

    model: Type[ModelT]
    schema: Type[SchemaT]

    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------ #
    # 只读
    # ------------------------------------------------------------------ #
    def find_by_id(self, entity_id: Any) -> Optional[SchemaT]:
        """按主键取单条，不存在返回 None。"""
        obj = self.session.get(self.model, entity_id)
        return self._to_schema(obj) if obj is not None else None

    def find_all(
        self,
        *,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order_by: Optional[str] = None,
        **filters: Any,
    ) -> list[SchemaT]:
        """按等值条件查询；None 值条件自动忽略。"""
        stmt = select(self.model)
        for field, value in filters.items():
            if value is None:
                continue
            if not hasattr(self.model, field):
                raise ValueError(f"{self.model.__name__} 无字段 {field!r}")
            stmt = stmt.where(getattr(self.model, field) == value)
        if order_by:
            column = getattr(self.model, order_by.lstrip("-"), None)
            if column is None:
                raise ValueError(f"{self.model.__name__} 无排序字段 {order_by!r}")
            stmt = stmt.order_by(column.desc() if order_by.startswith("-") else column.asc())
        if offset is not None:
            stmt = stmt.offset(offset)
        if limit is not None:
            stmt = stmt.limit(limit)
        rows = self.session.execute(stmt).scalars().all()
        return [self._to_schema(r) for r in rows]

    # ------------------------------------------------------------------ #
    # 写入
    # ------------------------------------------------------------------ #
    def save(self, data: SchemaT | Mapping[str, Any]) -> SchemaT:
        """Upsert：主键命中则更新，否则新增。返回 Pydantic 模型。

        更新分支是**局部更新**：只写调用方显式给的字段，未提供的列保持原值
        （见 `_dump` 的 exclude_unset 说明）。不 commit，只 flush。
        """
        payload = self._dump(data)
        pk_name = self._pk_name()
        pk_value = payload.get(pk_name)
        obj = self.session.get(self.model, pk_value) if pk_value is not None else None

        fields = self._only_model_fields(payload)
        if obj is None:
            obj = self.model(**fields)
            self.session.add(obj)
        else:
            for key, value in fields.items():
                if key != pk_name:
                    setattr(obj, key, value)

        self.session.flush()
        self.session.refresh(obj)
        return self._to_schema(obj)

    def delete(self, entity_id: Any) -> bool:
        """按主键删除，返回是否命中。"""
        obj = self.session.get(self.model, entity_id)
        if obj is None:
            return False
        self.session.delete(obj)
        self.session.flush()
        return True

    # ------------------------------------------------------------------ #
    # 内部工具（子类一般无需关心）
    # ------------------------------------------------------------------ #
    def _to_schema(self, obj: ModelT) -> SchemaT:
        return self.schema.model_validate(obj, from_attributes=True)

    @staticmethod
    def _dump(data: SchemaT | Mapping[str, Any]) -> dict[str, Any]:
        """把入参规整成 dict。

        ⚠️ Pydantic 输入必须用 `exclude_unset=True`：否则调用方没写的字段会带着
        模型默认值（多为 None）一起进库，`save()` 的「局部更新」会把**已有列直接
        抹成 NULL**。exclude_unset 只保留显式赋值过的字段；显式传 None 仍会带上
        （那才是「真的想置空」）。dict 输入本就只含调用方给的键，无需处理。
        """
        if isinstance(data, BaseModel):
            return data.model_dump(exclude_unset=True)
        return dict(data)

    def _only_model_fields(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        columns = {col.key for col in sa_inspect(self.model).columns}
        return {k: v for k, v in payload.items() if k in columns}

    def _pk_name(self) -> str:
        return sa_inspect(self.model).primary_key[0].key

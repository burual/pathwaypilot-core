"""schemas.py — Pydantic v2 模型（数据层/服务层对外的唯一契约）。

与 `db_models.py` 的分工：
    db_models  = 数据库里长什么样（内部实现）
    schemas    = 对外返回什么（稳定契约）
Repository 只把 ORM 对象翻译成这里的模型，绝不把 ORM 对象泄漏出去。

`from_attributes=True` 是 `model_validate(obj)` 能从 ORM 对象读属性的前提
（Pydantic v2 里它取代了 v1 的 `orm_mode`）。

⚠️ 同样地与 `tests/conftest.py` 里的等价桩逐字对齐，落地后桩自动失效。

文档约定（本文件所有字段都遵守）：
    * `description` —— 字段的业务含义与**边界行为**（可为空 / 谁生成 / 何时变），
      会出现在 OpenAPI 的 JSON Schema 里；
    * `examples`   —— 合法值示例，给 Swagger UI 的「Try it out」和读者做参照；
    * 模型级 `json_schema_extra={"example": {...}}` —— 该模型的完整报文示例。

⚠️ 这里只加**纯文档元数据**，不改任何默认值与必填性：
    `Field(description=..)` 不传 default，字段保持 required；
    `model_config` 在子类里写只写 `json_schema_extra`，Pydantic 会与基类的
    `from_attributes=True` **合并**（不是覆盖），因此 `model_validate(orm_obj)`
    照旧可用。这两点由 `tests/test_api_docs.py` 与既有数据层用例共同钉住。
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["AnalysisSchema", "GeneSchema", "PathwaySchema"]


class _OrmBase(BaseModel):
    """所有输出模型的基类：允许从 ORM 对象属性构造。"""

    model_config = ConfigDict(from_attributes=True)


class GeneSchema(_OrmBase):
    """基因（对外契约）。主键是数据库自增 int，不是 symbol。"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": 1,
                "symbol": "TP53",
                "name": "tumor protein p53",
                "pathway_id": "hsa04115",
                "chromosome": "17p13.1",
            }
        }
    )

    id: int | None = Field(
        default=None,
        description=(
            "数据库自增主键。**仅当对象已落库时才有值**；"
            "构造待插入对象时保持缺省，由数据库生成后在响应里回传。"
        ),
        examples=[1, 42],
    )
    symbol: str = Field(
        description="基因符号（HGNC symbol），存储与匹配时统一为大写。长度 1–32。",
        examples=["TP53", "BRCA1", "EGFR"],
    )
    name: str = Field(
        description="基因全称。长度 1–255，无唯一约束（允许同名录入）。",
        examples=["tumor protein p53", "BRCA1 DNA repair associated"],
    )
    pathway_id: str | None = Field(
        default=None,
        description=(
            "所属通路 id（KEGG 风格小写），长度 ≤32。"
            "`None` 表示尚未归属任何通路，**不代表查询失败**。"
        ),
        examples=["hsa04115", "hsa00010", None],
    )
    chromosome: str | None = Field(
        default=None,
        description="染色体定位，长度 ≤16。`None` 表示未知。",
        examples=["17p13.1", "13q13.1", None],
    )


class PathwaySchema(_OrmBase):
    """生物通路（对外契约）。主键是 KEGG 风格的**字符串** id。"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "hsa04115",
                "name": "p53 signaling pathway",
                "category": "Cellular Processes",
                "organism": "hsa",
                "gene_count": 68,
            }
        }
    )

    id: str = Field(
        description="通路 id。与 `GeneSchema.id` 不同，这里是**字符串且由外部给定**（非自增）。",
        examples=["hsa04115", "hsa00010"],
    )
    name: str = Field(
        description="通路名称。长度 ≤255。",
        examples=["p53 signaling pathway", "Glycolysis / Gluconeogenesis"],
    )
    category: str | None = Field(
        default=None,
        description="KEGG 一级分类（`Cellular Processes` / `Metabolism` 等），≤64 字符。",
        examples=["Cellular Processes", "Metabolism", None],
    )
    organism: str | None = Field(
        default=None,
        description="物种三字母码，≤16 字符。`hsa` = 人类。",
        examples=["hsa", "mmu", None],
    )
    gene_count: int = Field(
        default=0,
        description="该通路收录的基因数。缺省 0（表示未统计，而非该通路没有基因）。",
        examples=[0, 68],
    )


class AnalysisSchema(_OrmBase):
    """差异分析任务/结果记录（对外契约）。"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": 7,
                "method": "deseq2",
                "dataset": "airway",
                "created_at": "2026-09-15T15:55:34Z",
                "status": "done",
            }
        }
    )

    id: int | None = Field(
        default=None,
        description="数据库自增主键。未落库时为 `None`。",
        examples=[7, None],
    )
    method: str = Field(
        description="差异分析方法名（小写）。长度 ≤32。",
        examples=["deseq2", "edger", "limma"],
    )
    dataset: str = Field(
        description="使用的数据集标识。长度 ≤64。",
        examples=["airway", "gse12345"],
    )
    created_at: datetime | None = Field(
        default=None,
        description=(
            "任务创建时间（ISO 8601，UTC）。`None` 表示尚未写入时间戳。"
            "⚠️ 该列无数据库默认值，需由业务层显式赋值。"
        ),
        examples=["2026-09-15T15:55:34Z", None],
    )
    status: str = Field(
        default="done",
        description="任务状态。缺省 `done`（当前实现只区分字符串，未做枚举约束）。",
        examples=["done", "running", "failed"],
    )

"""app/routes/genes.py — /api/genes 资源路由（真实 HTTP 层）。

分工：路由只做三件事 —— 解析/校验入参、调 service、套统一响应信封。
业务逻辑在 service，SQL 在 repository，错误映射在 app/main.py 的异常处理器。

文档载荷（markdown 说明 + JSON 示例 + `responses` 条目）全在 `genes_docs.py`：
路由是**逻辑**、文档是**数据**，分开后本文件只剩可读的接口形状。
函数 docstring 保留**代码维护者**视角的说明（为什么这么写、坑在哪）。

⚠️ tags 只写在**路由装饰器**上，不写在 `APIRouter(tags=[...])` 里：
    FastAPI 对 tags 是**合并而非覆盖**（实测 router=['genes'] + route=['genes']
    会得到 ['genes', 'genes']），重复会让 /docs 侧栏出现同名分组两份。
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, ConfigDict, Field

from app.deps import get_gene_service
from app.routes.genes_docs import (
    BODY_ERROR,
    BY_SYMBOL_DESC,
    CREATE_DESC,
    DELETE_DESC,
    EXISTING_RESOURCE_ERRORS,
    LIST_DESC,
    OK_CREATED,
    OK_LIST,
    OK_NONE,
    OK_ONE,
    QUERY_ERROR,
    READ_DESC,
    TAG,
    UPDATE_DESC,
)
from example_gene_service import GeneService
from utils.response import created, list_response, success

__all__ = ["GeneCreateRequest", "GeneUpdateRequest", "router"]

router = APIRouter(prefix="/api/genes")

#: 用 Annotated 声明依赖，避免把 Depends(...) 写在默认值里（B008 误报的老写法）
ServiceDep = Annotated[GeneService, Depends(get_gene_service)]


# --------------------------------------------------------------------------- #
# 请求模型：约束与 ORM 列宽一致 —— 越界直接 422，而不是让 SQLite 静默截断
# --------------------------------------------------------------------------- #
class GeneCreateRequest(BaseModel):
    """POST 请求体。`extra="forbid"` 让拼错的字段暴露成 422，而不是被悄悄忽略。"""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "symbol": "TP53",
                "name": "tumor protein p53",
                "pathway_id": "hsa04115",
                "chromosome": "17p13.1",
            }
        },
    )

    symbol: str = Field(
        min_length=1,
        max_length=32,
        description="基因符号（官方 HGNC symbol），统一按大写存储与匹配。",
        examples=["TP53", "BRCA1", "EGFR"],
    )
    name: str = Field(
        min_length=1,
        max_length=255,
        description="基因全称。默认无唯一约束，允许同名重复录入。",
        examples=["tumor protein p53", "BRCA1 DNA repair associated"],
    )
    pathway_id: str | None = Field(
        default=None,
        max_length=32,
        description="所属通路 id，KEGG 风格小写，可为空（尚未归属任何通路时）。",
        examples=["hsa04115", "hsa00010"],
    )
    chromosome: str | None = Field(
        default=None,
        max_length=16,
        description="染色体定位，cell bank 风格书写，可为空。",
        examples=["17p13.1", "13q13.1"],
    )


class GeneUpdateRequest(BaseModel):
    """PUT 请求体。**全字段可选** —— 语义是局部更新（PATCH 化）。"""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"name": "p53 renamed"}},
    )

    symbol: str | None = Field(
        default=None,
        min_length=1,
        max_length=32,
        description="新符号。**不传 = 保持不变**；传 `null` 与不传在本模型上等价（符号列非空）。",
        examples=["TP53"],
    )
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="新全称。不传则保持不变。",
        examples=["p53 renamed"],
    )
    pathway_id: str | None = Field(
        default=None,
        max_length=32,
        description="新通路 id。**显式传 null 会把它清空**（与不传不同）。",
        examples=["hsa04115"],
    )
    chromosome: str | None = Field(
        default=None,
        max_length=16,
        description="新染色体定位。显式传 null 会把它清空。",
        examples=["17p13.1"],
    )


#: path 参数复用的描述（`gene_id` 在 3 条路由里出现）。
#: ⚠️ 只加 description、**不加 `ge=1` 之类的约束** —— 那会改变接口行为
#: （`/api/genes/0` 现在是走 service 得 404，加约束后变 422），属于语义变更而非文档。
GeneIdPath = Annotated[
    int,
    Path(description="基因的数据库自增主键（整数）。非整数会被挡成 422。", examples=[1]),
]


def _as_dict(entity: Any) -> dict[str, Any]:
    """Pydantic -> dict（service 的领域方法返回 Pydantic 模型）。"""
    return entity.model_dump() if hasattr(entity, "model_dump") else dict(entity)


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #
@router.post(
    "",
    status_code=201,
    tags=[TAG],
    summary="新建基因记录",
    response_description="新建成功，data 为含自增 id 的完整基因对象",
    description=CREATE_DESC,
    responses={**OK_CREATED, **BODY_ERROR},
)
def create_gene(payload: GeneCreateRequest, svc: ServiceDep) -> Any:
    """创建。201 + 信封，data 里带数据库生成的自增 id。"""
    return created(_as_dict(svc.create(payload.model_dump())), "gene created")


@router.get(
    "",
    tags=[TAG],
    summary="分页查询基因列表",
    response_description="查询成功，data 为基因对象数组，meta.count 为本页条数",
    description=LIST_DESC,
    responses={**OK_LIST, **QUERY_ERROR},
)
def list_genes(
    svc: ServiceDep,
    symbol: str | None = Query(
        default=None,
        description="按符号精确过滤，大小写不敏感。不传则不过滤。",
        examples=["TP53"],
    ),
    pathway_id: str | None = Query(
        default=None,
        description="按通路 id 精确过滤。不传则不过滤。",
        examples=["hsa04115"],
    ),
    limit: Annotated[
        int, Query(ge=1, le=200, description="返回条数上限，1–200。", examples=[20])
    ] = 20,
    offset: Annotated[
        int, Query(ge=0, description="跳过的条数，用于翻页。", examples=[0])
    ] = 0,
) -> Any:
    """列表。未给的过滤条件由 repository 自动忽略（None 不作为等值条件）。"""
    items = svc.list(symbol=symbol, pathway_id=pathway_id, limit=limit, offset=offset)
    return list_response([_as_dict(item) for item in items])


@router.get(
    "/by-symbol/{symbol}",
    tags=[TAG],
    summary="按基因符号查询",
    response_description="查询成功，data 为匹配的单个基因对象",
    description=BY_SYMBOL_DESC,
    responses={**OK_ONE, **EXISTING_RESOURCE_ERRORS},
)
def read_gene_by_symbol(
    symbol: Annotated[
        str,
        Path(description="基因符号，大小写不敏感。", examples=["TP53", "tp53"]),
    ],
    svc: ServiceDep,
) -> Any:
    """按 symbol 查询（symbol 会被 service 归一成大写再匹配）。

    ⚠️ 必须放在 `/{gene_id}` 之前注册：FastAPI 按注册顺序匹配，且
    `/by-symbol/TP53` 是两段路径、与单段 `/{gene_id}` 本就不冲突 —— 这里显式
    前置只是为了让意图一眼可见，别把顺序当成可随意调整的细节。
    """
    return success(_as_dict(svc.get_by_symbol(symbol)))


@router.get(
    "/{gene_id}",
    tags=[TAG],
    summary="按主键查询基因",
    response_description="查询成功，data 为单个基因对象",
    description=READ_DESC,
    responses={**OK_ONE, **EXISTING_RESOURCE_ERRORS},
)
def read_gene(gene_id: GeneIdPath, svc: ServiceDep) -> Any:
    """按主键查询。非整数主键由 FastAPI 直接挡成 422（不会走到 service）。"""
    return success(_as_dict(svc.get(str(gene_id))))


@router.put(
    "/{gene_id}",
    tags=[TAG],
    summary="局部更新基因",
    response_description="更新成功，data 为更新后的完整基因对象",
    description=UPDATE_DESC,
    responses={**OK_ONE, **EXISTING_RESOURCE_ERRORS, **BODY_ERROR},
)
def update_gene(gene_id: GeneIdPath, payload: GeneUpdateRequest, svc: ServiceDep) -> Any:
    """局部更新：只覆盖客户端显式给出的字段。

    ⚠️ `exclude_unset=True` 不是可选优化，而是正确性要求：不加它的话，模型里
    客户端**没写过**的字段会带着默认值 None 一起 dump 出来，服务层将其合并进
    current 后，未被触及的列会被一并写成 NULL（典型现象：只改 name，symbol 却空了）。
    """
    data = payload.model_dump(exclude_unset=True)
    return success(_as_dict(svc.update(str(gene_id), data)), "gene updated")


@router.delete(
    "/{gene_id}",
    tags=[TAG],
    summary="删除基因",
    response_description="删除成功，data 为 null，meta.deleted 说明是否真的删掉了一条",
    description=DELETE_DESC,
    responses={**OK_NONE, **EXISTING_RESOURCE_ERRORS},
)
def delete_gene(gene_id: GeneIdPath, svc: ServiceDep) -> Any:
    """删除。目标不存在时 service 抛 NotFoundError -> 404（幂等语义由调用方决定）。"""
    deleted = svc.delete(str(gene_id))
    return success(None, "gene deleted", deleted=bool(deleted))

"""app/openapi_models.py — **仅供 OpenAPI 文档使用**的响应信封模型。

为什么需要这个文件：本仓路由一律 `-> Any`，刻意让响应信封**原样透传**
（见 `app/main.py::health` 的注释）。代价是 `schemas.py` 里的输出模型不会出现在
`/docs` 里 —— 字段说明写了也看不见。本模块把信封补成 Pydantic 模型，
只用于给 OpenAPI 提供 schema。

⚠️ **不要**把它们用作路由的 `response_model`。一旦设成 response_model，
FastAPI 会按模型校验并**裁掉**模型里没有的键，以下运行时特性会静默消失：

    * `created()` 塞进 meta 的 `status=201`
    * `list_response()` 塞进 meta 的 `count`
    * `delete_gene()` 塞进 meta 的 `deleted`

正确用法（只注册 schema、不参与校验）：

    @router.get("/x", responses={200: {"model": ApiEnvelope[GeneSchema]}})

实测该写法下 `components.schemas` 会出现这些模型（所以 /docs 能看到字段说明与
示例），而运行时响应仍原封不动。本仓约定与 `utils/response.ApiResponse`
（TypedDict）保持一致，两者字段名由 `tests/test_api_docs.py` 钉住同步。
"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["ApiEnvelope", "ApiError", "T"]

T = TypeVar("T")


class ApiError(BaseModel):
    """失败信封里的 `error` 对象，与 `utils.response.error()` 的产出逐字对应。"""

    code: str = Field(
        description="机器可读的错误码。完整列表见 `docs/API.md` 的错误码表。",
        examples=["NOT_FOUND", "VALIDATION_ERROR", "SERVICE_ERROR"],
    )
    detail: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "结构化补充信息。普通业务错误为空对象 `{}`；"
            "入参校验失败时是 `{\"errors\": [{\"type\":..,\"loc\":..,\"msg\":..,\"input\":..}]}`。"
        ),
        examples=[{}, {"errors": [{"type": "int_parsing", "loc": ["path", "gene_id"]}]}],
    )
    status: int | None = Field(
        default=None,
        description=(
            "该错误对应的 HTTP 状态码，与响应行里的状态码一致。"
            "经由 `from_exception()` 生成的错误总是带此字段。"
        ),
        examples=[404, 422, 500],
    )


class ApiEnvelope(BaseModel, Generic[T]):
    """统一响应信封：`{success, data, message, meta, error}`。

    成功与失败**共用同一结构**（失败时 `success=false`、`data=null`、`error` 非空），
    因此客户端可以只写一套解包逻辑。

    Args:
        T: `data` 的载荷类型。列表端点用 `ApiEnvelope[list[GeneSchema]]`。
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "success": True,
                "data": {"id": 1, "symbol": "TP53", "name": "tumor protein p53"},
                "message": "ok",
                "meta": {},
                "error": None,
            }
        }
    )

    success: bool = Field(
        description="请求是否成功。**客户端应以此字段为准**，而不是只看 HTTP 状态码。",
        examples=[True, False],
    )
    data: T | None = Field(
        default=None,
        description="载荷。失败时为 `null`；删除类成功操作也为 `null`。",
    )
    message: str = Field(
        default="ok",
        description="人类可读的简短说明，仅供展示与日志，**不要用于程序判断**。",
        examples=["ok", "gene created", "gene not found: '999'"],
    )
    meta: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "附加元信息，随端点而异：列表端点有 `count`，创建有 `status: 201`，"
            "删除有 `deleted`。**其中的键不是稳定契约**，缺省时为空对象。"
        ),
        examples=[{"count": 1}, {"deleted": True}],
    )
    error: ApiError | None = Field(
        default=None,
        description="失败详情。成功时为 `null`。",
    )

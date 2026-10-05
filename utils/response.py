"""utils/response.py — 统一响应格式（成功 / 失败 / 分页 Response 构造器）。

单一职责：构造统一 API 响应信封。**不 import 本包内其他 utils 模块**。
信封结构：
    {"success": bool, "data": Any | None, "message": str,
     "meta": {...}, "error": {"code":.., "detail":.., "status":..} | None}

HTTP 状态码通过 `to_http()` 或 `error()` 的 status 字段携带，由框架层决定实际响应码。
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Optional, TypedDict

__all__ = [
    "ApiResponse",
    "created",
    "error",
    "from_exception",
    "is_success",
    "list_response",
    "merge_meta",
    "no_content",
    "page_meta",
    "paginate",
    "success",
    "to_http",
]


class ApiResponse(TypedDict, total=False):
    """统一响应信封类型。"""

    success: bool
    data: Any
    message: str
    meta: dict[str, Any]
    error: Optional[dict[str, Any]]


def success(data: Any = None, message: str = "ok", **meta: Any) -> ApiResponse:
    """构造成功响应信封。额外关键字参数进入 meta。"""
    return {"success": True, "data": data, "message": message, "meta": dict(meta), "error": None}


def error(
    code: str,
    message: str,
    *,
    detail: Optional[Mapping[str, Any]] = None,
    status: Optional[int] = None,
    **meta: Any,
) -> ApiResponse:
    """构造失败响应信封。`status` 记录期望的 HTTP 状态码（供框架层使用）。"""
    err: dict[str, Any] = {"code": code, "detail": dict(detail or {})}
    if status is not None:
        err["status"] = int(status)
    return {"success": False, "data": None, "message": message, "meta": dict(meta), "error": err}


def created(data: Any = None, message: str = "created", **meta: Any) -> ApiResponse:
    """构造「已创建」成功响应（meta.status=201，供框架层取用）。"""
    payload = success(data, message, **meta)
    payload["meta"]["status"] = 201
    return payload


def no_content(message: str = "no content") -> ApiResponse:
    """构造「无内容」成功响应（data=None）。"""
    return success(None, message)


def list_response(
    items: list[Any], message: str = "ok", **meta: Any
) -> ApiResponse:
    """构造列表响应：data 为数组，meta.count 记录条数。"""
    payload = success(list(items), message, **meta)
    payload["meta"]["count"] = len(items)
    return payload


def page_meta(total: int, page: int, page_size: int) -> dict[str, Any]:
    """生成分页元信息（页码从 1 开始；自动纠偏非法入参）。"""
    size = max(1, int(page_size))
    current = max(1, int(page))
    total = max(0, int(total))
    pages = max(1, math.ceil(total / size)) if total else 0
    return {
        "total": total,
        "page": current,
        "page_size": size,
        "pages": pages,
        "has_prev": current > 1,
        "has_next": current < pages,
        "offset": (current - 1) * size,
    }


def paginate(
    items: list[Any],
    total: int,
    page: int,
    page_size: int,
    message: str = "ok",
    **meta: Any,
) -> ApiResponse:
    """构造分页响应：data.items + data.pagination，meta 同步记录分页信息。"""
    pagination = page_meta(total, page, page_size)
    payload = success(
        {"items": list(items), "pagination": pagination}, message, **meta
    )
    payload["meta"].update(pagination)
    return payload


def from_exception(
    exc: BaseException,
    *,
    default_code: str = "INTERNAL_ERROR",
    default_status: int = 500,
    include_detail: bool = True,
) -> ApiResponse:
    """把异常映射为失败信封。

    鸭子类型识别 `code` / `http_status` / `detail` 属性（如 ServiceError），
    因此本模块无需 import 任何异常基类。
    """
    code = getattr(exc, "code", None) or default_code
    status = getattr(exc, "http_status", None) or default_status
    raw_detail = getattr(exc, "detail", None) if include_detail else None
    message = getattr(exc, "message", None) or str(exc) or exc.__class__.__name__
    return error(str(code), str(message), detail=raw_detail, status=int(status))


def merge_meta(payload: ApiResponse, **meta: Any) -> ApiResponse:
    """把额外键合并进响应信封的 meta（原地更新并返回）。"""
    merged = dict(payload.get("meta") or {})
    merged.update(meta)
    payload["meta"] = merged
    return payload


def is_success(payload: Mapping[str, Any]) -> bool:
    """判断信封是否代表成功（缺省视为失败）。"""
    return bool(payload.get("success"))


def to_http(payload: ApiResponse, default_status: int = 200) -> tuple[ApiResponse, int]:
    """拆出 (信封, HTTP 状态码)，供框架层直接返回。"""
    err = payload.get("error") or {}
    status = int(err.get("status") or payload.get("meta", {}).get("status") or default_status)
    return payload, status

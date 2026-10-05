"""app/main.py — FastAPI 应用工厂。

    uvicorn app.main:app --reload          # 开发
    uvicorn app.main:app --workers 1       # ⚠️ 有进程内状态（共享缓存）时别开多 worker

本文件只负责「把路由挂起来 + 定义错误映射 + 声明应用级文档元数据」。
业务/数据/装配分别在 routes / repositories / deps 三层。

文档元数据为什么要写在这里：`title` / `description` / `openapi_tags` 决定了
`/docs` 首屏与侧栏分组。放在应用工厂而非路由上，是为了让「有哪些分组」有一个
单一出处 —— 路由那边只声明 `tags=[TAG]`，分组说明在这里补齐。
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.openapi_models import ApiEnvelope
from app.routes.genes import router as genes_router
from base_service import ServiceError
from utils.response import error, from_exception, success, to_http

__all__ = ["app", "create_app"]

#: OpenAPI 的 info.description（markdown，会渲染在 /docs 顶部）。
#: ⚠️ 「鉴权」一节必须**如实**描述当前状态：本工作区磁盘上没有 `auth/` 包
#: （`tests/conftest.py` 里那份 jwt/hash/permissions 是测试用的参考实现），
#: 因此所有端点目前**不做鉴权**。请不要在这里写成「需要 Bearer Token」。
_APP_DESCRIPTION = """
PathwayPilot 的基因/通路数据服务。基于 FastAPI + SQLAlchemy 2.0 + Pydantic v2。

## 统一响应信封

**所有**响应（含错误）都是同一个五字段结构，客户端只需写一套解包逻辑：

```json
{"success": true, "data": {}, "message": "ok", "meta": {}, "error": null}
```

* 判断成功请用 `success` 字段，**不要只看 HTTP 状态码**；
* `message` 仅供展示，不要用于程序判断；
* `meta` 里的键（`count` / `status` / `deleted`）**不是稳定契约**；
* `error.code` 才是机器可读的错误标识。

## 鉴权

⚠️ **当前版本未启用任何鉴权** —— 所有端点可直接访问。本节如实记录现状，
以免读者误以为需要传 Token。

路线图上计划的方案（尚未实现）：`Authorization: Bearer <JWT>`，由独立的
`auth/` 包负责签发与校验。`docs/API.md` 的「认证方式」一节给出了启用后的
调用形式，可先按那个格式预留客户端代码。

## 错误处理

业务错误由 `ServiceError` 及其子类统一表达，经全局异常处理器映射为
`error.code` + HTTP 状态码；入参校验失败会套同一个信封（而非 FastAPI 默认的
`{"detail": [...]}`）。完整错误码表见 `docs/API.md`。

## 相关文档

* Swagger UI：`/docs`　·　ReDoc：`/redoc`　·　OpenAPI JSON：`/openapi.json`
* 面向使用者的中文文档（含全部 curl 示例）：仓库内 `docs/API.md`
"""

#: `/docs` 侧栏的分组顺序与说明。分组名必须与路由上的 `tags` 取值完全一致，
#: 否则 FastAPI 会另起一个没有描述的分组。
_OPENAPI_TAGS = [
    {
        "name": "genes",
        "description": (
            "基因资源的增删改查。主键是数据库自增整数；符号查询请用 "
            "`/api/genes/by-symbol/{symbol}`（大小写不敏感）。"
        ),
    },
    {
        "name": "meta",
        "description": "服务自身的状态端点，不涉及业务数据。",
    },
]

#: 文档里的示例请求地址。uvicorn 默认端口即 8000。
_SERVERS = [{"url": "http://localhost:8000", "description": "本地开发（uvicorn 默认端口）"}]

#: 成功响应用的信封 schema（`data` 为 null，供 health 这类端点复用）
_HEALTH_EXAMPLE: dict[str, Any] = {
    "success": True,
    "data": {"status": "ok"},
    "message": "ok",
    "meta": {},
    "error": None,
}


def create_app() -> FastAPI:
    """应用工厂：可以重复构造互不干扰的实例（测试里每个用例一份）。"""
    application = FastAPI(
        title="PathwayPilot API",
        version="0.1.0",
        summary="基因与通路数据的统一 HTTP 接口（统一响应信封 + 中文文档）",
        description=_APP_DESCRIPTION,
        openapi_tags=_OPENAPI_TAGS,
        servers=_SERVERS,
    )

    # --------------------------------------------------------------------- #
    # 错误映射：统一信封 + 由异常自带的 http_status 决定状态码
    #   放在全局而非每个路由 try/except —— 路由只表达正常路径，映射只有一份，
    #   以后新增路由不会漏掉错误处理。
    # --------------------------------------------------------------------- #
    @application.exception_handler(ServiceError)
    async def _service_error(_request: Request, exc: ServiceError) -> JSONResponse:
        payload = from_exception(exc)
        _, status = to_http(payload)
        return JSONResponse(status_code=status, content=payload)

    @application.exception_handler(RequestValidationError)
    async def _request_validation(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """入参校验失败也套同一信封（默认的 {"detail": [...]} 与本仓信封不一致）。

        ⚠️ 必须过一遍 jsonable_encoder：Pydantic 的 errors() 的 ctx 里可能带
        异常对象，直接丢给 JSONResponse 会 TypeError。
        """
        payload = error(
            "VALIDATION_ERROR",
            "request validation failed",
            detail={"errors": jsonable_encoder(exc.errors())},
            status=422,
        )
        return JSONResponse(status_code=422, content=payload)

    @application.get(
        "/api/health",
        tags=["meta"],
        summary="健康检查",
        response_description="服务存活，data.status 恒为 ok",
        description="""
存活探针：只要进程还能响应就返回 200。

**不检查下游依赖** —— 数据库/缓存不可用时本端点**依然返回 200**。
需要「依赖就绪」语义请另加 `/api/ready`（尚未实现）。

**输入**：无。

```bash
curl http://localhost:8000/api/health
```

**输出**

```json
{
  "success": true,
  "data": {"status": "ok"},
  "message": "ok",
  "meta": {},
  "error": null
}
```
""",
        responses={200: {"model": ApiEnvelope[Any], "content": {"application/json": {"example": _HEALTH_EXAMPLE}}}},
    )
    def health() -> Any:
        # 返回类型标 Any 而非 dict：信封是 TypedDict，写成具体类型会被 FastAPI
        # 当成 response_model 去做响应校验/裁剪，反而不符合本仓「信封原样透传」的意图。
        return success({"status": "ok"})

    application.include_router(genes_router)
    return application


#: uvicorn / gunicorn 的入口对象
app = create_app()

"""tests/test_api_docs.py — 文档契约测试：让「文档漏写」变成红灯。

为什么值得单独测：文档的失效方式很安静 —— 少写一个 `description`、增删字段忘了
同步、示例报文写错一个键，代码全都照跑，只有读者被误导。这类漂移无法靠代码评审
可靠拦截，但可以靠断言拦截。

本文件钉住四件事：
    1. 每个操作都有 summary / description / tags / response_description，
       且 summary 与 description 是**显式声明**的（不能是框架的兜底值）；
    2. 每个请求/输出模型的**每个字段**都有 description，且模型级 example 合法；
    3. 响应信封模型与 `utils/response.ApiResponse` 的字段名严格一致（防两边各改各的）；
    4. OpenAPI 里所有响应示例**真的能通过信封模型校验** —— 即示例不是手写臆造的。

⚠️ 这里不打真实数据库：OpenAPI 生成与 /docs 渲染都不碰 DB，所以本文件**不带
   `integration` 标记**，会随 pre-commit 快子集一起跑。
"""
from __future__ import annotations

import inspect
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from app.main import create_app
from app.openapi_models import ApiEnvelope
from app.routes.genes import GeneCreateRequest, GeneUpdateRequest
from app.routes.genes import router as genes_router
from schemas import AnalysisSchema, GeneSchema, PathwaySchema
from utils.response import ApiResponse

#: 需要被文档化的输出模型（`app/routes/genes.py` 的 responses 引用它们，
#: 因此会出现在 `components.schemas`，其字段说明也就会出现在 /docs）。
OUTPUT_MODELS: list[type[BaseModel]] = [GeneSchema, PathwaySchema, AnalysisSchema]

#: 需要被文档化的请求模型。
REQUEST_MODELS: list[type[BaseModel]] = [GeneCreateRequest, GeneUpdateRequest]

ALL_MODELS = [*REQUEST_MODELS, *OUTPUT_MODELS]

#: 信封要求的五个键，任何响应示例都必须齐全。
ENVELOPE_KEYS = {"success", "data", "message", "meta", "error"}


@pytest.fixture
def spec() -> dict[str, Any]:
    """真实应用的 OpenAPI 文档。"""
    return create_app().openapi()


def _operations(spec: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """展平成 (method, path, operation) 三元组。"""
    return [
        (method.upper(), path, op)
        for path, methods in spec["paths"].items()
        for method, op in methods.items()
    ]


# --------------------------------------------------------------------------- #
# 用例 1：每个操作都必须有完整的文档元数据
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("field", ["summary", "description", "tags"])
def test_every_operation_documents_required_fields(spec: dict[str, Any], field: str) -> None:
    """summary / description / tags 三者都必须非空。"""
    # Act / Assert
    for method, path, op in _operations(spec):
        assert op.get(field), f"{method} {path} 的 {field} 为空"


# --------------------------------------------------------------------------- #
# 用例 1b：文档元数据必须是**显式声明**的，不能靠框架兜底
#
# ⚠️ 上面那条只断言「非空」，而 FastAPI 会给两个字段自动兜底，于是它会假绿：
#     * summary —— `generate_operation_summary()` 在未声明时回退为
#       `route.name.replace("_", " ").title()`（`create_gene` -> "Create Gene"）；
#     * description —— 路由创建时 `description or cleandoc(endpoint.__doc__)`。
#   因此这里断言在**路由对象**上，并把兜底值显式排除掉。
# --------------------------------------------------------------------------- #
def _app_level_routes() -> list[APIRoute]:
    """直接挂在 app 上的 APIRoute（`/api/health` 是在 create_app 里定义的）。"""
    return [r for r in create_app().routes if isinstance(r, APIRoute)]


def _all_api_routes() -> list[APIRoute]:
    """本仓定义的**全部**业务路由。

    ⚠️ 不要用「遍历 `app.routes` 再按 isinstance(APIRoute) 过滤」来收集！
    本 FastAPI 版本里 `include_router` 是**惰性**的：挂到 app 上的是一个
    `fastapi.routing._IncludedRouter` 包装对象，而非展平的 APIRoute。
    那样过滤会**静默漏掉全部 6 条 genes 路由**，只剩 `/api/health`，
    循环于是平凡通过 —— 顺带让「删掉 summary」这类变异完全测不出来。
    （这个坑是变异校验发现的，不是猜的。）

    改为直接从 APIRouter 对象取。漏覆盖的风险由
    `test_route_collection_covers_every_operation` 交叉校验兜住。
    """
    own = [r for r in genes_router.routes if isinstance(r, APIRoute)]
    return [*own, *_app_level_routes()]


def test_route_collection_covers_every_operation(spec: dict[str, Any]) -> None:
    """交叉校验：上面收集到的路由必须与 OpenAPI 里的操作**一一对应**。

    没有这条，若有人新增一个 router 却忘了纳入 `_all_api_routes()`，
    下面那几条断言就会悄悄少检查一部分端点（"检查了"变成"检查了其中一些"）。
    """
    # Arrange
    from_spec = {(method, path) for method, path, _ in _operations(spec)}
    from_routes = {
        (method, route.path)
        # `APIRoute.methods` 的类型是 `set[str] | None`，直接迭代会被 mypy 拦下
        for route in _all_api_routes()
        for method in (route.methods or set())
        if method not in {"HEAD", "OPTIONS"}
    }

    # Assert
    assert from_routes == from_spec, (
        f"路由收集有缺口：仅 OpenAPI 有 {from_spec - from_routes}，"
        f"仅路由对象有 {from_routes - from_spec}"
    )


def test_every_operation_has_explicit_summary() -> None:
    """summary 必须是显式声明的一句话，不能是函数名转出来的自动值。"""
    # Arrange
    routes = _all_api_routes()

    # Assert
    assert routes, "没有解析到任何 APIRoute，测试失去意义"
    for route in routes:
        auto = route.name.replace("_", " ").title()
        assert route.summary, f"{route.path} 未声明 summary（会退化成自动值 {auto!r}）"
        assert route.summary != auto, f"{route.path} 的 summary 恰好等于自动生成值 {auto!r}"


def test_every_operation_has_explicit_description() -> None:
    """description 必须是显式传入的富文本，而不是函数 docstring 的兜底。"""
    # Arrange
    routes = _all_api_routes()

    # Assert
    for route in routes:
        docstring = inspect.cleandoc(route.endpoint.__doc__ or "")
        assert route.description, f"{route.path} 未声明 description"
        assert route.description != docstring, (
            f"{route.path} 的 description 只是函数 docstring —— 说明 description= 被删了"
        )


def test_response_description_is_overridden() -> None:
    """`response_description` 必须被显式覆写。

    它**非空但无用**：默认值就是 "Successful Response"，所以这里断言的是
    「不等于默认值」而不是「非空」—— 只测非空的话，全用默认值也能过。
    """
    # Act / Assert
    for route in _all_api_routes():
        assert route.response_description != "Successful Response", (
            f"{route.path} 的 response_description 仍是默认值"
        )


def test_every_operation_has_documented_responses(spec: dict[str, Any]) -> None:
    """每个状态码都要有说明文字，否则 /docs 里只剩一个光秃秃的 "200"。"""
    # Act / Assert
    for method, path, op in _operations(spec):
        responses = op.get("responses") or {}
        assert responses, f"{method} {path} 没有声明任何响应"
        for status, entry in responses.items():
            assert entry.get("description"), f"{method} {path} 的 {status} 响应没有 description"


def test_operation_tags_are_declared_and_not_duplicated(spec: dict[str, Any]) -> None:
    """tag 必须已在 `openapi_tags` 声明（否则 /docs 会另起一个无描述的分组），且不重复。

    重复这条是有来历的：`APIRouter(tags=[...])` 与路由级 `tags=` 是**合并**关系，
    两处都写同一个名字会得到 `['genes', 'genes']`，侧栏出现两份同名分组。
    """
    # Arrange
    declared = {tag["name"] for tag in spec.get("tags", [])}

    # Act / Assert
    for method, path, op in _operations(spec):
        tags = op.get("tags") or []
        assert tags, f"{method} {path} 没有 tags"
        assert len(tags) == len(set(tags)), f"{method} {path} 的 tags 有重复: {tags}"
        undeclared = set(tags) - declared
        assert not undeclared, f"{method} {path} 用了未声明的 tag: {undeclared}"


# --------------------------------------------------------------------------- #
# 用例 2：模型字段的文档
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("model", ALL_MODELS, ids=lambda m: m.__name__)
def test_every_model_field_has_description_and_examples(model: type[BaseModel]) -> None:
    """字段必须有 description 与 examples —— 这两样是 Swagger UI 里唯一能看的东西。"""
    # Act / Assert
    for name, field in model.model_fields.items():
        assert field.description, f"{model.__name__}.{name} 缺少 description"
        assert field.examples, f"{model.__name__}.{name} 缺少 examples"


@pytest.mark.parametrize("model", ALL_MODELS, ids=lambda m: m.__name__)
def test_model_level_example_is_present_and_valid(model: type[BaseModel]) -> None:
    """模型级 example 必须合法 —— 防止示例里写了个不存在的字段或错的类型。

    这条是「示例会不会骗人」的唯一保障：JSON Schema 不会校验 example。
    """
    # Arrange
    schema = model.model_json_schema()

    # Act
    example = schema.get("example")
    assert example is not None, f"{model.__name__} 缺少 json_schema_extra 里的 example"

    # Assert：示例必须能真正构造出该模型
    model.model_validate(example)


def test_model_examples_cover_every_documented_field() -> None:
    """模型级 example 应尽量覆盖所有字段，否则读者看不出字段长什么样。

    这里只对**必填字段**强制要求覆盖（可选字段留空是有意义的示例）。
    """
    # Act / Assert
    for model in ALL_MODELS:
        example = model.model_json_schema()["example"]
        required = {n for n, f in model.model_fields.items() if f.is_required()}
        missing = required - set(example)
        assert not missing, f"{model.__name__} 的 example 漏了必填字段: {missing}"


def test_output_models_are_registered_in_components(spec: dict[str, Any]) -> None:
    """输出模型必须真的出现在 `components.schemas`。

    ⚠️ 这是「字段说明写了却没人看得到」的防线：本仓路由一律 `-> Any`（信封原样透传），
    只有当路由的 `responses={...}` 里带了 `model=` 时，模型才会被注册进 OpenAPI。
    若哪天有人删掉那些 `model=`，字段文档就会从 /docs 里静默消失，而所有功能测试照绿。
    """
    # Arrange
    registered = set(spec.get("components", {}).get("schemas", {}))

    # Act / Assert
    assert GeneSchema.__name__ in registered, "GeneSchema 未注册进 OpenAPI components"
    assert any("ApiEnvelope" in name for name in registered), "信封模型未注册"


# --------------------------------------------------------------------------- #
# 用例 3：信封模型与运行时信封必须同构
# --------------------------------------------------------------------------- #
def test_envelope_model_matches_runtime_typeddict() -> None:
    """`app.openapi_models.ApiEnvelope` 的字段名必须与 `utils.response.ApiResponse` 一致。

    两者一个是 Pydantic 模型（给 OpenAPI 用）、一个是 TypedDict（运行时用）。
    分开维护是刻意的（模型不能作为 response_model，否则会裁剪信封），
    但字段名必须锁死，否则文档描述的就不是实际返回的东西。
    """
    # Act
    documented = set(ApiEnvelope.model_fields)
    runtime = set(ApiResponse.__annotations__)

    # Assert
    assert documented == runtime, f"信封字段不一致：仅文档有 {documented - runtime}，仅运行时有 {runtime - documented}"


# --------------------------------------------------------------------------- #
# 用例 4：OpenAPI 里的响应示例必须自洽
# --------------------------------------------------------------------------- #
def _examples(spec: dict[str, Any]) -> list[tuple[str, str, int, dict[str, Any]]]:
    """抽出所有 (method, path, status, example)。"""
    found: list[tuple[str, str, int, dict[str, Any]]] = []
    for method, path, op in _operations(spec):
        for status, entry in (op.get("responses") or {}).items():
            example = (entry.get("content") or {}).get("application/json", {}).get("example")
            if example is not None:
                found.append((method, path, int(status), example))
    return found


def test_response_examples_exist_for_every_operation(spec: dict[str, Any]) -> None:
    """每个操作至少要有一个带示例的响应 —— 示例比 schema 更好懂。"""
    # Act
    covered = {(m, p) for m, p, _, _ in _examples(spec)}
    all_ops = {(m, p) for m, p, _ in _operations(spec)}

    # Assert
    assert all_ops - covered == set(), f"这些操作没有任何响应示例: {all_ops - covered}"


def test_response_examples_validate_against_envelope(spec: dict[str, Any]) -> None:
    """响应示例必须是合法的信封结构，且不含多余的键。

    ⚠️ 这里**不能**断言「五个键全齐」。FastAPI 在 `openapi/utils.py` 的
    `get_openapi()` 结尾对整个文档做了：

        jsonable_encoder(OpenAPI(**output), by_alias=True, exclude_none=True)

    于是**任何 `null` 值都会从 OpenAPI 里被剥掉** —— 成功示例少了 `error: null`，
    失败示例少了 `data: null`。这是框架行为，与本仓代码无关，也无法通过配置关掉。

    因此真正的报文以两处为准（都不受该行为影响）：
        * 路由 `description` 里的 JSON 代码块（字符串，原样保留 null）；
        * `docs/API.md`（独立的 markdown，照抄真实输出）。
    """
    # Act / Assert
    for method, path, status, example in _examples(spec):
        label = f"{method} {path} 的 {status} 示例"
        try:
            ApiEnvelope[Any].model_validate(example)
        except ValidationError as exc:  # pragma: no cover - 失败时才有意义
            pytest.fail(f"{label} 不符合信封模型:\n{exc}")

        extra = set(example) - ENVELOPE_KEYS
        assert not extra, f"{label} 含信封之外的键: {extra}"

        # 只要求「真实非 null」的键存在：
        #   success/message/meta 恒有值；失败时 error 必有值。
        #   `data` 不能用同一条规则 —— `DELETE` 成功时 data 本来就是 null，
        #   会被 exclude_none 一并剥掉，硬要求它存在是错的。
        required = {"success", "message", "meta"}
        if not example["success"]:
            required.add("error")
        missing = required - set(example)
        assert not missing, f"{label} 缺少键 {missing}"


def test_response_examples_are_semantically_consistent(spec: dict[str, Any]) -> None:
    """示例的成功/失败语义必须自洽。

    这条抓的是「手写示例时最容易犯的错」：把 404 的示例写成 `success: true`，
    或者失败示例里 `error` 忘了填 —— 报文结构合法但语义矛盾。

    ⚠️ 一律用 `.get()` 取值：由于上面那条 `exclude_none` 约束，`error: null`
    这个键在成功示例里**是不存在的**，直接下标访问会 KeyError。
    用 `.get()` 则「键缺失」与「显式 null」都判为 None，两种情形都正确处理。
    """
    # Act / Assert
    for method, path, status, example in _examples(spec):
        label = f"{method} {path} 的 {status} 示例"
        if example["success"]:
            assert example.get("error") is None, f"{label} 声明成功却带了 error"
            assert status < 400, f"{label} 声明成功却是错误状态码"
        else:
            assert example.get("error") is not None, f"{label} 声明失败却没有 error"
            assert example.get("data") is None, f"{label} 声明失败却带了 data"
            assert status >= 400, f"{label} 声明失败却是成功状态码"


# --------------------------------------------------------------------------- #
# 用例 5：文档端点本身可用
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("url", ["/docs", "/redoc", "/openapi.json", "/api/health"])
def test_documentation_endpoints_are_served(url: str) -> None:
    """/docs、/redoc、/openapi.json 必须可访问 —— 否则「已生成文档」是假的。"""
    # Arrange
    app = create_app()

    # Act
    with TestClient(app) as client:
        response = client.get(url)

    # Assert
    assert response.status_code == 200

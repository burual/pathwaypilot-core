"""app/routes/genes_docs.py — `/api/genes` 的 **OpenAPI 文档载荷**（纯数据，无逻辑）。

拆出来的理由：这些 markdown 说明与 JSON 示例有 390+ 行，塞在 `genes.py` 里会让
路由逻辑被文档淹没（原文件 613 行，远超本仓「每文件 ≤300 行」的约定）。
文档是**数据**，路由是**逻辑**，两者的修改动机也不同 —— 分开后各改各的。

⚠️ 唯一的运行期约束：这些常量只经 `description=` / `responses=` 进入 OpenAPI
生成流程，**不参与任何请求处理**。改错它们只会让文档变难看，不会让接口行为变化
（反过来说：改了却没人发现，所以由 `tests/test_api_docs.py` 钉住）。

⚠️ 下面的响应 JSON **逐字照抄真实运行输出**（用 TestClient 实际打出来的）。
手编示例最容易漏 `meta` / `error` 键，或把 404 的 code 写成别的 ——
文档与实现不一致比没有文档更糟。**改接口后请一并更新这里与 `docs/API.md`。**
"""
from __future__ import annotations

from typing import Any

from app.openapi_models import ApiEnvelope
from schemas import GeneSchema

__all__ = [
    "BODY_ERROR",
    "BY_SYMBOL_DESC",
    "CREATE_DESC",
    "DELETE_DESC",
    "EXISTING_RESOURCE_ERRORS",
    "LIST_DESC",
    "OK_CREATED",
    "OK_LIST",
    "OK_NONE",
    "OK_ONE",
    "QUERY_ERROR",
    "READ_DESC",
    "TAG",
    "UPDATE_DESC",
]

#: 文档统一分组名（只此一处定义，避免各处硬编码拼错导致分组漂移）
TAG = "genes"


# --------------------------------------------------------------------------- #
# 真实报文示例
# --------------------------------------------------------------------------- #
_EX_GENE: dict[str, Any] = {
    "id": 1,
    "symbol": "TP53",
    "name": "tumor protein p53",
    "pathway_id": "hsa04115",
    "chromosome": "17p13.1",
}

_EX_CREATED: dict[str, Any] = {
    "success": True,
    "data": _EX_GENE,
    "message": "gene created",
    "meta": {"status": 201},
    "error": None,
}

_EX_OK: dict[str, Any] = {
    "success": True,
    "data": _EX_GENE,
    "message": "ok",
    "meta": {},
    "error": None,
}

_EX_LIST: dict[str, Any] = {
    "success": True,
    "data": [_EX_GENE],
    "message": "ok",
    "meta": {"count": 1},
    "error": None,
}

_EX_DELETED: dict[str, Any] = {
    "success": True,
    "data": None,
    "message": "gene deleted",
    "meta": {"deleted": True},
    "error": None,
}

_EX_NOT_FOUND: dict[str, Any] = {
    "success": False,
    "data": None,
    "message": "gene not found: '999'",
    "meta": {},
    "error": {"code": "NOT_FOUND", "detail": {}, "status": 404},
}

_EX_VALIDATION: dict[str, Any] = {
    "success": False,
    "data": None,
    "message": "request validation failed",
    "meta": {},
    "error": {
        "code": "VALIDATION_ERROR",
        "detail": {
            "errors": [
                {
                    "type": "int_parsing",
                    "loc": ["path", "gene_id"],
                    "msg": "Input should be a valid integer, unable to parse string as an integer",
                    "input": "abc",
                }
            ]
        },
        "status": 422,
    },
}


# --------------------------------------------------------------------------- #
# 响应条目构造
# --------------------------------------------------------------------------- #
def _resp(
    status: int,
    description: str,
    example: dict[str, Any],
    model: Any = None,
) -> dict[int | str, dict[str, Any]]:
    """构造 OpenAPI 的单个响应条目（纯文档用，不进任何运行时路径）。

    ⚠️ `model` 只会被 FastAPI 拿去**注册 schema**，不会成为 `response_model` ——
    响应仍原样透传（本仓刻意如此，见 `app/openapi_models.py` 的模块 docstring）。
    schema 与 example 必须同时给：schema 让 /docs 能展开字段说明，example 让人
    一眼看到真实报文长什么样。

    ⚠️ 返回类型必须写 `dict[int | str, dict[str, Any]]`：mypy 的 dict 键**不协变**，
    写成 `dict[int, ...]` 会让所有调用点的 `{**a, **b}` 展开报 dict-item 错。

    Args:
        status: HTTP 状态码，作为 `responses` 字典的键。
        description: 这个状态码代表什么。
        example: 完整响应体示例（**照抄真实输出**）。
        model: 信封模型，如 `ApiEnvelope[GeneSchema]`；None 表示只给示例。

    Returns:
        `{status: {...}}` 形式的单键字典，供调用方 `{**...}` 展开使用。
    """
    entry: dict[str, Any] = {
        "description": description,
        "content": {"application/json": {"example": example}},
    }
    if model is not None:
        # FastAPI 会把 model 解析成 content.application/json.schema（与上面的
        # example 并列，都在标准位置），因此两者可以共存。
        entry["model"] = model
    return {status: entry}


def _err(status: int, code: str, when: str, example: dict[str, Any]) -> dict[int | str, dict[str, Any]]:
    """构造 OpenAPI 的单个错误响应条目（信封模型 + 真实示例）。"""
    return _resp(status, f"{when}（信封 `error.code = {code}`）", example, model=ApiEnvelope[Any])


#: 需要「资源存在」的端点共用的错误响应（404 + 422），避免四条路由各写一份
EXISTING_RESOURCE_ERRORS: dict[int | str, dict[str, Any]] = {
    **_err(404, "NOT_FOUND", "目标基因不存在", _EX_NOT_FOUND),
    **_err(422, "VALIDATION_ERROR", "路径参数格式非法（如 id 不是整数）", _EX_VALIDATION),
}

#: 带请求体的端点额外要说明 body 校验失败的样子（loc 前缀会变成 ["body", ...]）
BODY_ERROR = _err(422, "VALIDATION_ERROR", "请求体缺字段或超出长度约束", _EX_VALIDATION)

#: 列表端点的 query 参数越界
QUERY_ERROR = _err(422, "VALIDATION_ERROR", "查询参数越界（如 limit>200）", _EX_VALIDATION)

#: 成功响应（按载荷类型区分：单对象 / 对象数组 / 无载荷）
OK_ONE = _resp(200, "查询成功", _EX_OK, model=ApiEnvelope[GeneSchema])
OK_CREATED = _resp(201, "创建成功", _EX_CREATED, model=ApiEnvelope[GeneSchema])
OK_LIST = _resp(200, "查询成功", _EX_LIST, model=ApiEnvelope[list[GeneSchema]])
OK_NONE = _resp(200, "删除成功", _EX_DELETED, model=ApiEnvelope[Any])


# --------------------------------------------------------------------------- #
# 路由描述（markdown，FastAPI 会在 /docs 里渲染）
# --------------------------------------------------------------------------- #
CREATE_DESC = """
新建一个基因记录，主键由数据库自增生成并在响应里回传。

**输入**

* `symbol`（必填）：基因符号，1–32 字符。服务层会统一按大写存储与匹配。
* `name`（必填）：基因全称，1–255 字符。
* `pathway_id`（可选）：所属通路，KEGG 风格小写 id（如 `hsa04115`），≤32 字符。
* `chromosome`（可选）：染色体位置，≤16 字符（如 `17p13.1`）。

请求体为 `extra="forbid"`：**多写一个字段就报 422**，而不是被静默忽略 ——
拼错的 `symbols` 会立刻暴露，不会变成一条字段全空的记录。

**输出**

`201 Created`，`data` 为落库后的完整对象（含生成的 `id`）。

```bash
curl -X POST http://localhost:8000/api/genes \\
  -H 'Content-Type: application/json' \\
  -d '{"symbol":"TP53","name":"tumor protein p53","pathway_id":"hsa04115","chromosome":"17p13.1"}'
```

```json
{
  "success": true,
  "data": {
    "id": 1,
    "symbol": "TP53",
    "name": "tumor protein p53",
    "pathway_id": "hsa04115",
    "chromosome": "17p13.1"
  },
  "message": "gene created",
  "meta": {"status": 201},
  "error": null
}
```
"""

LIST_DESC = """
分页查询基因，所有过滤条件都是可选的。

**输入（query 参数）**

* `symbol`：按符号精确匹配（同样按大写归一）。
* `pathway_id`：按通路 id 精确匹配。
* `limit`：返回条数，1–200，默认 20。
* `offset`：跳过条数，≥0，默认 0。

未提供的过滤条件由 repository 自动忽略（`None` 不会变成 `WHERE col IS NULL`）。

⚠️ 当前是「过滤 + 分页」而非「分页元信息」：响应 `meta` 只给 `count`
（本页实际条数），**没有** `total`。需要总数请另行统计。

**输出**

`200 OK`，`data` 为对象数组。

```bash
curl 'http://localhost:8000/api/genes?pathway_id=hsa04115&limit=5&offset=0'
```

```json
{
  "success": true,
  "data": [
    {"id": 1, "symbol": "TP53", "name": "tumor protein p53",
     "pathway_id": "hsa04115", "chromosome": "17p13.1"}
  ],
  "message": "ok",
  "meta": {"count": 1},
  "error": null
}
```
"""

BY_SYMBOL_DESC = """
按基因符号查询单条记录。

⚠️ 与按主键查询的区别：

* 路径是 `/by-symbol/{symbol}`，**两段**，与单段的 `/{gene_id}` 不冲突；
* 传入的是**符号字符串**（如 `TP53`），大小写不敏感（服务层归一成大写后匹配）；
* 符号不存在时同样返回 `404 NOT_FOUND`（而非空对象）。

**输出**

`200 OK`，`data` 为单个基因对象；找不到则 `404`。

```bash
curl http://localhost:8000/api/genes/by-symbol/tp53
```

```json
{
  "success": true,
  "data": {"id": 1, "symbol": "TP53", "name": "tumor protein p53",
           "pathway_id": "hsa04115", "chromosome": "17p13.1"},
  "message": "ok",
  "meta": {},
  "error": null
}
```
"""

READ_DESC = """
按数据库自增主键查询单条记录。

**输入**

* `gene_id`（path，必填）：**整数**主键。非整数（如 `abc`）会被 FastAPI 直接
  挡成 `422`，根本不会走到服务层。

**输出**

`200 OK`，`data` 为单个基因对象；主键不存在则 `404`。

```bash
curl http://localhost:8000/api/genes/1
```

```json
{
  "success": true,
  "data": {"id": 1, "symbol": "TP53", "name": "tumor protein p53",
           "pathway_id": "hsa04115", "chromosome": "17p13.1"},
  "message": "ok",
  "meta": {},
  "error": null
}
```

找不到时：

```json
{
  "success": false,
  "data": null,
  "message": "gene not found: '999'",
  "meta": {},
  "error": {"code": "NOT_FOUND", "detail": {}, "status": 404}
}
```
"""

UPDATE_DESC = """
局部更新（语义上更接近 PATCH）：**只覆盖请求体里显式给出的字段**，没给的字段保持原值。

**输入**

* `gene_id`（path，必填）：整数主键。
* 请求体全部字段可选：`symbol` / `name` / `pathway_id` / `chromosome`。

⚠️ 「不传」和「传 null」含义不同：

| 请求体 | 效果 |
|---|---|
| `{"name":"p53 renamed"}` | 只改 `name`，`symbol`/`pathway_id`/`chromosome` **原样保留** |
| `{"pathway_id": null}` | 把 `pathway_id` **显式置空** |

这是靠 `model_dump(exclude_unset=True)` 实现的：不加它，未写过的字段会带着默认值
`None` 一起 dump 出来，合并进现有记录后会把无关的列写成 NULL
（典型现象：只改 `name`，`symbol` 却空了）。

**输出**

`200 OK`，`data` 为更新后的完整对象；主键不存在则 `404`。

```bash
curl -X PUT http://localhost:8000/api/genes/1 \\
  -H 'Content-Type: application/json' \\
  -d '{"name":"p53 renamed"}'
```

```json
{
  "success": true,
  "data": {"id": 1, "symbol": "TP53", "name": "p53 renamed",
           "pathway_id": "hsa04115", "chromosome": "17p13.1"},
  "message": "gene updated",
  "meta": {},
  "error": null
}
```
"""

DELETE_DESC = """
按主键删除记录。

**输入**

* `gene_id`（path，必填）：整数主键。

**输出**

`200 OK`（**不是 204**）：本仓统一走响应信封，因此即使没有响应体数据，
也返回 `data: null`，并用 `meta.deleted` 说明是否真的删掉了一条。

⚠️ 目标不存在时返回 `404`，**不是幂等成功** —— 想做成幂等删除，
请在调用方把 404 视为「已达成目标」。

```bash
curl -X DELETE http://localhost:8000/api/genes/1
```

```json
{
  "success": true,
  "data": null,
  "message": "gene deleted",
  "meta": {"deleted": true},
  "error": null
}
```
"""

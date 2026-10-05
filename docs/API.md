# PathwayPilot API 参考

面向**接口使用者**的 HTTP 文档。本文件里的每一个响应报文都是**照抄真实运行输出**
（用 `TestClient` 实际打出来的），不是手写的示意。

| | |
|---|---|
| 交互式文档 | `/docs`（Swagger UI）· `/redoc`（ReDoc）· `/openapi.json` |
| 基础地址 | `http://localhost:8000`（本地开发；见 OpenAPI 的 `servers`） |
| 版本 | `0.1.0` |
| 数据格式 | 请求与响应均为 `application/json; charset=utf-8` |
| 鉴权 | ⚠️ **当前未启用**，见 [第 2 节](#2-认证方式) |

---

## 目录

1. [快速开始](#1-快速开始)
2. [认证方式](#2-认证方式)
3. [端点参考](#3-端点参考)
4. [错误码说明](#4-错误码说明)
5. [维护约定](#5-维护约定)

---

## 1. 快速开始

### 1.1 启动服务

```bash
# 依赖：fastapi / uvicorn / sqlalchemy / pydantic
python -m pip install -r requirements-dev.txt

# 启动（⚠️ 有进程内状态（共享缓存），workers 必须是 1）
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --reload
```

启动后打开 <http://localhost:8000/docs> 可交互试用全部端点。

### 1.2 冒烟验证

```bash
curl -s http://localhost:8000/api/health
```

```json
{
  "success": true,
  "data": { "status": "ok" },
  "message": "ok",
  "meta": {},
  "error": null
}
```

### 1.3 统一响应信封

**所有**响应 —— 成功与失败 —— 都是同一个五字段结构：

```json
{
  "success": true,
  "data": {},
  "message": "ok",
  "meta": {},
  "error": null
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `success` | bool | **判断成功请以此为准**，不要只看 HTTP 状态码 |
| `data` | any \| null | 载荷。失败时为 `null`；删除类成功操作也为 `null` |
| `message` | string | 人类可读说明。**仅供展示，不要用于程序判断** |
| `meta` | object | 附加元信息，**键不是稳定契约**：列表有 `count`、创建有 `status`、删除有 `deleted` |
| `error` | object \| null | 失败详情 `{code, detail, status}`；成功时为 `null` |

> 设计取舍：本服务的路由**刻意不声明 `response_model`**，让信封原样透传 ——
> 否则 FastAPI 会按模型裁剪响应，`meta` 里的 `count` / `status` / `deleted` 会被悄悄丢掉。
> OpenAPI 里的 schema 由 `app/openapi_models.py`（**仅文档用**）提供。

### 1.4 列表的过滤与分页

`GET /api/genes` 的查询参数：

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `symbol` | string | — | 按符号精确过滤，**大小写不敏感**。不传 = 不过滤 |
| `pathway_id` | string | — | 按通路 id 精确过滤。不传 = 不过滤 |
| `limit` | int | `20` | 返回条数，`1–200`，越界返回 422 |
| `offset` | int | `0` | 跳过条数，`≥0`，用于翻页 |

⚠️ 当前实现返回的是**本页条数**（`meta.count`），**没有** `total` 字段。
需要总数请另行统计，不要试图从 `data.length` 推断。

---

## 2. 认证方式

### 2.1 当前状态：未启用

**本版本没有任何鉴权。** 所有端点可直接访问，无需任何请求头。

这一点值得强调，因为仓库里确实有 JWT / 密码哈希 / 权限矩阵的实现与测试
（`tests/test_auth.py`），但它们**只是测试用的参考契约**，
磁盘上并不存在 `auth/` 包，也没有任何路由挂载鉴权依赖。

因此：**不要**在客户端里传 `Authorization` 头并期待它被校验 —— 传了也会被忽略
（不会报错，但也不会提供任何保护）。生产部署前必须自行在网关层补齐鉴权。

### 2.2 路线图上的方案（尚未实现）

计划采用 `Authorization: Bearer <JWT>`，由独立的 `auth/` 包负责签发与校验。
参考契约与 `tests/test_auth.py` 保持一致，可据此**预先**准备客户端代码：

| 项 | 约定 |
|---|---|
| 请求头 | `Authorization: Bearer <token>` |
| 算法 | `HS256` |
| 载荷（claims） | `sub`（主体标识）、`iat`（签发时间）、`exp`（过期时间），可附加自定义 claim（如 `role`） |
| 默认有效期 | 1800 秒 |
| 令牌非法/过期 | `401`，`error.code = "INVALID_TOKEN"` |
| 权限不足 | `403`，`error.code = "ACCESS_DENIED"`（fail-closed：未知角色零权限） |

启用后的调用形态（**当前会失败，因为还没有签发端点和校验**）：

```bash
TOKEN="<your-jwt>"
curl -s http://localhost:8000/api/genes/1 -H "Authorization: Bearer $TOKEN"
```

---

## 3. 端点参考

| 方法 | 路径 | 说明 | 成功码 |
|---|---|---|---|
| `POST` | `/api/genes` | 新建基因记录 | `201` |
| `GET` | `/api/genes` | 分页查询基因列表 | `200` |
| `GET` | `/api/genes/by-symbol/{symbol}` | 按基因符号查询（大小写不敏感） | `200` |
| `GET` | `/api/genes/{gene_id}` | 按数据库自增主键查询 | `200` |
| `PUT` | `/api/genes/{gene_id}` | 局部更新（只改显式给出的字段） | `200` |
| `DELETE` | `/api/genes/{gene_id}` | 删除 | `200` |
| `GET` | `/api/health` | 健康检查 | `200` |

---

### 3.1 新建基因记录

```http
POST /api/genes
Content-Type: application/json
```

**请求体**

| 字段 | 类型 | 必填 | 约束 | 说明 |
|---|---|---|---|---|
| `symbol` | string | ✅ | 1–32 字符 | 基因符号，存储与匹配时统一为大写 |
| `name` | string | ✅ | 1–255 字符 | 基因全称，无唯一约束 |
| `pathway_id` | string \| null | ❌ | ≤32 字符 | 所属通路，KEGG 风格小写（如 `hsa04115`） |
| `chromosome` | string \| null | ❌ | ≤16 字符 | 染色体定位（如 `17p13.1`） |

⚠️ 请求体是 `extra="forbid"`：**多写一个字段就返回 422**，而不是被静默忽略。
把 `symbol` 拼成 `symbols` 会立刻报错，不会变成一条字段全空的记录。

```bash
curl -s -X POST http://localhost:8000/api/genes \
  -H 'Content-Type: application/json' \
  -d '{
        "symbol": "TP53",
        "name": "tumor protein p53",
        "pathway_id": "hsa04115",
        "chromosome": "17p13.1"
      }'
```

`201 Created`：

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
  "meta": { "status": 201 },
  "error": null
}
```

`id` 由数据库自增生成，**只在响应里回传**，请求中不要传（传了会被 422 拒绝）。

---

### 3.2 分页查询基因列表

```bash
curl -s 'http://localhost:8000/api/genes?pathway_id=hsa04115&limit=5&offset=0'
```

`200 OK`：

```json
{
  "success": true,
  "data": [
    {
      "id": 1,
      "symbol": "TP53",
      "name": "tumor protein p53",
      "pathway_id": "hsa04115",
      "chromosome": "17p13.1"
    }
  ],
  "message": "ok",
  "meta": { "count": 1 },
  "error": null
}
```

空结果不是错误：返回 `200` + `data: []` + `meta.count: 0`。
参数越界（如 `limit=201`、`offset=-1`）返回 `422`。

---

### 3.3 按基因符号查询

```bash
# 大小写不敏感：tp53 / TP53 / Tp53 等价
curl -s http://localhost:8000/api/genes/by-symbol/tp53
```

`200 OK`：`data` 为单个基因对象（结构同 3.1 的 `data`）。

路径是**两段**（`/by-symbol/{symbol}`），与单段的 `/{gene_id}` 不冲突。
符号不存在时返回 `404 NOT_FOUND`，而不是空对象。

---

### 3.4 按主键查询

```bash
curl -s http://localhost:8000/api/genes/1
```

`200 OK`：

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
  "message": "ok",
  "meta": {},
  "error": null
}
```

主键**必须是整数**：`/api/genes/abc` 会被挡成 `422`，根本不会走到业务层。

---

### 3.5 局部更新

```bash
curl -s -X PUT http://localhost:8000/api/genes/1 \
  -H 'Content-Type: application/json' \
  -d '{"name": "p53 renamed"}'
```

`200 OK`（注意 `symbol` / `pathway_id` / `chromosome` 都**原样保留**）：

```json
{
  "success": true,
  "data": {
    "id": 1,
    "symbol": "TP53",
    "name": "p53 renamed",
    "pathway_id": "hsa04115",
    "chromosome": "17p13.1"
  },
  "message": "gene updated",
  "meta": {},
  "error": null
}
```

**「不传」与「传 null」语义不同** —— 这是最容易踩的坑：

| 请求体 | 效果 |
|---|---|
| `{"name": "p53 renamed"}` | 只改 `name`，其余字段**保持原值** |
| `{"pathway_id": null}` | 把 `pathway_id` **显式清空** |

请求体所有字段都是可选的（语义上是 PATCH）。目标不存在返回 `404`。

---

### 3.6 删除

```bash
curl -s -X DELETE http://localhost:8000/api/genes/1
```

`200 OK`（**不是 204** —— 本服务统一走信封，因此仍有响应体）：

```json
{
  "success": true,
  "data": null,
  "message": "gene deleted",
  "meta": { "deleted": true },
  "error": null
}
```

⚠️ 目标不存在时返回 `404`，**不是幂等成功**。
若需要幂等删除语义，请在调用方把 `404` 视为「目标已达成」。

---

### 3.7 健康检查

```bash
curl -s http://localhost:8000/api/health
```

返回 `200` + `data.status = "ok"`。

⚠️ 这是**存活探针**：只要有进程响应就返回 200，**不检查数据库/缓存**。
依赖不可用时它依然 200。需要「依赖就绪」语义请另加 `/api/ready`（尚未实现）。

---

## 4. 错误码说明

### 4.1 错误码表

`error.code` 是**机器可读**的稳定标识，`error.status` 与 HTTP 状态码一致。

**当前端点实际会返回的：**

| `code` | HTTP | 触发条件 | 客户端建议动作 |
|---|---|---|---|
| `NOT_FOUND` | 404 | 目标基因不存在（按 id 或按 symbol 查询、更新、删除） | 提示不存在；删除场景可视为「已达成」 |
| `VALIDATION_ERROR` | 422 | ① 入参校验失败（缺必填、超长、`limit` 越界、id 非整数、请求体含未知字段）<br>② 主键格式非法（无法转 int） | 修正请求后重试，**不要**盲目重试 |
| `SERVICE_ERROR` | 500 | 未预期的服务端异常（兜底） | 记录并上报；稍后重试 |

**已在异常体系中定义、但当前没有任何端点会返回：**

| `code` | HTTP | 说明 |
|---|---|---|
| `CONFLICT` | 409 | 资源冲突（如唯一约束）。`ConflictError` 已定义，暂无调用点 |
| `ACCESS_DENIED` | 403 | 权限不足。需要 `auth/` 落地后才会出现，见 [2.2](#22-路线图上的方案尚未实现) |
| `INVALID_TOKEN` | 401 | 令牌非法/过期。同上，属规划中 |

### 4.2 错误信封结构

```json
{
  "success": false,
  "data": null,
  "message": "gene not found: '999'",
  "meta": {},
  "error": {
    "code": "NOT_FOUND",
    "detail": {},
    "status": 404
  }
}
```

* `detail` 对普通业务错误是空对象 `{}`；
* **入参校验失败**时 `detail.errors` 是 FastAPI/Pydantic 的错误列表；
* 500 响应**不会**回传 traceback（只给 `SERVICE_ERROR` + 通用 message）。

### 4.3 排错示例

**404 —— 目标不存在**

```bash
curl -s http://localhost:8000/api/genes/999
```

```json
{
  "success": false,
  "data": null,
  "message": "gene not found: '999'",
  "meta": {},
  "error": { "code": "NOT_FOUND", "detail": {}, "status": 404 }
}
```

**422 —— 路径参数类型不对**（注意 `detail.errors[0].loc` 指向 `path`）

```bash
curl -s http://localhost:8000/api/genes/abc
```

```json
{
  "success": false,
  "data": null,
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
          "input": "abc"
        }
      ]
    },
    "status": 422
  }
}
```

**422 —— 请求体缺字段**（`loc` 前缀为 `body`，可能一次返回多条）

```bash
curl -s -X POST http://localhost:8000/api/genes \
  -H 'Content-Type: application/json' \
  -d '{"symbol": ""}'
```

```json
{
  "success": false,
  "data": null,
  "message": "request validation failed",
  "meta": {},
  "error": {
    "code": "VALIDATION_ERROR",
    "detail": {
      "errors": [
        {
          "type": "string_too_short",
          "loc": ["body", "symbol"],
          "msg": "String should have at least 1 character",
          "input": "",
          "ctx": { "min_length": 1 }
        },
        {
          "type": "missing",
          "loc": ["body", "name"],
          "msg": "Field required",
          "input": { "symbol": "" }
        }
      ]
    },
    "status": 422
  }
}
```

> 注意本服务的 422 也用统一信封，而**不是** FastAPI 默认的 `{"detail": [...]}`。
> 只按 `detail` 解析的客户端在这里会取不到东西，请统一读 `error.code`。

---

## 5. 维护约定

改动 API 时请同步维护本文件与 OpenAPI 元数据，否则文档会与实现悄悄分叉：

| 你改了什么 | 需要同步的地方 |
|---|---|
| 新增/修改端点 | 路由装饰器的 `summary` / `description` / `tags` / `response_description` / `responses`，以及本文件第 3 节 |
| 文档文案或响应示例 | `app/routes/genes_docs.py`（markdown 说明 + JSON 示例都是**数据**，集中在这个模块）与本文件 |
| 新增/修改字段 | Pydantic 模型的 `Field(description=..., examples=[...])` 与本文件的字段表 |
| 新增错误码 | `base_service.py` 的异常类 + 本文件 4.1 的错误码表 |
| 信封结构变化 | `utils/response.py` 与 `app/openapi_models.py`（两者字段名由 `tests/test_api_docs.py` 钉住） |

这些约束并非只靠自觉：`tests/test_api_docs.py` 会断言
① 每个操作都有 `summary`/`description`/`tags`/`response_description`；
② 每个请求/输出模型字段都有 `description`；
③ 信封模型与 `utils/response.ApiResponse` 的字段名一致；
④ 文档里的示例值能真正通过模型校验。
漏写文档会让测试变红 —— 文档与代码一起被门禁保护。

### 5.1 已知框架约束：OpenAPI 里的示例看不到 `null`

`/docs` 与 `/openapi.json` 中，响应示例里的 **`null` 值会被 FastAPI 剥掉**。
原因是 `fastapi/openapi/utils.py` 的 `get_openapi()` 结尾对整个文档执行了：

```python
jsonable_encoder(OpenAPI(**output), by_alias=True, exclude_none=True)
```

后果：成功示例看不到 `"error": null`，失败示例看不到 `"data": null`，
`DELETE` 成功示例看不到 `"data": null`。这是**框架行为，无法通过配置关闭**。

对读者没有实际影响 —— 运行时响应始终包含全部五个键。若需要逐字精确的报文，
以本文件的示例为准（本文件是独立 markdown，字符串不受该行为影响），
或直接看路由 `description` 里的 JSON 代码块。


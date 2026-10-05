"""tests/test_routes.py — 路由层测试模板（≥3 个用例）。

策略：**把 service 层整层 mock 掉**（FastAPI `dependency_overrides`），
只验证两件事，不碰任何业务逻辑：

    ① 路由返回的 HTTP 状态码；
    ② 返回体的 JSON 信封结构（success / data / error / meta / message）。

落地到真实仓库时：让 `test_client` 指向真实 app（如 `gateway.main:app`），
把 `override("gene_service", ...)` 的键换成真实的依赖函数名即可 ——
下方所有断言（状态码 + 信封字段）无需改动。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from base_service import NotFoundError, ValidationError

#: 统一响应信封必须包含的键（与 utils/response.py 的约定一致）
ENVELOPE_KEYS = {"success", "data", "message", "meta", "error"}


@pytest.fixture
def fake_gene_service() -> MagicMock:
    """service 层替身：只摆出路由真正会调用的方法。"""
    svc = MagicMock(name="GeneService")
    svc.get_by_symbol.return_value = {
        "symbol": "TP53",
        "name": "tumor protein p53",
        "pathway_id": "hsa04115",
    }
    return svc


@pytest.fixture
def fake_pathway_service() -> MagicMock:
    svc = MagicMock(name="PathwayService")
    svc.list.return_value = [
        {"id": "hsa04115", "name": "p53 signaling pathway"},
        {"id": "hsa00010", "name": "Glycolysis / Gluconeogenesis"},
    ]
    return svc


@pytest.fixture
def fake_analysis_service() -> MagicMock:
    svc = MagicMock(name="AnalysisService")
    # 返回体里带上服务端生成的 id / status，模拟真实 service 行为
    svc.create.side_effect = lambda payload: {**payload, "id": 1, "status": "done"}
    return svc


# --------------------------------------------------------------------------- #
# 用例 1：健康检查（最简单的 200 + 信封结构）
# --------------------------------------------------------------------------- #
def test_health_returns_200_with_success_envelope(test_client) -> None:
    # Arrange: 无依赖、无入参，直接打健康检查
    url = "/api/health"

    # Act
    response = test_client.get(url)

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert ENVELOPE_KEYS.issubset(body.keys())
    assert body["success"] is True
    assert body["error"] is None
    assert body["data"] == {"status": "ok"}


# --------------------------------------------------------------------------- #
# 用例 2：命中资源 -> 200 且透传 service 返回值
# --------------------------------------------------------------------------- #
def test_get_gene_returns_200_and_passes_through_service_payload(
    test_client, override, fake_gene_service
) -> None:
    # Arrange: 用替身替换 gene_service 依赖
    override("gene_service", fake_gene_service)

    # Act
    response = test_client.get("/api/genes/TP53")

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["symbol"] == "TP53"
    assert body["data"]["pathway_id"] == "hsa04115"
    # 路由确实把路径参数原样交给了 service
    fake_gene_service.get_by_symbol.assert_called_once_with("TP53")


# --------------------------------------------------------------------------- #
# 用例 3：service 抛 NotFoundError -> 映射成 404 + error 信封
# --------------------------------------------------------------------------- #
def test_get_gene_maps_not_found_error_to_404(
    test_client, override, fake_gene_service
) -> None:
    # Arrange: 让替身抛服务层异常
    fake_gene_service.get_by_symbol.side_effect = NotFoundError(
        "gene not found: 'NOPE'", detail={"symbol": "NOPE"}
    )
    override("gene_service", fake_gene_service)

    # Act
    response = test_client.get("/api/genes/NOPE")

    # Assert
    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["status"] == 404
    assert body["error"]["detail"] == {"symbol": "NOPE"}


# --------------------------------------------------------------------------- #
# 用例 4：列表路由 -> meta.count 与查询参数透传
# --------------------------------------------------------------------------- #
def test_list_pathways_returns_meta_count_and_forwards_limit(
    test_client, override, fake_pathway_service
) -> None:
    # Arrange
    override("pathway_service", fake_pathway_service)

    # Act
    response = test_client.get("/api/pathways", params={"limit": 5})

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["meta"]["count"] == 2
    assert [item["id"] for item in body["data"]] == ["hsa04115", "hsa00010"]
    assert fake_pathway_service.list.call_args.kwargs["limit"] == 5


# --------------------------------------------------------------------------- #
# 用例 5：POST 创建 -> 201 + created 信封（meta.status=201）
# --------------------------------------------------------------------------- #
def test_create_analysis_returns_201_created_envelope(
    test_client, override, fake_analysis_service
) -> None:
    # Arrange
    override("analysis_service", fake_analysis_service)
    payload = {"method": "deseq2", "dataset": "airway"}

    # Act
    response = test_client.post("/api/analyses", json=payload)

    # Assert
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    assert body["meta"]["status"] == 201
    assert body["data"]["id"] == 1
    fake_analysis_service.create.assert_called_once_with(payload)


# --------------------------------------------------------------------------- #
# 用例 6：入参不合法 -> service 抛 ValidationError -> 422
# --------------------------------------------------------------------------- #
def test_create_analysis_maps_validation_error_to_422(
    test_client, override, fake_analysis_service
) -> None:
    # Arrange
    fake_analysis_service.create.side_effect = ValidationError(
        "method is required", detail={"field": "method"}
    )
    override("analysis_service", fake_analysis_service)

    # Act
    response = test_client.post("/api/analyses", json={"dataset": "airway"})

    # Assert
    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["detail"] == {"field": "method"}

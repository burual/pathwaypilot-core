"""app/__init__.py — API 层包入口。

⚠️ 顶层**不** import 子模块，只提供懒加载属性：
`app.main` -> `app.routes.genes` -> `app.deps` -> `app.db`，若在本文件顶层就把
这些拉进来，任何 `import app.xxx` 都会先执行到一半的 `app/__init__`，形成
「包初始化期间再导入同包」的环（症状是莫名的 AttributeError/ImportError）。

    from app import create_app        # 等价于 from app.main import create_app
"""
from __future__ import annotations

from typing import Any

__all__ = ["create_app", "get_db", "get_gene_service"]


def __getattr__(name: str) -> Any:
    """PEP 562 模块级懒加载：首次访问属性时才做真正的导入。"""
    if name == "create_app":
        from app.main import create_app
        return create_app
    if name == "get_db":
        from app.db import get_db
        return get_db
    if name == "get_gene_service":
        from app.deps import get_gene_service
        return get_gene_service
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

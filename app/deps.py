"""app/deps.py — 依赖装配：Session -> Repository -> Service。

路由只声明「我要一个 GeneService」，具体怎么拼由这里决定。测试想换数据源，
只需覆盖 `app.db.get_db`（或整个 service 依赖），路由代码一行不动。
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db import get_db
from example_gene_service import GeneService
from repositories import GeneRepository, get_repository
from utils.cache import MemoryBackend

__all__ = ["get_gene_service", "reset_shared_cache", "shared_cache"]


#: 进程级共享缓存。
#: ⚠️ 为什么不能省：`GeneService` 自带的 SimpleTTLCache 是**随实例创建**的，而
#: FastAPI 每个请求都会新建一个 service 实例 —— 各请求各自持有一份空缓存，等于
#: 没缓存。缓存要真的生效，载体必须是进程级的（这里用内存后端；生产换成
#: `utils.cache.RedisBackend` 即可，两者都遵守 `base_service.CacheProtocol`）。
_shared_cache = MemoryBackend()


def shared_cache() -> MemoryBackend:
    """当前进程的共享缓存后端（测试用它断言/清理缓存状态）。"""
    return _shared_cache


def reset_shared_cache() -> None:
    """清空共享缓存。

    ⚠️ 测试必须每个用例前后各调一次：共享缓存是**跨用例存活**的模块级状态，
    不清就会让上一个用例缓存住的实体泄漏到下一个用例里，出现「单跑绿、全跑红」。
    """
    _shared_cache.clear()


def get_gene_service(db: Annotated[Session, Depends(get_db)]) -> GeneService:
    """请求级装配：一个请求一个 Session，一个 GeneService，共享一个缓存后端。

    用 `Annotated[Session, Depends(...)]` 而不是 `db: Session = Depends(...)`：
    后者是「在默认值里调用函数」，ruff 的 B008 会判违规（tests/conftest.py 里
    是靠 per-file-ignore 压掉的，新代码不必再欠这笔债）。
    """
    return GeneService(get_repository(GeneRepository, db), cache=_shared_cache)

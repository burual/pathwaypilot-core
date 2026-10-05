"""app/db.py — 引擎 / 会话工厂 / FastAPI 会话依赖（真实数据库接线）。

分层位置：
    路由（app/routes） -> 依赖（app/deps） -> 服务（base_service/example_*）
      -> 数据层（repositories） -> ORM（db_models） -> 本模块提供的 Session
"""
from __future__ import annotations

import os
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

import db_models

__all__ = ["DATABASE_URL", "SessionLocal", "create_db_engine", "get_db", "init_db"]

#: 默认落盘位置（SQLite 单文件，零外部依赖）。用环境变量覆盖可切到其他库：
#:     PATHWAYPILOT_DATABASE_URL=postgresql+psycopg://user:pw@host/db
DEFAULT_DATABASE_URL = "sqlite+pysqlite:///./pathwaypilot.db"
DATABASE_URL = os.environ.get("PATHWAYPILOT_DATABASE_URL", DEFAULT_DATABASE_URL)


def create_db_engine(url: str = DATABASE_URL) -> Engine:
    """按 URL 建引擎。

    `check_same_thread=False` 只对 SQLite 有意义：FastAPI 的同步路由跑在
    线程池里，不放开这个限制会直接报 SQLite objects created in a thread...
    （⚠️ 它放开的是「同一连接跨线程」的检查，不保证并发安全 —— 并发安全由
    连接池负责，别把单连接跨线程当成并发方案。）
    """
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args, future=True)


#: 进程级引擎与会话工厂
engine: Engine = create_db_engine()
#: expire_on_commit=False：commit 之后仍可读已加载对象的属性（否则会触发一次
#: 额外的 SELECT，且刚提交就访问会拿到 DetachedInstanceError 的变体）。
SessionLocal = sessionmaker(
    bind=engine, autoflush=False, expire_on_commit=False, class_=Session
)


def init_db(target: Engine | None = None) -> None:
    """建表（幂等）。启动脚本里调一次；测试用各自的内存引擎，不走这里。"""
    db_models.Base.metadata.create_all(target if target is not None else engine)


def get_db() -> Iterator[Session]:
    """会话依赖：**一请求一事务**（Unit of Work）。

    为什么 commit 在 Web 层：BaseService 与 Repository 都**只 flush 不 commit**
    （见 repositories/base.py 的说明）。事务边界遵循「谁开的事务谁收尾」，在服务里
    就是「一次请求」。抛异常则整体回滚，不留半截写入。

    ⚠️ 用 `yield` 的依赖，其收尾代码在响应生成之后才执行，因此这里**不能**再抛
    HTTPException（FastAPI 0.106 起已废弃该用法）—— 错误处理请放在路由或异常
    处理器里。
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

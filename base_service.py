"""base_service.py — 服务层统一基类（CRUD 模板 + 异常包装 + 日志/耗时 + 缓存钩子）。

除标准库与 typing 外，只依赖一个同仓模块：`utils.cache` 的未命中哨兵
（`_MISS` / `CacheMiss`）。哨兵必须**全进程唯一**才有意义 —— 后端、服务层、
调用方三方用的必须是同一个对象，比较才能用 `is` 成立，所以不在此另造一份。
与 service_protocols.py 正交：BaseService 负责「实现侧的横切关注点」，
Protocol 负责「调用侧的接口契约」。
"""
from __future__ import annotations

import asyncio
import functools
import logging
import time
from abc import ABC
from typing import Any, Callable, Generic, Mapping, Optional, Protocol, TypeVar, runtime_checkable

from utils.cache import _MISS, CacheMiss

__all__ = [
    "AccessDeniedError",
    "BaseService",
    "CacheProtocol",
    "ConflictError",
    "NotFoundError",
    "ServiceError",
    "SimpleTTLCache",
    "ValidationError",
    "log_call",
    "service_error_handler",
]

T = TypeVar("T")   # 实体类型
ID = TypeVar("ID")  # 主键类型

_module_logger = logging.getLogger("service")


# ========================================================================== #
# 1. 统一异常体系
# ========================================================================== #
class ServiceError(Exception):
    """服务层统一异常基类。所有子类异常都会带上 code / http_status / detail。"""

    code: str = "SERVICE_ERROR"
    http_status: int = 500

    def __init__(self, message: str, *, detail: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail: Mapping[str, Any] = dict(detail or {})

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "detail": dict(self.detail)}

    def __str__(self) -> str:  # pragma: no cover - 展示用
        return f"[{self.code}] {self.message}"


class NotFoundError(ServiceError):
    code = "NOT_FOUND"
    http_status = 404


class ValidationError(ServiceError):
    code = "VALIDATION_ERROR"
    http_status = 422


class ConflictError(ServiceError):
    code = "CONFLICT"
    http_status = 409


class AccessDeniedError(ServiceError):
    code = "ACCESS_DENIED"
    http_status = 403


# ========================================================================== #
# 2. 统一异常处理包装 + 日志/耗时（支持 sync / async，@dec 与 @dec(...) 两种写法）
# ========================================================================== #
def _logger_of(args: tuple[Any, ...]) -> logging.Logger:
    """若被装饰的是方法且实例带 .logger，则用它，否则用模块 logger。"""
    if args:
        candidate = getattr(args[0], "logger", None)
        if isinstance(candidate, logging.Logger):
            return candidate
    return _module_logger


def _display_name(func: Callable[..., Any], args: tuple[Any, ...]) -> str:
    """运行期解析日志/异常里的「类.方法」名。

    ⚠️ 不要在装饰期取 `func.__qualname__`：get/list/create/update/delete 定义在
    BaseService 上，继承后 qualname 恒为 `BaseService.get`，子类调用时也无法区分，
    线上排查看不出到底是哪个实体出错。改为按 `type(args[0])` 反查，得到
    `SearchService.get` 这类真实归属。非方法调用（无 self）回退到 qualname。
    """
    if args and not isinstance(args[0], type):
        owner = type(args[0])
        if getattr(owner, func.__name__, None) is not None:
            return f"{owner.__name__}.{func.__name__}"
    return func.__qualname__


def service_error_handler(
    func: Optional[Callable[..., Any]] = None,
    *,
    wrap_unexpected: bool = True,
    level: int = logging.DEBUG,
) -> Any:
    """统一异常处理 + 自动记录方法名与耗时。

    - 已知 ServiceError：记 WARNING 后原样上抛（保留 code / http_status）。
    - 未知 Exception：记 EXCEPTION（含栈），默认包装为 ServiceError（__cause__ 保留原始异常）。
    - 成功：记 level 级日志，内容含方法名与耗时（ms）。

    用法：
        @service_error_handler
        def foo(self): ...

        @service_error_handler(wrap_unexpected=False)
        def bar(self): ...
    """

    def decorate(f: Callable[..., Any]) -> Callable[..., Any]:
        if asyncio.iscoroutinefunction(f):

            @functools.wraps(f)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                label = _display_name(f, args)
                t0 = time.perf_counter()
                try:
                    result = await f(*args, **kwargs)
                except ServiceError as exc:
                    _logger_of(args).warning(
                        "%s failed in %.1fms: %s", label,
                        (time.perf_counter() - t0) * 1000, exc.message,
                    )
                    raise
                except Exception as exc:
                    _logger_of(args).exception(
                        "%s raised unexpected error in %.1fms",
                        label, (time.perf_counter() - t0) * 1000,
                    )
                    if wrap_unexpected:
                        raise ServiceError(f"{label} failed: {exc}") from exc
                    raise
                _logger_of(args).log(
                    level, "%s ok in %.1fms", label, (time.perf_counter() - t0) * 1000
                )
                return result

            return async_wrapper

        @functools.wraps(f)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            label = _display_name(f, args)
            t0 = time.perf_counter()
            try:
                result = f(*args, **kwargs)
            except ServiceError as exc:
                _logger_of(args).warning(
                    "%s failed in %.1fms: %s", label,
                    (time.perf_counter() - t0) * 1000, exc.message,
                )
                raise
            except Exception as exc:
                _logger_of(args).exception(
                    "%s raised unexpected error in %.1fms",
                    label, (time.perf_counter() - t0) * 1000,
                )
                if wrap_unexpected:
                    raise ServiceError(f"{label} failed: {exc}") from exc
                raise
            _logger_of(args).log(
                level, "%s ok in %.1fms", label, (time.perf_counter() - t0) * 1000
            )
            return result

        return sync_wrapper

    if func is None:
        return decorate
    return decorate(func)


def log_call(level: int = logging.DEBUG) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """仅记录「方法名 + 耗时」，不做异常包装（给不需要统一兜底的方法用）。"""

    def decorate(f: Callable[..., Any]) -> Callable[..., Any]:
        if asyncio.iscoroutinefunction(f):

            @functools.wraps(f)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                label = _display_name(f, args)
                t0 = time.perf_counter()
                try:
                    return await f(*args, **kwargs)
                finally:
                    _logger_of(args).log(
                        level, "%s took %.1fms", label, (time.perf_counter() - t0) * 1000
                    )

            return async_wrapper

        @functools.wraps(f)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            label = _display_name(f, args)
            t0 = time.perf_counter()
            try:
                return f(*args, **kwargs)
            finally:
                _logger_of(args).log(
                    level, "%s took %.1fms", label, (time.perf_counter() - t0) * 1000
                )

        return sync_wrapper

    return decorate


# ========================================================================== #
# 3. 缓存策略接口（默认进程内 TTL，可替换为 Redis）
# ========================================================================== #
@runtime_checkable
class CacheProtocol(Protocol):
    """缓存策略契约（与 `utils.cache.CacheBackend` 同构）。

    ⚠️ `get()` 必须遵守哨兵契约：命中返回原值（**可能是 None**），
    未命中返回 `utils.cache._MISS`。用 None 表示未命中即违反契约。
    """

    def get(self, key: str) -> Any | CacheMiss: ...
    def set(self, key: str, value: Any, ttl: int | None = None) -> None: ...
    def delete(self, key: str) -> None: ...


class SimpleTTLCache:
    """极简进程内 TTL 缓存（无语义依赖，够用作默认实现）。"""

    def __init__(self) -> None:
        self._data: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | CacheMiss:
        item = self._data.get(key)
        if item is None:
            return _MISS  # 键不存在
        expire_at, value = item
        if expire_at and expire_at < time.time():
            self._data.pop(key, None)
            return _MISS  # 已过期
        return value      # 命中：value 可能是 None

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        expire_at = (time.time() + ttl) if ttl else 0.0
        self._data[key] = (expire_at, value)

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def delete_prefix(self, prefix: str) -> int:
        keys = [k for k in self._data if k.startswith(prefix)]
        for k in keys:
            self._data.pop(k, None)
        return len(keys)

    def clear(self) -> None:
        self._data.clear()


# ========================================================================== #
# 4. BaseService —— CRUD 模板方法 + 缓存钩子
# ========================================================================== #
class BaseService(Generic[T, ID], ABC):
    """服务层基类。

    子类只需覆写 5 个数据层钩子（_fetch/_query/_persist/_persist_update/_remove）
    + 2 个可选校验钩子，即可直接获得带异常包装、日志耗时、缓存的 CRUD。
    """

    #: 实体名，用于日志与缓存 key 前缀
    entity_name: str = "entity"
    #: 是否启用缓存（子类可在类级别或 __init__ 里打开）
    enable_cache: bool = False
    #: 默认缓存 TTL（秒）
    cache_ttl: int = 60

    def __init__(
        self,
        *,
        cache: Optional[CacheProtocol] = None,
        enable_cache: Optional[bool] = None,
        cache_ttl: Optional[int] = None,
    ) -> None:
        self.logger = logging.getLogger(f"service.{type(self).__name__}")
        self._cache: CacheProtocol = cache if cache is not None else SimpleTTLCache()
        if cache is not None:
            self.enable_cache = True
        if enable_cache is not None:
            self.enable_cache = enable_cache
        if cache_ttl is not None:
            self.cache_ttl = cache_ttl

    # ---------------------- 缓存钩子（可选，可被覆写接 Redis） ------------------ #
    def cache_key(self, scope: str, *parts: Any) -> str:
        tail = ":".join(str(p) for p in parts)
        return f"{self.entity_name}:{scope}:{tail}" if tail else f"{self.entity_name}:{scope}"

    def cache_get(self, key: str) -> Any:
        """读取缓存，返回「值」或「未命中哨兵」。

        契约（哨兵模式）：
          * 命中       -> 原样返回缓存值，**None 是合法命中值**；
          * 未命中     -> 返回 `_MISS`；
          * 未启用缓存 -> 也返回 `_MISS`（对调用方等价于「没缓存」，会去取数）。

        ⚠️ 调用方一律写 `if value is not _MISS:`，**不要**再写 `is not None` ——
        后者把「缓存了 None」当成未命中，正是「返回 None 的结果永远进不了缓存」的成因。

        返回类型刻意标成 `Any`（而非 `Any | CacheMiss`）：mypy 无法对
        `value is not <哨兵实例>` 做类型收窄，若标成并集会逼着每个调用点写
        `cast(...)`，得不偿失。精确的哨兵契约留在 `SimpleTTLCache.get` /
        `CacheProtocol.get` / `utils.cache.CacheBackend.get` 上。
        """
        if not self.enable_cache:
            return _MISS
        result = self._cache.get(key)
        if result is _MISS:
            return _MISS     # 显式传哨兵：未命中 ≠ None
        return result        # 命中：原样返回，None 也照返回

    def cache_set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        if self.enable_cache:
            self._cache.set(key, value, ttl if ttl is not None else self.cache_ttl)

    def cache_delete(self, key: str) -> None:
        if self.enable_cache:
            self._cache.delete(key)

    def _invalidate_scope(self, scope: str) -> None:
        """按前缀失效某类缓存（list / search 等）。默认用 SimpleTTLCache.delete_prefix。"""
        if not self.enable_cache:
            return
        prefix = f"{self.entity_name}:{scope}:"
        deleter = getattr(self._cache, "delete_prefix", None)
        if callable(deleter):
            deleter(prefix)

    # -------------------------------- CRUD 模板 ------------------------------ #
    @service_error_handler
    def get(self, entity_id: ID) -> T:
        key = self.cache_key("get", entity_id)
        cached = self.cache_get(key)
        if cached is not _MISS:
            return cached
        entity = self._fetch(entity_id)
        if entity is None:
            raise NotFoundError(f"{self.entity_name} not found: {entity_id!r}")
        self.cache_set(key, entity, self.cache_ttl)
        return entity

    @service_error_handler
    def list(self, **filters: Any) -> list[T]:
        key = self.cache_key("list", *(f"{k}={v}" for k, v in sorted(filters.items())))
        cached = self.cache_get(key)
        if cached is not _MISS:
            return cached
        items = list(self._query(filters))
        self.cache_set(key, items, self.cache_ttl)
        return items

    @service_error_handler
    def create(self, payload: Mapping[str, Any]) -> T:
        data = self._validate_create(payload)
        entity = self._persist(data)
        self._invalidate_scope("list")
        return entity

    @service_error_handler
    def update(self, entity_id: ID, payload: Mapping[str, Any]) -> T:
        current = self._fetch(entity_id)
        if current is None:
            raise NotFoundError(f"{self.entity_name} not found: {entity_id!r}")
        data = self._validate_update(entity_id, payload, current)
        entity = self._persist_update(entity_id, data, current)
        self.cache_delete(self.cache_key("get", entity_id))
        self._invalidate_scope("list")
        return entity

    @service_error_handler
    def delete(self, entity_id: ID) -> bool:
        current = self._fetch(entity_id)
        if current is None:
            raise NotFoundError(f"{self.entity_name} not found: {entity_id!r}")
        removed = self._remove(entity_id, current)
        self.cache_delete(self.cache_key("get", entity_id))
        self._invalidate_scope("list")
        return bool(removed)

    # --------------------- 数据层钩子：子类必须覆写 ---------------------------- #
    def _fetch(self, entity_id: ID) -> Optional[T]:
        raise NotImplementedError(f"{type(self).__name__}._fetch() 必须实现")

    def _query(self, filters: Mapping[str, Any]) -> list[T]:
        raise NotImplementedError(f"{type(self).__name__}._query() 必须实现")

    def _persist(self, data: Mapping[str, Any]) -> T:
        raise NotImplementedError(f"{type(self).__name__}._persist() 必须实现")

    def _persist_update(self, entity_id: ID, data: Mapping[str, Any], current: T) -> T:
        raise NotImplementedError(f"{type(self).__name__}._persist_update() 必须实现")

    def _remove(self, entity_id: ID, current: T) -> bool:
        raise NotImplementedError(f"{type(self).__name__}._remove() 必须实现")

    # --------------------- 校验钩子：可选覆写（默认透传） --------------------- #
    def _validate_create(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return payload

    def _validate_update(
        self, entity_id: ID, payload: Mapping[str, Any], current: T
    ) -> Mapping[str, Any]:
        return payload

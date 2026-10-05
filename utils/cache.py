"""utils/cache.py — 缓存工具（内存/Redis 可插拔装饰器 + ETag / 条件请求）。

单一职责：缓存与 HTTP 缓存语义。**不 import 本包内其他 utils 模块**。
默认内存后端（线程安全）；Redis 通过 set_backend(适配器) 注入，本模块不硬依赖 redis。
"""
from __future__ import annotations

import asyncio
import functools
import hashlib
import pickle
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Any, Callable, Final, Optional, Protocol, runtime_checkable

__all__ = [
    "_MISS",
    "CacheBackend",
    "CacheMiss",
    "MemoryBackend",
    "RedisBackend",
    "cache_clear",
    "cache_delete",
    "cache_get",
    "cache_set",
    "cache_size",
    "conditional_headers",
    "etag_matches",
    "generate_etag",
    "get_backend",
    "http_date",
    "make_cache_key",
    "memory_cache",
    "set_backend",
]


# ========================================================================== #
# 0. 未命中哨兵（sentinel）—— 与「命中但值是 None」严格区分
# ========================================================================== #
class CacheMiss:
    """缓存「未命中」的哨兵类型（全局唯一实例见 `_MISS`）。

    为什么需要它：缓存的值本身**就可能就是 None** —— 函数合法地返回 None，或业务
    刻意缓存「查无此人」这类负结果。若用 None 同时表示「未命中」和「命中但值是
    None」，两者不可区分，于是 `if value is not None: return value` 永远打不中，
    退化成「返回 None 的函数永远不会被缓存」。

    约定：
      * 一律用身份比较 `value is _MISS` 判断未命中，不要用 `==`；
      * `repr(_MISS) == "<MISS>"`，日志/断言里一眼可辨；
      * `bool(_MISS) is False`，让 `if not value:` 这类旧写法也按「未命中」处理。
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "<MISS>"

    def __bool__(self) -> bool:
        return False


#: 全局唯一的未命中哨兵。比较必须用 `is`。
_MISS: Final[CacheMiss] = CacheMiss()


# ========================================================================== #
# 后端抽象（可注入 Redis 等实现）
# ========================================================================== #
@runtime_checkable
class CacheBackend(Protocol):
    """缓存后端契约。实现 get/set/delete/clear/size 即可接入。

    ⚠️ `get()` 的返回值契约（哨兵模式）：
      - 键存在        -> 返回真正的值（**可能是 None**，这是合法命中）
      - 键不存在/已过期 -> 返回哨兵 `_MISS`

    任何实现都**不得**再用 None 表示「未命中」，否则不兼容本契约 —— 会把
    「缓存了 None」误判成 miss。
    """

    def get(self, key: str) -> Any | CacheMiss: ...
    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None: ...
    def delete(self, key: str) -> None: ...
    def clear(self) -> None: ...
    def size(self) -> int: ...


class MemoryBackend:
    """线程安全的进程内 TTL 缓存（默认后端）。"""

    def __init__(self, maxsize: int = 2048) -> None:
        self._maxsize = maxsize
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.RLock()

    def get(self, key: str) -> Any | CacheMiss:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return _MISS  # 键不存在（值存的是 (expire_at, value) 元组，不会误判）
            expire_at, value = item
            if expire_at and expire_at < time.time():
                self._data.pop(key, None)
                return _MISS  # 已过期，顺手驱逐
            self._data.move_to_end(key)
            return value      # 命中：value 可能是 None

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        with self._lock:
            expire_at = (time.time() + ttl) if ttl else 0.0
            self._data[key] = (expire_at, value)
            self._data.move_to_end(key)
            while len(self._data) > self._maxsize:
                self._data.popitem(last=False)

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def delete_prefix(self, prefix: str) -> int:
        with self._lock:
            keys = [k for k in self._data if k.startswith(prefix)]
            for k in keys:
                self._data.pop(k, None)
            return len(keys)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._data)


class RedisBackend:
    """Redis 缓存后端参考实现（实现 CacheBackend 协议 + 上述哨兵契约）。

    **不硬依赖 redis**：client 由外部注入（`redis.Redis` / 任何鸭子类型对象），
    因此本模块保持零第三方依赖，utils 各子模块之间也无需互相 import。

    ⚠️ 与内存后端最大的不同：Redis 里「键不存在」和「值就是 None」都会 GET 到 nil，
    单看返回值二者不可分辨。若直接把 nil 当未命中，就等于把「缓存了 None」误判成
    miss —— 正是本次要修的 bug 在 Redis 上的翻版。所以这里**先序列化成非空 bytes
    再写**（`pickle.dumps(None)` 是合法的非空 bytes），于是：

        GET 到 nil    -> 键确实不存在 -> 返回 `_MISS`
        GET 到 bytes  -> 反序列化后原样返回（None 也是合法命中）

    异步客户端（redis.asyncio）需要另写一层 async 包装，本类只覆盖同步用法。
    """

    def __init__(
        self,
        client: Any,
        *,
        prefix: str = "pp:",
        default_ttl: Optional[int] = None,
        dumps: Callable[[Any], bytes] = pickle.dumps,
        loads: Callable[[bytes], Any] = pickle.loads,
    ) -> None:
        self._client = client
        self._prefix = prefix
        self._default_ttl = default_ttl
        self._dumps = dumps
        self._loads = loads

    def _key(self, key: str) -> str:
        """加命名空间前缀：多个应用共用一个 Redis 实例时互不干扰。"""
        return f"{self._prefix}{key}"

    def get(self, key: str) -> Any | CacheMiss:
        raw = self._client.get(self._key(key))
        if raw is None:  # nil = 真未命中
            return _MISS
        return self._loads(raw)

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        effective = self._default_ttl if ttl is None else ttl
        payload = self._dumps(value)
        if effective:
            self._client.set(self._key(key), payload, ex=int(effective))
        else:
            self._client.set(self._key(key), payload)

    def delete(self, key: str) -> None:
        self._client.delete(self._key(key))

    def delete_prefix(self, prefix: str) -> int:
        """按前缀批量失效（BaseService._invalidate_scope 依赖此方法）。"""
        pattern = f"{self._key(prefix)}*"
        removed = 0
        for found in self._client.scan_iter(match=pattern):
            removed += int(self._client.delete(found) or 0)
        return removed

    def clear(self) -> None:
        """只清空**本命名空间**下的键，不 flushdb（避免误删同实例的其他数据）。"""
        self.delete_prefix("")

    def size(self) -> int:
        return sum(1 for _ in self._client.scan_iter(match=f"{self._key('')}*"))


_backend: CacheBackend = MemoryBackend()


def set_backend(backend: CacheBackend) -> None:
    """替换全局缓存后端（如传入 Redis 适配器）。"""
    global _backend
    _backend = backend


def get_backend() -> CacheBackend:
    """返回当前缓存后端。"""
    return _backend


# ========================================================================== #
# Key / 装饰器
# ========================================================================== #
def make_cache_key(*parts: Any, prefix: str = "cache") -> str:
    """由任意部件拼出稳定缓存 key（None 表示为空段，长度超 200 时哈希收敛）。"""
    tail = ":".join("" if p is None else str(p) for p in parts)
    key = f"{prefix}:{tail}" if tail else prefix
    if len(key) > 200:
        key = f"{prefix}:sha1:{hashlib.sha1(key.encode('utf-8')).hexdigest()}"
    return key


def memory_cache(
    ttl: int = 60,
    *,
    maxsize: int = 2048,
    key_func: Optional[Callable[..., str]] = None,
    prefix: str = "fn",
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """缓存装饰器（支持 sync/async）。key 默认由函数限定名 + 参数生成。

    用法：
        @memory_cache(ttl=60)
        def expensive(x): ...
    """

    def decorate(func: Callable[..., Any]) -> Callable[..., Any]:
        store = MemoryBackend(maxsize=maxsize)

        def _build_key(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
            if key_func is not None:
                return key_func(*args, **kwargs)
            return make_cache_key(func.__qualname__, *args, *sorted(kwargs.items()), prefix=prefix)

        def _get(key: str) -> Any | CacheMiss:
            return store.get(key)

        def _set(key: str, value: Any) -> None:
            store.set(key, value, ttl)

        if asyncio.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                key = _build_key(args, kwargs)
                hit = _get(key)
                if hit is not _MISS:   # 注意：不能用 `is not None`，None 也是合法命中
                    return hit
                value = await func(*args, **kwargs)
                _set(key, value)
                return value

            async_wrapper.cache_clear = store.clear  # type: ignore[attr-defined]
            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            key = _build_key(args, kwargs)
            hit = _get(key)
            if hit is not _MISS:       # 注意：不能用 `is not None`，None 也是合法命中
                return hit
            value = func(*args, **kwargs)
            _set(key, value)
            return value

        sync_wrapper.cache_clear = store.clear  # type: ignore[attr-defined]
        return sync_wrapper

    return decorate


# ========================================================================== #
# ETag / 条件请求
# ========================================================================== #
def generate_etag(payload: Any, *, weak: bool = False) -> str:
    """由任意可序列化内容生成 ETag（含引号，符合 HTTP 头格式）。"""
    if isinstance(payload, (bytes, bytearray)):
        raw = bytes(payload)
    elif isinstance(payload, str):
        raw = payload.encode("utf-8")
    else:
        raw = repr(payload).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()[:32]
    return ('W/"%s"' % digest) if weak else ('"%s"' % digest)


def etag_matches(if_none_match: Optional[str], etag: str) -> bool:
    """条件请求比较：支持 '*'、逗号列表、list 入参；弱比较（忽略 W/ 前缀）。"""
    if not if_none_match:
        return False
    candidates = if_none_match if isinstance(if_none_match, list) else str(if_none_match).split(",")
    normalized = etag.lstrip("W/").strip('"')

    def _norm(value: str) -> str:
        v = value.strip()
        if v == "*":
            return "*"
        return v.lstrip("W/").strip('"')

    return any(_norm(c) in ("*", normalized) for c in candidates)


def http_date(dt: Optional[datetime] = None) -> str:
    """格式化为 HTTP-date（RFC 7231）；默认当前 UTC 时间。"""
    moment = dt or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return format_datetime(moment.astimezone(timezone.utc), usegmt=True)


def conditional_headers(
    etag: str, *, max_age: int = 0, private: bool = False, last_modified: Optional[datetime] = None
) -> dict[str, str]:
    """构造 ETag/Cache-Control（及可选 Last-Modified）响应头。"""
    visibility = "private" if private else "public"
    headers = {
        "ETag": etag,
        "Cache-Control": f"{visibility}, max-age={max(0, int(max_age))}",
    }
    if last_modified is not None:
        headers["Last-Modified"] = http_date(last_modified)
    return headers


# ========================================================================== #
# 便捷读写（走当前后端）
# ========================================================================== #
def cache_get(key: str) -> Any | CacheMiss:
    """读取缓存（走当前后端）。

    命中返回原值（**可能是 None**），未命中返回哨兵 `_MISS`：

        value = cache_get(k)
        if value is not _MISS:
            ...  # 命中，value 可能就是 None
    """
    return _backend.get(key)


def cache_set(key: str, value: Any, ttl: Optional[int] = None) -> None:
    """写入缓存（走当前后端，ttl=None 表示用后端默认/不过期）。"""
    _backend.set(key, value, ttl)


def cache_delete(key: str) -> None:
    """删除单个 key。"""
    _backend.delete(key)


def cache_clear() -> None:
    """清空当前后端。"""
    _backend.clear()


def cache_size() -> int:
    """当前后端条目数。"""
    return _backend.size()

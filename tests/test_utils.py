"""tests/test_utils.py — 工具层测试模板（≥3 个用例）。

工具层是**纯函数/无状态**的，所以这一层不需要任何 mock：直接喂输入、断言输出。
另附两条「架构约束测试」，把 utils 的设计纪律固化成可执行的回归防线：

    ① 5 个模块之间**零 import**（text.py 不依赖 cache.py ...）；
    ② 每个模块的顶层函数数 ≤ 15（单一职责的量化上限）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from utils import cache as cache_utils
from utils import response as response_utils
from utils import text as text_utils
from utils import time as time_utils
from utils import validators as validators_utils

# --------------------------------------------------------------------------- #
# 架构约束（把设计纪律写成测试）
# --------------------------------------------------------------------------- #
UTILS_MODULES = {
    "text": text_utils,
    "cache": cache_utils,
    "time": time_utils,
    "validators": validators_utils,
    "response": response_utils,
}


def _top_level_functions(tree: ast.Module) -> list[str]:
    return [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _utils_imports(tree: ast.Module) -> set[str]:
    """收集模块里真实 import 的 utils 子模块（AST 解析，不会误伤 docstring）。"""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names if a.name.split(".")[0] == "utils")
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                found.add(f"<relative level={node.level}>")
            elif node.module and node.module.split(".")[0] == "utils":
                found.add(node.module)
    return found


def test_utils_modules_are_mutually_independent() -> None:
    for name, module in UTILS_MODULES.items():
        # Arrange
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))

        # Act
        dependencies = _utils_imports(tree)

        # Assert
        assert dependencies == set(), f"utils/{name}.py 不应依赖其他子模块：{dependencies}"


def test_utils_module_function_budget_is_at_most_15() -> None:
    for name, module in UTILS_MODULES.items():
        # Arrange
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))

        # Act
        functions = _top_level_functions(tree)

        # Assert
        assert len(functions) <= 15, f"utils/{name}.py 顶层函数过多：{len(functions)}"


# --------------------------------------------------------------------------- #
# utils/text.py
# --------------------------------------------------------------------------- #
def test_text_cleaning_and_html_stripping() -> None:
    # Arrange
    dirty = "  a\x00  b\n c "
    marked_up = "<b>p53</b>&amp;"

    # Act
    cleaned = text_utils.clean_text(dirty)
    stripped = text_utils.strip_html(marked_up)

    # Assert
    assert cleaned == "a b c"
    assert stripped == "p53&"
    assert text_utils.normalize_whitespace("a\u3000 b") == "a b"
    assert text_utils.is_blank("   ") is True
    assert text_utils.contains_cjk("基因") is True
    assert text_utils.contains_cjk("TP53") is False


def test_text_truncate_counts_suffix_and_tokens_are_ready_for_search() -> None:
    # Arrange / Act / Assert
    truncated = text_utils.truncate("abcdef", 4)
    assert truncated == "abc…" and len(truncated) == 4
    assert text_utils.truncate("ab", 5) == "ab"           # 不超长则原样返回

    tokens = text_utils.tokenize("TP53 基因")
    assert "tp53" in tokens                                # jieba 缺失也有正则回退
    assert text_utils.tokenize_query("a TP53", min_len=2) == ["tp53"]
    assert text_utils.normalize_term("  TP53  ") == "tp53"
    assert text_utils.dedupe_preserve_order(["a", "b", "a"]) == ["a", "b"]


def test_text_regex_helpers() -> None:
    # Arrange
    kegg_line = "id=hsa04115"

    # Act / Assert
    assert text_utils.first_match(r"hsa(\d+)", kegg_line) == "hsa04115"
    assert text_utils.first_match(r"mmu(\d+)", kegg_line) is None
    assert text_utils.find_all(r"\d+", "a1b22c333") == ["1", "22", "333"]
    assert text_utils.remove_punctuation("a-b,c!", keep="-") == "a-bc"


# --------------------------------------------------------------------------- #
# utils/cache.py
# --------------------------------------------------------------------------- #
def test_memory_cache_invokes_function_only_once() -> None:
    # Arrange
    calls: list[int] = []

    @cache_utils.memory_cache(ttl=60)
    def expensive(value: int) -> int:
        calls.append(value)
        return value * 2

    # Act
    first = expensive(21)
    second = expensive(21)

    # Assert
    assert first == second == 42
    assert calls == [21]                 # 第二次命中缓存
    expensive.cache_clear()
    assert expensive(21) == 42
    assert calls == [21, 21]             # 清空后重新计算


def test_memory_cache_caches_none_result_and_skips_function_body() -> None:
    """回归（哨兵模式）：**返回 None 的函数也必须被缓存**。

    旧实现用 `if hit is not None:` 判命中，于是「命中但值是 None」被当成未命中，
    每次调用都会重跑函数体 —— 本用例就是那条回归的防线：`calls` 只应记一次。
    """
    # Arrange
    calls: list[str] = []

    @cache_utils.memory_cache(ttl=60)
    def lookup(key: str) -> None:
        calls.append(key)                # 只有真正执行函数体才会计数
        return None

    # Act
    first = lookup("hsa04115")
    second = lookup("hsa04115")

    # Assert
    assert first is None and second is None
    assert calls == ["hsa04115"]         # 第二次命中缓存的 None，函数体未再执行

    lookup.cache_clear()                 # 清空后应重新计算
    assert lookup("hsa04115") is None
    assert calls == ["hsa04115", "hsa04115"]


def test_memory_backend_returns_sentinel_on_miss_but_keeps_cached_none() -> None:
    """MemoryBackend 的哨兵契约：未命中给 `_MISS`；写进去的 None 读出来还是 None。"""
    # Arrange
    backend = cache_utils.MemoryBackend()

    # Act / Assert
    assert backend.get("absent") is cache_utils._MISS        # 未命中不是 None

    backend.set("null", None, ttl=60)
    hit = backend.get("null")
    assert hit is None and hit is not cache_utils._MISS      # 命中可以就是 None

    assert isinstance(cache_utils._MISS, cache_utils.CacheMiss)
    assert repr(cache_utils._MISS) == "<MISS>"
    assert bool(cache_utils._MISS) is False                  # 布尔语境下按「未命中」处理


class _FakeRedisClient:
    """极简同步 Redis 替身：只实现 `RedisBackend` 用到的那几个方法。

    `get` 对缺失键返回 None（= 真 redis 的 nil 语义）—— 这正是「未命中」与
    「缓存的值是 None」在 Redis 上会同形的地方，也是本用例要钉死之处。
    """

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    def set(self, key: str, value: bytes, ex: int | None = None) -> None:
        self.store[key] = value

    def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            removed += self.store.pop(key, None) is not None
        return int(removed)

    def scan_iter(self, match: str) -> list[str]:
        return [k for k in list(self.store) if k.startswith(match.rstrip("*"))]


def test_redis_backend_keeps_none_distinct_from_miss() -> None:
    """Redis 后端同样遵守哨兵契约：GET 到 nil 才是未命中，被缓存的 None 仍是命中。"""
    # Arrange: 值先 pickle 成非空 bytes 再写，因此 nil 只可能代表「键不存在」
    backend = cache_utils.RedisBackend(_FakeRedisClient(), prefix="t:")

    # Act / Assert
    assert backend.get("absent") is cache_utils._MISS        # 键不存在 -> 哨兵

    backend.set("null", None, ttl=60)
    assert backend.get("null") is None                       # 缓存了 None，仍算命中

    backend.set("num", 7)
    assert backend.get("num") == 7
    assert backend.size() == 2

    backend.delete("num")
    assert backend.get("num") is cache_utils._MISS
    assert backend.size() == 1

    assert backend.delete_prefix("zzz") == 0
    assert backend.delete_prefix("n") == 1                   # 只剩 t:null
    assert backend.size() == 0

    backend.set("x", 1)
    backend.clear()                                          # 只清本命名空间
    assert backend.size() == 0


def test_etag_generation_and_conditional_matching() -> None:
    # Arrange
    etag = cache_utils.generate_etag({"id": "hsa04115"})

    # Act / Assert
    assert etag.startswith('"') and etag.endswith('"')
    assert cache_utils.etag_matches(etag, etag) is True          # If-None-Match 相同
    assert cache_utils.etag_matches('W/' + etag, etag) is True   # 弱比较
    assert cache_utils.etag_matches("*", etag) is True           # 通配
    assert cache_utils.etag_matches('"deadbeef"', etag) is False
    assert cache_utils.etag_matches(None, etag) is False

    headers = cache_utils.conditional_headers(etag, max_age=120, private=True)
    assert headers["ETag"] == etag
    assert "max-age=120" in headers["Cache-Control"]
    assert "private" in headers["Cache-Control"]
    assert cache_utils.http_date().endswith("GMT")


def test_cache_backend_is_swappable_and_restored() -> None:
    # Arrange
    original = cache_utils.get_backend()
    replacement = cache_utils.MemoryBackend(maxsize=8)

    try:
        # Act
        cache_utils.set_backend(replacement)
        cache_utils.cache_set("pathway:hsa04115", {"id": "hsa04115"}, ttl=60)
        hit = cache_utils.cache_get("pathway:hsa04115")
        cache_utils.cache_delete("pathway:hsa04115")
        after_delete = cache_utils.cache_get("pathway:hsa04115")
        size = cache_utils.cache_size()

        # Assert
        assert hit == {"id": "hsa04115"}
        assert after_delete is cache_utils._MISS   # ⚠️ 未命中给哨兵，不再是 None
        assert size == 0
    finally:
        cache_utils.set_backend(original)
    assert cache_utils.get_backend() is original


# --------------------------------------------------------------------------- #
# utils/time.py（注意模块名与标准库同名，导入时请起别名）
# --------------------------------------------------------------------------- #
def test_timestamp_and_iso_conversions_are_utc_based() -> None:
    # Arrange
    epoch = time_utils.from_timestamp(0)

    # Act / Assert
    assert epoch.year == 1970 and epoch.tzinfo is not None
    assert time_utils.to_iso(epoch) == "1970-01-01T00:00:00Z"
    assert time_utils.to_iso(epoch, millis=True).endswith("Z")

    millis = time_utils.to_millis(time_utils.now_utc())
    assert abs(time_utils.from_millis(millis).timestamp() - millis / 1000) < 0.01


def test_parse_format_timezone_and_humanize() -> None:
    # Arrange / Act / Assert
    assert time_utils.parse_datetime("2026-09-14T12:00:00Z").hour == 12
    assert time_utils.parse_datetime("2026-09-14").day == 14
    assert time_utils.format_datetime(time_utils.from_timestamp(0), "%Y") == "1970"
    assert time_utils.to_timezone(time_utils.from_timestamp(0), "Asia/Shanghai").hour == 8
    assert time_utils.humanize_duration(3723) == "1h 2m 3s"
    assert time_utils.humanize_duration(0.45) == "450ms"

    with pytest.raises(ValueError):
        time_utils.parse_datetime("definitely-not-a-date")


def test_timers_return_milliseconds() -> None:
    # Arrange
    timer = time_utils.start_timer()

    # Act
    elapsed = timer()

    # Assert
    assert isinstance(elapsed, float) and elapsed >= 0.0
    assert time_utils.elapsed_ms(0.0, 0.5) == 500.0


# --------------------------------------------------------------------------- #
# utils/validators.py
# --------------------------------------------------------------------------- #
def test_kegg_and_gene_identifier_validation() -> None:
    # Arrange / Act / Assert
    assert validators_utils.is_valid_kegg_id("hsa04115") is True
    assert validators_utils.is_valid_kegg_id("map04115") is True
    assert validators_utils.is_valid_kegg_id("hsa415") is False
    assert validators_utils.parse_kegg_id("HSA04115") == ("hsa", "04115")
    assert validators_utils.parse_kegg_id("nope") is None
    assert validators_utils.is_valid_kegg_gene_id("hsa:7157") is True
    assert validators_utils.is_valid_gene_symbol("TP53") is True
    assert validators_utils.is_valid_gene_symbol("HLA-A") is True
    assert validators_utils.is_valid_gene_symbol("1ABC") is False


def test_other_format_validators() -> None:
    # Arrange / Act / Assert
    assert validators_utils.is_valid_email("a.b+tag@mail.co") is True
    assert validators_utils.is_valid_email("bad@@x") is False
    assert validators_utils.is_valid_uniprot_id("P04637") is True
    assert validators_utils.is_valid_ensembl_id("ENSG00000141510.18") is True
    assert validators_utils.is_valid_rsid("rs7412") is True
    assert validators_utils.is_valid_pmid("12345678") is True
    assert validators_utils.is_valid_ipv4("192.168.1.1") is True
    assert validators_utils.is_valid_ipv4("999.1.1.1") is False
    assert validators_utils.is_strong_password("Abc12345!") is True
    assert validators_utils.is_strong_password("abc12345") is False


def test_ensure_raises_validation_error_with_field() -> None:
    # Arrange / Act
    with pytest.raises(validators_utils.ValidationError) as excinfo:
        validators_utils.ensure(False, "bad value", field="symbol")

    # Assert
    assert excinfo.value.field == "symbol"
    validators_utils.ensure(True, "never raised")     # 条件成立时静默通过


# --------------------------------------------------------------------------- #
# utils/response.py
# --------------------------------------------------------------------------- #
def test_success_and_error_envelope_shape() -> None:
    # Arrange / Act
    ok = response_utils.success({"id": 1}, "ok", request_id="r1")
    bad = response_utils.error("NOT_FOUND", "gone", detail={"id": 9}, status=404)

    # Assert
    assert ok["success"] is True and ok["data"] == {"id": 1}
    assert ok["meta"]["request_id"] == "r1" and ok["error"] is None
    assert bad["success"] is False and bad["data"] is None
    assert bad["error"] == {"code": "NOT_FOUND", "detail": {"id": 9}, "status": 404}
    assert response_utils.is_success(ok) is True
    assert response_utils.is_success(bad) is False


def test_pagination_helpers_compute_meta() -> None:
    # Arrange / Act
    meta = response_utils.page_meta(total=95, page=3, page_size=10)
    paged = response_utils.paginate(list(range(10)), total=95, page=3, page_size=10)

    # Assert
    assert meta == {
        "total": 95, "page": 3, "page_size": 10, "pages": 10,
        "has_prev": True, "has_next": True, "offset": 20,
    }
    assert paged["data"]["pagination"]["page"] == 3
    assert paged["meta"]["total"] == 95
    assert response_utils.list_response([1, 2, 3])["meta"]["count"] == 3


def test_chained_helpers_created_no_content_and_exception_mapping() -> None:
    # Arrange: 任意带 code/http_status/detail 的异常（鸭子类型，无需共同基类）
    class FakeError(Exception):
        code = "NOT_FOUND"
        http_status = 404

        def __init__(self) -> None:
            super().__init__("pathway not found")
            self.message = "pathway not found"
            self.detail = {"id": "hsa04115"}

    # Act
    created = response_utils.created({"id": 2})
    empty = response_utils.no_content()
    envelope = response_utils.from_exception(FakeError())
    payload, status = response_utils.to_http(envelope)

    # Assert
    assert created["meta"]["status"] == 201
    assert empty["data"] is None
    assert envelope["error"]["code"] == "NOT_FOUND"
    assert envelope["error"]["detail"] == {"id": "hsa04115"}
    assert status == 404 and payload is envelope
    assert response_utils.merge_meta(response_utils.success(), trace="t")["meta"]["trace"] == "t"

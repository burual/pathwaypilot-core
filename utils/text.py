"""utils/text.py — 文本处理工具（中文分词封装 / 字符串清洗 / 正则工具）。

单一职责：只做文本处理。**不 import 本包内其他 utils 模块**。
可选依赖 jieba：缺失时自动回退到正则分词，不抛错。
"""
from __future__ import annotations

import html as _html
import re
import unicodedata
from typing import Callable, Iterable, Optional, Pattern, Union

__all__ = [
    "clean_text",
    "contains_cjk",
    "dedupe_preserve_order",
    "find_all",
    "first_match",
    "is_blank",
    "normalize_term",
    "normalize_whitespace",
    "remove_punctuation",
    "strip_html",
    "tokenize",
    "tokenize_query",
    "truncate",
]

_CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WORD = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")
_WS = re.compile(r"\s+")
_TAG = re.compile(r"<[^>]+>")

PatternLike = Union[str, Pattern[str]]


def _tokenizer() -> Callable[[str], list[str]]:
    """返回分词器：优先 jieba，缺失则回退正则分词（按字/词切分）。"""
    try:  # 可选依赖，懒加载
        import jieba  # type: ignore

        jieba.setLogLevel(60)

        def _cut(text: str) -> list[str]:
            return [w for w in jieba.lcut(text) if w.strip()]

        return _cut
    except Exception:
        return lambda text: _WORD.findall(text)


def clean_text(
    text: Optional[str],
    *,
    strip: bool = True,
    collapse_whitespace: bool = True,
    remove_control: bool = True,
    unicode_nfkc: bool = False,
) -> str:
    """通用文本清洗：去控制字符 / 折叠空白 / 去首尾空白 / 可选 NFKC 归一化。"""
    if not text:
        return ""
    out = str(text)
    if remove_control:
        out = _CONTROL.sub("", out)
    if unicode_nfkc:
        out = unicodedata.normalize("NFKC", out)
    if collapse_whitespace:
        out = _WS.sub(" ", out)
    if strip:
        out = out.strip()
    return out


def normalize_whitespace(text: str) -> str:
    """把任意连续空白（含全角空格）折叠为单个半角空格并去首尾。"""
    if not text:
        return ""
    return _WS.sub(" ", text.replace("\u3000", " ")).strip()


def is_blank(text: Optional[str]) -> bool:
    """判断字符串是否为 None / 空 / 纯空白。"""
    return text is None or not str(text).strip()


def contains_cjk(text: str) -> bool:
    """是否包含中日韩统一表意文字（用于判断是否走中文分词）。"""
    return bool(text) and _CJK.search(text) is not None


def truncate(text: str, max_len: int, suffix: str = "…") -> str:
    """按字符数截断，超长时追加后缀（后缀计入总长）。max_len<=0 返回空串。"""
    if max_len <= 0:
        return ""
    if text is None:
        return ""
    if len(text) <= max_len:
        return text
    if len(suffix) >= max_len:
        return text[:max_len]
    return text[: max_len - len(suffix)] + suffix


def strip_html(text: str) -> str:
    """去除 HTML 标签并反转义实体（&amp; 之类）。"""
    if not text:
        return ""
    return _html.unescape(_TAG.sub("", text))


def remove_punctuation(text: str, keep: str = "") -> str:
    """删除标点符号；`keep` 中列出的字符不会被删（如保留 '-' '_'）。"""
    if not text:
        return ""
    keep_set = set(keep)
    return "".join(
        ch for ch in text
        if ch in keep_set or not unicodedata.category(ch).startswith("P")
    )


def tokenize(text: str, *, lower: bool = True, dedupe: bool = True) -> list[str]:
    """中文分词（jieba 优先，回退正则）；可选小写与去重。"""
    if is_blank(text):
        return []
    tokens = _tokenizer()(clean_text(text, unicode_nfkc=True))
    result = [t.lower() for t in tokens] if lower else list(tokens)
    return dedupe_preserve_order(result) if dedupe else result


def tokenize_query(query: str, *, min_len: int = 1) -> list[str]:
    """面向检索查询的分词：清洗 -> 分词 -> 过滤过短 token -> 去重。"""
    tokens = tokenize(query, lower=True, dedupe=True)
    return [t for t in tokens if len(t) >= min_len]


def normalize_term(term: str) -> str:
    """归一化检索词：NFKC + 小写 + 去首尾空白 + 折叠内部空白。"""
    if not term:
        return ""
    return normalize_whitespace(clean_text(term, unicode_nfkc=True).lower())


def dedupe_preserve_order(items: Iterable[str]) -> list[str]:
    """去重且保持首次出现顺序。"""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def first_match(
    pattern: PatternLike, text: str, *, group: int = 0, flags: int = 0
) -> Optional[str]:
    """返回首个匹配（默认整个匹配），无匹配返回 None。可传已编译 Pattern。"""
    if text is None:
        return None
    compiled = re.compile(pattern, flags) if isinstance(pattern, str) else pattern
    m = compiled.search(text)
    return m.group(group) if m else None


def find_all(
    pattern: PatternLike, text: str, *, group: int = 0, flags: int = 0
) -> list[str]:
    """返回所有匹配（默认整个匹配），无匹配返回空列表。可传已编译 Pattern。"""
    if not text:
        return []
    compiled = re.compile(pattern, flags) if isinstance(pattern, str) else pattern
    return [m.group(group) for m in compiled.finditer(text)]

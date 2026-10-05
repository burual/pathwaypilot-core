"""utils/validators.py — 输入校验（邮箱 / 基因 ID / KEGG ID 等格式校验）。

单一职责：格式校验与断言。**不 import 本包内其他 utils 模块**。
仅依赖标准库；不引入 pydantic，便于在任意层复用。
"""
from __future__ import annotations

import ipaddress
import re
from typing import Optional, Tuple
from urllib.parse import urlparse

__all__ = [
    "ValidationError",
    "ensure",
    "is_strong_password",
    "is_valid_email",
    "is_valid_ensembl_id",
    "is_valid_gene_symbol",
    "is_valid_ipv4",
    "is_valid_kegg_gene_id",
    "is_valid_kegg_id",
    "is_valid_pmid",
    "is_valid_rsid",
    "is_valid_uniprot_id",
    "is_valid_url",
    "parse_kegg_id",
]

_EMAIL = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
# HGNC 风格基因符号：字母开头，允许数字与 '-'，长度 1..15（如 TP53 / HLA-A / C12orf4）
_GENE_SYMBOL = re.compile(r"^[A-Za-z][A-Za-z0-9\-]{0,14}$")
# KEGG 通路 ID：物种/参考前缀 + 5 位数字（hsa04115 / map04115 / ko04115 / mmu00010）
_KEGG_ID = re.compile(r"^(?P<org>[a-z]{2,4}|map|ko)(?P<num>\d{5})$")
# KEGG 基因 ID：organism:entrez（hsa:7157）
_KEGG_GENE = re.compile(r"^[a-z]{2,4}:\d+$")
# UniProt 登录号（覆盖常见 6/10 位形态）
_UNIPROT = re.compile(
    r"^(?:[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})$"
)
# Ensembl 基因 ID：ENS[物种]G + 11 位数字（可带版本号）
_ENSEMBL = re.compile(r"^ENS[A-Z]*G\d{11}(?:\.\d+)?$")
_RSID = re.compile(r"^rs\d+$")
_PMID = re.compile(r"^\d{1,9}$")


class ValidationError(ValueError):
    """校验失败异常（自带 field 便于上层定位）。"""

    def __init__(self, message: str, *, field: Optional[str] = None) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


def ensure(condition: object, message: str, *, field: Optional[str] = None) -> None:
    """条件为假时抛 ValidationError。用作命令式断言校验。"""
    if not condition:
        raise ValidationError(message, field=field)


def is_valid_email(email: Optional[str]) -> bool:
    """校验邮箱格式（结构级，不做 DNS 验证）。"""
    return bool(email) and _EMAIL.match(str(email).strip()) is not None


def is_valid_gene_symbol(symbol: Optional[str]) -> bool:
    """校验基因符号格式（HGNC 风格，大小写不敏感）。"""
    return bool(symbol) and _GENE_SYMBOL.match(str(symbol).strip()) is not None


def is_valid_kegg_id(kegg_id: Optional[str]) -> bool:
    """校验 KEGG 通路 ID（如 hsa04115 / map04115 / ko04115）。"""
    return bool(kegg_id) and _KEGG_ID.match(str(kegg_id).strip().lower()) is not None


def parse_kegg_id(kegg_id: str) -> Optional[Tuple[str, str]]:
    """拆解 KEGG 通路 ID -> (organism, number)；非法返回 None（organism 小写保留）。"""
    if not kegg_id:
        return None
    m = _KEGG_ID.match(str(kegg_id).strip().lower())
    return (m.group("org"), m.group("num")) if m else None


def is_valid_kegg_gene_id(kegg_gene_id: Optional[str]) -> bool:
    """校验 KEGG 基因 ID（如 hsa:7157）。"""
    return bool(kegg_gene_id) and _KEGG_GENE.match(str(kegg_gene_id).strip().lower()) is not None


def is_valid_uniprot_id(uniprot_id: Optional[str]) -> bool:
    """校验 UniProt 登录号（常见 6/10 位形态）。"""
    return bool(uniprot_id) and _UNIPROT.match(str(uniprot_id).strip().upper()) is not None


def is_valid_ensembl_id(ensembl_id: Optional[str]) -> bool:
    """校验 Ensembl 基因 ID（如 ENSG00000141510 / ENSG00000141510.18）。"""
    return bool(ensembl_id) and _ENSEMBL.match(str(ensembl_id).strip().upper()) is not None


def is_valid_rsid(rsid: Optional[str]) -> bool:
    """校验 dbSNP rsID（如 rs7412）。"""
    return bool(rsid) and _RSID.match(str(rsid).strip().lower()) is not None


def is_valid_pmid(pmid: Optional[str]) -> bool:
    """校验 PubMed ID（纯数字，最长 9 位）。"""
    return bool(pmid) and _PMID.match(str(pmid).strip()) is not None


def is_valid_url(url: Optional[str], *, schemes: Tuple[str, ...] = ("http", "https")) -> bool:
    """校验 URL：必须有 scheme 与 netloc，且 scheme 在白名单内。"""
    if not url:
        return False
    try:
        parsed = urlparse(str(url).strip())
    except ValueError:
        return False
    return parsed.scheme in schemes and bool(parsed.netloc)


def is_valid_ipv4(value: Optional[str]) -> bool:
    """校验 IPv4 地址（严格模式，拒绝前导零歧义）。"""
    if not value:
        return False
    try:
        ipaddress.IPv4Address(str(value).strip())
        return True
    except (ipaddress.AddressValueError, ValueError):
        return False


def is_strong_password(password: Optional[str], *, min_length: int = 8) -> bool:
    """强密码校验：长度达标 + 含大小写字母、数字、特殊字符各至少一。"""
    if not password or len(password) < min_length:
        return False
    has_lower = any(c.islower() for c in password)
    has_upper = any(c.isupper() for c in password)
    has_digit = any(c.isdigit() for c in password)
    has_special = any(not c.isalnum() for c in password)
    return has_lower and has_upper and has_digit and has_special

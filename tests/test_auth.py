"""tests/test_auth.py — 鉴权层测试模板（≥2 个用例）。

约定契约（`auth/` 落地后请保持同名同义）：

    auth.jwt         TokenError / create_access_token(subject, *, secret, expires_in,
                                                     extra, now)
                     decode_access_token(token, *, secret, now)
    auth.password    hash_password(password) / verify_password(password, hashed)
    auth.permissions PermissionDenied / permissions_for(role) / has_permission(role, perm)
                     check_permission(role, perm) / require_role(required, role)

`now` 参数让过期用例**无需 sleep**，测试是确定性的。

⚠️ 若当前工作区尚无 `auth/` 包，`tests/conftest.py` 会注入一份严格遵循上述契约的
   参考实现，所以本文件任何时候都能跑；真实 `auth/` 落地后自动切过去。
"""
from __future__ import annotations

import pytest

# conftest 已保证 auth.* 可导入（真实实现优先，缺失则注入参考实现）
from auth.jwt import TokenError, create_access_token, decode_access_token
from auth.password import hash_password, verify_password
from auth.permissions import (
    PermissionDenied,
    check_permission,
    has_permission,
    permissions_for,
    require_role,
)

SECRET = "unit-test-secret"
FIXED_NOW = 1_800_000_000  # 固定时钟，过期用例不依赖真实时间


# --------------------------------------------------------------------------- #
# 用例 1：JWT 签发 -> 校验，claims 原样回传
# --------------------------------------------------------------------------- #
def test_jwt_round_trip_returns_claims_and_honours_extra() -> None:
    # Arrange
    subject = "user-42"

    # Act
    token = create_access_token(
        subject, secret=SECRET, expires_in=1800, extra={"role": "user"}, now=FIXED_NOW
    )
    claims = decode_access_token(token, secret=SECRET, now=FIXED_NOW + 10)

    # Assert
    assert token.count(".") == 2                 # header.payload.signature
    assert claims["sub"] == subject
    assert claims["role"] == "user"
    assert claims["iat"] == FIXED_NOW
    assert claims["exp"] == FIXED_NOW + 1800


def test_jwt_rejects_tampered_signature_and_wrong_secret() -> None:
    # Arrange
    token = create_access_token("user-42", secret=SECRET, now=FIXED_NOW)
    head, body, signature = token.split(".")
    tampered = f"{head}.{body}." + ("A" if signature[-1] != "A" else "B")

    # Act / Assert: 篡改签名
    with pytest.raises(TokenError) as tampered_error:
        decode_access_token(tampered, secret=SECRET, now=FIXED_NOW)
    assert tampered_error.value.http_status == 401
    assert tampered_error.value.code == "INVALID_TOKEN"

    # Act / Assert: 用错误的密钥校验
    with pytest.raises(TokenError, match="signature"):
        decode_access_token(token, secret="another-secret", now=FIXED_NOW)


def test_jwt_rejects_expired_and_malformed_token() -> None:
    # Arrange
    short_lived = create_access_token("user-42", secret=SECRET, expires_in=60, now=FIXED_NOW)

    # Act / Assert: 过期（时钟前进 10 分钟，无需真的等待）
    with pytest.raises(TokenError, match="expired"):
        decode_access_token(short_lived, secret=SECRET, now=FIXED_NOW + 600)

    # Act / Assert: 结构非法
    with pytest.raises(TokenError, match="malformed"):
        decode_access_token("not-a-jwt", secret=SECRET, now=FIXED_NOW)
    with pytest.raises(TokenError):
        decode_access_token("", secret=SECRET, now=FIXED_NOW)


# --------------------------------------------------------------------------- #
# 用例 2：密码哈希 —— 加盐、不可逆格式、能正确校验
# --------------------------------------------------------------------------- #
def test_hash_password_is_salted_and_verifiable() -> None:
    # Arrange
    password = "Abc12345!"

    # Act
    first = hash_password(password)
    second = hash_password(password)

    # Assert
    assert first != second                       # 每次盐不同
    assert password not in first                 # 明文不入库
    assert first.count("$") == 3                 # algo$iterations$salt$hash
    assert verify_password(password, first) is True
    assert verify_password(password, second) is True


def test_verify_password_rejects_wrong_password_and_broken_hash() -> None:
    # Arrange
    stored = hash_password("Abc12345!")

    # Act / Assert
    assert verify_password("wrong-password", stored) is False
    assert verify_password("", stored) is False
    assert verify_password("Abc12345!", "") is False
    assert verify_password("Abc12345!", "garbage") is False       # 格式错误不抛异常
    assert verify_password("Abc12345!", "md5$1$salt$deadbeef") is False  # 算法不符


# --------------------------------------------------------------------------- #
# 用例 3：权限矩阵 + 拒绝时抛 PermissionDenied
# --------------------------------------------------------------------------- #
def test_permission_matrix_allows_and_denies_by_role() -> None:
    # Arrange / Act
    guest_can_read = has_permission("guest", "pathway:read")
    guest_cannot_write = has_permission("guest", "pathway:write")
    admin_can_manage = has_permission("admin", "user:manage")
    unknown_role_perms = permissions_for("nobody")

    # Assert
    assert guest_can_read is True
    assert guest_cannot_write is False
    assert admin_can_manage is True
    assert unknown_role_perms == frozenset()     # 未知角色 = 零权限（fail-closed）


def test_check_permission_raises_denied_with_403_semantics() -> None:
    # Arrange / Act
    check_permission("admin", "analysis:delete")      # 有权限：不抛

    # Assert
    with pytest.raises(PermissionDenied) as excinfo:
        check_permission("user", "analysis:delete")
    assert excinfo.value.code == "ACCESS_DENIED"
    assert excinfo.value.http_status == 403


def test_require_role_enforces_role_hierarchy() -> None:
    # Arrange / Act / Assert
    assert require_role("user", "admin") is True      # 更高角色可降级使用
    assert require_role("guest", "guest") is True

    with pytest.raises(PermissionDenied, match="required"):
        require_role("admin", "user")                 # 权限不足

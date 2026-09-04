"""비밀번호 해시 (로컬 users 테이블 전용).

외부 의존성 없이 표준 라이브러리 PBKDF2-HMAC-SHA256 을 쓴다.
저장 형식: ``pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>``
"""

from __future__ import annotations

import hashlib
import hmac
import os

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 200_000
SALT_BYTES = 16


def hash_password(password: str, *, iterations: int = ITERATIONS) -> str:
    salt = os.urandom(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{ALGORITHM}${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    """평문 비밀번호가 저장된 해시와 일치하는지 확인한다.

    형식이 깨졌거나 값이 없으면 조용히 False (예외를 던져 로그인 흐름을 깨지 않는다).
    """
    if not stored:
        return False
    try:
        algorithm, raw_iterations, salt_hex, digest_hex = stored.split("$")
        if algorithm != ALGORITHM:
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        iterations = int(raw_iterations)
    except (ValueError, AttributeError):
        return False

    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(candidate, expected)

"""비밀번호 해시."""

from __future__ import annotations

from app.security import hash_password, verify_password


def test_해시는_평문을_포함하지_않는다():
    stored = hash_password("secret1234")
    assert "secret1234" not in stored
    assert stored.startswith("pbkdf2_sha256$")


def test_같은_비밀번호도_매번_다른_해시가_된다():
    assert hash_password("same") != hash_password("same")


def test_올바른_비밀번호만_검증에_통과한다():
    stored = hash_password("correct-horse")
    assert verify_password("correct-horse", stored) is True
    assert verify_password("wrong", stored) is False


def test_깨진_해시나_빈_값은_예외없이_실패한다():
    assert verify_password("pw", None) is False
    assert verify_password("pw", "") is False
    assert verify_password("pw", "not-a-hash") is False
    assert verify_password("pw", "md5$1$aa$bb") is False
    assert verify_password("pw", "pbkdf2_sha256$notanint$aa$bb") is False

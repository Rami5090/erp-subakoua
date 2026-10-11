from auth_core import (
    lock_remaining, register_failure, register_success, session_expired, sha256_hex, verify_password,
)


def test_fail_closed_without_configuration():
    assert verify_password("anything") is False
    assert verify_password("", "secret") is False
    assert verify_password("x", "", "") is False
    assert verify_password("x", "   ", "  ") is False


def test_plain_and_hash_passwords():
    assert verify_password("s3cret", plain="s3cret")
    assert not verify_password("S3cret", plain="s3cret")
    h = sha256_hex("s3cret")
    assert verify_password("s3cret", sha256=h)
    assert verify_password("s3cret", sha256=h.upper())
    assert not verify_password("nope", sha256=h)


def test_unicode_password_is_supported():
    assert verify_password("mot-de-passe-é", plain="mot-de-passe-é")


def test_lockout_after_repeated_failures_then_release():
    st = {}
    for _ in range(4):
        assert register_failure(st, 1000.0) == 0
    assert register_failure(st, 1000.0) == 60
    assert lock_remaining(st, 1010.0) == 50
    assert lock_remaining(st, 1061.0) == 0
    register_success(st)
    assert st["failures"] == 0 and lock_remaining(st, 1000.0) == 0


def test_session_expiry():
    assert session_expired(None, 100.0)
    assert not session_expired(100.0, 200.0, ttl=3600)
    assert session_expired(100.0, 100.0 + 3601, ttl=3600)

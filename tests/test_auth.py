"""Unit tests for capability-based agent authentication (auth.py)."""

import time
import pytest
from pathlib import Path

from screenbox.auth import TokenAuth, AuthError


class TestTokenCreation:
    def test_create_basic_token(self):
        auth = TokenAuth(secret="test-secret")
        token = auth.create("agent-1", ["desktop-a", "desktop-b"])
        assert isinstance(token, str)
        assert "." in token

    def test_create_wildcard_token(self):
        auth = TokenAuth(secret="test-secret")
        token = auth.create("admin", ["*"])
        claims = auth.validate(token)
        assert claims["desktops"] == ["*"]

    def test_create_with_expiry(self):
        auth = TokenAuth(secret="test-secret")
        token = auth.create("agent-1", ["d1"], exp_seconds=3600)
        claims = auth.validate(token)
        assert "exp" in claims
        assert claims["exp"] > time.time()

    def test_create_no_expiry(self):
        auth = TokenAuth(secret="test-secret")
        token = auth.create("agent-1", ["d1"])
        claims = auth.validate(token)
        assert "exp" not in claims

    def test_different_secrets_different_tokens(self):
        auth1 = TokenAuth(secret="secret-1")
        auth2 = TokenAuth(secret="secret-2")
        token1 = auth1.create("agent", ["d1"])
        token2 = auth2.create("agent", ["d1"])
        assert token1 != token2


class TestTokenValidation:
    def test_validate_valid_token(self):
        auth = TokenAuth(secret="test-secret")
        token = auth.create("agent-1", ["desktop-a"])
        claims = auth.validate(token)
        assert claims["agent"] == "agent-1"
        assert claims["desktops"] == ["desktop-a"]

    def test_validate_tampered_payload(self):
        auth = TokenAuth(secret="test-secret")
        token = auth.create("agent-1", ["d1"])
        # Tamper with payload
        parts = token.split(".")
        tampered = "x" + parts[0][1:]
        with pytest.raises(AuthError, match="signature"):
            auth.validate(f"{tampered}.{parts[1]}")

    def test_validate_tampered_signature(self):
        auth = TokenAuth(secret="test-secret")
        token = auth.create("agent-1", ["d1"])
        parts = token.split(".")
        tampered_sig = "x" + parts[1][1:]
        with pytest.raises(AuthError, match="signature"):
            auth.validate(f"{parts[0]}.{tampered_sig}")

    def test_validate_wrong_secret(self):
        auth1 = TokenAuth(secret="secret-1")
        auth2 = TokenAuth(secret="secret-2")
        token = auth1.create("agent-1", ["d1"])
        with pytest.raises(AuthError, match="signature"):
            auth2.validate(token)

    def test_validate_expired_token(self):
        auth = TokenAuth(secret="test-secret")
        token = auth.create("agent-1", ["d1"], exp_seconds=-1)
        with pytest.raises(AuthError, match="expired"):
            auth.validate(token)

    def test_validate_bad_format_no_dot(self):
        auth = TokenAuth(secret="test-secret")
        with pytest.raises(AuthError, match="format"):
            auth.validate("not-a-valid-token")

    def test_validate_bad_format_too_many_dots(self):
        auth = TokenAuth(secret="test-secret")
        with pytest.raises(AuthError, match="format"):
            auth.validate("a.b.c")

    def test_validate_bad_encoding(self):
        auth = TokenAuth(secret="test-secret")
        with pytest.raises(AuthError):
            auth.validate("!!!.!!!")

    def test_validate_empty_string(self):
        auth = TokenAuth(secret="test-secret")
        with pytest.raises(AuthError):
            auth.validate("")


class TestAccessControl:
    def test_check_access_allowed(self):
        auth = TokenAuth(secret="test-secret")
        token = auth.create("agent-1", ["desktop-a", "desktop-b"])
        claims = auth.check_access(token, "desktop-a")
        assert claims["agent"] == "agent-1"

    def test_check_access_denied(self):
        auth = TokenAuth(secret="test-secret")
        token = auth.create("agent-1", ["desktop-a"])
        with pytest.raises(AuthError, match="no access"):
            auth.check_access(token, "desktop-b")

    def test_check_access_wildcard(self):
        auth = TokenAuth(secret="test-secret")
        token = auth.create("admin", ["*"])
        claims = auth.check_access(token, "any-desktop-id")
        assert claims["agent"] == "admin"

    def test_check_access_wildcard_any_id(self):
        auth = TokenAuth(secret="test-secret")
        token = auth.create("admin", ["*"])
        # Should work for any desktop ID
        auth.check_access(token, "xyz-123")
        auth.check_access(token, "another-one")

    def test_check_access_multiple_desktops(self):
        auth = TokenAuth(secret="test-secret")
        desktops = [f"desktop-{i}" for i in range(20)]
        token = auth.create("agent-1", desktops)
        for d in desktops:
            auth.check_access(token, d)

    def test_check_access_expired(self):
        auth = TokenAuth(secret="test-secret")
        token = auth.create("agent-1", ["d1"], exp_seconds=-1)
        with pytest.raises(AuthError, match="expired"):
            auth.check_access(token, "d1")


class TestSecretResolution:
    def test_explicit_secret(self):
        auth = TokenAuth(secret="my-secret")
        token = auth.create("a", ["d"])
        auth.validate(token)  # should not raise

    def test_env_secret(self, monkeypatch):
        monkeypatch.setenv("SCREENBOX_MASTER_SECRET", "env-secret-123")
        auth = TokenAuth()
        token = auth.create("a", ["d"])
        # Validate with same env
        auth2 = TokenAuth()
        auth2.validate(token)

    def test_auto_generated_secret(self, tmp_path):
        auth = TokenAuth(base_dir=tmp_path)
        secret_file = tmp_path / "master-secret"
        assert secret_file.exists()
        # Same base_dir -> same secret -> validates
        auth2 = TokenAuth(base_dir=tmp_path)
        token = auth.create("a", ["d"])
        auth2.validate(token)

    def test_auto_secret_persists(self, tmp_path):
        auth1 = TokenAuth(base_dir=tmp_path)
        token = auth1.create("a", ["d"])
        # New instance from same dir should use same secret
        auth2 = TokenAuth(base_dir=tmp_path)
        claims = auth2.validate(token)
        assert claims["agent"] == "a"

    def test_auto_secret_file_permissions(self, tmp_path):
        import stat
        TokenAuth(base_dir=tmp_path)
        secret_file = tmp_path / "master-secret"
        mode = secret_file.stat().st_mode & 0o777
        assert mode == 0o600

"""Capability-based agent authentication.

Tokens are HMAC-SHA256 signed JSON payloads: {agent, desktops, exp?}.
Stateless -- no database, no sessions. The master secret is the only state.

Token format: base64url(payload_json).base64url(hmac_signature)

Usage:
    from screenbox.auth import TokenAuth
    auth = TokenAuth(secret="...")           # or auto from env/file
    token = auth.create("agent-1", ["desktop-a", "desktop-b"])
    claims = auth.validate(token)            # raises AuthError on failure
    auth.check_access(token, "desktop-a")    # raises AuthError if not allowed
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Optional


class AuthError(Exception):
    """Authentication or authorization failure."""
    pass


class TokenAuth:
    """HMAC-SHA256 token-based auth for agent -> desktop access control.

    Master secret resolution order:
    1. Explicit `secret` parameter
    2. SCREENBOX_MASTER_SECRET env var
    3. Auto-generated file at ~/.screenbox/master-secret
    """

    def __init__(self, secret: Optional[str] = None,
                 base_dir: Optional[Path] = None):
        self._secret = self._resolve_secret(secret, base_dir)

    def _resolve_secret(self, secret: Optional[str],
                        base_dir: Optional[Path]) -> bytes:
        """Resolve master secret from explicit value, env, or file."""
        if secret:
            return secret.encode("utf-8")

        env_secret = os.environ.get("SCREENBOX_MASTER_SECRET")
        if env_secret:
            return env_secret.encode("utf-8")

        # Auto-generate and persist
        secret_file = (base_dir or Path.home() / ".screenbox") / "master-secret"
        if secret_file.exists():
            return secret_file.read_text().strip().encode("utf-8")

        # Generate new secret
        new_secret = secrets.token_hex(32)
        secret_file.parent.mkdir(parents=True, exist_ok=True)
        secret_file.write_text(new_secret)
        try:
            secret_file.chmod(0o600)
        except OSError:
            pass
        return new_secret.encode("utf-8")

    def _sign(self, payload: bytes) -> bytes:
        """HMAC-SHA256 sign payload."""
        return hmac.new(self._secret, payload, hashlib.sha256).digest()

    def _b64encode(self, data: bytes) -> str:
        """URL-safe base64 encode without padding."""
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    def _b64decode(self, s: str) -> bytes:
        """URL-safe base64 decode with padding restoration."""
        padding = 4 - len(s) % 4
        if padding != 4:
            s += "=" * padding
        return base64.urlsafe_b64decode(s)

    def create(self, agent_id: str, desktops: list[str],
               exp_seconds: Optional[int] = None) -> str:
        """Create a signed agent token.

        Args:
            agent_id: Unique agent identifier.
            desktops: List of desktop IDs this agent can access.
                      Use ["*"] for access to all desktops.
            exp_seconds: Token lifetime in seconds. None = no expiry.

        Returns:
            Signed token string (base64url.base64url format).
        """
        payload = {"agent": agent_id, "desktops": desktops}
        if exp_seconds is not None:
            payload["exp"] = int(time.time()) + exp_seconds

        payload_bytes = json.dumps(payload, separators=(",", ":"),
                                    sort_keys=True).encode("utf-8")
        sig = self._sign(payload_bytes)
        return f"{self._b64encode(payload_bytes)}.{self._b64encode(sig)}"

    def validate(self, token: str) -> dict:
        """Validate token signature and expiry.

        Returns:
            Claims dict: {agent, desktops, exp?}

        Raises:
            AuthError: If token is invalid, tampered, or expired.
        """
        parts = token.split(".")
        if len(parts) != 2:
            raise AuthError("Invalid token format")

        try:
            payload_bytes = self._b64decode(parts[0])
            sig_bytes = self._b64decode(parts[1])
        except Exception:
            raise AuthError("Invalid token encoding")

        # Verify signature
        expected_sig = self._sign(payload_bytes)
        if not hmac.compare_digest(sig_bytes, expected_sig):
            raise AuthError("Invalid token signature")

        # Parse payload
        try:
            claims = json.loads(payload_bytes)
        except json.JSONDecodeError:
            raise AuthError("Invalid token payload")

        # Validate structure
        if "agent" not in claims or "desktops" not in claims:
            raise AuthError("Token missing required fields (agent, desktops)")
        if not isinstance(claims["desktops"], list):
            raise AuthError("Token desktops must be a list")

        # Check expiry
        if "exp" in claims and time.time() > claims["exp"]:
            raise AuthError(f"Token expired at {claims['exp']}")

        return claims

    def check_access(self, token: str, desktop_id: str) -> dict:
        """Validate token and check access to specific desktop.

        Returns:
            Claims dict if access is granted.

        Raises:
            AuthError: If token invalid or desktop not in allowed list.
        """
        claims = self.validate(token)
        allowed = claims["desktops"]
        if "*" not in allowed and desktop_id not in allowed:
            raise AuthError(
                f"Agent '{claims['agent']}' has no access to desktop '{desktop_id}'. "
                f"Allowed: {allowed}"
            )
        return claims

    @property
    def enabled(self) -> bool:
        """Whether auth is configured (secret exists)."""
        return bool(self._secret)

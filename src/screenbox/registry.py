"""Agent registry and session management -- SQLite backend.

Tables:
  agents             - registered agents (id, api_key_hash, display_name, status)
  sessions           - active sessions (token, agent_id, source_ip, timestamps)
  desktop_assignments - which agent owns which desktop (admin-only release)

DB file: ~/.screenbox/screenbox.db (inside screenbox-data volume)
"""

import hashlib
import logging
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("screenbox.registry")


class Registry:
    """SQLite-backed agent registry with per-thread connections."""

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        # Init tables on main thread
        conn = self._get_conn()
        conn.execute("PRAGMA journal_mode=WAL")
        self._init_tables()
        log.info("Registry opened: %s", db_path)

    def _get_conn(self) -> sqlite3.Connection:
        """Get thread-local SQLite connection."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self._db_path))
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    def _init_tables(self):
        self._get_conn().executescript("""
            CREATE TABLE IF NOT EXISTS agents (
                id            TEXT PRIMARY KEY,
                api_key_hash  TEXT NOT NULL,
                display_name  TEXT,
                created_at    REAL NOT NULL,
                status        TEXT NOT NULL DEFAULT 'active'
                    CHECK(status IN ('active', 'suspended'))
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token         TEXT PRIMARY KEY,
                agent_id      TEXT NOT NULL REFERENCES agents(id),
                source_ip     TEXT,
                started_at    REAL NOT NULL,
                last_activity REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS desktop_assignments (
                desktop_id    TEXT PRIMARY KEY,
                agent_id      TEXT NOT NULL REFERENCES agents(id),
                assigned_at   REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS desktop_locks (
                desktop_id    TEXT PRIMARY KEY,
                locked_by     TEXT,
                locked_at     REAL NOT NULL,
                reason        TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS desktop_shares (
                token         TEXT PRIMARY KEY,
                desktop_id    TEXT NOT NULL,
                created_by    TEXT,
                created_at    REAL NOT NULL,
                expires_at    REAL NOT NULL
            );
        """)
        self._get_conn().commit()

    def close(self):
        self._get_conn().close()

    # -- Agents --

    @staticmethod
    def _hash_key(api_key: str) -> str:
        return hashlib.sha256(api_key.encode()).hexdigest()

    def register_agent(self, agent_id: str, display_name: str = "") -> str:
        """Register new agent. Returns api_key (shown once, not stored)."""
        existing = self._get_conn().execute(
            "SELECT id FROM agents WHERE id = ?", (agent_id,)
        ).fetchone()
        if existing:
            raise ValueError(f"Agent '{agent_id}' already registered")

        api_key = secrets.token_hex(32)
        self._get_conn().execute(
            "INSERT INTO agents (id, api_key_hash, display_name, created_at) "
            "VALUES (?, ?, ?, ?)",
            (agent_id, self._hash_key(api_key), display_name or agent_id, time.time()),
        )
        self._get_conn().commit()
        log.info("Agent registered: %s", agent_id)
        return api_key

    def list_agents(self) -> list[dict]:
        rows = self._get_conn().execute(
            "SELECT id, display_name, created_at, status FROM agents ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]

    def suspend_agent(self, agent_id: str) -> bool:
        """Suspend agent -- invalidates all sessions."""
        cur = self._get_conn().execute(
            "UPDATE agents SET status = 'suspended' WHERE id = ?", (agent_id,)
        )
        if cur.rowcount:
            # Kill all sessions
            self._get_conn().execute("DELETE FROM sessions WHERE agent_id = ?", (agent_id,))
            self._get_conn().commit()
            log.info("Agent suspended: %s", agent_id)
            return True
        return False

    def activate_agent(self, agent_id: str) -> bool:
        cur = self._get_conn().execute(
            "UPDATE agents SET status = 'active' WHERE id = ?", (agent_id,)
        )
        self._get_conn().commit()
        return bool(cur.rowcount)

    def delete_agent(self, agent_id: str) -> bool:
        """Delete agent, sessions, and assignments."""
        self._get_conn().execute("DELETE FROM sessions WHERE agent_id = ?", (agent_id,))
        self._get_conn().execute("DELETE FROM desktop_assignments WHERE agent_id = ?", (agent_id,))
        cur = self._get_conn().execute("DELETE FROM agents WHERE id = ?", (agent_id,))
        self._get_conn().commit()
        return bool(cur.rowcount)

    def reset_agent_key(self, agent_id: str) -> Optional[str]:
        """Generate new API key for agent. Returns new key or None."""
        existing = self._get_conn().execute(
            "SELECT id FROM agents WHERE id = ?", (agent_id,)
        ).fetchone()
        if not existing:
            return None
        new_key = secrets.token_hex(32)
        self._get_conn().execute(
            "UPDATE agents SET api_key_hash = ? WHERE id = ?",
            (self._hash_key(new_key), agent_id),
        )
        # Invalidate sessions
        self._get_conn().execute("DELETE FROM sessions WHERE agent_id = ?", (agent_id,))
        self._get_conn().commit()
        log.info("API key reset for agent: %s", agent_id)
        return new_key

    # -- Sessions --

    def login(self, agent_id: str, api_key: str,
              source_ip: str = "") -> Optional[str]:
        """Authenticate agent, create session. Returns session token or None."""
        row = self._get_conn().execute(
            "SELECT api_key_hash, status FROM agents WHERE id = ?", (agent_id,)
        ).fetchone()
        if not row:
            return None
        if row["status"] != "active":
            return None
        if row["api_key_hash"] != self._hash_key(api_key):
            return None

        token = secrets.token_hex(32)
        now = time.time()
        self._get_conn().execute(
            "INSERT INTO sessions (token, agent_id, source_ip, started_at, last_activity) "
            "VALUES (?, ?, ?, ?, ?)",
            (token, agent_id, source_ip, now, now),
        )
        self._get_conn().commit()
        log.info("Agent login: %s from %s", agent_id, source_ip or "unknown")
        return token

    def validate_session(self, token: str) -> Optional[dict]:
        """Validate session token. Returns {agent_id, source_ip} or None.

        Also updates last_activity timestamp (implicit heartbeat).
        """
        row = self._get_conn().execute(
            "SELECT s.token, s.agent_id, s.source_ip, s.started_at, "
            "a.status FROM sessions s JOIN agents a ON s.agent_id = a.id "
            "WHERE s.token = ?",
            (token,),
        ).fetchone()
        if not row or row["status"] != "active":
            return None
        self._get_conn().execute(
            "UPDATE sessions SET last_activity = ? WHERE token = ?",
            (time.time(), token),
        )
        self._get_conn().commit()
        return {"agent_id": row["agent_id"], "source_ip": row["source_ip"]}

    def logout(self, token: str) -> bool:
        cur = self._get_conn().execute("DELETE FROM sessions WHERE token = ?", (token,))
        self._get_conn().commit()
        return bool(cur.rowcount)

    def list_sessions(self) -> list[dict]:
        """All active sessions with agent info."""
        rows = self._get_conn().execute(
            "SELECT s.token, s.agent_id, a.display_name, s.source_ip, "
            "s.started_at, s.last_activity "
            "FROM sessions s JOIN agents a ON s.agent_id = a.id "
            "ORDER BY s.last_activity DESC"
        ).fetchall()
        now = time.time()
        result = []
        for r in rows:
            idle = now - r["last_activity"]
            duration = now - r["started_at"]
            result.append({
                "token_prefix": r["token"][:8] + "...",
                "agent_id": r["agent_id"],
                "display_name": r["display_name"],
                "source_ip": r["source_ip"],
                "started_at": r["started_at"],
                "duration_s": int(duration),
                "idle_s": int(idle),
                "status": "idle" if idle > 300 else "active",
            })
        return result

    # -- Desktop Assignments --

    def assign_desktop(self, desktop_id: str, agent_id: str) -> bool:
        """Assign desktop to agent. Fails if already assigned to another."""
        existing = self._get_conn().execute(
            "SELECT agent_id FROM desktop_assignments WHERE desktop_id = ?",
            (desktop_id,),
        ).fetchone()
        if existing and existing["agent_id"] != agent_id:
            return False
        self._get_conn().execute(
            "INSERT OR REPLACE INTO desktop_assignments "
            "(desktop_id, agent_id, assigned_at) VALUES (?, ?, ?)",
            (desktop_id, agent_id, time.time()),
        )
        self._get_conn().commit()
        log.info("Desktop %s assigned to %s", desktop_id, agent_id)
        return True

    def release_desktop(self, desktop_id: str) -> bool:
        """Admin-only: release desktop assignment."""
        cur = self._get_conn().execute(
            "DELETE FROM desktop_assignments WHERE desktop_id = ?",
            (desktop_id,),
        )
        self._get_conn().commit()
        return bool(cur.rowcount)

    def get_assignment(self, desktop_id: str) -> Optional[str]:
        """Get agent_id assigned to desktop, or None."""
        row = self._get_conn().execute(
            "SELECT agent_id FROM desktop_assignments WHERE desktop_id = ?",
            (desktop_id,),
        ).fetchone()
        return row["agent_id"] if row else None

    def get_agent_desktops(self, agent_id: str) -> list[str]:
        """Get all desktop IDs assigned to an agent."""
        rows = self._get_conn().execute(
            "SELECT desktop_id FROM desktop_assignments WHERE agent_id = ?",
            (agent_id,),
        ).fetchall()
        return [r["desktop_id"] for r in rows]

    # -- Desktop Locks --

    def lock_desktop(self, desktop_id: str, locked_by: str = "", reason: str = "") -> bool:
        self._get_conn().execute(
            "INSERT OR REPLACE INTO desktop_locks (desktop_id, locked_by, locked_at, reason) "
            "VALUES (?, ?, ?, ?)",
            (desktop_id, locked_by, time.time(), reason),
        )
        self._get_conn().commit()
        return True

    def unlock_desktop(self, desktop_id: str) -> bool:
        cur = self._get_conn().execute(
            "DELETE FROM desktop_locks WHERE desktop_id = ?", (desktop_id,))
        self._get_conn().commit()
        return bool(cur.rowcount)

    def is_locked(self, desktop_id: str) -> bool:
        row = self._get_conn().execute(
            "SELECT desktop_id FROM desktop_locks WHERE desktop_id = ?",
            (desktop_id,),
        ).fetchone()
        return row is not None

    def get_lock(self, desktop_id: str) -> dict | None:
        row = self._get_conn().execute(
            "SELECT * FROM desktop_locks WHERE desktop_id = ?",
            (desktop_id,),
        ).fetchone()
        return dict(row) if row else None

    def list_assignments(self) -> list[dict]:
        """All desktop assignments."""
        rows = self._get_conn().execute(
            "SELECT da.desktop_id, da.agent_id, a.display_name, da.assigned_at "
            "FROM desktop_assignments da "
            "JOIN agents a ON da.agent_id = a.id "
            "ORDER BY da.assigned_at"
        ).fetchall()
        return [dict(r) for r in rows]

    # -- Desktop Shares --

    def create_share(self, desktop_id: str, ttl: int = 3600,
                     created_by: str = "") -> dict:
        """Create a share link for a desktop. Returns {token, expires_at}."""
        token = secrets.token_urlsafe(32)
        now = time.time()
        expires_at = now + ttl
        self._get_conn().execute(
            "INSERT INTO desktop_shares (token, desktop_id, created_by, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (token, desktop_id, created_by, now, expires_at),
        )
        self._get_conn().commit()
        log.info("Share created for desktop %s by %s (ttl=%ds)", desktop_id, created_by or "admin", ttl)
        return {"token": token, "desktop_id": desktop_id, "expires_at": expires_at}

    def validate_share(self, token: str) -> Optional[dict]:
        """Validate share token. Returns {desktop_id, created_by, expires_at} or None."""
        row = self._get_conn().execute(
            "SELECT desktop_id, created_by, expires_at FROM desktop_shares WHERE token = ?",
            (token,),
        ).fetchone()
        if not row:
            return None
        if time.time() > row["expires_at"]:
            # Expired -- clean up
            self._get_conn().execute("DELETE FROM desktop_shares WHERE token = ?", (token,))
            self._get_conn().commit()
            return None
        return dict(row)

    def revoke_share(self, token: str) -> bool:
        """Revoke a single share link."""
        cur = self._get_conn().execute("DELETE FROM desktop_shares WHERE token = ?", (token,))
        self._get_conn().commit()
        return bool(cur.rowcount)

    def revoke_desktop_shares(self, desktop_id: str) -> int:
        """Revoke all share links for a desktop. Returns count revoked."""
        cur = self._get_conn().execute(
            "DELETE FROM desktop_shares WHERE desktop_id = ?", (desktop_id,)
        )
        self._get_conn().commit()
        return cur.rowcount

    def revoke_all_shares(self) -> int:
        """Admin: revoke all share links. Returns count revoked."""
        cur = self._get_conn().execute("DELETE FROM desktop_shares")
        self._get_conn().commit()
        return cur.rowcount

    def list_shares(self, desktop_id: str = "") -> list[dict]:
        """List active (non-expired) share links. Optionally filter by desktop."""
        now = time.time()
        if desktop_id:
            rows = self._get_conn().execute(
                "SELECT token, desktop_id, created_by, created_at, expires_at "
                "FROM desktop_shares WHERE desktop_id = ? AND expires_at > ? "
                "ORDER BY created_at DESC",
                (desktop_id, now),
            ).fetchall()
        else:
            rows = self._get_conn().execute(
                "SELECT token, desktop_id, created_by, created_at, expires_at "
                "FROM desktop_shares WHERE expires_at > ? "
                "ORDER BY created_at DESC",
                (now,),
            ).fetchall()
        return [dict(r) for r in rows]

    def cleanup_expired_shares(self) -> int:
        """Remove expired share links. Returns count removed."""
        cur = self._get_conn().execute(
            "DELETE FROM desktop_shares WHERE expires_at < ?", (time.time(),)
        )
        self._get_conn().commit()
        return cur.rowcount

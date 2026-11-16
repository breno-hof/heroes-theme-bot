"""Per-guild configuration, persisted in SQLite.

Each Discord server (guild) is a tenant with its own voice channel, bridge
token and allowed browser origins. Bridge tokens are generated here and only
their SHA-256 digest is stored; a token both authenticates a VTT client and
identifies which tenant it belongs to.
"""

import hashlib
import json
import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
    guild_id         INTEGER PRIMARY KEY,
    voice_channel_id INTEGER,
    token_hash       TEXT UNIQUE,
    allowed_origins  TEXT NOT NULL DEFAULT '[]'
) STRICT
"""


@dataclass(frozen=True, slots=True)
class Tenant:
    guild_id: int
    voice_channel_id: int | None
    has_token: bool
    allowed_origins: tuple[str, ...]

    def allows_origin(self, origin: str | None) -> bool:
        """An empty list allows any origin; otherwise the browser's Origin header must match exactly."""
        return not self.allowed_origins or origin in self.allowed_origins


def _hash_token(token: str) -> str:
    # Tokens are 256 bits of randomness, so a plain fast hash is sufficient (no salt or stretching needed).
    return hashlib.sha256(token.encode()).hexdigest()


def normalize_origin(value: str) -> str:
    """Validate a browser origin and return it as `scheme://host[:port]`. Raises ValueError."""
    parts = urlsplit(value.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"{value!r} is not an http(s) origin such as https://foundry.example.com")
    if parts.path not in ("", "/") or parts.query or parts.fragment or parts.username:
        raise ValueError(f"{value!r} must be an origin only, without path or query")
    return f"{parts.scheme}://{parts.netloc.lower()}"


class TenantStore:
    """Synchronous store; every operation is a single indexed statement on a local file."""

    def __init__(self, path: Path | str) -> None:
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, autocommit=True)
        self._db.row_factory = sqlite3.Row
        self._db.execute(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def get(self, guild_id: int) -> Tenant | None:
        row = self._db.execute("SELECT * FROM tenants WHERE guild_id = ?", (guild_id,)).fetchone()
        return _to_tenant(row) if row else None

    def find_by_token(self, token: str) -> Tenant | None:
        row = self._db.execute("SELECT * FROM tenants WHERE token_hash = ?", (_hash_token(token),)).fetchone()
        return _to_tenant(row) if row else None

    def with_voice_channel(self) -> list[Tenant]:
        rows = self._db.execute("SELECT * FROM tenants WHERE voice_channel_id IS NOT NULL").fetchall()
        return [_to_tenant(row) for row in rows]

    def set_voice_channel(self, guild_id: int, channel_id: int | None) -> None:
        self._upsert(guild_id, "voice_channel_id", channel_id)

    def set_allowed_origins(self, guild_id: int, origins: list[str]) -> None:
        self._upsert(guild_id, "allowed_origins", json.dumps(origins))

    def rotate_token(self, guild_id: int) -> str:
        """Generate a new bridge token, invalidating the previous one. The plaintext is returned once only."""
        token = secrets.token_urlsafe(32)
        self._upsert(guild_id, "token_hash", _hash_token(token))
        return token

    def delete(self, guild_id: int) -> None:
        self._db.execute("DELETE FROM tenants WHERE guild_id = ?", (guild_id,))

    def _upsert(self, guild_id: int, column: str, value: int | str | None) -> None:
        # `column` is always one of the literals above, never user input.
        self._db.execute(
            f"INSERT INTO tenants (guild_id, {column}) VALUES (?, ?) "  # noqa: S608
            f"ON CONFLICT (guild_id) DO UPDATE SET {column} = excluded.{column}",
            (guild_id, value),
        )


def _to_tenant(row: sqlite3.Row) -> Tenant:
    return Tenant(
        guild_id=row["guild_id"],
        voice_channel_id=row["voice_channel_id"],
        has_token=row["token_hash"] is not None,
        allowed_origins=tuple(json.loads(row["allowed_origins"])),
    )

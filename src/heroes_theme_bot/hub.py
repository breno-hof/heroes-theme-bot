"""WebSocket server that VTT clients connect to."""

import asyncio
import json
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from http import HTTPStatus
from typing import Any

import structlog
from pydantic import ValidationError
from websockets.asyncio.server import ServerConnection, broadcast, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from heroes_theme_bot import __version__
from heroes_theme_bot.config import Settings
from heroes_theme_bot.protocol import (
    PROTOCOL_VERSION,
    CloseCode,
    Error,
    ErrorCode,
    Hello,
    VoiceSpeaking,
    Welcome,
)
from heroes_theme_bot.tenants import Tenant, TenantStore

log = structlog.stdlib.get_logger(__name__)

# Clients only ever send a small hello frame.
MAX_FRAME_BYTES = 4096


class _Rejected(Exception):
    def __init__(self, code: ErrorCode, close_code: int, message: str) -> None:
        super().__init__(message)
        self.code: ErrorCode = code
        self.close_code = close_code
        self.message = message


class TriggerHub:
    """Authenticates VTT clients, resolves their tenant, and fans speaking events out per guild."""

    def __init__(self, settings: Settings, tenants: TenantStore) -> None:
        self._settings = settings
        self._tenants = tenants
        self._clients: dict[int, set[ServerConnection]] = {}
        # guild_id -> user_id -> last speaking=True event, replayed to clients that connect mid-sentence
        self._speaking: dict[int, dict[str, VoiceSpeaking]] = {}
        self.port = settings.ws_port

    def client_count(self, guild_id: int) -> int:
        return len(self._clients.get(guild_id, ()))

    @asynccontextmanager
    async def run(self) -> AsyncIterator[None]:
        async with serve(
            self._handle,
            self._settings.ws_host,
            self._settings.ws_port,
            process_request=self._process_request,
            max_size=MAX_FRAME_BYTES,
            server_header=None,
        ) as server:
            self.port = server.sockets[0].getsockname()[1]
            log.info("hub_listening", host=self._settings.ws_host, port=self.port)
            yield
        log.info("hub_stopped")

    def publish(self, event: VoiceSpeaking) -> None:
        """Send an event to the clients of the guild it belongs to, and to nobody else."""
        guild_id = int(event.guild_id)
        speaking = self._speaking.setdefault(guild_id, {})
        if event.speaking:
            speaking[event.user_id] = event
        else:
            speaking.pop(event.user_id, None)
        broadcast(self._clients.get(guild_id, ()), event.encode())

    def clear_speaking(self, guild_id: int) -> None:
        """Forget who was speaking, e.g. when the bot leaves the guild's voice channel."""
        self._speaking.pop(guild_id, None)

    async def drop_guild(self, guild_id: int) -> None:
        """Disconnect a guild's clients so they re-authenticate, e.g. after its token or origins change."""
        for connection in list(self._clients.get(guild_id, ())):
            with suppress(ConnectionClosed):
                await connection.close(CloseCode.UNAUTHORIZED, "credentials changed")

    def _process_request(self, connection: ServerConnection, request: Request) -> Response | None:
        if request.path == "/healthz":
            return Response(HTTPStatus.OK, "OK", Headers({"Content-Type": "text/plain"}), b"ok\n")
        return None

    async def _handle(self, connection: ServerConnection) -> None:
        session_id = secrets.token_urlsafe(12)
        logger = log.bind(session_id=session_id, remote=str(connection.remote_address))
        try:
            hello, tenant = await self._handshake(connection)
        except _Rejected as rejection:
            logger.warning("client_rejected", code=rejection.code, reason=rejection.message)
            with suppress(ConnectionClosed):
                await connection.send(Error(code=rejection.code, message=rejection.message).encode())
                await connection.close(rejection.close_code, rejection.code)
            return
        except ConnectionClosed:
            return

        guild_id = tenant.guild_id
        logger = logger.bind(
            guild_id=guild_id, client_kind=hello.client.kind, client_version=hello.client.version
        )
        clients = self._clients.setdefault(guild_id, set())
        try:
            await connection.send(Welcome(session_id=session_id, server_version=__version__).encode())
            for event in list(self._speaking.get(guild_id, {}).values()):
                await connection.send(event.encode())
            clients.add(connection)
            logger.info("client_connected", clients=len(clients))
            # The protocol has no further client frames; drain until the peer disconnects.
            async for _ in connection:
                pass
        except ConnectionClosed:
            pass
        finally:
            clients.discard(connection)
            logger.info("client_disconnected", clients=len(clients))

    async def _handshake(self, connection: ServerConnection) -> tuple[Hello, Tenant]:
        try:
            async with asyncio.timeout(self._settings.ws_handshake_timeout_s):
                raw = await connection.recv()
        except TimeoutError:
            raise _Rejected("handshake_timeout", CloseCode.HANDSHAKE_TIMEOUT, "no hello received") from None

        try:
            payload: Any = json.loads(raw)
        except ValueError:
            raise _Rejected("bad_message", CloseCode.BAD_MESSAGE, "hello is not valid JSON") from None
        if isinstance(payload, dict) and payload.get("v") != PROTOCOL_VERSION:
            raise _Rejected(
                "unsupported_version",
                CloseCode.UNSUPPORTED_VERSION,
                f"server speaks protocol v{PROTOCOL_VERSION}",
            )
        try:
            hello = Hello.model_validate(payload)
        except ValidationError:
            raise _Rejected("bad_message", CloseCode.BAD_MESSAGE, "malformed hello") from None

        # The token is looked up by its hash, so it both authenticates the client and selects the tenant.
        tenant = self._tenants.find_by_token(hello.token)
        if tenant is None:
            raise _Rejected("unauthorized", CloseCode.UNAUTHORIZED, "invalid token")
        origin = connection.request.headers.get("Origin") if connection.request else None
        if not tenant.allows_origin(origin):
            raise _Rejected("unauthorized", CloseCode.UNAUTHORIZED, "origin not allowed")
        return hello, tenant

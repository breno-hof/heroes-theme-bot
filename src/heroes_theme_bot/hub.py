"""WebSocket server that VTT clients connect to."""

import asyncio
import hmac
import json
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from http import HTTPStatus
from typing import Any, cast

import structlog
from pydantic import ValidationError
from websockets.asyncio.server import ServerConnection, broadcast, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response
from websockets.typing import Origin

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
    """Authenticates VTT clients and fans speaking events out to them."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._clients: set[ServerConnection] = set()
        # user_id -> last speaking=True event, replayed to clients that connect mid-sentence
        self._speaking: dict[str, VoiceSpeaking] = {}
        self.port = settings.ws_port

    @property
    def client_count(self) -> int:
        return len(self._clients)

    @asynccontextmanager
    async def run(self) -> AsyncIterator[None]:
        origins = self._settings.ws_allowed_origins
        async with serve(
            self._handle,
            self._settings.ws_host,
            self._settings.ws_port,
            process_request=self._process_request,
            origins=cast("list[Origin | None]", origins) if origins else None,
            max_size=MAX_FRAME_BYTES,
            server_header=None,
        ) as server:
            self.port = server.sockets[0].getsockname()[1]
            log.info("hub_listening", host=self._settings.ws_host, port=self.port)
            yield
        log.info("hub_stopped")

    def publish(self, event: VoiceSpeaking) -> None:
        if event.speaking:
            self._speaking[event.user_id] = event
        else:
            self._speaking.pop(event.user_id, None)
        broadcast(self._clients, event.encode())

    def _process_request(self, connection: ServerConnection, request: Request) -> Response | None:
        if request.path == "/healthz":
            return Response(HTTPStatus.OK, "OK", Headers({"Content-Type": "text/plain"}), b"ok\n")
        return None

    async def _handle(self, connection: ServerConnection) -> None:
        session_id = secrets.token_urlsafe(12)
        logger = log.bind(session_id=session_id, remote=str(connection.remote_address))
        try:
            hello = await self._handshake(connection)
        except _Rejected as rejection:
            logger.warning("client_rejected", code=rejection.code, reason=rejection.message)
            await connection.send(Error(code=rejection.code, message=rejection.message).encode())
            await connection.close(rejection.close_code, rejection.code)
            return
        except ConnectionClosed:
            return

        logger = logger.bind(client_kind=hello.client.kind, client_version=hello.client.version)
        try:
            await connection.send(Welcome(session_id=session_id, server_version=__version__).encode())
            for event in list(self._speaking.values()):
                await connection.send(event.encode())
            self._clients.add(connection)
            logger.info("client_connected", clients=self.client_count)
            # The protocol has no further client frames; drain until the peer disconnects.
            async for _ in connection:
                pass
        except ConnectionClosed:
            pass
        finally:
            self._clients.discard(connection)
            logger.info("client_disconnected", clients=self.client_count)

    async def _handshake(self, connection: ServerConnection) -> Hello:
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

        expected = self._settings.ws_token.get_secret_value().encode()
        if not hmac.compare_digest(hello.token.encode(), expected):
            raise _Rejected("unauthorized", CloseCode.UNAUTHORIZED, "invalid token")
        return hello

import json
import urllib.request
from collections.abc import AsyncIterator
from typing import Any

import pytest
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from heroes_theme_bot.config import Settings
from heroes_theme_bot.hub import TriggerHub
from heroes_theme_bot.protocol import VoiceSpeaking

TOKEN = "correct-horse-battery-staple"


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "discord_token": "unused",
        "guild_id": 1,
        "voice_channel_id": 2,
        "ws_token": TOKEN,
        "ws_host": "127.0.0.1",
        "ws_port": 0,
        "ws_handshake_timeout_s": 0.2,
    }
    return Settings(_env_file=None, **{**values, **overrides})


@pytest.fixture
async def hub() -> AsyncIterator[TriggerHub]:
    hub = TriggerHub(make_settings())
    async with hub.run():
        yield hub


def hello(token: str = TOKEN, v: int = 1) -> str:
    return json.dumps({"v": v, "type": "hello", "token": token, "client": {"kind": "test", "version": "0"}})


def speaking(user_id: str, value: bool) -> VoiceSpeaking:
    return VoiceSpeaking(
        guild_id="100000000000000010",
        channel_id="100000000000000011",
        user_id=user_id,
        speaking=value,
        at=1,
    )


async def recv(ws: ClientConnection) -> dict[str, Any]:
    message: dict[str, Any] = json.loads(await ws.recv())
    return message


async def expect_rejection(ws: ClientConnection, code: str, close_code: int) -> None:
    assert (await recv(ws))["code"] == code
    with pytest.raises(ConnectionClosed) as closed:
        await ws.recv()
    assert closed.value.rcvd is not None
    assert closed.value.rcvd.code == close_code


async def test_authenticated_client_receives_events(hub: TriggerHub) -> None:
    async with connect(f"ws://127.0.0.1:{hub.port}") as ws:
        await ws.send(hello())
        welcome = await recv(ws)
        assert welcome["type"] == "welcome"
        assert welcome["sessionId"]

        # Publish from the server side once the client is registered.
        while hub.client_count == 0:
            await ws.ping()
        hub.publish(speaking("100000000000000001", True))
        event = await recv(ws)
        assert event == {
            "v": 1,
            "type": "voice.speaking",
            "guildId": "100000000000000010",
            "channelId": "100000000000000011",
            "userId": "100000000000000001",
            "speaking": True,
            "at": 1,
        }


async def test_late_joiner_gets_current_speakers(hub: TriggerHub) -> None:
    hub.publish(speaking("100000000000000001", True))
    hub.publish(speaking("100000000000000002", True))
    hub.publish(speaking("100000000000000002", False))
    async with connect(f"ws://127.0.0.1:{hub.port}") as ws:
        await ws.send(hello())
        assert (await recv(ws))["type"] == "welcome"
        assert (await recv(ws))["userId"] == "100000000000000001"


async def test_wrong_token_is_rejected(hub: TriggerHub) -> None:
    async with connect(f"ws://127.0.0.1:{hub.port}") as ws:
        await ws.send(hello(token="wrong"))
        await expect_rejection(ws, "unauthorized", 4401)
    assert hub.client_count == 0


async def test_wrong_protocol_version_is_rejected(hub: TriggerHub) -> None:
    async with connect(f"ws://127.0.0.1:{hub.port}") as ws:
        await ws.send(hello(v=2))
        await expect_rejection(ws, "unsupported_version", 4426)


async def test_malformed_hello_is_rejected(hub: TriggerHub) -> None:
    async with connect(f"ws://127.0.0.1:{hub.port}") as ws:
        await ws.send("{not json")
        await expect_rejection(ws, "bad_message", 4400)


async def test_silent_client_times_out(hub: TriggerHub) -> None:
    async with connect(f"ws://127.0.0.1:{hub.port}") as ws:
        await expect_rejection(ws, "handshake_timeout", 4408)


async def test_unlisted_origin_is_refused() -> None:
    hub = TriggerHub(make_settings(ws_allowed_origins=["https://foundry.example.com"]))
    async with hub.run():
        with pytest.raises(InvalidStatus):
            await connect(f"ws://127.0.0.1:{hub.port}", origin="https://evil.example.com")  # type: ignore[arg-type]
        async with connect(f"ws://127.0.0.1:{hub.port}", origin="https://foundry.example.com") as ws:  # type: ignore[arg-type]
            await ws.send(hello())
            assert (await recv(ws))["type"] == "welcome"


async def test_healthz(hub: TriggerHub) -> None:
    import asyncio

    def fetch() -> tuple[int, bytes]:
        with urllib.request.urlopen(f"http://127.0.0.1:{hub.port}/healthz", timeout=2) as response:
            return response.status, response.read()

    assert await asyncio.to_thread(fetch) == (200, b"ok\n")

import asyncio
import json
import urllib.request
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from heroes_theme_bot.config import Settings
from heroes_theme_bot.hub import TriggerHub
from heroes_theme_bot.protocol import VoiceSpeaking
from heroes_theme_bot.tenants import TenantStore

GUILD_A = 100000000000000010
GUILD_B = 100000000000000020
ALICE = "100000000000000001"
BOB = "100000000000000002"


@dataclass
class Env:
    hub: TriggerHub
    tenants: TenantStore
    token_a: str
    token_b: str

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.hub.port}"


@pytest.fixture
async def env() -> AsyncIterator[Env]:
    settings = Settings(
        _env_file=None,
        discord_token="unused",
        ws_host="127.0.0.1",
        ws_port=0,
        ws_handshake_timeout_s=0.2,
    )
    tenants = TenantStore(":memory:")
    hub = TriggerHub(settings, tenants)
    async with hub.run():
        yield Env(hub, tenants, tenants.rotate_token(GUILD_A), tenants.rotate_token(GUILD_B))


def hello(token: str, v: int = 1) -> str:
    return json.dumps({"v": v, "type": "hello", "token": token, "client": {"kind": "test", "version": "0"}})


def speaking(guild_id: int, user_id: str, value: bool) -> VoiceSpeaking:
    return VoiceSpeaking(
        guild_id=str(guild_id), channel_id="100000000000000011", user_id=user_id, speaking=value, at=1
    )


async def recv(ws: ClientConnection) -> dict[str, Any]:
    message: dict[str, Any] = json.loads(await ws.recv())
    return message


async def join(ws: ClientConnection, env: Env, token: str, guild_id: int) -> None:
    """Complete the handshake and wait until the hub has registered the client."""
    before = env.hub.client_count(guild_id)
    await ws.send(hello(token))
    assert (await recv(ws))["type"] == "welcome"
    while env.hub.client_count(guild_id) == before:
        await ws.ping()


async def expect_rejection(ws: ClientConnection, code: str, close_code: int) -> None:
    assert (await recv(ws))["code"] == code
    await expect_close(ws, close_code)


async def expect_close(ws: ClientConnection, close_code: int) -> None:
    with pytest.raises(ConnectionClosed) as closed:
        await ws.recv()
    assert closed.value.rcvd is not None
    assert closed.value.rcvd.code == close_code


async def test_authenticated_client_receives_events(env: Env) -> None:
    async with connect(env.url) as ws:
        await join(ws, env, env.token_a, GUILD_A)
        env.hub.publish(speaking(GUILD_A, ALICE, True))
        assert await recv(ws) == {
            "v": 1,
            "type": "voice.speaking",
            "guildId": str(GUILD_A),
            "channelId": "100000000000000011",
            "userId": ALICE,
            "speaking": True,
            "at": 1,
        }


async def test_events_only_reach_clients_of_the_same_guild(env: Env) -> None:
    async with connect(env.url) as a, connect(env.url) as b:
        await join(a, env, env.token_a, GUILD_A)
        await join(b, env, env.token_b, GUILD_B)

        env.hub.publish(speaking(GUILD_A, ALICE, True))
        env.hub.publish(speaking(GUILD_B, BOB, True))

        assert (await recv(a))["userId"] == ALICE
        assert (await recv(b))["userId"] == BOB
        # Neither client has a second frame waiting: nothing leaked across tenants.
        for ws in (a, b):
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(ws.recv(), 0.1)


async def test_late_joiner_gets_current_speakers_of_its_guild_only(env: Env) -> None:
    env.hub.publish(speaking(GUILD_A, ALICE, True))
    env.hub.publish(speaking(GUILD_A, BOB, True))
    env.hub.publish(speaking(GUILD_A, BOB, False))
    env.hub.publish(speaking(GUILD_B, BOB, True))
    async with connect(env.url) as ws:
        await join(ws, env, env.token_a, GUILD_A)
        assert (await recv(ws))["userId"] == ALICE
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(ws.recv(), 0.1)


async def test_unknown_token_is_rejected(env: Env) -> None:
    async with connect(env.url) as ws:
        await ws.send(hello("wrong"))
        await expect_rejection(ws, "unauthorized", 4401)
    assert env.hub.client_count(GUILD_A) == 0


async def test_rotating_the_token_locks_out_the_old_one(env: Env) -> None:
    async with connect(env.url) as ws:
        await join(ws, env, env.token_a, GUILD_A)
        new_token = env.tenants.rotate_token(GUILD_A)
        await env.hub.drop_guild(GUILD_A)
        await expect_close(ws, 4401)

    async with connect(env.url) as ws:
        await ws.send(hello(env.token_a))
        await expect_rejection(ws, "unauthorized", 4401)
    async with connect(env.url) as ws:
        await join(ws, env, new_token, GUILD_A)


async def test_allowed_origins_are_enforced_per_guild(env: Env) -> None:
    env.tenants.set_allowed_origins(GUILD_A, ["https://foundry.example.com"])

    async with connect(env.url, origin="https://evil.example.com") as ws:  # type: ignore[arg-type]
        await ws.send(hello(env.token_a))
        await expect_rejection(ws, "unauthorized", 4401)
    async with connect(env.url, origin="https://foundry.example.com") as ws:  # type: ignore[arg-type]
        await join(ws, env, env.token_a, GUILD_A)
    # Guild B set no restriction, so the same origin is fine with its token.
    async with connect(env.url, origin="https://evil.example.com") as ws:  # type: ignore[arg-type]
        await join(ws, env, env.token_b, GUILD_B)


async def test_wrong_protocol_version_is_rejected(env: Env) -> None:
    async with connect(env.url) as ws:
        await ws.send(hello(env.token_a, v=2))
        await expect_rejection(ws, "unsupported_version", 4426)


async def test_malformed_hello_is_rejected(env: Env) -> None:
    async with connect(env.url) as ws:
        await ws.send("{not json")
        await expect_rejection(ws, "bad_message", 4400)


async def test_silent_client_times_out(env: Env) -> None:
    async with connect(env.url) as ws:
        await expect_rejection(ws, "handshake_timeout", 4408)


async def test_healthz(env: Env) -> None:
    def fetch() -> tuple[int, bytes]:
        with urllib.request.urlopen(f"http://127.0.0.1:{env.hub.port}/healthz", timeout=2) as response:
            return response.status, response.read()

    assert await asyncio.to_thread(fetch) == (200, b"ok\n")

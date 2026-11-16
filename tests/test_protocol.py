"""Contract test against the JSON Schema exported by heroes-theme-core.

Regenerate the fixture after changing the protocol in the core:
    node -e "import('@breno-hof/heroes-theme-core').then(m => \
        console.log(JSON.stringify(m.protocolJsonSchema(), null, 2)))" > tests/protocol.schema.json
"""

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from heroes_theme_bot.protocol import Error, Hello, VoiceSpeaking, Welcome, _Message

SCHEMA: dict[str, Any] = json.loads((Path(__file__).parent / "protocol.schema.json").read_text())

SERVER_MESSAGES: list[_Message] = [
    Welcome(session_id="abc", server_version="0.1.0"),
    VoiceSpeaking(
        guild_id="100000000000000010",
        channel_id="100000000000000011",
        user_id="100000000000000001",
        speaking=True,
        at=1_794_000_000_000,
    ),
    Error(code="unauthorized", message="invalid token"),
]


@pytest.mark.parametrize("message", SERVER_MESSAGES, ids=lambda m: type(m).__name__)
def test_server_messages_match_core_schema(message: _Message) -> None:
    Draft202012Validator(SCHEMA["server"]).validate(json.loads(message.encode()))


def test_hello_accepted_by_core_schema_is_accepted_here() -> None:
    hello = {"v": 1, "type": "hello", "token": "t", "client": {"kind": "foundry", "version": "0.1.0"}}
    Draft202012Validator(SCHEMA["client"]).validate(hello)
    assert Hello.model_validate(hello).client.kind == "foundry"

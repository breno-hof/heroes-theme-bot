"""Wire protocol models.

Mirrors `protocol.ts` in heroes-theme-core, which is the source of truth.
`tests/test_protocol.py` validates these models against the JSON Schema
exported by the core, so drift fails CI.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

PROTOCOL_VERSION: Literal[1] = 1

Snowflake = Annotated[str, Field(pattern=r"^\d{15,22}$")]


class CloseCode:
    BAD_MESSAGE = 4400
    UNAUTHORIZED = 4401
    HANDSHAKE_TIMEOUT = 4408
    UNSUPPORTED_VERSION = 4426


class _Message(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")

    v: Literal[1] = 1

    def encode(self) -> str:
        return self.model_dump_json(by_alias=True)


class ClientInfo(BaseModel):
    kind: str = Field(min_length=1, max_length=32)
    version: str = Field(min_length=1, max_length=32)


class Hello(_Message):
    type: Literal["hello"] = "hello"
    token: str = Field(min_length=1)
    client: ClientInfo


class Welcome(_Message):
    type: Literal["welcome"] = "welcome"
    session_id: str
    server_version: str


class VoiceSpeaking(_Message):
    type: Literal["voice.speaking"] = "voice.speaking"
    guild_id: Snowflake
    channel_id: Snowflake
    user_id: Snowflake
    speaking: bool
    at: int = Field(ge=0)


ErrorCode = Literal["unauthorized", "unsupported_version", "bad_message", "handshake_timeout"]


class Error(_Message):
    type: Literal["error"] = "error"
    code: ErrorCode
    message: str

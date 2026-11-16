"""Speaking detection for discord.py.

discord.py has no "user is speaking" event, and Discord's SPEAKING voice
opcode is only sent once per user (to announce their SSRC), not per utterance.
So speech is detected from the audio stream itself: a user is speaking while
RTP packets with their SSRC keep arriving, and silent once they stop.

Only the unencrypted 12-byte RTP header is read. Audio is never decrypted,
decoded or stored, which also makes this independent of DAVE end-to-end
encryption.
"""

import asyncio
import struct
from collections.abc import Callable
from typing import Any

import discord
import structlog
from discord.gateway import DiscordVoiceWebSocket
from discord.voice_state import VoiceConnectionState

log = structlog.stdlib.get_logger(__name__)

SpeakingCallback = Callable[[int, bool], None]

_RTP_HEADER = struct.Struct(">BBHII")
_OPUS_PAYLOAD_TYPE = 120


def parse_rtp_ssrc(packet: bytes) -> int | None:
    """Return the SSRC of an Opus RTP packet, or None for RTCP and anything else."""
    if len(packet) < _RTP_HEADER.size:
        return None
    first, second, _seq, _timestamp, ssrc = _RTP_HEADER.unpack_from(packet)
    if first >> 6 != 2 or second & 0x7F != _OPUS_PAYLOAD_TYPE:
        return None
    return int(ssrc)


class SpeakingTracker:
    """Turns "a packet arrived for SSRC x" into per-user speaking start/stop callbacks.

    Not thread-safe: every method must be called on the event loop.
    """

    def __init__(
        self,
        *,
        silence_timeout_s: float,
        on_change: SpeakingCallback,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._silence_timeout_s = silence_timeout_s
        self._on_change = on_change
        self._loop = loop
        self._user_by_ssrc: dict[int, int] = {}
        self._last_packet: dict[int, float] = {}
        self._timers: dict[int, asyncio.TimerHandle] = {}

    def map_ssrc(self, ssrc: int, user_id: int) -> None:
        previous = self._user_by_ssrc.get(ssrc)
        if previous == user_id:
            return
        if previous is not None and ssrc in self._timers:
            self._on_change(previous, False)
        self._user_by_ssrc[ssrc] = user_id
        # Audio can arrive before the SSRC announcement; report the speech now that we know whose it is.
        if ssrc in self._timers:
            self._on_change(user_id, True)

    def forget_user(self, user_id: int) -> None:
        for ssrc in [s for s, u in self._user_by_ssrc.items() if u == user_id]:
            self._stop(ssrc)
            del self._user_by_ssrc[ssrc]

    def packet_received(self, ssrc: int) -> None:
        self._last_packet[ssrc] = self._loop.time()
        if ssrc in self._timers:
            return
        self._timers[ssrc] = self._loop.call_later(self._silence_timeout_s, self._check_silence, ssrc)
        user_id = self._user_by_ssrc.get(ssrc)
        if user_id is not None:
            self._on_change(user_id, True)

    def clear(self) -> None:
        for ssrc in list(self._timers):
            self._stop(ssrc)
        self._user_by_ssrc.clear()

    def _check_silence(self, ssrc: int) -> None:
        remaining = self._last_packet[ssrc] + self._silence_timeout_s - self._loop.time()
        if remaining > 0:
            self._timers[ssrc] = self._loop.call_later(remaining, self._check_silence, ssrc)
        else:
            self._stop(ssrc)

    def _stop(self, ssrc: int) -> None:
        timer = self._timers.pop(ssrc, None)
        self._last_packet.pop(ssrc, None)
        if timer is None:
            return
        timer.cancel()
        user_id = self._user_by_ssrc.get(ssrc)
        if user_id is not None:
            self._on_change(user_id, False)


class SpeakingObserver:
    """Base for the discord.Client that owns a SpeakingVoiceClient."""

    silence_timeout_s: float

    def on_speaking_change(self, guild_id: int, channel_id: int, user_id: int, speaking: bool) -> None:
        raise NotImplementedError


class SpeakingVoiceClient(discord.VoiceClient):
    """VoiceClient that reports speaking changes to its client (a `SpeakingObserver`).

    Connect with `channel.connect(cls=SpeakingVoiceClient, self_deaf=False)`;
    a deafened bot receives no audio packets.
    """

    def __init__(self, client: discord.Client, channel: discord.abc.Connectable) -> None:
        if not isinstance(client, SpeakingObserver):
            raise TypeError("SpeakingVoiceClient requires a client deriving from SpeakingObserver")
        self._observer: SpeakingObserver = client
        self._tracker = SpeakingTracker(
            silence_timeout_s=client.silence_timeout_s,
            on_change=self._emit,
            loop=client.loop,
        )
        super().__init__(client, channel)

    def create_connection_state(self) -> VoiceConnectionState:
        state = VoiceConnectionState(self, hook=self._on_voice_ws_message)
        state.add_socket_listener(self._on_udp_packet)
        return state

    async def disconnect(self, *, force: bool = False) -> None:
        self._tracker.clear()
        await super().disconnect(force=force)

    def _emit(self, user_id: int, speaking: bool) -> None:
        if self.user is not None and user_id == self.user.id:
            return
        self._observer.on_speaking_change(self.guild.id, self.channel.id, user_id, speaking)

    async def _on_voice_ws_message(self, ws: DiscordVoiceWebSocket, msg: dict[str, Any]) -> None:
        op, data = msg.get("op"), msg.get("d") or {}
        try:
            if op == ws.SPEAKING:
                self._tracker.map_ssrc(int(data["ssrc"]), int(data["user_id"]))
            elif op == ws.CLIENT_DISCONNECT:
                self._tracker.forget_user(int(data["user_id"]))
        except KeyError, TypeError, ValueError:
            log.warning("unexpected_voice_ws_payload", op=op)

    def _on_udp_packet(self, packet: bytes) -> None:
        # Runs on discord.py's socket reader thread: do the cheap parse here, hop to the loop for state.
        ssrc = parse_rtp_ssrc(packet)
        if ssrc is not None:
            self.loop.call_soon_threadsafe(self._tracker.packet_received, ssrc)

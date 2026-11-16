import time

import discord
import structlog

from heroes_theme_bot.config import Settings
from heroes_theme_bot.hub import TriggerHub
from heroes_theme_bot.protocol import VoiceSpeaking
from heroes_theme_bot.voice_listener import SpeakingObserver, SpeakingVoiceClient

log = structlog.stdlib.get_logger(__name__)


class HeroesThemeBot(SpeakingObserver, discord.Client):
    """Joins the configured voice channel and publishes speaking changes to the hub."""

    def __init__(self, settings: Settings, hub: TriggerHub) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.voice_states = True
        super().__init__(intents=intents)
        self._settings = settings
        self._hub = hub
        self.silence_timeout_s = settings.silence_timeout_ms / 1000

    async def on_ready(self) -> None:
        # Also fires after a gateway reconnect, so this doubles as "rejoin if we lost the channel".
        log.info("discord_ready", user=str(self.user))
        await self._ensure_voice()

    async def _ensure_voice(self) -> None:
        guild = self.get_guild(self._settings.guild_id)
        if guild is None:
            log.error("guild_not_found", guild_id=self._settings.guild_id)
            return
        voice = guild.voice_client
        if isinstance(voice, discord.VoiceClient) and voice.is_connected():
            return
        channel = guild.get_channel(self._settings.voice_channel_id)
        if not isinstance(channel, discord.VoiceChannel | discord.StageChannel):
            log.error("voice_channel_not_found", channel_id=self._settings.voice_channel_id)
            return
        try:
            await channel.connect(cls=SpeakingVoiceClient, self_deaf=False, self_mute=True)
        except Exception:
            log.exception("voice_connect_failed", channel_id=channel.id)
            return
        log.info("voice_connected", guild_id=guild.id, channel_id=channel.id)

    def on_speaking_change(self, channel_id: int, user_id: int, speaking: bool) -> None:
        log.debug("speaking_change", user_id=user_id, speaking=speaking)
        self._hub.publish(
            VoiceSpeaking(
                guild_id=str(self._settings.guild_id),
                channel_id=str(channel_id),
                user_id=str(user_id),
                speaking=speaking,
                at=time.time_ns() // 1_000_000,
            )
        )

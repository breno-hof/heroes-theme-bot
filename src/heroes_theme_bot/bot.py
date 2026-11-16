import time

import discord
import structlog
from discord import app_commands

from heroes_theme_bot.commands import ThemeCommands
from heroes_theme_bot.config import Settings
from heroes_theme_bot.hub import TriggerHub
from heroes_theme_bot.protocol import VoiceSpeaking
from heroes_theme_bot.tenants import TenantStore
from heroes_theme_bot.voice_listener import SpeakingObserver, SpeakingVoiceClient

log = structlog.stdlib.get_logger(__name__)


class HeroesThemeBot(SpeakingObserver, discord.Client):
    """Listens in each configured guild's voice channel and publishes speaking changes to the hub."""

    def __init__(self, settings: Settings, hub: TriggerHub, tenants: TenantStore) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.voice_states = True
        super().__init__(intents=intents)
        self.hub = hub
        self.tenants = tenants
        self.silence_timeout_s = settings.silence_timeout_ms / 1000
        self.tree = app_commands.CommandTree(self)
        self.tree.add_command(ThemeCommands(self))

    async def setup_hook(self) -> None:
        await self.tree.sync()

    async def on_ready(self) -> None:
        # Also fires after a gateway reconnect, so this doubles as "rejoin if we lost a channel".
        log.info("discord_ready", user=str(self.user), guilds=len(self.guilds))
        for tenant in self.tenants.with_voice_channel():
            await self.ensure_voice(tenant.guild_id)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        # The bot was removed from the server: its token must stop working and its data is no longer needed.
        log.info("tenant_removed", guild_id=guild.id)
        self.tenants.delete(guild.id)
        self.hub.clear_speaking(guild.id)
        await self.hub.drop_guild(guild.id)

    def is_listening(self, guild_id: int) -> bool:
        guild = self.get_guild(guild_id)
        voice = guild.voice_client if guild else None
        return isinstance(voice, discord.VoiceClient) and voice.is_connected()

    async def ensure_voice(self, guild_id: int) -> bool:
        """Join (or move to) the guild's configured voice channel. Returns whether it is now listening."""
        tenant = self.tenants.get(guild_id)
        guild = self.get_guild(guild_id)
        if tenant is None or tenant.voice_channel_id is None or guild is None:
            return False
        channel = guild.get_channel(tenant.voice_channel_id)
        if not isinstance(channel, discord.VoiceChannel):
            log.warning("voice_channel_not_found", guild_id=guild_id, channel_id=tenant.voice_channel_id)
            return False
        voice = guild.voice_client
        try:
            if isinstance(voice, discord.VoiceClient) and voice.is_connected():
                if voice.channel.id != channel.id:
                    await voice.move_to(channel)
            else:
                if voice is not None:
                    await voice.disconnect(force=True)
                await channel.connect(cls=SpeakingVoiceClient, self_deaf=False, self_mute=True)
        except Exception:
            log.exception("voice_connect_failed", guild_id=guild_id, channel_id=channel.id)
            return False
        log.info("voice_connected", guild_id=guild_id, channel_id=channel.id)
        return True

    async def leave_voice(self, guild_id: int) -> None:
        guild = self.get_guild(guild_id)
        if guild is not None and guild.voice_client is not None:
            await guild.voice_client.disconnect(force=True)
        self.hub.clear_speaking(guild_id)

    def on_speaking_change(self, guild_id: int, channel_id: int, user_id: int, speaking: bool) -> None:
        log.debug("speaking_change", guild_id=guild_id, user_id=user_id, speaking=speaking)
        self.hub.publish(
            VoiceSpeaking(
                guild_id=str(guild_id),
                channel_id=str(channel_id),
                user_id=str(user_id),
                speaking=speaking,
                at=time.time_ns() // 1_000_000,
            )
        )

"""The `/heroestheme` slash command: per-guild configuration for server administrators."""

import re
from typing import TYPE_CHECKING

import discord
import structlog
from discord import app_commands

from heroes_theme_bot.tenants import normalize_origin

if TYPE_CHECKING:
    from heroes_theme_bot.bot import HeroesThemeBot

log = structlog.stdlib.get_logger(__name__)


class ThemeCommands(app_commands.Group):
    """Admin-only configuration.

    `default_permissions` hides the command from members without Administrator
    in Discord's UI. Server owners can override that default under Integrations,
    so `interaction_check` enforces the permission again on every invocation.
    Every reply is ephemeral: only the admin who ran the command sees it.
    """

    def __init__(self, bot: HeroesThemeBot) -> None:
        super().__init__(
            name="heroestheme",
            description="Configure Hero's Theme for this server",
            guild_only=True,
            default_permissions=discord.Permissions(administrator=True),
        )
        self._bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id is not None and interaction.permissions.administrator:
            return True
        await interaction.response.send_message(
            "Only server administrators can use this command.", ephemeral=True
        )
        return False

    @app_commands.command(description="Choose the voice channel the bot listens in, and join it")
    @app_commands.describe(channel="Voice channel to listen in")
    async def channel(self, interaction: discord.Interaction, channel: discord.VoiceChannel) -> None:
        guild_id = _guild_id(interaction)
        await interaction.response.defer(ephemeral=True)
        self._bot.tenants.set_voice_channel(guild_id, channel.id)
        log.info("tenant_channel_set", guild_id=guild_id, channel_id=channel.id)
        connected = await self._bot.ensure_voice(guild_id)
        tenant = self._bot.tenants.get(guild_id)
        lines = [
            f"Listening in {channel.mention}."
            if connected
            else f"Saved {channel.mention}, but I could not join it. "
            "Check my **View Channel** and **Connect** permissions."
        ]
        if tenant is None or not tenant.has_token:
            lines.append("Next, run `/heroestheme token` to get the bridge token for your VTT.")
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @app_commands.command(
        description="Generate a new bridge token for your VTT (invalidates the previous one)"
    )
    async def token(self, interaction: discord.Interaction) -> None:
        guild_id = _guild_id(interaction)
        token = self._bot.tenants.rotate_token(guild_id)
        log.info("tenant_token_rotated", guild_id=guild_id)
        await interaction.response.send_message(
            f"Bridge token for this server:\n```\n{token}\n```\n"
            "Paste it into the VTT module settings. It is shown **only this once**; "
            "running this command again replaces it and disconnects clients using the old one.",
            ephemeral=True,
        )
        await self._bot.hub.drop_guild(guild_id)

    @app_commands.command(description="Restrict which websites may connect with this server's token")
    @app_commands.describe(
        origins="Space-separated origins, e.g. https://foundry.example.com. Leave empty to allow any."
    )
    async def origins(self, interaction: discord.Interaction, origins: str | None = None) -> None:
        guild_id = _guild_id(interaction)
        try:
            parsed = sorted({normalize_origin(item) for item in re.split(r"[\s,]+", origins or "") if item})
        except ValueError as error:
            await interaction.response.send_message(f"Not saved: {error}", ephemeral=True)
            return
        self._bot.tenants.set_allowed_origins(guild_id, parsed)
        log.info("tenant_origins_set", guild_id=guild_id, origins=parsed)
        listing = "\n".join(f"- `{origin}`" for origin in parsed)
        await interaction.response.send_message(
            f"Allowed origins:\n{listing}" if parsed else "Any origin may connect with this server's token.",
            ephemeral=True,
        )
        await self._bot.hub.drop_guild(guild_id)

    @app_commands.command(description="Show this server's Hero's Theme configuration")
    async def status(self, interaction: discord.Interaction) -> None:
        guild_id = _guild_id(interaction)
        tenant = self._bot.tenants.get(guild_id)
        channel_id = tenant.voice_channel_id if tenant else None
        origins = tenant.allowed_origins if tenant else ()
        await interaction.response.send_message(
            "\n".join(
                [
                    f"**Voice channel:** {f'<#{channel_id}>' if channel_id else 'not set'}"
                    f" ({'connected' if self._bot.is_listening(guild_id) else 'not connected'})",
                    f"**Bridge token:** {'set' if tenant and tenant.has_token else 'not set'}",
                    f"**Allowed origins:** {', '.join(f'`{o}`' for o in origins) if origins else 'any'}",
                    f"**Connected VTT clients:** {self._bot.hub.client_count(guild_id)}",
                ]
            ),
            ephemeral=True,
        )

    @app_commands.command(description="Leave the voice channel and stop listening in this server")
    async def leave(self, interaction: discord.Interaction) -> None:
        guild_id = _guild_id(interaction)
        await interaction.response.defer(ephemeral=True)
        self._bot.tenants.set_voice_channel(guild_id, None)
        await self._bot.leave_voice(guild_id)
        log.info("tenant_channel_cleared", guild_id=guild_id)
        await interaction.followup.send(
            "Left the voice channel. The token and origins are kept.", ephemeral=True
        )


def _guild_id(interaction: discord.Interaction) -> int:
    if interaction.guild_id is None:  # unreachable: the group is guild-only and interaction_check enforces it
        raise app_commands.NoPrivateMessage
    return interaction.guild_id

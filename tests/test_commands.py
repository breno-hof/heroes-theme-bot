from types import SimpleNamespace
from typing import Any, cast

import discord

from heroes_theme_bot.bot import HeroesThemeBot
from heroes_theme_bot.commands import ThemeCommands
from heroes_theme_bot.config import Settings
from heroes_theme_bot.hub import TriggerHub
from heroes_theme_bot.tenants import TenantStore

GUILD = 100000000000000010


def make_bot() -> HeroesThemeBot:
    settings = Settings(_env_file=None, discord_token="unused", ws_port=0)
    tenants = TenantStore(":memory:")
    return HeroesThemeBot(settings, TriggerHub(settings, tenants), tenants)


class FakeResponse:
    def __init__(self) -> None:
        self.sent: list[tuple[str, bool]] = []

    async def send_message(self, content: str, *, ephemeral: bool = False) -> None:
        self.sent.append((content, ephemeral))


def fake_interaction(*, administrator: bool, guild_id: int | None = GUILD) -> Any:
    return SimpleNamespace(
        guild_id=guild_id,
        permissions=discord.Permissions(administrator=administrator),
        response=FakeResponse(),
    )


def group(bot: HeroesThemeBot) -> ThemeCommands:
    return cast(ThemeCommands, bot.tree.get_command("heroestheme"))


async def run(bot: HeroesThemeBot, name: str, interaction: Any, *args: Any) -> None:
    """Invoke a subcommand's callback directly, bypassing Discord."""
    callback: Any = getattr(group(bot), name).callback
    await callback(group(bot), interaction, *args)


async def test_command_is_registered_as_admin_only_and_guild_only() -> None:
    bot = make_bot()
    payload = group(bot).to_dict(bot.tree)

    assert payload["default_member_permissions"] == discord.Permissions(administrator=True).value
    assert payload["dm_permission"] is False
    assert sorted(option["name"] for option in payload["options"]) == [
        "channel",
        "leave",
        "origins",
        "status",
        "token",
    ]
    # Discord rejects the whole sync if any description exceeds 100 characters.
    for option in payload["options"]:
        assert len(option["description"]) <= 100
        for parameter in option.get("options", []):
            assert len(parameter["description"]) <= 100


async def test_non_admin_is_refused_even_if_discord_shows_the_command() -> None:
    interaction = fake_interaction(administrator=False)
    assert await group(make_bot()).interaction_check(interaction) is False
    assert interaction.response.sent == [("Only server administrators can use this command.", True)]


async def test_admin_passes_the_check() -> None:
    interaction = fake_interaction(administrator=True)
    assert await group(make_bot()).interaction_check(interaction) is True
    assert interaction.response.sent == []


async def test_token_command_replies_privately_and_stores_a_working_token() -> None:
    bot = make_bot()
    interaction = fake_interaction(administrator=True)
    await run(bot, "token", interaction)

    [(content, ephemeral)] = interaction.response.sent
    assert ephemeral is True
    token = content.split("```")[1].strip()
    tenant = bot.tenants.find_by_token(token)
    assert tenant is not None
    assert tenant.guild_id == GUILD


async def test_origins_command_validates_and_stores() -> None:
    bot = make_bot()
    bad = fake_interaction(administrator=True)
    await run(bot, "origins", bad, "not-an-origin")
    assert bad.response.sent[0][0].startswith("Not saved")
    assert bot.tenants.get(GUILD) is None

    good = fake_interaction(administrator=True)
    await run(bot, "origins", good, "https://B.example.com, https://a.example.com/")
    tenant = bot.tenants.get(GUILD)
    assert tenant is not None
    assert tenant.allowed_origins == ("https://a.example.com", "https://b.example.com")

    cleared = fake_interaction(administrator=True)
    await run(bot, "origins", cleared, None)
    tenant = bot.tenants.get(GUILD)
    assert tenant is not None
    assert tenant.allowed_origins == ()

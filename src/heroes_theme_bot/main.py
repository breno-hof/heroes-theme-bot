import asyncio
import signal
import sys

import discord
import structlog
from pydantic import ValidationError

from heroes_theme_bot import __version__
from heroes_theme_bot.bot import HeroesThemeBot
from heroes_theme_bot.config import Settings
from heroes_theme_bot.hub import TriggerHub
from heroes_theme_bot.logging import configure_logging

log = structlog.stdlib.get_logger(__name__)


async def run(settings: Settings) -> None:
    hub = TriggerHub(settings)
    bot = HeroesThemeBot(settings, hub)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # `docker stop` sends SIGTERM; close the Discord session cleanly instead of being killed.
        loop.add_signal_handler(sig, lambda: loop.create_task(bot.close()))

    async with hub.run(), bot:
        log.info("starting", version=__version__)
        await bot.start(settings.discord_token.get_secret_value())
    log.info("stopped")


def main() -> None:
    try:
        settings = Settings()
    except ValidationError as error:
        # Report which variables are wrong without echoing their values (they may be secrets).
        problems = "; ".join(
            f"HT_{'.'.join(str(part) for part in issue['loc']).upper()}: {issue['msg']}"
            for issue in error.errors()
        )
        sys.exit(f"Invalid configuration: {problems}")
    configure_logging(settings)
    try:
        asyncio.run(run(settings))
    except (discord.LoginFailure, discord.PrivilegedIntentsRequired) as error:
        log.error("discord_login_failed", reason=str(error))
        sys.exit(1)


if __name__ == "__main__":
    main()

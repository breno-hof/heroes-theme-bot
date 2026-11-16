# heroes-theme-bot

Discord voice bridge of **Hero's Theme**. The bot sits in a voice channel, works out who is speaking, and pushes `voice.speaking` events over an authenticated WebSocket to VTT clients such as [heroes-theme-foundry](https://github.com/breno-hof/heroes-theme-foundry). The clients decide what to play using [heroes-theme-core](https://github.com/breno-hof/heroes-theme-core).

## How speaking is detected

discord.py has no speaking event, and Discord only announces a user's audio stream ID (SSRC) once rather than per utterance. The bot therefore watches the incoming audio packets: a user is speaking while RTP packets with their SSRC keep arriving, and silent `HT_SILENCE_TIMEOUT_MS` after the last one. Only the unencrypted packet header is read; audio is never decrypted, decoded or stored. See `src/heroes_theme_bot/voice_listener.py`.

Debounce, hold and cooldown are deliberately not applied here. They live in the core's `ThemeStateManager`, which runs in the VTT client.

## Run

Create a Discord application with a bot user and invite it with the **Connect** and **View Channel** permissions. No privileged intents are needed.

```sh
cp .env.example .env   # fill in the token, IDs and a WebSocket secret
docker compose up -d
```

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `HT_DISCORD_TOKEN` | yes | | Bot token |
| `HT_GUILD_ID` | yes | | Guild to operate in |
| `HT_VOICE_CHANNEL_ID` | yes | | Voice channel joined on startup |
| `HT_WS_TOKEN` | yes | | Shared secret for VTT clients, at least 16 characters |
| `HT_WS_HOST` / `HT_WS_PORT` | no | `0.0.0.0` / `8765` | WebSocket listen address |
| `HT_WS_ALLOWED_ORIGINS` | no | any | JSON list of allowed browser origins |
| `HT_SILENCE_TIMEOUT_MS` | no | `300` | Gap without audio that counts as "stopped speaking" |
| `HT_LOG_LEVEL` / `HT_LOG_FORMAT` | no | `INFO` / `json` | Logging |

`GET /healthz` on the WebSocket port returns `200 ok`.

The server speaks plain `ws://`. A Foundry world served over HTTPS can only connect to `wss://`, so put the bot behind a TLS-terminating reverse proxy (Caddy, nginx, Traefik) unless both run on localhost.

## Develop

```sh
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

`tests/protocol.schema.json` is exported from the core and keeps the Pydantic models in `protocol.py` honest; regenerate it when the protocol changes (command in `tests/test_protocol.py`).

To release, bump `version` in `pyproject.toml`, merge, then push a matching tag (`v0.1.0`). CI publishes `ghcr.io/breno-hof/heroes-theme-bot` with the version tags; every merge to `main` publishes `edge`.

## 📄 License
This project is open-source under the **AGPL-3.0** license — see the [LICENSE](LICENSE) file for details. You are free to use and modify it, provided that any derivative work remains open-source and credits the original author.

Copyright (c) 2026 breno-hof. Licensed under the AGPL-3.0 License.

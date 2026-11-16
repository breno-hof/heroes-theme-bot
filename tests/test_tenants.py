from pathlib import Path

import pytest

from heroes_theme_bot.tenants import TenantStore, normalize_origin

GUILD_A = 100000000000000010
GUILD_B = 100000000000000020


@pytest.fixture
def store() -> TenantStore:
    return TenantStore(":memory:")


def test_unknown_guild_has_no_tenant(store: TenantStore) -> None:
    assert store.get(GUILD_A) is None
    assert store.find_by_token("anything") is None


def test_settings_are_independent_per_guild(store: TenantStore) -> None:
    store.set_voice_channel(GUILD_A, 111)
    store.set_allowed_origins(GUILD_A, ["https://a.example.com"])
    store.set_voice_channel(GUILD_B, 222)

    a, b = store.get(GUILD_A), store.get(GUILD_B)
    assert a is not None
    assert b is not None
    assert (a.voice_channel_id, a.allowed_origins) == (111, ("https://a.example.com",))
    assert (b.voice_channel_id, b.allowed_origins) == (222, ())
    assert {t.guild_id for t in store.with_voice_channel()} == {GUILD_A, GUILD_B}

    store.set_voice_channel(GUILD_A, None)
    assert [t.guild_id for t in store.with_voice_channel()] == [GUILD_B]


def test_token_identifies_its_guild_and_rotation_invalidates_the_old_one(store: TenantStore) -> None:
    token_a = store.rotate_token(GUILD_A)
    token_b = store.rotate_token(GUILD_B)
    assert store.find_by_token(token_a).guild_id == GUILD_A  # type: ignore[union-attr]
    assert store.find_by_token(token_b).guild_id == GUILD_B  # type: ignore[union-attr]

    rotated = store.rotate_token(GUILD_A)
    assert store.find_by_token(token_a) is None
    assert store.find_by_token(rotated).guild_id == GUILD_A  # type: ignore[union-attr]


def test_token_is_not_stored_in_plaintext(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "tenants.db"
    store = TenantStore(path)
    token = store.rotate_token(GUILD_A)
    store.close()
    assert token.encode() not in path.read_bytes()


def test_data_survives_reopening(tmp_path: Path) -> None:
    path = tmp_path / "tenants.db"
    first = TenantStore(path)
    first.set_voice_channel(GUILD_A, 111)
    token = first.rotate_token(GUILD_A)
    first.close()

    tenant = TenantStore(path).find_by_token(token)
    assert tenant is not None
    assert tenant.voice_channel_id == 111


def test_delete_removes_everything(store: TenantStore) -> None:
    token = store.rotate_token(GUILD_A)
    store.delete(GUILD_A)
    assert store.get(GUILD_A) is None
    assert store.find_by_token(token) is None


def test_allows_origin(store: TenantStore) -> None:
    store.set_voice_channel(GUILD_A, 111)
    open_tenant = store.get(GUILD_A)
    assert open_tenant is not None
    assert open_tenant.allows_origin(None)
    assert open_tenant.allows_origin("https://anything.example.com")

    store.set_allowed_origins(GUILD_A, ["https://foundry.example.com"])
    restricted = store.get(GUILD_A)
    assert restricted is not None
    assert restricted.allows_origin("https://foundry.example.com")
    assert not restricted.allows_origin("https://evil.example.com")
    assert not restricted.allows_origin(None)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://foundry.example.com", "https://foundry.example.com"),
        ("https://Foundry.Example.com/", "https://foundry.example.com"),
        (" http://localhost:30000 ", "http://localhost:30000"),
    ],
)
def test_normalize_origin(raw: str, expected: str) -> None:
    assert normalize_origin(raw) == expected


@pytest.mark.parametrize(
    "raw", ["foundry.example.com", "ftp://x.example.com", "https://x.example.com/game", "https://", "*"]
)
def test_normalize_origin_rejects(raw: str) -> None:
    with pytest.raises(ValueError, match="origin"):
        normalize_origin(raw)

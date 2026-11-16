import asyncio
import struct

from heroes_theme_bot.voice_listener import SpeakingTracker, parse_rtp_ssrc

TIMEOUT = 0.05


def rtp(ssrc: int, payload_type: int = 120) -> bytes:
    return struct.pack(">BBHII", 0x80, payload_type, 1, 960, ssrc) + b"\x00" * 20


def test_parse_rtp_ssrc_reads_opus_packets() -> None:
    assert parse_rtp_ssrc(rtp(4242)) == 4242
    assert parse_rtp_ssrc(rtp(4242, payload_type=120 | 0x80)) == 4242  # marker bit set


def test_parse_rtp_ssrc_ignores_everything_else() -> None:
    assert parse_rtp_ssrc(b"\x80\x78") is None  # truncated
    assert parse_rtp_ssrc(rtp(4242, payload_type=200)) is None  # RTCP sender report
    assert parse_rtp_ssrc(b"\x00" + rtp(4242)[1:]) is None  # wrong RTP version


def make_tracker() -> tuple[SpeakingTracker, list[tuple[int, bool]]]:
    events: list[tuple[int, bool]] = []
    tracker = SpeakingTracker(
        silence_timeout_s=TIMEOUT,
        on_change=lambda user_id, speaking: events.append((user_id, speaking)),
        loop=asyncio.get_running_loop(),
    )
    return tracker, events


async def test_start_on_first_packet_and_stop_after_silence() -> None:
    tracker, events = make_tracker()
    tracker.map_ssrc(1, 100)
    tracker.packet_received(1)
    tracker.packet_received(1)
    assert events == [(100, True)]
    await asyncio.sleep(TIMEOUT * 3)
    assert events == [(100, True), (100, False)]


async def test_continuous_packets_keep_the_user_speaking() -> None:
    tracker, events = make_tracker()
    tracker.map_ssrc(1, 100)
    for _ in range(8):
        tracker.packet_received(1)
        await asyncio.sleep(TIMEOUT / 2)
    assert events == [(100, True)]


async def test_audio_before_ssrc_announcement_is_reported_once_mapped() -> None:
    tracker, events = make_tracker()
    tracker.packet_received(1)
    assert events == []
    tracker.map_ssrc(1, 100)
    assert events == [(100, True)]


async def test_forgetting_a_speaking_user_reports_stop() -> None:
    tracker, events = make_tracker()
    tracker.map_ssrc(1, 100)
    tracker.packet_received(1)
    tracker.forget_user(100)
    assert events == [(100, True), (100, False)]
    await asyncio.sleep(TIMEOUT * 2)
    assert len(events) == 2


async def test_clear_stops_everyone() -> None:
    tracker, events = make_tracker()
    tracker.map_ssrc(1, 100)
    tracker.map_ssrc(2, 200)
    tracker.packet_received(1)
    tracker.packet_received(2)
    tracker.clear()
    assert sorted(events) == [(100, False), (100, True), (200, False), (200, True)]

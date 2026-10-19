"""
Phase 4.2: server-side LiveKit recording, consent enforcement and the
hand-off of the finalized recording to the existing pipeline.

LiveKit is faked everywhere: the RTC SDK through an injected rtc module, the
server API through monkeypatched route helpers. Storage and the database are
the isolated test locations set up by conftest.py.
"""
from __future__ import annotations

import asyncio
import json
import time
import wave
from pathlib import Path
from typing import Dict, List

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.api import live_routes
from app.api.main import app
from app.core import config, db
from app.core.storage import MeetingStorage
from app.services.live import recorder as recorder_mod
from app.services.live.recorder import (
    LiveKitRecorderRoom,
    RecordingManager,
    RecordingSession,
    finalize_and_persist,
    recover_interrupted_segments,
    segment_dir_for,
    set_recording_manager,
)
from app.services.live.recording_audio import (
    RECORDING_FILENAME,
    TrackWriter,
    attribute_utterances,
    build_meeting_recording,
    finalize_segment,
    normalize_frame,
    wav_info,
)

SR = 16000
FRAME = SR // 100  # 10 ms


def tone(freq: float, n: int = FRAME, amp: int = 8000, phase: int = 0) -> bytes:
    t = (np.arange(n) + phase) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.int16).tobytes()


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def dominant_freq(samples: np.ndarray) -> float:
    spectrum = np.abs(np.fft.rfft(samples.astype(np.float64)))
    return float(np.fft.rfftfreq(len(samples), 1 / SR)[int(np.argmax(spectrum))])


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def make_session(tmp_path, consents: Dict[str, bool], clock=None, events=None) -> RecordingSession:
    return RecordingSession(
        meeting_id="m1",
        segment_id="seg1",
        segment_dir=tmp_path / "seg",
        consent_lookup=lambda identity: consents.get(identity, False),
        on_event=(lambda e, d: events.append((e, d))) if events is not None else None,
        clock=clock or FakeClock(),
    )


# ---------------------------------------------------------------------------
# Audio primitives
# ---------------------------------------------------------------------------

def test_normalize_frame_downmixes_and_resamples():
    left = (np.ones(480) * 1000).astype(np.int16)
    right = (np.ones(480) * 3000).astype(np.int16)
    stereo_48k = np.column_stack([left, right]).reshape(-1).tobytes()
    out = normalize_frame(stereo_48k, 48000, 2, 16000)
    assert out.dtype == np.int16
    assert len(out) == 160
    assert np.all(out == 2000)


def test_track_writer_pads_gaps_on_the_shared_clock(tmp_path):
    writer = TrackWriter(tmp_path / "t.pcm", track_sid="TR1", identity="a", name="A")
    frame = np.ones(FRAME, dtype=np.int16)
    writer.write(frame, 1.01)        # first frame ends at 1.01 s -> starts at 1.00 s
    writer.write(frame, 1.02)
    writer.write(frame, 2.03)        # ~1 s mute: padded with silence
    writer.close()
    assert writer.start_offset == SR
    assert writer.padded_samples == pytest.approx(SR, abs=FRAME)
    assert writer.samples == writer.padded_samples + 3 * FRAME


# ---------------------------------------------------------------------------
# Consent enforcement inside the session
# ---------------------------------------------------------------------------

def test_arm_refuses_missing_or_incomplete_consent(tmp_path):
    session = make_session(tmp_path, {"alice": True})
    assert session.arm([("alice", "Alice"), ("bob", "Bob")]) == "consent_missing"
    assert not session.capturing
    assert session.audio_frame("TR_A", "alice", tone(440), SR, 1) is False

    empty = make_session(tmp_path / "e", {})
    assert empty.arm([]) == "no_participants"


def test_two_participants_tracks_are_captured_and_time_aligned(tmp_path):
    clock = FakeClock()
    session = make_session(tmp_path, {"alice": True, "bob": True}, clock=clock)
    assert session.arm([("alice", "Alice"), ("bob", "Bob")]) is None

    # Alice talks 0-2 s at 440 Hz; Bob talks 1-3 s at 1000 Hz, concurrently.
    for i in range(300):
        clock.t = 100.0 + (i + 1) * 0.01
        if i < 200:
            assert session.audio_frame("TR_A", "alice", tone(440, phase=i * FRAME), SR, 1)
        if i >= 100:
            assert session.audio_frame("TR_B", "bob", tone(1000, phase=i * FRAME), SR, 1)
    assert session.state == "recording"
    session.halt("host_stopped")
    session.close_writers()

    result = finalize_segment(session.segment_dir)
    assert not result["empty"]
    tracks = {t["identity"]: t for t in result["tracks"]}
    assert set(tracks) == {"alice", "bob"}
    assert tracks["alice"]["start_offset_seconds"] == pytest.approx(0.0, abs=0.02)
    assert tracks["bob"]["start_offset_seconds"] == pytest.approx(1.0, abs=0.02)

    alice = read_wav(session.segment_dir / tracks["alice"]["file"])
    bob = read_wav(session.segment_dir / tracks["bob"]["file"])
    assert dominant_freq(alice) == pytest.approx(440, abs=5)
    assert dominant_freq(bob) == pytest.approx(1000, abs=5)

    mix = read_wav(session.segment_dir / "segment.wav")
    assert len(mix) == pytest.approx(3 * SR, abs=FRAME)
    assert dominant_freq(mix[: SR // 2]) == pytest.approx(440, abs=5)          # only Alice
    assert dominant_freq(mix[int(2.5 * SR):]) == pytest.approx(1000, abs=5)    # only Bob
    overlap = np.abs(np.fft.rfft(mix[int(1.2 * SR): int(1.8 * SR)].astype(float)))
    freqs = np.fft.rfftfreq(int(0.6 * SR), 1 / SR)
    top_two = sorted(freqs[np.argsort(overlap)[-2:]])
    assert top_two == pytest.approx([440, 1000], abs=5)                        # both, mixed not concatenated


def test_unconsented_participant_joining_stops_capture(tmp_path):
    events: List = []
    session = make_session(tmp_path, {"alice": True}, events=events)
    session.arm([("alice", "Alice")])
    assert session.audio_frame("TR_A", "alice", tone(440), SR, 1)

    session.participant_joined("mallory", "Mallory")
    assert not session.capturing
    assert session.stop_reason == "unconsented_participant_joined"
    assert session.stop_identity == "mallory"
    assert session.audio_frame("TR_A", "alice", tone(440), SR, 1) is False
    assert ("capture_halted", {"reason": "unconsented_participant_joined", "identity": "mallory"}) in events


def test_consented_late_joiner_is_captured(tmp_path):
    consents = {"alice": True, "bob": True}
    session = make_session(tmp_path, consents)
    session.arm([("alice", "Alice")])
    session.participant_joined("bob", "Bob")
    assert session.capturing
    assert session.should_subscribe("bob")
    assert session.audio_frame("TR_B", "bob", tone(1000), SR, 1)


def test_revocation_stops_capture_and_regrant_does_not_resume(tmp_path):
    consents = {"alice": True, "bob": True}
    session = make_session(tmp_path, consents)
    session.arm([("alice", "Alice"), ("bob", "Bob")])
    session.audio_frame("TR_B", "bob", tone(1000), SR, 1)

    session.consent_changed("bob", False)
    assert session.stop_reason == "consent_revoked"
    assert not session.capturing

    session.consent_changed("bob", True)
    assert not session.capturing
    assert session.audio_frame("TR_A", "alice", tone(440), SR, 1) is False


def test_audio_from_unconsented_identity_is_never_written(tmp_path):
    session = make_session(tmp_path, {"alice": True})
    session.arm([("alice", "Alice")])
    # Unknown identity (not in the room): dropped without being stored.
    assert session.audio_frame("TR_X", "ghost", tone(300), SR, 1) is False
    assert session.capturing
    # Present but unconsented (bypassed the join check): capture halts.
    session.participants["intruder"] = "Intruder"
    assert session.audio_frame("TR_X", "intruder", tone(300), SR, 1) is False
    assert session.stop_reason == "unconsented_audio"
    assert "TR_X" not in session.writers


def test_late_frame_after_departure_does_not_stop_others(tmp_path):
    session = make_session(tmp_path, {"alice": True, "bob": True})
    session.arm([("alice", "Alice"), ("bob", "Bob")])
    session.audio_frame("TR_B", "bob", tone(1000), SR, 1)
    session.participant_left("bob")
    assert session.audio_frame("TR_B", "bob", tone(1000), SR, 1) is False   # buffered straggler
    assert session.capturing
    assert session.audio_frame("TR_A", "alice", tone(440), SR, 1) is True


def test_resubscribed_track_with_same_sid_gets_a_new_file(tmp_path):
    clock = FakeClock()
    session = make_session(tmp_path, {"alice": True}, clock=clock)
    session.arm([("alice", "Alice")])
    for i in range(10):
        clock.t += 0.01
        session.audio_frame("TR_A", "alice", tone(440, phase=i * FRAME), SR, 1)
    session.track_ended("TR_A")                       # connection dropped
    clock.t += 1.0
    for i in range(10):                               # resumed with the same track sid
        clock.t += 0.01
        assert session.audio_frame("TR_A", "alice", tone(440, phase=i * FRAME), SR, 1)
    session.halt("host_stopped")
    session.close_writers()
    result = finalize_segment(session.segment_dir)
    files = sorted(t["file"] for t in result["tracks"])
    assert files == ["track_alice_TR_A.wav", "track_alice_TR_A_2.wav"]
    second = next(t for t in result["tracks"] if t["file"].endswith("_2.wav"))
    assert second["start_offset_seconds"] == pytest.approx(1.1, abs=0.02)
    assert sum(t["duration_seconds"] for t in result["tracks"]) == pytest.approx(0.2, abs=0.01)


def test_departure_keeps_recording_until_room_is_empty(tmp_path):
    session = make_session(tmp_path, {"alice": True, "bob": True})
    session.arm([("alice", "Alice"), ("bob", "Bob")])
    session.audio_frame("TR_B", "bob", tone(1000), SR, 1)
    session.participant_left("bob")
    assert session.capturing
    assert session.writers["TR_B"].closed
    session.participant_left("alice")
    assert session.stop_reason == "no_participants"


def test_empty_recording_is_reported_empty(tmp_path):
    session = make_session(tmp_path, {"alice": True})
    session.arm([("alice", "Alice")])
    session.halt("host_stopped")
    session.close_writers()
    result = finalize_segment(session.segment_dir)
    assert result["empty"] is True
    assert result["segment_file"] is None
    assert not (session.segment_dir / "segment.wav").exists()


def test_partial_pcm_from_a_crash_is_finalized(tmp_path):
    session = make_session(tmp_path, {"alice": True})
    session.arm([("alice", "Alice")])
    for _ in range(20):
        session.audio_frame("TR_A", "alice", tone(440), SR, 1)
    # Simulate a kill mid-write: the OS drops the handle (no TrackWriter.close,
    # no tracks.json rewrite) and half a sample is left at the end.
    session.writers["TR_A"]._fh.close()
    with open(session.writers["TR_A"].path, "ab") as fh:
        fh.write(b"\x01")
    result = finalize_segment(session.segment_dir)
    assert not result["empty"]
    assert result["tracks"][0]["duration_seconds"] == pytest.approx(0.2, abs=0.01)


def test_segments_are_concatenated_chronologically_with_manifest(tmp_path):
    segments = []
    for seq, freq in ((2, 1000), (1, 440)):
        seg_dir = tmp_path / "segments" / f"{seq:03d}"
        session = RecordingSession(
            meeting_id="m1", segment_id=f"s{seq}", segment_dir=seg_dir,
            consent_lookup=lambda _i: True,
        )
        session.arm([("alice", "Alice")])
        for i in range(100):
            session.audio_frame("TR", "alice", tone(freq, phase=i * FRAME), SR, 1)
        session.halt("host_stopped")
        session.close_writers()
        res = finalize_segment(seg_dir)
        segments.append({"segment_id": f"s{seq}", "seq": seq, "dir": seg_dir, **res})

    manifest = build_meeting_recording(tmp_path, segments, "m1")
    audio = read_wav(tmp_path / RECORDING_FILENAME)
    assert [s["seq"] for s in manifest["segments"]] == [1, 2]
    assert dominant_freq(audio[: SR // 2]) == pytest.approx(440, abs=5)
    assert dominant_freq(audio[-SR // 2:]) == pytest.approx(1000, abs=5)
    assert manifest["segments"][1]["offset_seconds"] == pytest.approx(2.0, abs=0.05)
    assert not list(tmp_path.glob("tmp_*"))


def test_speaker_attribution_uses_participant_tracks(tmp_path):
    clock = FakeClock()
    seg_dir = tmp_path / "segments" / "001"
    session = RecordingSession(
        meeting_id="m1", segment_id="s1", segment_dir=seg_dir,
        consent_lookup=lambda _i: True, clock=clock,
    )
    session.arm([("alice", "Alice"), ("bob", "Bob")])
    silence = bytes(FRAME * 2)
    for i in range(200):
        clock.t = 100.0 + (i + 1) * 0.01
        session.audio_frame("TA", "alice", tone(440) if i < 100 else silence, SR, 1)
        session.audio_frame("TB", "bob", silence if i < 100 else tone(1000), SR, 1)
    session.halt("host_stopped")
    session.close_writers()
    res = finalize_segment(seg_dir)
    manifest = build_meeting_recording(tmp_path, [{"segment_id": "s1", "seq": 1, "dir": seg_dir, **res}], "m1")
    out = attribute_utterances(tmp_path, manifest, [
        {"utt_id": "u1", "start": 0.1, "end": 0.9, "speaker_id": "SPEAKER_00"},
        {"utt_id": "u2", "start": 1.1, "end": 1.9, "speaker_id": "SPEAKER_01"},
    ])
    assert [u["participant_identity"] for u in out] == ["alice", "bob"]


# ---------------------------------------------------------------------------
# LiveKit RTC adapter, driven by a fake rtc module
# ---------------------------------------------------------------------------

class _Kind:
    KIND_AUDIO = "audio"
    KIND_VIDEO = "video"


class FakeTrack:
    def __init__(self, sid, kind):
        self.sid = sid
        self.kind = kind
        self.queue: asyncio.Queue = asyncio.Queue()


class FakePublication:
    def __init__(self, room, participant, sid, kind):
        self.room, self.participant = room, participant
        self.sid, self.kind = sid, kind
        self.track = FakeTrack(sid, kind)
        self.subscribed = False

    def set_subscribed(self, value):
        self.room.subscribe_calls.append((self.participant.identity, self.sid, value))
        if value and not self.subscribed:
            self.subscribed = True
            self.room.emit("track_subscribed", self.track, self, self.participant)


class FakeParticipant:
    def __init__(self, room, identity, name):
        self.identity, self.name = identity, name
        self.track_publications = {
            f"A_{identity}": FakePublication(room, self, f"A_{identity}", _Kind.KIND_AUDIO),
            f"V_{identity}": FakePublication(room, self, f"V_{identity}", _Kind.KIND_VIDEO),
        }

    def audio(self) -> FakePublication:
        return self.track_publications[f"A_{self.identity}"]


class FakeRtcRoom:
    instances: List["FakeRtcRoom"] = []
    initial: List = []

    def __init__(self):
        self.handlers = {}
        self.remote_participants = {}
        self.subscribe_calls = []
        self.connect_options = None
        self.disconnected = False
        FakeRtcRoom.instances.append(self)

    def on(self, event, handler):
        self.handlers[event] = handler

    def emit(self, event, *args):
        if event in self.handlers:
            self.handlers[event](*args)

    def add(self, identity, name):
        participant = FakeParticipant(self, identity, name)
        self.remote_participants[identity] = participant
        return participant

    async def connect(self, url, token, options):
        self.connect_options = options
        for identity, name in FakeRtcRoom.initial:
            self.add(identity, name)

    async def disconnect(self):
        self.disconnected = True


class FakeAudioStream:
    def __init__(self, track, sample_rate=48000, num_channels=1):
        self.track = track

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.track.queue.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def aclose(self):
        pass


class _Frame:
    def __init__(self, data):
        self.data, self.sample_rate, self.num_channels = data, SR, 1


class _FrameEvent:
    def __init__(self, data):
        self.frame = _Frame(data)


class FakeRtc:
    Room = FakeRtcRoom
    AudioStream = FakeAudioStream
    TrackKind = _Kind

    class RoomOptions:
        def __init__(self, auto_subscribe=True):
            self.auto_subscribe = auto_subscribe


def test_adapter_subscribes_only_consented_audio_after_arming(tmp_path):
    FakeRtcRoom.instances.clear()
    FakeRtcRoom.initial = [("alice", "Alice"), ("bob", "Bob")]
    session = make_session(tmp_path, {"alice": True, "bob": True})
    session._clock = time.monotonic

    async def scenario():
        adapter = LiveKitRecorderRoom(session, url="wss://fake", token="t", rtc_module=FakeRtc)
        runner = asyncio.ensure_future(adapter.run())
        while not FakeRtcRoom.instances or not session.capturing:
            await asyncio.sleep(0.01)
        room = FakeRtcRoom.instances[0]
        assert room.connect_options.auto_subscribe is False
        alice = room.remote_participants["alice"].audio().track
        bob = room.remote_participants["bob"].audio().track
        for i in range(30):
            alice.queue.put_nowait(_FrameEvent(tone(440, phase=i * FRAME)))
            bob.queue.put_nowait(_FrameEvent(tone(1000, phase=i * FRAME)))
            await asyncio.sleep(0.005)
        while session.frames_captured < 60:
            await asyncio.sleep(0.01)

        # An unconsented participant joins: capture stops, recorder leaves.
        room.add("mallory", "Mallory")
        room.emit("participant_connected", room.remote_participants["mallory"])
        await asyncio.wait_for(runner, timeout=5)
        alice.queue.put_nowait(_FrameEvent(tone(440)))
        return room

    room = asyncio.run(scenario())
    subscribed = {(identity, sid) for identity, sid, value in room.subscribe_calls if value}
    assert subscribed == {("alice", "A_alice"), ("bob", "A_bob")}       # never video, never mallory
    assert room.disconnected
    assert session.stop_reason == "unconsented_participant_joined"
    assert session.frames_captured == 60

    session.close_writers()
    result = finalize_segment(session.segment_dir)
    assert {t["identity"] for t in result["tracks"]} == {"alice", "bob"}


def test_adapter_never_subscribes_when_initial_consent_is_incomplete(tmp_path):
    FakeRtcRoom.instances.clear()
    FakeRtcRoom.initial = [("alice", "Alice"), ("bob", "Bob")]
    session = make_session(tmp_path, {"alice": True})

    adapter = LiveKitRecorderRoom(session, url="wss://fake", token="t", rtc_module=FakeRtc)
    asyncio.run(asyncio.wait_for(adapter.run(), timeout=5))
    room = FakeRtcRoom.instances[0]
    assert room.subscribe_calls == []
    assert session.stop_reason == "consent_missing"
    assert room.disconnected


# ---------------------------------------------------------------------------
# API: consent, recording lifecycle and pipeline hand-off
# ---------------------------------------------------------------------------

class ScriptedRoom:
    """Stands in for LiveKitRecorderRoom: arms with the current roster, then idles."""

    rosters: Dict[str, List] = {}
    fail_with: Exception | None = None

    def __init__(self, session, meeting):
        self.session = session
        self.room_name = meeting["room_name"]

    async def run(self):
        if ScriptedRoom.fail_with is not None:
            raise ScriptedRoom.fail_with
        loop = asyncio.get_running_loop()
        halted = asyncio.Event()
        self.session.add_halt_listener(lambda: loop.call_soon_threadsafe(halted.set))
        if self.session.arm(ScriptedRoom.rosters.get(self.room_name, [])) is None:
            await halted.wait()


@pytest.fixture
def live_api(monkeypatch):
    monkeypatch.setattr(config, "LIVEKIT_URL", "wss://unit-test.livekit.invalid")
    monkeypatch.setattr(config, "LIVEKIT_API_KEY", "APIunittest")
    monkeypatch.setattr(config, "LIVEKIT_API_SECRET", "unit_test_secret_0123456789abcdefghij")
    monkeypatch.setattr(config, "JOB_RUNNER", "inline")

    roster: Dict[str, List[Dict]] = {}

    async def fake_participants(room_name):
        return {"room_name": room_name, "participants": [
            {"identity": p["identity"], "name": p["name"]} for p in roster.get(room_name, [])
        ]}

    async def fake_noop(*args, **kwargs):
        return {"status": "ok", "room_name": kwargs.get("room_name", args[0] if args else "")}

    monkeypatch.setattr(live_routes, "get_live_room_participants", fake_participants)
    monkeypatch.setattr(live_routes, "update_live_participant_consent", fake_noop)
    monkeypatch.setattr(live_routes, "end_live_room", fake_noop)
    monkeypatch.setattr(live_routes, "create_live_room", lambda **kw: fake_noop(**kw))
    monkeypatch.setattr(live_routes, "_ROSTER_SYNC_INTERVAL", 0.0)

    pipeline_calls: List = []
    monkeypatch.setattr(live_routes, "run_pipeline", lambda mid, path: pipeline_calls.append((mid, Path(path))))

    ScriptedRoom.rosters = {}
    ScriptedRoom.fail_with = None
    manager = RecordingManager(room_factory=ScriptedRoom, stop_timeout=5)
    set_recording_manager(manager)

    with TestClient(app) as client:
        yield client, roster, manager, pipeline_calls
    set_recording_manager(None)


def join(client, roster, room, name, *, host=False, consent=True):
    res = client.post("/api/live/token", json={
        "room_name": room,
        "participant_name": name,
        "participant_identity": name.lower(),
        "is_host": host,
        "metadata": {"consented": consent},
    })
    assert res.status_code == 200, res.text
    data = res.json()
    roster.setdefault(room, []).append({"identity": data["participant_identity"], "name": name})
    ScriptedRoom.rosters[room] = [(p["identity"], p["name"]) for p in roster[room]]
    return data["meeting_id"], {"Authorization": f"Bearer {data['token']}"}


def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def feed(session, identity, track, freq, frames=50):
    for i in range(frames):
        assert session.audio_frame(track, identity, tone(freq, phase=i * FRAME), SR, 1)


def test_token_records_join_form_consent_and_one_host(live_api):
    client, roster, _, _ = live_api
    mid, _ = join(client, roster, "room-token", "Hana", host=True, consent=True)
    _, _ = join(client, roster, "room-token", "Gus", consent=False)
    assert db.is_participant_consented(mid, "hana") is True
    assert db.is_participant_consented(mid, "gus") is False

    second_host = client.post("/api/live/token", json={
        "room_name": "room-token", "participant_name": "Eve", "participant_identity": "eve", "is_host": True,
    })
    assert second_host.status_code == 403


def test_recording_cannot_start_without_complete_consent(live_api):
    client, roster, manager, _ = live_api
    mid, host = join(client, roster, "room-block", "Hana", host=True)
    _, guest = join(client, roster, "room-block", "Gus", consent=False)

    res = client.post(f"/api/live/meetings/{mid}/recording/start", headers=host)
    assert res.status_code == 409
    assert [b["identity"] for b in res.json()["detail"]["blocked"]] == ["gus"]
    assert not manager.is_active(mid)
    assert db.get_recording_segments(mid) == []

    # Guests cannot start recording, and anonymous callers are rejected.
    assert client.post(f"/api/live/meetings/{mid}/recording/start", headers=guest).status_code == 403
    assert client.post(f"/api/live/meetings/{mid}/recording/start").status_code == 401

    state = client.get(f"/api/live/meetings/{mid}/recording", headers=guest).json()
    assert state["status"] == "idle"
    assert state["consent"]["consent_blocked"] is True
    assert state["can_start"] is False


def test_participants_can_only_change_their_own_consent(live_api):
    client, roster, _, _ = live_api
    mid, host = join(client, roster, "room-own", "Hana", host=True)
    join(client, roster, "room-own", "Gus", consent=False)
    res = client.post("/api/live/rooms/room-own/consent", json={"identity": "gus", "consented": True}, headers=host)
    assert res.status_code == 403
    assert db.is_participant_consented(mid, "gus") is False


def test_full_recording_lifecycle_processes_exactly_once(live_api):
    client, roster, manager, pipeline_calls = live_api
    mid, host = join(client, roster, "room-ok", "Hana", host=True)
    _, guest = join(client, roster, "room-ok", "Gus")

    res = client.post(f"/api/live/meetings/{mid}/recording/start", headers=host)
    assert res.status_code == 202
    assert res.json()["status"] == "starting"            # no REC before audio arrives
    assert res.json()["capture_active"] is False

    assert wait_until(lambda: manager.active_session(mid) and manager.active_session(mid).capturing)
    session = manager.active_session(mid)
    feed(session, "hana", "TR_H", 440)
    feed(session, "gus", "TR_G", 1000)

    state = client.get(f"/api/live/meetings/{mid}/recording", headers=guest).json()
    assert state["status"] == "recording"
    assert state["capture_active"] is True
    assert state["live"]["frames_captured"] == 100
    assert {t["identity"] for t in state["live"]["tracks"]} == {"hana", "gus"}

    # Processing is refused while capture is active.
    assert client.post(f"/api/live/meetings/{mid}/recording/process", headers=host).status_code == 409

    stopped = client.post(f"/api/live/meetings/{mid}/recording/stop", headers=host).json()
    assert stopped["status"] == "recorded"
    assert stopped["segments"][0]["state"] == "finalized"
    assert stopped["segments"][0]["stop_reason"] == "host_stopped"
    assert stopped["artifact"]["frames"] > 0
    assert stopped["artifact"]["sample_rate"] == 16000
    assert set(stopped["artifact"]["participants"]) == {"Hana", "Gus"}
    assert stopped["can_process"] is True

    # Stop is idempotent.
    assert client.post(f"/api/live/meetings/{mid}/recording/stop", headers=host).json()["status"] == "recorded"

    first = client.post(f"/api/live/meetings/{mid}/recording/process", headers=host)
    second = client.post(f"/api/live/meetings/{mid}/recording/process", headers=host)
    assert first.status_code == 202 and first.json()["already_submitted"] is False
    assert second.status_code == 200 and second.json()["already_submitted"] is True
    recording = MeetingStorage(mid).recording / RECORDING_FILENAME
    assert pipeline_calls == [(mid, recording)]
    job = db.get_job_status(mid)
    assert job.state == "queued" and Path(job.file_path) == recording

    # A processed meeting cannot start a new segment.
    assert client.post(f"/api/live/meetings/{mid}/recording/start", headers=host).status_code == 409

    manifest = json.loads((MeetingStorage(mid).recording / "recording_manifest.json").read_text())
    assert {t["identity"] for t in manifest["segments"][0]["tracks"]} == {"hana", "gus"}


def test_worker_claims_the_live_recording_once(live_api, monkeypatch):
    client, roster, manager, pipeline_calls = live_api
    monkeypatch.setattr(config, "JOB_RUNNER", "worker")
    mid, host = join(client, roster, "room-worker", "Hana", host=True)
    client.post(f"/api/live/meetings/{mid}/recording/start", headers=host)
    assert wait_until(lambda: manager.active_session(mid) and manager.active_session(mid).capturing)
    feed(manager.active_session(mid), "hana", "TR_H", 440)
    client.post(f"/api/live/meetings/{mid}/recording/stop", headers=host)
    assert client.post(f"/api/live/meetings/{mid}/recording/process", headers=host).status_code == 202
    assert client.post(f"/api/live/meetings/{mid}/recording/process", headers=host).status_code == 200
    assert pipeline_calls == []                         # worker mode: queued only

    with db.get_conn() as conn:
        rows = conn.execute("SELECT state, file_path FROM jobs WHERE meeting_id = ?", (mid,)).fetchall()
    assert len(rows) == 1 and rows[0]["state"] == "queued"
    # scripts/worker.py resolves the artifact from the job's file_path.
    from scripts.worker import resolve_recording_path
    assert resolve_recording_path(mid, rows[0]["file_path"]) == MeetingStorage(mid).recording / RECORDING_FILENAME


def test_consent_revocation_stops_capture_and_requires_new_segment(live_api):
    client, roster, manager, _ = live_api
    mid, host = join(client, roster, "room-revoke", "Hana", host=True)
    _, guest = join(client, roster, "room-revoke", "Gus")
    client.post(f"/api/live/meetings/{mid}/recording/start", headers=host)
    assert wait_until(lambda: manager.active_session(mid) and manager.active_session(mid).capturing)
    session = manager.active_session(mid)
    feed(session, "gus", "TR_G", 1000)

    res = client.post("/api/live/rooms/room-revoke/consent", json={"identity": "gus", "consented": False}, headers=guest)
    assert res.status_code == 200
    assert session.capturing is False                   # halted synchronously by the request
    assert session.audio_frame("TR_G", "gus", tone(1000), SR, 1) is False
    assert wait_until(lambda: not manager.is_active(mid))

    state = client.get(f"/api/live/meetings/{mid}/recording", headers=host).json()
    assert state["status"] == "recorded"
    assert state["last_stop_reason"] == "consent_revoked"
    assert state["consent"]["consent_blocked"] is True

    # Re-granting consent does not resume anything...
    client.post("/api/live/rooms/room-revoke/consent", json={"identity": "gus", "consented": True}, headers=guest)
    time.sleep(0.1)
    assert not manager.is_active(mid)
    assert len(db.get_recording_segments(mid)) == 1

    # ...until the host explicitly starts a new segment.
    assert client.post(f"/api/live/meetings/{mid}/recording/start", headers=host).status_code == 202
    assert wait_until(lambda: manager.active_session(mid) and manager.active_session(mid).capturing)
    feed(manager.active_session(mid), "hana", "TR_H2", 440)
    state = client.post(f"/api/live/meetings/{mid}/recording/stop", headers=host).json()
    assert [s["seq"] for s in state["segments"]] == [1, 2]
    assert state["artifact"]["segments"] == 2

    events = [e["event_type"] for e in client.get(f"/api/live/meetings/{mid}/events", headers=host).json()["events"]]
    assert "consent_revoked" in events and "recording_halted" in events and events.count("recording_finalized") == 2


def test_unconsented_join_during_recording_stops_capture(live_api):
    client, roster, manager, _ = live_api
    mid, host = join(client, roster, "room-join", "Hana", host=True)
    client.post(f"/api/live/meetings/{mid}/recording/start", headers=host)
    assert wait_until(lambda: manager.active_session(mid) and manager.active_session(mid).capturing)
    session = manager.active_session(mid)
    feed(session, "hana", "TR_H", 440)

    join(client, roster, "room-join", "Gus", consent=False)
    session.participant_joined("gus", "Gus")             # what the RTC adapter reports
    assert not session.capturing
    assert wait_until(lambda: not manager.is_active(mid))
    seg = db.get_recording_segments(mid)[0]
    assert seg["stop_reason"] == "unconsented_participant_joined"
    assert seg["state"] == "finalized"


def test_host_termination_finalizes_recording_and_ends_meeting(live_api):
    client, roster, manager, _ = live_api
    mid, host = join(client, roster, "room-end", "Hana", host=True)
    _, guest = join(client, roster, "room-end", "Gus")
    client.post(f"/api/live/meetings/{mid}/recording/start", headers=host)
    assert wait_until(lambda: manager.active_session(mid) and manager.active_session(mid).capturing)
    feed(manager.active_session(mid), "gus", "TR_G", 1000)

    assert client.post("/api/live/rooms/room-end/end", headers=guest).status_code == 403
    res = client.post("/api/live/rooms/room-end/end", headers=host)
    assert res.status_code == 200
    assert res.json()["recording_segment"]["stop_reason"] == "meeting_ended"
    assert not manager.is_active(mid)
    assert db.get_live_meeting(mid)["status"] == "ended"
    state = client.get(f"/api/live/meetings/{mid}/recording", headers=host).json()
    assert state["status"] == "recorded" and state["can_process"] is True
    assert client.post(f"/api/live/meetings/{mid}/recording/start", headers=host).status_code == 409


def test_empty_recording_is_discarded_and_cannot_be_processed(live_api):
    client, roster, manager, pipeline_calls = live_api
    mid, host = join(client, roster, "room-empty", "Hana", host=True)
    client.post(f"/api/live/meetings/{mid}/recording/start", headers=host)
    assert wait_until(lambda: manager.active_session(mid) and manager.active_session(mid).capturing)
    state = client.post(f"/api/live/meetings/{mid}/recording/stop", headers=host).json()
    assert state["segments"][0]["state"] == "discarded"
    assert state["artifact"] is None
    assert client.post(f"/api/live/meetings/{mid}/recording/process", headers=host).status_code == 409
    assert pipeline_calls == []


def test_recorder_failure_is_surfaced(live_api):
    client, roster, manager, _ = live_api
    ScriptedRoom.fail_with = ConnectionError("signal connection refused")
    mid, host = join(client, roster, "room-fail", "Hana", host=True)
    client.post(f"/api/live/meetings/{mid}/recording/start", headers=host)
    assert wait_until(lambda: not manager.is_active(mid))
    state = client.get(f"/api/live/meetings/{mid}/recording", headers=host).json()
    assert state["status"] == "failed"
    assert state["segments"][0]["state"] == "failed"
    assert state["segments"][0]["stop_reason"] == "recorder_error"
    assert state["segments"][0]["error"] == "Recorder failed: ConnectionError"


def test_interrupted_segment_is_recovered_on_startup(live_api):
    client, roster, manager, _ = live_api
    mid, host = join(client, roster, "room-crash", "Hana", host=True)
    meeting = db.get_live_meeting(mid)
    segment = db.create_recording_segment(mid, "hana", [{"identity": "hana"}])
    db.update_recording_segment(segment["segment_id"], state="recording")
    seg_dir = segment_dir_for(mid, segment["seq"], segment["segment_id"])
    session = RecordingSession(
        meeting_id=mid, segment_id=segment["segment_id"], segment_dir=seg_dir,
        consent_lookup=lambda _i: True,
    )
    session.arm([("hana", "Hana")])
    feed(session, "hana", "TR_H", 440)
    session.writers["TR_H"]._fh.close()                 # process dies; the OS drops the handle

    assert segment["segment_id"] in recover_interrupted_segments(manager)
    seg = db.get_recording_segment(segment["segment_id"])
    assert seg["state"] == "finalized" and seg["stop_reason"] == "interrupted"
    assert wav_info(MeetingStorage(mid).recording / RECORDING_FILENAME)["frames"] > 0
    assert meeting["room_name"] == "room-crash"


def test_server_shutdown_finalizes_active_recording(live_api):
    client, roster, manager, _ = live_api
    mid, host = join(client, roster, "room-shutdown", "Hana", host=True)
    client.post(f"/api/live/meetings/{mid}/recording/start", headers=host)
    assert wait_until(lambda: manager.active_session(mid) and manager.active_session(mid).capturing)
    feed(manager.active_session(mid), "hana", "TR_H", 440)
    client.portal.call(manager.shutdown)
    seg = db.get_recording_segments(mid)[0]
    assert seg["state"] == "finalized" and seg["stop_reason"] == "server_shutdown"


def test_recording_state_never_leaks_secrets(live_api):
    client, roster, _, _ = live_api
    mid, host = join(client, roster, "room-secret", "Hana", host=True)
    body = json.dumps(client.get(f"/api/live/meetings/{mid}/recording", headers=host).json())
    assert config.LIVEKIT_API_SECRET not in body
    assert host["Authorization"].split()[1] not in body

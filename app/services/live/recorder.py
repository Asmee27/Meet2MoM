"""
app/services/live/recorder.py
=============================
Server-side LiveKit audio recorder (Phase 4.2).

A hidden, subscribe-only participant joins the room with the official
LiveKit Python RTC SDK and writes every consented participant's audio track
to disk. Consent is enforced here, not in the browser:

* capture is armed only if every connected participant has consented;
* audio tracks are subscribed only after arming, and only for consented
  participants (auto-subscribe is off);
* an unconsented join, a revocation, an empty room, the host ending the
  meeting, a recorder disconnect or a server shutdown halts capture at once;
* a halted segment is finalized and never resumes. The host must explicitly
  start a new segment.

RecordingSession holds the state machine and has no LiveKit dependency, so
it can be driven by fakes in tests. LiveKitRecorderRoom wires the SDK events
to it. RecordingManager owns the active sessions and their persistence.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.core import db
from app.core.storage import MeetingStorage
from app.services.live.recording_audio import (
    TARGET_SAMPLE_RATE,
    TrackWriter,
    build_meeting_recording,
    discard_segment_audio,
    finalize_segment,
    normalize_frame,
    safe_component,
    write_tracks_meta,
)

logger = logging.getLogger("live_recorder")

RECORDER_IDENTITY_PREFIX = "m2m-recorder-"

STATE_STARTING = "starting"
STATE_RECORDING = "recording"
STATE_STOPPING = "stopping"
STATE_FINALIZED = "finalized"
STATE_DISCARDED = "discarded"
STATE_FAILED = "failed"
TERMINAL_STATES = (STATE_FINALIZED, STATE_DISCARDED, STATE_FAILED)

# Stop reasons that mean a consent rule was hit.
CONSENT_STOP_REASONS = (
    "consent_missing",
    "consent_revoked",
    "unconsented_participant_joined",
    "unconsented_audio",
)


def is_recorder_identity(identity: str) -> bool:
    return (identity or "").startswith(RECORDER_IDENTITY_PREFIX)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RecordingSession:
    """Consent-enforcing capture state for one recording segment."""

    def __init__(
        self,
        *,
        meeting_id: str,
        segment_id: str,
        segment_dir: Path,
        consent_lookup: Callable[[str], bool],
        on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        sample_rate: int = TARGET_SAMPLE_RATE,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.meeting_id = meeting_id
        self.segment_id = segment_id
        self.segment_dir = Path(segment_dir)
        self.segment_dir.mkdir(parents=True, exist_ok=True)
        self._consent_lookup = consent_lookup
        self._on_event = on_event
        self.sample_rate = sample_rate
        self._clock = clock
        self._lock = threading.RLock()
        self._halt_listeners: List[Callable[[], None]] = []

        self.state = STATE_STARTING
        self.capturing = False
        self.stop_reason: Optional[str] = None
        self.stop_identity: Optional[str] = None
        self.error: Optional[str] = None
        self.capture_started_at: Optional[str] = None
        self.stopped_at: Optional[str] = None
        self.frames_captured = 0
        self.last_frame_monotonic: Optional[float] = None
        self.participants: Dict[str, str] = {}
        self._consented: set[str] = set()
        self.writers: Dict[str, TrackWriter] = {}
        self._t0: Optional[float] = None

    # -- events -------------------------------------------------------------

    def add_halt_listener(self, callback: Callable[[], None]) -> None:
        with self._lock:
            already = self.stop_reason is not None
            self._halt_listeners.append(callback)
        if already:
            callback()

    def _emit(self, event: str, detail: Dict[str, Any]) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(event, detail)
        except Exception:
            logger.exception("Recording event handler failed for %s", event)

    # -- lifecycle ----------------------------------------------------------

    def arm(self, roster: List[Tuple[str, str]]) -> Optional[str]:
        """
        Called once the recorder is connected, with the participants it sees.
        Returns None if capture is armed, otherwise the stop reason.
        """
        with self._lock:
            if self.stop_reason is not None:
                return self.stop_reason
            for identity, name in roster:
                self.participants[identity] = name or identity
            if not roster:
                self.halt("no_participants")
                return self.stop_reason
            missing = [identity for identity, _ in roster if not self._consent_lookup(identity)]
            if missing:
                self.halt("consent_missing", identity=missing[0], detail={"identities": missing})
                return self.stop_reason
            self._consented = {identity for identity, _ in roster}
            self.capturing = True
            self._t0 = self._clock()
        self._emit("capture_armed", {"participants": [i for i, _ in roster]})
        return None

    def halt(
        self,
        reason: str,
        identity: Optional[str] = None,
        detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Stop capturing immediately. The first reason wins; later calls are no-ops."""
        with self._lock:
            if self.stop_reason is not None:
                return
            self.stop_reason = reason
            self.stop_identity = identity
            self.stopped_at = _utc_now()
            self.capturing = False
            if self.state in (STATE_STARTING, STATE_RECORDING):
                self.state = STATE_STOPPING
            listeners = list(self._halt_listeners)
        self._emit("capture_halted", {"reason": reason, "identity": identity, **(detail or {})})
        for callback in listeners:
            try:
                callback()
            except Exception:
                logger.exception("Halt listener failed")

    def fail(self, error: str) -> None:
        with self._lock:
            self.error = error
        self.halt("recorder_error")

    # -- roster and consent -------------------------------------------------

    def participant_joined(self, identity: str, name: str) -> None:
        with self._lock:
            self.participants[identity] = name or identity
            if not self.capturing:
                return
            if self._consent_lookup(identity):
                self._consented.add(identity)
                return
        self.halt("unconsented_participant_joined", identity=identity)

    def participant_left(self, identity: str) -> None:
        with self._lock:
            self.participants.pop(identity, None)
            self._consented.discard(identity)
            for writer in self.writers.values():
                if writer.identity == identity:
                    writer.close()
            self._persist_tracks()
            empty = self.capturing and not self.participants
        if empty:
            self.halt("no_participants")

    def consent_changed(self, identity: str, consented: bool) -> None:
        """A granted consent never resumes capture; a revocation always halts it."""
        if consented:
            return
        with self._lock:
            self._consented.discard(identity)
            active = self.capturing
        if active:
            self.halt("consent_revoked", identity=identity)

    def should_subscribe(self, identity: str) -> bool:
        with self._lock:
            return self.capturing and identity in self._consented

    # -- audio --------------------------------------------------------------

    def audio_frame(
        self,
        track_sid: str,
        identity: str,
        data: Any,
        sample_rate: int,
        num_channels: int,
    ) -> bool:
        """Persist one frame. Returns False if the frame was dropped."""
        started = False
        with self._lock:
            if not self.capturing:
                return False
            if identity not in self.participants:
                # Late frame from someone who already left: drop, never store.
                return False
            if identity not in self._consented:
                unconsented = True
            else:
                unconsented = False
                writer = self._writer_for(track_sid, identity)
                pcm = normalize_frame(data, sample_rate, num_channels, self.sample_rate)
                first_frame_of_track = writer.frames == 0
                writer.write(pcm, self._clock() - self._t0)
                if first_frame_of_track:
                    self._persist_tracks()
                self.frames_captured += 1
                self.last_frame_monotonic = self._clock()
                if self.state == STATE_STARTING:
                    self.state = STATE_RECORDING
                    self.capture_started_at = _utc_now()
                    started = True
        if unconsented:
            self.halt("unconsented_audio", identity=identity)
            return False
        if started:
            self._emit("capture_started", {"capture_started_at": self.capture_started_at})
        return True

    def _writer_for(self, track_sid: str, identity: str) -> TrackWriter:
        """Current writer for a track. A track resubscribed after it ended (e.g.
        a participant's connection resumed with the same track sid) gets a new
        file, so no frame is ever written to, or lost in, a closed writer."""
        writer = self.writers.get(track_sid)
        if writer is not None and not writer.closed:
            return writer
        key, part = track_sid, 1
        while key in self.writers:
            part += 1
            key = f"{track_sid}#{part}"
        if writer is not None:
            # Keep the closed writer's metadata under a unique key.
            self.writers[key] = writer
            key = track_sid
        suffix = f"_{part}" if part > 1 else ""
        filename = f"track_{safe_component(identity)}_{safe_component(track_sid, 24)}{suffix}.pcm"
        new_writer = TrackWriter(
            self.segment_dir / filename,
            track_sid=track_sid,
            identity=identity,
            name=self.participants.get(identity, identity),
            sample_rate=self.sample_rate,
        )
        self.writers[key] = new_writer
        return new_writer

    def track_ended(self, track_sid: str) -> None:
        with self._lock:
            writer = self.writers.get(track_sid)
            if writer is not None:
                writer.close()
                self._persist_tracks()

    def _persist_tracks(self) -> None:
        write_tracks_meta(
            self.segment_dir,
            [w.to_meta() for w in self.writers.values() if w.frames > 0],
        )

    def close_writers(self) -> None:
        with self._lock:
            for writer in self.writers.values():
                writer.close()
            self._persist_tracks()

    # -- reporting ----------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            now = self._clock()
            return {
                "state": self.state,
                "capturing": self.capturing,
                "stop_reason": self.stop_reason,
                "frames_captured": self.frames_captured,
                "seconds_since_last_frame": (
                    round(now - self.last_frame_monotonic, 2)
                    if self.last_frame_monotonic is not None else None
                ),
                "elapsed_seconds": round(now - self._t0, 1) if self._t0 is not None and self.capturing else None,
                "tracks": [
                    {
                        "identity": w.identity,
                        "name": w.name,
                        "track_sid": w.track_sid,
                        "seconds": round(((w.start_offset or 0) + w.samples) / w.sample_rate, 2),
                        "active": not w.closed,
                    }
                    for w in self.writers.values()
                ],
            }


class LiveKitRecorderRoom:
    """Connects a RecordingSession to a real LiveKit room via livekit.rtc."""

    def __init__(
        self,
        session: RecordingSession,
        *,
        url: str,
        token: str,
        rtc_module: Any = None,
        connect_timeout: float = 20.0,
    ):
        self.session = session
        self.url = url
        self.token = token
        self.connect_timeout = connect_timeout
        if rtc_module is None:
            from livekit import rtc as rtc_module  # lazy: heavy native library
        self.rtc = rtc_module

    async def run(self) -> None:
        rtc = self.rtc
        session = self.session
        loop = asyncio.get_running_loop()
        halted = asyncio.Event()
        session.add_halt_listener(lambda: loop.call_soon_threadsafe(halted.set))
        tasks: Dict[str, asyncio.Task] = {}
        audio_kind = rtc.TrackKind.KIND_AUDIO

        def subscribe_if_allowed(publication, participant) -> None:
            if publication.kind == audio_kind and session.should_subscribe(participant.identity):
                publication.set_subscribed(True)

        def on_participant_connected(participant) -> None:
            if not is_recorder_identity(participant.identity):
                session.participant_joined(participant.identity, participant.name)

        def on_participant_disconnected(participant) -> None:
            if not is_recorder_identity(participant.identity):
                session.participant_left(participant.identity)

        def on_track_published(publication, participant) -> None:
            subscribe_if_allowed(publication, participant)

        def on_track_subscribed(track, publication, participant) -> None:
            if track.kind != audio_kind:
                return
            if not session.should_subscribe(participant.identity):
                publication.set_subscribed(False)
                return
            if track.sid not in tasks:
                tasks[track.sid] = asyncio.ensure_future(self._consume(track, participant))

        def on_track_unsubscribed(track, publication, participant) -> None:
            task = tasks.pop(getattr(track, "sid", None), None)
            if task is not None:
                task.cancel()
            session.track_ended(track.sid)

        def on_disconnected(*_args) -> None:
            session.halt("recorder_disconnected")

        room = rtc.Room()
        room.on("participant_connected", on_participant_connected)
        room.on("participant_disconnected", on_participant_disconnected)
        room.on("track_published", on_track_published)
        room.on("track_subscribed", on_track_subscribed)
        room.on("track_unsubscribed", on_track_unsubscribed)
        room.on("disconnected", on_disconnected)

        try:
            await asyncio.wait_for(
                room.connect(self.url, self.token, rtc.RoomOptions(auto_subscribe=False)),
                timeout=self.connect_timeout,
            )
        except BaseException:
            try:
                await room.disconnect()
            except Exception:
                pass
            raise
        try:
            roster = [
                (p.identity, p.name)
                for p in room.remote_participants.values()
                if not is_recorder_identity(p.identity)
            ]
            if session.arm(roster) is None:
                for participant in list(room.remote_participants.values()):
                    for publication in list(participant.track_publications.values()):
                        subscribe_if_allowed(publication, participant)
                await halted.wait()
        finally:
            for task in tasks.values():
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks.values(), return_exceptions=True)
            try:
                await room.disconnect()
            except Exception:
                logger.warning("Recorder disconnect raised; continuing with finalization")

    async def _consume(self, track, participant) -> None:
        stream = self.rtc.AudioStream(track, sample_rate=self.session.sample_rate, num_channels=1)
        try:
            async for event in stream:
                frame = event.frame
                self.session.audio_frame(
                    track.sid,
                    participant.identity,
                    frame.data,
                    frame.sample_rate,
                    frame.num_channels,
                )
                if not self.session.capturing:
                    break
        finally:
            try:
                await stream.aclose()
            except Exception:
                pass
            self.session.track_ended(track.sid)


@dataclass
class _ActiveRecording:
    session: RecordingSession
    task: asyncio.Task
    room_name: str


RoomFactory = Callable[[RecordingSession, Dict[str, Any]], Any]


def segment_dir_for(meeting_id: str, seq: int, segment_id: str) -> Path:
    storage = MeetingStorage(meeting_id)
    return storage.recording / "segments" / f"{seq:03d}_{segment_id[:12]}"


def _default_room_factory(session: RecordingSession, meeting: Dict[str, Any]) -> LiveKitRecorderRoom:
    from app.core import config
    from app.services.live.livekit_service import generate_recorder_token

    identity = f"{RECORDER_IDENTITY_PREFIX}{session.segment_id[:8]}"
    token = generate_recorder_token(meeting["room_name"], identity)
    return LiveKitRecorderRoom(session, url=config.LIVEKIT_URL, token=token)


class RecordingManager:
    """Owns the active recordings of this API process."""

    def __init__(self, room_factory: Optional[RoomFactory] = None, stop_timeout: float = 20.0):
        self._room_factory = room_factory or _default_room_factory
        self._active: Dict[str, _ActiveRecording] = {}
        self.stop_timeout = stop_timeout

    # -- queries ------------------------------------------------------------

    def active_session(self, meeting_id: str) -> Optional[RecordingSession]:
        active = self._active.get(meeting_id)
        return active.session if active else None

    def is_active(self, meeting_id: str) -> bool:
        return meeting_id in self._active

    # -- commands -----------------------------------------------------------

    async def start(
        self,
        meeting: Dict[str, Any],
        started_by: str,
        participants: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        meeting_id = meeting["meeting_id"]
        segment = db.create_recording_segment(meeting_id, started_by, participants)
        seg_dir = segment_dir_for(meeting_id, segment["seq"], segment["segment_id"])
        MeetingStorage(meeting_id).ensure_dirs()
        session = RecordingSession(
            meeting_id=meeting_id,
            segment_id=segment["segment_id"],
            segment_dir=seg_dir,
            consent_lookup=lambda identity: db.is_participant_consented(meeting_id, identity),
            on_event=lambda event, detail: self._on_session_event(meeting_id, segment["segment_id"], event, detail),
        )
        try:
            room = self._room_factory(session, meeting)
        except Exception as exc:
            session.fail(f"Recorder could not be created: {type(exc).__name__}")
            await asyncio.to_thread(self._finalize, session)
            return db.get_recording_segment(segment["segment_id"])
        task = asyncio.ensure_future(self._run(session, room))
        self._active[meeting_id] = _ActiveRecording(session, task, meeting["room_name"])
        return db.get_recording_segment(segment["segment_id"])

    async def stop(self, meeting_id: str, reason: str = "host_stopped") -> Optional[Dict[str, Any]]:
        """Halt capture and wait until the segment is finalized. Idempotent."""
        active = self._active.get(meeting_id)
        if active is None:
            return None
        active.session.halt(reason)
        try:
            await asyncio.wait_for(asyncio.shield(active.task), timeout=self.stop_timeout)
        except asyncio.TimeoutError:
            logger.error("Recorder for meeting %s did not stop within %.0fs", meeting_id, self.stop_timeout)
        return db.get_recording_segment(active.session.segment_id)

    def notify_consent(self, meeting_id: str, identity: str, consented: bool) -> None:
        session = self.active_session(meeting_id)
        if session is not None:
            session.consent_changed(identity, consented)

    async def shutdown(self) -> None:
        for meeting_id in list(self._active):
            await self.stop(meeting_id, reason="server_shutdown")

    # -- internals ----------------------------------------------------------

    async def _run(self, session: RecordingSession, room: Any) -> None:
        try:
            await room.run()
        except asyncio.CancelledError:
            session.halt("server_shutdown")
        except Exception as exc:
            # Never log exception text from the SDK verbatim at error level:
            # connection errors can echo the signalling URL and token.
            logger.error(
                "Recorder for meeting %s failed: %s", session.meeting_id, type(exc).__name__
            )
            session.fail(f"Recorder failed: {type(exc).__name__}")
        finally:
            if session.stop_reason is None:
                session.halt("recorder_disconnected")
            try:
                await asyncio.to_thread(self._finalize, session)
            finally:
                self._active.pop(session.meeting_id, None)

    def _on_session_event(self, meeting_id: str, segment_id: str, event: str, detail: Dict[str, Any]) -> None:
        if event == "capture_started":
            db.update_recording_segment(
                segment_id,
                state=STATE_RECORDING,
                capture_started_at=detail.get("capture_started_at"),
            )
            db.record_live_event(meeting_id, "recording_started", segment_id=segment_id)
        elif event == "capture_halted":
            db.update_recording_segment(segment_id, state=STATE_STOPPING, stop_reason=detail.get("reason"))
            db.record_live_event(
                meeting_id,
                "recording_halted",
                identity=detail.get("identity"),
                segment_id=segment_id,
                detail={"reason": detail.get("reason")},
            )
        elif event == "capture_armed":
            db.update_recording_segment(segment_id, participants=[
                {"identity": identity} for identity in detail.get("participants", [])
            ])

    def _finalize(self, session: RecordingSession) -> None:
        session.close_writers()
        finalize_and_persist(
            session.meeting_id,
            session.segment_id,
            stop_reason=session.stop_reason,
            stopped_at=session.stopped_at,
            capture_started_at=session.capture_started_at,
            error=session.error,
        )
        with session._lock:
            segment = db.get_recording_segment(session.segment_id)
            session.state = segment["state"] if segment else STATE_FAILED


def finalize_and_persist(
    meeting_id: str,
    segment_id: str,
    *,
    stop_reason: Optional[str],
    stopped_at: Optional[str],
    capture_started_at: Optional[str],
    error: Optional[str] = None,
) -> Dict[str, Any]:
    """Finalize a segment's audio on disk, persist the result and rebuild recording.wav."""
    segment = db.get_recording_segment(segment_id)
    seg_dir = segment_dir_for(meeting_id, segment["seq"], segment_id)
    try:
        result = finalize_segment(seg_dir) if seg_dir.exists() else {
            "segment_file": None, "duration_seconds": 0.0, "empty": True, "tracks": [], "peak": 0,
        }
    except Exception as exc:
        logger.exception("Finalizing segment %s failed", segment_id)
        result = {"segment_file": None, "duration_seconds": 0.0, "empty": True, "tracks": [], "peak": 0}
        error = error or f"Finalization failed: {type(exc).__name__}"

    if result["empty"]:
        discard_segment_audio(seg_dir)
        state = STATE_FAILED if error else STATE_DISCARDED
        error = error or "No audible audio was captured in this segment."
        artifact = None
    else:
        state = STATE_FINALIZED
        artifact = str(seg_dir / result["segment_file"])

    db.update_recording_segment(
        segment_id,
        state=state,
        stop_reason=stop_reason,
        stopped_at=stopped_at or _utc_now(),
        capture_started_at=capture_started_at or segment.get("capture_started_at"),
        tracks=result["tracks"],
        artifact_path=artifact,
        duration_seconds=result["duration_seconds"],
        error=error,
    )
    db.record_live_event(
        meeting_id,
        "recording_finalized" if state == STATE_FINALIZED else f"recording_{state}",
        segment_id=segment_id,
        detail={
            "reason": stop_reason,
            "duration_seconds": result["duration_seconds"],
            "tracks": len(result["tracks"]),
        },
    )
    rebuild_meeting_recording(meeting_id)
    return db.get_recording_segment(segment_id)


def rebuild_meeting_recording(meeting_id: str) -> Optional[Dict[str, Any]]:
    storage = MeetingStorage(meeting_id)
    segments = []
    for seg in db.get_recording_segments(meeting_id):
        if seg["state"] != STATE_FINALIZED or not seg.get("artifact_path"):
            continue
        seg_dir = segment_dir_for(meeting_id, seg["seq"], seg["segment_id"])
        segments.append({**seg, "dir": seg_dir, "segment_file": Path(seg["artifact_path"]).name})
    storage.recording.mkdir(parents=True, exist_ok=True)
    return build_meeting_recording(storage.recording, segments, meeting_id)


def recover_interrupted_segments(manager: Optional[RecordingManager] = None) -> List[str]:
    """
    Finalize segments a crashed or killed process left non-terminal. Audio
    already on disk was captured under valid consent, so it is kept.
    """
    recovered = []
    for seg in db.get_unfinished_recording_segments():
        if manager is not None and manager.is_active(seg["meeting_id"]):
            continue
        try:
            finalize_and_persist(
                seg["meeting_id"],
                seg["segment_id"],
                stop_reason=seg.get("stop_reason") or "interrupted",
                stopped_at=None,
                capture_started_at=seg.get("capture_started_at"),
            )
            recovered.append(seg["segment_id"])
        except Exception:
            logger.exception("Could not recover recording segment %s", seg["segment_id"])
    return recovered


_manager: Optional[RecordingManager] = None


def get_recording_manager() -> RecordingManager:
    global _manager
    if _manager is None:
        _manager = RecordingManager()
    return _manager


def set_recording_manager(manager: Optional[RecordingManager]) -> None:
    """Swap the process-wide manager (tests inject fakes)."""
    global _manager
    _manager = manager

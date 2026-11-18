"""
Speaker start-up guard (silent: a fake output stream, no sound card is touched).

On the Windows laptop an exclusive speaker stream sometimes started but never
played, and the blocking warm-up write hung the node forever (docs/TESTING.md,
2026-11-18). Speaker.open() must now give up on such a stream with an AudioError,
retry, and fall back to shared mode with a visible warning.
"""
import pytest

from shout_lan import audio_io


class FakeStream:
    """write_available stays 0 when `stuck` (the device never takes any samples)."""
    created = []

    def __init__(self, stuck, **kwargs):
        self.stuck = stuck
        self.exclusive = kwargs.get("extra_settings") is not None
        self.written = 0
        self.closed = False
        FakeStream.created.append(self)

    @property
    def write_available(self):
        return 0 if self.stuck else 1024

    def start(self):
        pass

    def write(self, data):
        self.written += len(data)
        return False

    def abort(self):
        pass

    def close(self):
        self.closed = True


def make_speaker(monkeypatch, stuck_exclusive, stuck_shared):
    FakeStream.created = []

    def fake_output_stream(**kwargs):
        exclusive = kwargs.get("extra_settings") is not None
        return FakeStream(stuck_exclusive if exclusive else stuck_shared, **kwargs)

    monkeypatch.setattr(audio_io, "check_device", lambda device, kind: None)
    monkeypatch.setattr(audio_io, "_is_wasapi", lambda device: True)
    monkeypatch.setattr(audio_io, "_channels", lambda device, kind: 2)
    monkeypatch.setattr(audio_io.sd, "OutputStream", fake_output_stream)
    monkeypatch.setattr(audio_io.sd, "WasapiSettings", lambda exclusive: object())
    monkeypatch.setattr(audio_io.time, "sleep", lambda s: None)
    spk = audio_io.Speaker(None)
    spk.WARMUP_TIMEOUT = 0.05
    return spk


def test_working_exclusive_stream_gets_its_warm_up(monkeypatch):
    spk = make_speaker(monkeypatch, stuck_exclusive=False, stuck_shared=False)
    spk.open()
    assert spk.mode == "exclusive" and spk.warning is None
    assert spk.stream.written == spk.WARMUP_SAMPLES


def test_exclusive_stream_that_never_plays_falls_back_to_shared(monkeypatch):
    spk = make_speaker(monkeypatch, stuck_exclusive=True, stuck_shared=False)
    spk.open()                                   # must return, not hang
    assert spk.mode == "shared"
    assert "did not start playing" in spk.warning
    exclusive = [s for s in FakeStream.created if s.exclusive]
    assert len(exclusive) == 3 and all(s.closed for s in exclusive)   # retried, each closed


def test_speaker_that_never_plays_at_all_is_a_clear_error(monkeypatch):
    spk = make_speaker(monkeypatch, stuck_exclusive=True, stuck_shared=True)
    with pytest.raises(audio_io.AudioError, match="did not start playing"):
        spk.open()
    assert spk.stream is None and spk.mode == "closed"

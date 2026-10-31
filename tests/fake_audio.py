"""
fake_audio.py - SILENT stand-ins for the speaker and microphone (tests only).

They let the REAL acoustic code path (AcousticChannel, half-duplex muting,
carrier sensing, the hardware tools) run without making a sound: whatever a
FakeSpeaker "plays" is streamed in small chunks, in (scaled) real time, into
the FakeMicrophones of every node that can "hear" it - including its own,
because a real laptop's microphone hears its own speaker too.
Nothing here touches sounddevice or the sound card.
"""

import threading
import time

import numpy as np

from shout_lan import config


class FakeAir:
    def __init__(self, gain=0.3, noise=0.002, time_scale=1.0, hears=None):
        self.gain, self.noise, self.time_scale = gain, noise, time_scale
        self.hears = hears            # optional {speaker_name: [mic_name, ...]}
        self.mics = {}
        self.lock = threading.Lock()
        self.active = 0               # transmissions currently "in the air"

    def add_mic(self, name, mic):
        with self.lock:
            self.mics[name] = mic

    def play(self, speaker_name, samples, chunk=4800):
        samples = np.asarray(samples, dtype=np.float32)
        with self.lock:
            self.active += 1
        try:
            self._stream(speaker_name, samples, chunk)
        finally:
            with self.lock:
                self.active -= 1

    def _stream(self, speaker_name, samples, chunk):
        for i in range(0, len(samples), chunk):
            part = samples[i:i + chunk] * self.gain
            with self.lock:
                targets = [m for n, m in self.mics.items()
                           if self.hears is None or n in self.hears.get(speaker_name, [n])]
            for mic in targets:
                mic.push(part)
            time.sleep(len(part) / config.SAMPLE_RATE * self.time_scale)


class FakeMicrophone:
    def __init__(self, air, name):
        self.air, self.name = air, name
        self.lock = threading.Lock()
        self.pending = []
        self.overflows = 0
        self.level = 0.0
        self.rng = np.random.default_rng()
        self.running = False
        air.add_mic(name, self)

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def push(self, audio):
        with self.lock:
            self.pending.append(audio)

    def read_available(self):
        """
        Like a real microphone: only as many samples as (scaled) time has passed,
        plus background noise. Sound "played" into the air arrives via push().
        """
        now = time.time()
        last = getattr(self, "_last", now)
        self._last = now
        elapsed = int((now - last) * config.SAMPLE_RATE / self.air.time_scale)
        with self.lock:
            parts, self.pending = self.pending, []
        audio = np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)
        if len(audio) < elapsed and self.air.active == 0:   # quiet time -> just room noise
            audio = np.concatenate([audio, np.zeros(min(elapsed - len(audio), 48000),
                                                    dtype=np.float32)])
        if len(audio) == 0:
            return audio
        audio = audio + self.rng.normal(0, self.air.noise, len(audio)).astype(np.float32)
        self.level = float(np.sqrt(np.mean(audio * audio)))
        return audio


class FakeSpeaker:
    def __init__(self, air, name, refuse_exclusive=False):
        self.air, self.name = air, name
        self.underflows = 0
        self.mode = "closed"
        self.warning = None
        self.refuse_exclusive = refuse_exclusive

    def open(self):
        if self.refuse_exclusive:
            self.warning = "Speaker exclusive mode refused (fake) - using shared mode"
            self.mode = "shared"
        else:
            self.mode = "exclusive"

    def play(self, samples):
        if self.mode == "closed":
            self.open()
        self.air.play(self.name, samples)

    def close(self):
        self.mode = "closed"


def fake_acoustic_channel(air, name, **speaker_kw):
    """An AcousticChannel whose microphone and speaker are silent fakes."""
    from shout_lan.channel import AcousticChannel
    ch = AcousticChannel.__new__(AcousticChannel)
    ch.mic = FakeMicrophone(air, name)
    ch.speaker = FakeSpeaker(air, name, **speaker_kw)
    ch.mic_device = ch.speaker_device = name
    return ch

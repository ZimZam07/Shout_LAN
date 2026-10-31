"""
channel.py - the transmission medium.

AcousticChannel  : the REAL medium - this laptop's speaker and microphone.
SimulatedChannel : a SOFTWARE stand-in for the air, used for automated tests
                   and as a clearly labelled backup demo mode. It still sends
                   real modulated audio samples between nodes (so the modem,
                   framing and protocols are all exercised), but the "air" is
                   a Python object, not sound. Everything it produces is
                   labelled "SIMULATED".

Both offer the same methods, so the engine does not care which one it uses:
    start(), stop(), read() -> new audio samples, transmit(samples), level()
"""

import random
import threading
import time

import numpy as np

from shout_lan import config
from shout_lan.audio_io import Microphone, Speaker


class AcousticChannel:
    kind = "acoustic"
    label = "Acoustic link (sound)"

    def __init__(self, mic_device=None, speaker_device=None, exclusive=True):
        self.mic = Microphone(mic_device, exclusive=exclusive)
        self.speaker = Speaker(speaker_device)
        self.mic_device = mic_device
        self.speaker_device = speaker_device

    def start(self):
        self.mic.start()

    def stop(self):
        self.mic.stop()
        self.speaker.close()

    def read(self):
        return self.mic.read_available()

    def transmit(self, samples):
        self.speaker.play(samples)

    def level(self):
        return self.mic.level

    @property
    def overflows(self):
        return self.mic.overflows


class AcousticLoopbackHub:
    """
    Single-laptop testing only: TWO engines in one program share this
    laptop's ONE microphone and ONE speaker (the mic can only be opened once
    in exclusive mode). Frames really travel speaker -> air -> microphone;
    each engine ignores the frames it sent itself (by source address).
    """

    def __init__(self, mic_device=None, speaker_device=None, exclusive=True):
        self.mic = Microphone(mic_device, exclusive=exclusive)
        self.speaker = Speaker(speaker_device)
        self.members = []
        self.lock = threading.Lock()
        self.thread = None
        self.running = False

    def attach(self, channel):
        with self.lock:
            self.members.append(channel)
            first = len(self.members) == 1
        if first:
            self.mic.start()
            self.running = True
            self.thread = threading.Thread(target=self._fanout, daemon=True)
            self.thread.start()

    def detach(self, channel):
        with self.lock:
            if channel in self.members:
                self.members.remove(channel)
            last = not self.members
        if last and self.running:
            self.running = False
            self.thread.join(timeout=2)
            self.mic.stop()
            self.speaker.close()

    def _fanout(self):
        while self.running:
            audio = self.mic.read_available()
            if len(audio):
                with self.lock:
                    members = list(self.members)
                for m in members:
                    m._hear(audio)
            time.sleep(0.02)


class LoopbackAcousticChannel:
    kind = "acoustic"
    label = "Acoustic link (single-laptop loopback)"
    half_duplex = False          # both nodes share one mic, so nobody may go deaf

    def __init__(self, hub):
        self.hub = hub
        self.lock = threading.Lock()
        self.pending = []

    def start(self):
        self.hub.attach(self)

    def stop(self):
        self.hub.detach(self)

    def _hear(self, audio):
        with self.lock:
            self.pending.append(audio)

    def read(self):
        with self.lock:
            parts, self.pending = self.pending, []
        return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)

    def transmit(self, samples):
        self.hub.speaker.play(samples)

    def level(self):
        return self.hub.mic.level

    @property
    def overflows(self):
        return self.hub.mic.overflows


class SimulatedAir:
    """
    The shared "air" for simulated nodes in one program. When one node
    transmits, every OTHER node receives the same samples (a broadcast medium,
    just like sound), with optional attenuation, noise and random frame loss.
    Limitation: overlapping transmissions are queued one after another rather
    than mixed, so this channel does NOT model collisions (the ALOHA
    collision behaviour is studied in aloha_sim.py instead).
    """

    def __init__(self, noise=0.002, gain=0.3, loss=0.0, time_scale=1.0, seed=None):
        self.noise = noise
        self.gain = gain
        self.loss = loss                    # probability that a transmission is lost completely
        self.time_scale = time_scale        # < 1.0 makes tests run faster than real time
        self.nodes = []
        self.lock = threading.Lock()
        self.rng = random.Random(seed)
        self.busy_until = 0.0               # for carrier sensing in the simulation

    def attach(self, channel):
        with self.lock:
            self.nodes.append(channel)

    def detach(self, channel):
        with self.lock:
            if channel in self.nodes:
                self.nodes.remove(channel)

    def broadcast(self, sender, samples):
        lost = self.rng.random() < self.loss
        with self.lock:
            receivers = [n for n in self.nodes if n is not sender]
            duration = len(samples) / config.SAMPLE_RATE * self.time_scale
            self.busy_until = max(self.busy_until, time.time() + duration)
        if not lost:
            for node in receivers:
                node._hear(np.asarray(samples, dtype=np.float32) * self.gain)
        return lost


class SimulatedChannel:
    kind = "simulated"
    label = "SIMULATED channel (software, not sound)"

    def __init__(self, air):
        self.air = air
        self.lock = threading.Lock()
        self.pending = []                   # samples waiting to be read by this node
        self.np_rng = np.random.default_rng()
        self.last_lost = False

    def start(self):
        self.air.attach(self)

    def stop(self):
        self.air.detach(self)

    def _hear(self, samples):
        with self.lock:
            self.pending.append(samples)

    def read(self):
        """
        Return what this node 'heard' since the last call. To keep the receiver
        working like with a microphone, quiet background noise is always added.
        """
        with self.lock:
            parts, self.pending = self.pending, []
        if not parts:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(parts + [np.zeros(4800, dtype=np.float32)])
        audio = audio + self.np_rng.normal(0, self.air.noise, len(audio)).astype(np.float32)
        return audio

    def transmit(self, samples):
        self.last_lost = self.air.broadcast(self, samples)
        time.sleep(len(samples) / config.SAMPLE_RATE * self.air.time_scale)

    def level(self):
        return 0.1 if time.time() < self.air.busy_until else self.air.noise

    overflows = 0

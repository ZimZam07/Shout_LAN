"""
audio_io.py - PHYSICAL LAYER hardware access: the real speaker and microphone.

Important finding (see docs/TESTING.md, M1): on Windows laptops the normal
("shared") microphone path runs noise suppression that treats our steady
tones as background noise and removes them. Opening the microphone through
WASAPI in *exclusive* mode bypasses that processing, so it is the default.
"""

import collections
import queue
import threading
import time

import numpy as np
import sounddevice as sd

from shout_lan import config

# Audio path settings (chosen by measurements, docs/TESTING.md M6)
OUT_LATENCY = "high"      # speaker buffer: "low" (PortAudio default) or "high"
IN_LATENCY = "high"       # microphone buffer
OUT_EXCLUSIVE = True      # WASAPI exclusive mode for the speaker: Windows shared-mode playback
                          # dropped whole buffers mid-frame (18.6% BER vs 0% exclusive, TESTING.md M6)


class AudioError(Exception):
    """Raised with a human-readable message when an audio device can't be used."""


# ---------------------------------------------------------------- device lists
def _hostapi_name(dev):
    return sd.query_hostapis(dev["hostapi"])["name"]


def list_devices():
    """
    Return the real input/output devices found on this laptop, e.g.
    {"inputs": [{"index": 9, "name": "...", "api": "Windows WASAPI"}], "outputs": [...]}.
    Nothing is invented: if Windows reports no microphone the list is empty.
    """
    inputs, outputs = [], []
    for index, dev in enumerate(sd.query_devices()):
        item = {"index": index, "name": dev["name"], "api": _hostapi_name(dev)}
        if dev["max_input_channels"] > 0:
            inputs.append(item)
        if dev["max_output_channels"] > 0:
            outputs.append(item)
    return {"inputs": inputs, "outputs": outputs}


def default_devices():
    """
    Pick sensible defaults: the WASAPI version of the microphone and speaker
    (WASAPI is needed for exclusive mode). Falls back to Windows' defaults.
    """
    devs = sd.query_devices()
    mic = spk = None
    for index, dev in enumerate(devs):
        if _hostapi_name(dev) != "Windows WASAPI":
            continue
        if mic is None and dev["max_input_channels"] > 0:
            mic = index
        if spk is None and dev["max_output_channels"] > 0:
            spk = index
    default_in, default_out = sd.default.device
    if mic is None:
        mic = default_in
    if spk is None:
        spk = default_out
    return mic, spk


def check_device(device, kind):
    """Raise AudioError with a clear message if `device` doesn't exist or can't do `kind`."""
    try:
        dev = sd.query_devices(device, kind=kind) if device is None else sd.query_devices(device)
    except Exception as exc:
        raise AudioError(f"Audio device {device} does not exist ({exc})") from exc
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    if dev[key] < 1:
        raise AudioError(f"Device {device} ({dev['name']}) has no {kind} channels")
    return dev


def _is_wasapi(device):
    return device is not None and _hostapi_name(sd.query_devices(device)) == "Windows WASAPI"


def _channels(device, kind):
    dev = sd.query_devices(device)
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    if dev[key] < 1:
        raise AudioError(f"Device {device} ({dev['name']}) has no {kind} channels")
    return min(dev[key], 2)


# ---------------------------------------------------------------- microphone
class Microphone:
    """
    Continuous microphone capture.

    sounddevice calls `_callback` from its own audio thread every few
    milliseconds with a new chunk. We only copy the chunk into a queue
    (producer); the receiver thread takes chunks out (consumer). Doing real
    work inside the callback would make the audio driver drop samples.
    """

    def __init__(self, device=None, exclusive=True, block_size=2048):
        check_device(device, "input")
        self.device = device
        self.exclusive = exclusive and _is_wasapi(device)
        self.block_size = block_size
        self.chunks = queue.Queue()
        self.overflows = 0               # how many times the driver lost audio
        self.level = 0.0                 # RMS of the latest chunk (for the UI meter)
        self.stream = None

    def _callback(self, data, frames, time_info, status):
        if status.input_overflow:
            self.overflows += 1
        mono = data[:, 0].copy()
        self.level = float(np.sqrt(np.mean(mono * mono)))
        self.chunks.put(mono)

    def start(self):
        try:
            channels = _channels(self.device, "input")
            extra = sd.WasapiSettings(exclusive=True) if self.exclusive else None
            self.stream = sd.InputStream(device=self.device, channels=channels,
                                         samplerate=config.SAMPLE_RATE, dtype="float32",
                                         blocksize=self.block_size, callback=self._callback,
                                         extra_settings=extra, latency=IN_LATENCY)
            self.stream.start()
        except Exception as exc:          # PortAudioError, ValueError, ...
            self.stream = None
            raise AudioError(f"Could not open microphone {self.device}: {exc}") from exc

    def stop(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            finally:
                self.stream = None

    def read_available(self):
        """All audio captured since the last call, as one numpy array."""
        parts = []
        while True:
            try:
                parts.append(self.chunks.get_nowait())
            except queue.Empty:
                break
        if not parts:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(parts)


# ---------------------------------------------------------------- interference generator
NOISE_BAND = (2000, 4000)     # Hz: covers both BFSK tones (2500 / 3500 Hz)
NOISE_FULL_RMS = 0.35         # level 1.0 = as loud as our tones at the default volume 0.5
# "clicks" (ClickGenerator): burst length and repeat interval in samples (48 kHz),
# chosen offline against the interleaver geometry (docs/TESTING.md, "Clicks")
CLICK_LEN = 864               # 18 ms: shorter than one 20 ms bit
CLICK_INTERVAL = 26 * 960     # 520 ms = 26 bit times
NOISE_KINDS = ("hiss", "clicks")
DEFAULT_NOISE_KIND = "clicks"  # what the demo uses
# default level per kind (0..1). hiss: 1.0 = noise RMS equal to our tones;
# clicks: 1.0 = burst peak at full scale
DEFAULT_NOISE_LEVEL = {"hiss": 0.25, "clicks": 0.8}


def check_noise_kind(kind):
    if kind not in NOISE_KINDS:
        raise ValueError(f"noise kind must be one of {list(NOISE_KINDS)}")
    return kind


class NoiseGenerator:
    """
    GENERATED interference for demonstrations: random (Gaussian) noise passed
    through a band-pass filter, so its energy sits on top of our tone band
    like a noisy room would - but repeatably, at a chosen level.
    The filter memory is kept between calls, so consecutive blocks join up
    into one continuous noise with no clicks.
    """

    def __init__(self, band=NOISE_BAND, sample_rate=config.SAMPLE_RATE, seed=None):
        from scipy.signal import butter
        self.sos = butter(4, band, btype="band", fs=sample_rate, output="sos")
        self.zi = np.zeros((self.sos.shape[0], 2))
        self.rng = np.random.default_rng(seed)
        # white noise of RMS 1 keeps this fraction of its power after the filter
        self.norm = np.sqrt(sample_rate / 2 / (band[1] - band[0]))
        self.band = band

    def block(self, n, level):
        """n samples of band-limited noise with RMS = level * NOISE_FULL_RMS."""
        from scipy.signal import sosfilt
        white = self.rng.normal(0, 1, n)
        out, self.zi = sosfilt(self.sos, white, zi=self.zi)
        return (out * self.norm * level * NOISE_FULL_RMS).astype(np.float32)


class ClickGenerator:
    """
    GENERATED impulsive interference ("clicks"): short loud bursts at our two tone
    frequencies, repeating at a fixed interval - like a ticking or clicking
    machine next to the laptops.

    This is deliberately the kind of noise that forward error correction with
    interleaving handles well, and the opposite of what plain frames survive:
      - each burst is shorter than one bit (CLICK_LEN), so it damages at most a
        few ADJACENT bits, which the interleaver sends to different codewords;
      - the interval (CLICK_INTERVAL) is chosen against the interleaver geometry
        so that two bursts never land in the same Hamming(7,4) codeword of a
        block (docs/TESTING.md, "Clicks"), so each codeword has at most one
        error and is repaired;
      - a plain frame is hit several times, and every hit can flip a bit,
        which the CRC then rejects.
    Each burst uses ONE of the two tone frequencies (chosen at random) with a
    random phase. It knows nothing about the frames being sent.
    """

    def __init__(self, sample_rate=config.SAMPLE_RATE, seed=None,
                 length=None, interval=None):
        from scipy.signal.windows import tukey
        self.sample_rate = sample_rate
        self.length = length or CLICK_LEN
        self.interval = interval or CLICK_INTERVAL
        self.window = tukey(self.length, 0.2)
        self.rng = np.random.default_rng(seed)
        self.pos = int(self.rng.integers(0, self.interval))   # samples since the last burst start
        self.freqs = (config.FREQ_0, config.FREQ_1)
        if self.pos < self.length:
            self._cur = self._burst()

    def _burst(self):
        f = self.freqs[int(self.rng.integers(0, 2))]
        t = np.arange(self.length) / self.sample_rate
        return self.window * np.sin(2 * np.pi * f * t + self.rng.uniform(0, 2 * np.pi))

    def block(self, n, level):
        """n samples of the click train; burst peak amplitude = level (1.0 = full scale)."""
        out = np.zeros(n, dtype=np.float64)
        i = 0
        while i < n:
            # self.pos = samples since the current period started; a burst fills [0, length)
            if self.pos == 0:
                self._cur = self._burst()
            if self.pos < self.length:
                take = min(self.length - self.pos, n - i)
                out[i:i + take] = self._cur[self.pos:self.pos + take]
            else:
                take = min(self.interval - self.pos, n - i)
            self.pos = (self.pos + take) % self.interval
            i += take
        return (out * level).astype(np.float32)


# ---------------------------------------------------------------- speaker
class Speaker:
    """
    Plays audio through the real speaker. play() blocks until the sound is finished.

    The output stream is opened once and kept open: opening a new stream for
    every frame (sd.play) added ~1.1 s of delay per transmission in our
    measurements, which would slow down every ACK.
    """

    def __init__(self, device=None):
        check_device(device, "output")
        self.device = device
        self.lock = threading.Lock()     # only one transmission at a time (half-duplex)
        self.stream = None
        self.channels = 1
        self.underflows = 0              # times the output ran dry while playing (= a gap in the sound)
        self.mode = "closed"             # "exclusive" / "shared" once open
        self.warning = None              # set if we had to fall back to shared mode
        self.write_lock = threading.Lock()   # only one writer to the stream at a time
        # interference generator state (see set_noise)
        self.noise_on = False
        self.noise_level = 0.0
        self.noise_kind = DEFAULT_NOISE_KIND
        self.noise_gen = NoiseGenerator()
        self.click_gen = ClickGenerator()
        self._mix_cond = threading.Condition()
        self._mix_items = collections.deque()
        self._mix_thread = None

    # The warm-up silence must be taken by the device within this time. On the
    # Windows test laptop an exclusive output stream started right after the
    # exclusive microphone sometimes never began playing: the stream said
    # "active" but its buffer never drained, so a blocking write waited forever
    # and the node hung at start-up (docs/TESTING.md, 2026-11-18).
    WARMUP_SAMPLES = 2400
    WARMUP_TIMEOUT = 2.0

    def _open_stream(self, exclusive):
        extra = sd.WasapiSettings(exclusive=True) if exclusive else None
        stream = sd.OutputStream(device=self.device, channels=self.channels,
                                 samplerate=config.SAMPLE_RATE, dtype="float32",
                                 latency=OUT_LATENCY, extra_settings=extra)
        stream.start()
        # warm-up silence, written only as fast as the device takes it (a
        # blocking write could wait forever on a device that is not playing)
        left = self.WARMUP_SAMPLES
        deadline = time.time() + self.WARMUP_TIMEOUT
        while left > 0:
            room = min(stream.write_available, left)
            if room > 0:
                stream.write(np.zeros((room, self.channels), dtype=np.float32))
                left -= room
            elif time.time() > deadline:
                try:
                    stream.abort()
                    stream.close()
                except Exception:
                    pass
                raise AudioError(f"speaker {self.device} opened but did not start playing "
                                 f"within {self.WARMUP_TIMEOUT:.0f} s")
            else:
                time.sleep(0.005)
        return stream

    def open(self):
        """
        Reserve the speaker. Exclusive mode is preferred: Windows' shared-mode
        playback dropped whole buffers mid-frame (TESTING.md M6). If another
        program is using the speaker, exclusive mode is refused - then we fall
        back to shared mode and say so (reliability is lower in that mode).
        """
        if self.stream is not None:
            return
        self.channels = _channels(self.device, "output")
        if OUT_EXCLUSIVE and _is_wasapi(self.device):
            error = None
            for attempt in range(3):         # a stream that did not start is retried
                try:
                    self.stream = self._open_stream(True)
                    self.mode, self.warning = "exclusive", None
                    return
                except Exception as exc:
                    error = exc
                    time.sleep(0.5)
            self.warning = (f"Speaker exclusive mode failed ({error}) - another program is "
                            f"probably playing audio. Using shared mode: expect more errors. "
                            f"Close other audio apps and restart the engine.")
        try:
            self.stream = self._open_stream(False)
        except AudioError:
            raise
        except Exception as exc:
            raise AudioError(f"Could not open speaker {self.device}: {exc}") from exc
        self.mode = "shared"

    def play(self, samples):
        with self.lock:
            item = self._submit_to_mixer(samples)
            if item is not None:
                # the interference generator is running: the mixer thread plays this
                # frame on top of the noise; wait until all of it has been written
                item["written"].wait()
                if item["error"]:
                    raise AudioError(f"Could not play on speaker {self.device}: {item['error']}")
                time.sleep(self.stream.latency if self.stream is not None else 0)
                return
            try:
                with self.write_lock:
                    if self.stream is None:
                        self.open()
                    data = np.asarray(samples, dtype=np.float32)
                    data = np.repeat(data[:, None], self.channels, axis=1)
                    if self.stream.write(data):      # blocks until the data is in the output buffer
                        self.underflows += 1             # True = the speaker ran out of samples (gap)
                time.sleep(self.stream.latency)  # ...and until the buffer has actually played
            except Exception as exc:
                self.close()
                raise AudioError(f"Could not play on speaker {self.device}: {exc}") from exc

    # ------------------------------------------------ interference generator (mixer)
    # While the generator is on, ONE mixer thread owns the speaker stream and
    # writes fixed blocks back to back: noise in every block, plus the next
    # part of any frame waiting to be played. Frame samples are added at
    # consecutive positions of a continuous stream, so the frame keeps exactly
    # its sample timing (no gaps, no jumps); the noise never stops for a frame.
    # When the generator is off this thread does not exist and play() writes
    # directly, exactly as before.
    MIX_BLOCK = 1024

    def set_noise(self, on, level, kind=None):
        with self._mix_cond:
            self.noise_on = bool(on)
            self.noise_level = max(0.0, min(1.0, float(level)))
            if kind is not None:
                self.noise_kind = check_noise_kind(kind)
            if self.noise_on and self._mix_thread is None:
                self._mix_thread = threading.Thread(target=self._mixer, daemon=True, name="speaker-mixer")
                self._mix_thread.start()
            self._mix_cond.notify_all()

    def _submit_to_mixer(self, samples):
        with self._mix_cond:
            if self._mix_thread is None:
                return None
            item = {"samples": np.asarray(samples, dtype=np.float32), "pos": 0,
                    "written": threading.Event(), "error": None}
            self._mix_items.append(item)
            return item

    def _mixer(self):
        n = self.MIX_BLOCK
        level_now = 0.0
        while True:
            with self._mix_cond:
                if not self.noise_on and not self._mix_items and level_now == 0.0:
                    self._mix_thread = None
                    return
                target = self.noise_level if self.noise_on else 0.0
                gen = self.click_gen if self.noise_kind == "clicks" else self.noise_gen
                block = np.zeros(n, dtype=np.float32)
                finished = []
                filled = 0
                while self._mix_items and filled < n:
                    item = self._mix_items[0]
                    part = item["samples"][item["pos"]:item["pos"] + n - filled]
                    block[filled:filled + len(part)] += part
                    filled += len(part)
                    item["pos"] += len(part)
                    if item["pos"] >= len(item["samples"]):
                        finished.append(self._mix_items.popleft())
            # level ramps over one block when switched, so the noise never clicks
            levels = np.linspace(level_now, target, n, dtype=np.float32)
            block += gen.block(n, 1.0) * levels
            level_now = target
            np.clip(block, -1.0, 1.0, out=block)
            error = None
            try:
                with self.write_lock:
                    if self.stream is None:
                        self.open()
                    if self.stream.write(np.repeat(block[:, None], self.channels, axis=1)):
                        self.underflows += 1
            except Exception as exc:
                error = str(exc)
            for item in finished:
                item["written"].set()
            if error:
                with self._mix_cond:
                    self.noise_on = False
                    self.warning = f"Interference generator stopped: {error}"
                    for item in self._mix_items:
                        item["error"] = error
                        item["written"].set()
                    self._mix_items.clear()
                    self._mix_thread = None
                return

    def close(self):
        with self._mix_cond:
            self.noise_on = False
            thread = self._mix_thread
        if thread is not None:
            thread.join(timeout=2)
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None
            self.mode = "closed"


def system_output_volume():
    """
    The operating system's output volume (0-100) if it can be read, else None.
    Measurement tools record it at the start and end of every run: a volume
    changed by hand in the middle of a run makes that run's numbers invalid.
    (Only implemented for macOS; Windows returns None.)
    """
    import subprocess
    import sys
    if sys.platform != "darwin":
        return None
    try:
        out = subprocess.run(["osascript", "-e", "output volume of (get volume settings)"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        return int(out)
    except Exception:
        return None


def record_while_playing(signal, mic_device=None, speaker_device=None,
                         exclusive=True, lead=0.3, tail=0.4):
    """
    Loopback helper for diagnostics: start recording, play `signal`, keep
    recording a little longer, and return everything the microphone heard.
    """
    mic = Microphone(mic_device, exclusive=exclusive)
    spk = Speaker(speaker_device)
    mic.start()
    try:
        time.sleep(lead)
        spk.play(signal)
        time.sleep(tail)
    finally:
        mic.stop()
        spk.close()
    return mic.read_available(), mic

"""
audio_io.py - PHYSICAL LAYER hardware access: the real speaker and microphone.

Important finding (see docs/TESTING.md, M1): on Windows laptops the normal
("shared") microphone path runs noise suppression that treats our steady
tones as background noise and removes them. Opening the microphone through
WASAPI in *exclusive* mode bypasses that processing, so it is the default.
"""

import queue
import threading
import time

import numpy as np
import sounddevice as sd

from shout_lan import config


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
                                         extra_settings=extra)
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


# ---------------------------------------------------------------- speaker
class Speaker:
    """
    Plays audio through the real speaker. play() blocks until the sound is finished.

    The output stream is opened once and kept open: opening a new stream for
    every frame (sd.play) added ~1.1 s of delay per transmission in our
    measurements, which would slow down every ACK.
    """

    def __init__(self, device=None):
        self.device = device
        self.lock = threading.Lock()     # only one transmission at a time (half-duplex)
        self.stream = None
        self.channels = 1

    def _open(self):
        self.channels = _channels(self.device, "output")
        self.stream = sd.OutputStream(device=self.device, channels=self.channels,
                                      samplerate=config.SAMPLE_RATE, dtype="float32")
        self.stream.start()
        self.stream.write(np.zeros((2400, self.channels), dtype=np.float32))   # warm-up silence

    def play(self, samples):
        with self.lock:
            try:
                if self.stream is None:
                    self._open()
                data = np.asarray(samples, dtype=np.float32)
                data = np.repeat(data[:, None], self.channels, axis=1)
                self.stream.write(data)          # blocks until the data is in the output buffer
                time.sleep(self.stream.latency)  # ...and until the buffer has actually played
            except Exception as exc:
                self.close()
                raise AudioError(f"Could not play on speaker {self.device}: {exc}") from exc

    def close(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None


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

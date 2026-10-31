"""
config.py - shared settings for every part of SHOUT LAN.

Both laptops MUST use the same physical-layer values (sample rate, tone
frequencies, bit time, preamble), exactly like two network cards must agree
on the same signalling rules before they can talk.
"""

# ---------------- Physical layer (sound) ----------------
SAMPLE_RATE = 48000          # audio samples per second (both laptops' default)

# BFSK: bit 0 is sent as one tone, bit 1 as another tone.
FREQ_0 = 2500                # Hz used for bit 0  (chosen by calibration, TESTING.md M6;
FREQ_1 = 3500                # Hz used for bit 1   run Calibrate in a new room!)

BIT_TIME = 0.020             # seconds per bit -> 1 / 0.020 = 50 bits per second

# The preamble is a short "chirp" (a tone sliding from low to high pitch).
# The receiver searches the microphone signal for this known shape to find
# exactly where a frame starts (frame synchronisation).
PREAMBLE_TIME = 0.100        # seconds
PREAMBLE_F_START = 1000      # Hz
PREAMBLE_F_END = 4000        # Hz
GAP_TIME = 0.010             # short silence between the preamble and the first bit

TX_VOLUME = 0.5              # 0.0 - 1.0, amplitude of generated tones

# How similar (0..1) a piece of audio must be to the preamble to count as a frame start.
DETECT_THRESHOLD = 0.35

# ---------------- Local control link (TCP) ----------------
CONTROL_HOST = "127.0.0.1"   # local only: the UI <-> engine link never leaves this laptop
CONTROL_PORT = 5000

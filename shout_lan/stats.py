"""
stats.py - performance counters. Every number shown in the UI comes from here,
and every number here is counted by the engine when the event really happens.
"""

import threading
import time


class Stats:
    COUNTERS = ("frames_tx", "data_frames_tx", "frames_rx_ok", "crc_failures", "acks_tx",
                "acks_rx", "retransmissions", "timeouts", "duplicates", "messages_sent",
                "messages_delivered", "messages_failed", "messages_received",
                "payload_bytes_acked", "pings_sent", "pongs_received", "injected_events",
                "channel_busy_deferrals", "backoffs", "forwarded", "ttl_expired")

    def __init__(self, source_label):
        self.lock = threading.Lock()        # several threads update the counters
        self.source = source_label          # "Measured - acoustic" or "Measured - SIMULATED channel"
        self.reset()

    def reset(self):
        with self.lock:
            self.c = {name: 0 for name in self.COUNTERS}
            self.rtts = []                  # seconds, data frame -> ACK
            self.ping_rtts = []
            self.busy_time = 0.0            # seconds spent delivering messages
            self.started = time.time()

    def add(self, name, amount=1):
        with self.lock:
            self.c[name] += amount

    def add_rtt(self, seconds):
        with self.lock:
            self.rtts.append(seconds)

    def add_ping_rtt(self, seconds):
        with self.lock:
            self.ping_rtts.append(seconds)

    def add_busy_time(self, seconds):
        with self.lock:
            self.busy_time += seconds

    @staticmethod
    def _summary(values):
        if not values:
            return None
        return {"min": round(min(values), 3), "avg": round(sum(values) / len(values), 3),
                "max": round(max(values), 3), "count": len(values)}

    def snapshot(self):
        """A consistent copy of everything, plus derived metrics."""
        with self.lock:
            c = dict(self.c)
            rtts, pings, busy = list(self.rtts), list(self.ping_rtts), self.busy_time
        done = c["messages_delivered"] + c["messages_failed"]
        attempts = c["data_frames_tx"]
        return {
            "source": self.source,
            "counters": c,
            # message delivery ratio: messages confirmed / messages finished
            "delivery_ratio": round(c["messages_delivered"] / done, 3) if done else None,
            # frame loss: data frame transmissions that needed to be repeated
            "frame_loss": round(c["retransmissions"] / attempts, 3) if attempts else None,
            # goodput: useful bytes that were acknowledged, per second of sending
            "goodput_bps": round(c["payload_bytes_acked"] * 8 / busy, 2) if busy > 0 else None,
            "rtt": self._summary(rtts),
            "recent_rtts": [round(v, 2) for v in rtts[-30:]],
            "ping_rtt": self._summary(pings),
            "ping_loss": (round(1 - c["pongs_received"] / c["pings_sent"], 3)
                          if c["pings_sent"] else None),
        }

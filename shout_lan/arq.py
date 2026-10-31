"""
arq.py - DATA LINK reliability: ARQ (Automatic Repeat reQuest).

  RetransmissionTimer  : how long to wait for an ACK (adapts like TCP's timer)
  StopAndWaitSender    : send one frame, wait for its ACK, resend on timeout
  StopAndWaitReceiver  : ACK every good frame, deliver each frame only once

Selective Repeat is added in Milestone 6.
"""

import queue
import time

from shout_lan import frame as fr

MAX_RETRIES = 5           # after 1 + 5 attempts the message is reported as FAILED


class RetransmissionTimer:
    """
    Retransmission timeout (RTO) calculation, the same idea TCP uses (RFC 6298):
        SRTT   = smoothed average of measured waiting times
        RTTVAR = how much the measurements vary
        RTO    = SRTT + 4 * RTTVAR       (never below `minimum`)
    "Waiting time" = from the end of our transmission until the ACK arrived,
    so it does not depend on how long the data frame was.
    Karn's rule: a frame that was retransmitted gives no measurement, because
    we can't tell which copy the ACK belongs to.
    """

    def __init__(self, initial, minimum):
        self.initial = initial
        self.minimum = minimum
        self.srtt = None
        self.rttvar = None

    def sample(self, wait):
        if self.srtt is None:
            self.srtt = wait
            self.rttvar = wait / 2
        else:
            self.rttvar = 0.75 * self.rttvar + 0.25 * abs(self.srtt - wait)
            self.srtt = 0.875 * self.srtt + 0.125 * wait

    def timeout(self):
        if self.srtt is None:
            return self.initial
        return max(self.minimum, self.srtt + 4 * self.rttvar)


class StopAndWaitSender:
    """
    Stop-and-Wait ARQ:
      1. send frame k
      2. wait for ACK k (or a timeout)
      3. ACK received  -> go on with frame k+1
         timeout       -> send frame k again (up to MAX_RETRIES times)
    Only ONE frame is ever "in flight", which is simple but slow.
    """

    name = "Stop-and-Wait"

    def __init__(self, engine):
        self.engine = engine

    def send(self, msg_id, dst, pieces, base_flags=0, final=None, ftype=fr.DATA,
             origin=None, ttl=fr.DEFAULT_TTL):
        """Send all fragments of one message. Returns True only if every fragment was ACKed."""
        e = self.engine
        for index, (payload, flags) in enumerate(pieces):
            seq = e.next_seq()
            frame = fr.Frame(ftype, src=e.node_id, dst=dst, seq=seq, payload=payload,
                             flags=flags | base_flags,
                             origin=origin if origin is not None else e.node_id,
                             final=final if final is not None else dst, ttl=ttl)
            if not self._send_one(msg_id, frame, index, len(pieces)):
                return False
        return True

    def _send_one(self, msg_id, frame, index, total):
        e = self.engine
        for attempt in range(MAX_RETRIES + 1):
            if attempt:
                e.before_retransmit(attempt)
                e.stats.add("retransmissions")
                e.emit("retransmit", seq=frame.seq, attempt=attempt, msg_id=msg_id,
                       text=f"Retransmitting frame seq {frame.seq} (attempt {attempt + 1})")
            started = time.time()
            e.transmit(frame, retry=attempt, msg_id=msg_id)
            sent = time.time()
            timeout = e.timer.timeout() * e.time_scale
            ack = e.wait_for_ack(frame.dst, frame.seq, timeout)
            if ack is not None:
                if attempt == 0:                      # Karn's rule
                    e.timer.sample((time.time() - sent) / e.time_scale)
                e.stats.add_rtt(time.time() - started)
                e.emit("message_progress", msg_id=msg_id, done=index + 1, total=total,
                       text=f"Frame {index + 1}/{total} acknowledged (seq {frame.seq})")
                return True
            e.stats.add("timeouts")
            e.emit("timeout", seq=frame.seq, msg_id=msg_id, waited=round(timeout, 2),
                   text=f"No ACK for seq {frame.seq} after {timeout:.1f} s - timeout")
        return False


class StopAndWaitReceiver:
    """
    Receiver side of Stop-and-Wait for ONE neighbour.
    Every correct frame is ACKed - even a duplicate, because a duplicate
    means our previous ACK was lost and the sender is still waiting.
    A duplicate is NOT delivered again (exactly-once delivery).
    """

    def __init__(self):
        self.last_seq = None

    def accept(self, seq):
        """Returns True if this is a new frame (deliver it), False if it is a duplicate."""
        if seq == self.last_seq:
            return False
        self.last_seq = seq
        return True


def drain(q):
    """Empty a queue (helper used when starting a new wait)."""
    try:
        while True:
            q.get_nowait()
    except queue.Empty:
        pass


# =====================================================================
# Selective Repeat ARQ (sliding window, window size 4) - docs/SCOPE.md 10.1
# =====================================================================
WINDOW = 4
SEQ_SPACE = 256


def seq_offset(seq, base):
    """How far `seq` is after `base`, counting around the 0..255 circle."""
    return (seq - base) % SEQ_SPACE


class SelectiveRepeatSender:
    """
    Sender side:
      1. send every not-yet-acknowledged frame in the window (up to 4) back to
         back; the last one carries the POLL flag ("please answer with a SACK")
      2. wait for ONE SACK, which says exactly which frames arrived
      3. mark those frames acknowledged, slide the window forward, and send
         ONLY the missing frames plus any new ones that now fit
    On a timeout (the SACK or the POLL frame was lost) only the OLDEST
    unacknowledged frame is resent, with POLL; the receiver then answers with
    a fresh SACK showing everything it has.
    """

    name = "Selective Repeat"

    def __init__(self, engine):
        self.engine = engine

    def send(self, msg_id, dst, pieces, base_flags=0, final=None, origin=None,
             ttl=fr.DEFAULT_TTL):
        e = self.engine
        n = len(pieces)
        frames = [fr.Frame(fr.SRDATA, src=e.node_id, dst=dst, seq=e.next_seq(), payload=p,
                           flags=f | base_flags,
                           origin=origin if origin is not None else e.node_id,
                           final=final if final is not None else dst, ttl=ttl)
                  for p, f in pieces]
        acked = [False] * n
        sent_before = [False] * n
        base = 0                         # index of the oldest unacknowledged frame
        timeouts_in_a_row = 0
        only_oldest = False
        drop_index = e._take_injection("drop_frame")      # e.g. 3 = lose the 3rd frame once
        while base < n:
            in_window = list(range(base, min(base + WINDOW, n)))
            todo = [i for i in in_window if not acked[i]]
            burst = todo[:1] if only_oldest else todo
            e.emit("sr_window", msg_id=msg_id, window=[frames[i].seq for i in in_window],
                   sending=[frames[i].seq for i in burst],
                   text=f"Window [{', '.join(str(frames[i].seq) for i in in_window)}] - "
                        f"sending seq {', '.join(str(frames[i].seq) for i in burst)}")
            burst_start = time.time()
            had_retransmission = False
            for k, i in enumerate(burst):
                f = frames[i]
                if k == len(burst) - 1:
                    f.flags |= fr.F_POLL
                else:
                    f.flags &= ~fr.F_POLL
                if sent_before[i]:
                    had_retransmission = True
                    e.stats.add("retransmissions")
                    e.emit("retransmit", seq=f.seq, msg_id=msg_id,
                           text=f"Selective Repeat: resending ONLY seq {f.seq}")
                first_time = not sent_before[i]
                sent_before[i] = True
                if first_time and drop_index and i == drop_index - 1:
                    e.stats.add("injected_events")
                    e.emit("frame_dropped", seq=f.seq, msg_id=msg_id,
                           text=f"Frame seq {f.seq} NOT sent (injected frame loss)")
                    continue
                e.transmit(f, retry=0 if first_time else 1, msg_id=msg_id)
            sent_at = time.time()
            timeout = e.timer.timeout() * e.time_scale
            sack = e.wait_for_sack(dst, timeout)
            if sack is None:
                timeouts_in_a_row += 1
                e.stats.add("timeouts")
                e.emit("timeout", msg_id=msg_id, waited=round(timeout, 2),
                       text=f"No SACK after {timeout:.1f} s - timeout; resending the oldest "
                            f"missing frame (seq {frames[base].seq}) with POLL")
                if timeouts_in_a_row > MAX_RETRIES:
                    return False
                only_oldest = True
                e.before_retransmit(timeouts_in_a_row)
                continue
            if not had_retransmission:                        # Karn's rule
                e.timer.sample((time.time() - sent_at) / e.time_scale)
            e.stats.add_rtt(time.time() - burst_start)
            timeouts_in_a_row = 0
            only_oldest = False
            newly = self.apply_sack(sack, frames, acked, base)
            missing = [frames[i].seq for i in in_window if not acked[i]]
            e.emit("sack_rx", msg_id=msg_id, acked=newly, missing=missing,
                   text=f"SACK says received seq {newly or 'nothing new'}"
                        + (f"; still missing seq {missing}" if missing else ""))
            while base < n and acked[base]:
                base += 1
                e.emit("message_progress", msg_id=msg_id, done=base, total=n,
                       text=f"Frame {base}/{n} acknowledged (seq {frames[base - 1].seq})")
        return True

    @staticmethod
    def apply_sack(sack, frames, acked, base):
        """Mark the frames this SACK acknowledges. Returns the newly acknowledged seqs."""
        ref = sack.seq
        bitmap = sack.payload[0] if sack.payload else 0
        cumulative = bool(sack.flags & fr.F_FIRST)
        newly = []
        for i in range(base, min(base + WINDOW, len(frames))):
            if acked[i]:
                continue
            ahead = seq_offset(frames[i].seq, ref)       # frame is `ahead` places after ref
            behind = seq_offset(ref, frames[i].seq)      # frame is `behind` places before ref
            if (cumulative and 0 < behind <= 2 * WINDOW) or (ahead < 8 and (bitmap >> ahead) & 1):
                acked[i] = True
                newly.append(frames[i].seq)
        return newly


class SelectiveRepeatReceiver:
    """
    Receiver side for ONE neighbour:
      - frames inside the window [rcv_base, rcv_base+3] are BUFFERED even if an
        earlier one is missing (out-of-order buffering)
      - frames are passed up strictly in order: only when the frame at
        rcv_base is present, then rcv_base moves forward. A frame is passed up
        exactly when rcv_base moves past it, so it can never be delivered twice.
      - duplicates (already buffered, or up to 8 behind rcv_base = already
        delivered) are reported in the SACK, but their data is thrown away.
      - a FIRST fragment that is not a duplicate starts a new message (this
        also recovers if the sender gave up on a previous message).
    """

    def __init__(self):
        self.rcv_base = None
        self.buffer = {}               # seq -> frame (out-of-order frames waiting)
        self.duplicates = 0

    def _is_old(self, seq):
        return self.rcv_base is not None and 0 < seq_offset(self.rcv_base, seq) <= 2 * WINDOW

    def accept(self, f):
        """Store one DATA-SR frame. Returns the frames that can now be delivered, in order."""
        if self._is_old(f.seq) or f.seq in self.buffer:
            self.duplicates += 1
            return []
        if f.flags & fr.F_FIRST and f.seq != self.rcv_base:
            self.rcv_base = f.seq                       # a new message starts here
            self.buffer = {s: x for s, x in self.buffer.items() if seq_offset(s, f.seq) < WINDOW}
        if self.rcv_base is None:
            if len(self.buffer) < 2 * WINDOW:
                self.buffer[f.seq] = f                  # can't place it yet - keep it for later
            return []
        if seq_offset(f.seq, self.rcv_base) < WINDOW:
            self.buffer[f.seq] = f
        ready = []
        while self.rcv_base in self.buffer:
            ready.append(self.buffer.pop(self.rcv_base))
            self.rcv_base = (self.rcv_base + 1) % SEQ_SPACE
        return ready

    def sack(self):
        """(reference seq, bitmap byte, cumulative?) describing what has arrived."""
        if self.rcv_base is None:
            ref = min(self.buffer) if self.buffer else 0
            cumulative = False
        else:
            ref, cumulative = self.rcv_base, True
        bitmap = 0
        for s in self.buffer:
            off = seq_offset(s, ref)
            if off < 8:
                bitmap |= 1 << off
        return ref, bitmap, cumulative

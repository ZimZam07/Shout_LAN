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

    def send(self, msg_id, dst, pieces, base_flags=0, final=None, ftype=fr.DATA):
        """Send all fragments of one message. Returns True only if every fragment was ACKed."""
        e = self.engine
        for index, (payload, flags) in enumerate(pieces):
            seq = e.next_seq()
            frame = fr.Frame(ftype, src=e.node_id, dst=dst, seq=seq, payload=payload,
                             flags=flags | base_flags, origin=e.node_id,
                             final=final if final is not None else dst)
            if not self._send_one(msg_id, frame, index, len(pieces)):
                return False
        return True

    def _send_one(self, msg_id, frame, index, total):
        e = self.engine
        for attempt in range(MAX_RETRIES + 1):
            if attempt:
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

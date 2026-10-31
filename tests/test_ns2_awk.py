"""
Checks ns2/analysis.awk against a tiny HAND-WRITTEN trace (ns2/sample_trace.tr).
This verifies the analysis script's arithmetic only - it is NOT an NS-2 simulation.

Expected values worked out by hand:
  flow 1 (TCP): packets 1 and 2 reach node 2 at 1.05 s and 1.15 s, sent at 1.0 s and 1.1 s
      -> 2 packets, 2080 bytes, throughput 2080*8 / (1.15-1.05) / 1000 = 166.40 kbit/s,
         average delay (0.05 + 0.05) / 2 = 50.00 ms, 0 drops
  flow 2 (CBR): packet 3 reaches node 2 at 1.08 s (sent 1.0 s) -> delay 80.00 ms;
      packet 4 is dropped at 1.12 s -> 1 drop; only one packet -> throughput 0
  the ACK (to node 0) is ignored
"""

import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AWK = shutil.which("awk") or shutil.which("gawk")


@pytest.mark.skipif(AWK is None, reason="awk not available")
def test_analysis_awk_on_hand_written_trace():
    out = subprocess.run([AWK, "-f", "analysis.awk", "sample_trace.tr"], cwd=os.path.join(ROOT, "ns2"),
                         capture_output=True, text=True, check=True).stdout
    rows = {line.split()[0]: line.split() for line in out.splitlines()
            if line.split() and line.split()[0] in ("1", "2")}
    assert rows["1"][1:] == ["2", "2080", "166.40", "50.00", "0"]
    assert rows["2"][1:] == ["1", "1000", "0.00", "80.00", "1"]
    assert "total drops: 1" in out

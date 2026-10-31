# NS-2 module (separate from SHOUT LAN's acoustic engine)

**Status: OPTIONAL / PENDING academic requirement, and NOT YET RUN.**

NS-2 is not installed on the development laptop: installing it in WSL needs the user's sudo password. **No simulation results exist yet**, and none are shown anywhere in the project.

This module is a *simulation of ordinary wired links*, as in Lab Assignment 8. It doesn't simulate sound. It models SHOUT LAN's relay topology: Laptop A → Laptop B (relay) → Laptop C, plus a second sender, so that the relay link becomes a bottleneck.

## Files
| File | Purpose |
|---|---|
| `shoutlan_topology.tcl` | 4 nodes; 2 Mb access links; a 1 Mb, 20 ms bottleneck B→C with a 10-packet DropTail queue; TCP/FTP A→C and UDP/CBR (0.8 Mb/s) other→C; writes `out.tr` and `out.nam` |
| `analysis.awk` | Per-flow packets, bytes, throughput, average end-to-end delay and drops at the receiving node, read from the trace (Lab Assignment 8 AWK style) |
| `sample_trace.tr` | A tiny **hand-written** trace used only to unit-test `analysis.awk` (`tests/test_ns2_awk.py`). It is **not** simulation output |

## Run (WSL Ubuntu)
```bash
sudo apt install -y ns2 nam          # once (needs your password)
cd /mnt/c/Users/<you>/Downloads/Shout_LAN/ns2
ns shoutlan_topology.tcl             # -> out.tr, out.nam
awk -f analysis.awk out.tr           # metrics at node 2 (Laptop C)
nam out.nam                          # animation (needs a WSL GUI / WSLg)
```

## What to expect (qualitatively, to check against the real output)
- The bottleneck receives up to 2 + 2 Mb/s but can send only 1 Mb/s, so the B→C queue fills and **drops appear** (`d` events).
- UDP/CBR doesn't slow down. TCP reacts to the drops with congestion control, so its throughput is lower and varies (TCP congestion control, Module 3).
- End-to-end delay is at least 10 ms + 20 ms propagation, plus transmission and queueing delay.

Fill in the real numbers only after running the simulation.

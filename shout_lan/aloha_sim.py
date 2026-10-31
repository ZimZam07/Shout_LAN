"""
aloha_sim.py - SIMULATION of many nodes sharing one channel with ALOHA.

This is NOT acoustic communication. With only two laptops we cannot create
heavy traffic, so - like in the textbook - we simulate many nodes and
compare the measured throughput with the theoretical formulas:

    Pure ALOHA     S = G * e^(-2G)    (max 18.4 % at G = 0.5)
    Slotted ALOHA  S = G * e^(-G)     (max 36.8 % at G = 1.0)

G = offered load (frames attempted per frame-time, from all nodes together)
S = throughput   (frames per frame-time that get through without collision)
"""

import math

import numpy as np


def theory_pure(g):
    return g * math.exp(-2 * g)


def theory_slotted(g):
    return g * math.exp(-g)


def simulate_pure(g, frame_times=20000, seed=0):
    """
    Frames start at random (Poisson) times. A frame survives only if no other
    frame starts less than one frame-time before or after it (vulnerable
    period = 2 frame-times).
    """
    rng = np.random.default_rng(seed)
    n = rng.poisson(g * frame_times)
    starts = np.sort(rng.uniform(0, frame_times, n))
    gap_before = np.diff(starts, prepend=-np.inf)
    gap_after = np.diff(starts, append=np.inf)
    ok = (gap_before >= 1.0) & (gap_after >= 1.0)
    return ok.sum() / frame_times


def simulate_slotted(g, slots=20000, seed=0):
    """Frames may only start at slot boundaries; a slot succeeds if exactly one frame uses it."""
    rng = np.random.default_rng(seed)
    attempts = rng.poisson(g, slots)
    return np.count_nonzero(attempts == 1) / slots


def sweep(loads=None, frame_times=20000, seed=0):
    loads = loads or [round(0.1 * i, 1) for i in range(1, 31)]
    return {"kind": "Simulation", "loads": loads,
            "pure": [round(simulate_pure(g, frame_times, seed + i), 4) for i, g in enumerate(loads)],
            "slotted": [round(simulate_slotted(g, frame_times, seed + i), 4)
                        for i, g in enumerate(loads)],
            "pure_theory": [round(theory_pure(g), 4) for g in loads],
            "slotted_theory": [round(theory_slotted(g), 4) for g in loads]}

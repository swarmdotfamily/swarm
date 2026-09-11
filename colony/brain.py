"""
Two brains with one interface.

ConnectomeBrain  the real thing: flycoin's 165,122-neuron LIF over the male
                 CNS, market features on labellar/olfactory/looming neurons,
                 verdict read from MN9 (eat) and DNp01 (flee). ~7 s a decision.

StubBrain        a cheap stand-in with the same call shape, used ONLY to tune
                 the ecology (birth/upkeep/death constants) over thousands of
                 ticks in seconds. It is not a fly and nothing it says goes
                 near a chain. Its "genome" has the same structure so genome
                 code is exercised identically.

verdict()        the one rule that turns firing rates into an action, shared.
"""
import sys
import numpy as np

from .config import FLYCOIN, Ecology
from .cloud import load_keep

# what the five market features land on, and what those neurons are in a fly
SENSES = {
    "appetite_primary":   ("sweet taste", "LB3c / LB1e", "momentum"),
    "appetite_secondary": ("sweet taste, secondary", "LB4a / LB1d / LB3b", "liquidity"),
    "aversive":           ("bitter taste", "LB1c", "danger"),
    "threat":             ("looming", "LPLC2", "crash"),
    "social":             ("cVA pheromone", "ORN_DA1", "social"),
}
REGIONS = (
    ("optic lobes", ("ol_intrinsic", "ol_sensory", "visual_projection", "visual_centrifugal")),
    ("central brain", ("cb_intrinsic", "cb_endocrine", "cb_efferent")),
    ("brain sensory", ("cb_sensory", "cb_sensory_tbc")),
    ("descending", ("descending_neuron", "efferent_descending", "sensory_descending")),
    ("ascending", ("ascending_neuron", "sensory_ascending", "efferent_ascending")),
    ("ventral cord", ("vnc_intrinsic", "vnc_endocrine", "vnc_efferent", "vnc_sensory", "vnc_tbc")),
    ("motor", ("cb_motor", "vnc_motor")),
)


def verdict(rates, eco: Ecology):
    """
    DNp01 (Giant Fiber, escape) above the veto -> SELL, whatever else fires.
    MN9 (proboscis extension, eat) above threshold -> BUY.
    Otherwise HOLD. Thresholds measured in flycoin/io_map.py.
    """
    if rates["ABORT"] >= eco.abort_hz:
        return "SELL"
    if rates["LAUNCH"] >= eco.launch_hz:
        return "BUY"
    return "HOLD"


class ConnectomeBrain:
    name = "connectome"

    def __init__(self, eco: Ecology):
        if str(FLYCOIN) not in sys.path:
            sys.path.insert(0, str(FLYCOIN))
        from flysim import FlyBrain          # noqa: E402
        from io_map import FlyDesk           # noqa: E402
        from train import relevant_types     # noqa: E402
        self.fb = FlyBrain(FLYCOIN / "build" / "graph.npz")
        self.desk = FlyDesk(self.fb, steps=eco.steps)
        codes, _ = relevant_types(self.fb, self.desk)
        self.free = np.sort(codes)
        self.n_types = self.fb.n_types
        self.neurons = self.fb.n
        # which of the 165k neurons the site can draw (those with a soma position)
        keep = load_keep()
        self.remap = None
        self.n_cloud = 0
        if keep is not None:
            self.remap = np.full(self.fb.n, -1, dtype=np.int64)
            self.remap[keep] = np.arange(len(keep))
            self.n_cloud = len(keep)
        # region membership for "what is lit"
        self.region_of = np.full(self.fb.n, -1, dtype=np.int8)
        for i, (_, classes) in enumerate(REGIONS):
            self.region_of[np.isin(self.fb.superclass, classes)] = i
        self.region_total = np.bincount(self.region_of[self.region_of >= 0], minlength=len(REGIONS))
        self.record = dict(self.desk.readouts)
        for k, sel in self.desk.inputs.items():
            self.record["in:" + k] = sel

    def sense(self, feat, gains, seed):
        """Rates on the command neurons plus the set of neurons that fired, packed as bits."""
        drive = self.desk.encode(feat)
        r = self.fb.run(drive, steps=self.desk.steps, gains=gains,
                        record=self.record, seed=int(seed))
        out = {k: float(r[k].mean()) for k in self.desk.readouts}
        out["_net_hz"] = float(r["_total_hz"])
        out["_spikes_per_s"] = float(r["_spikes_per_sec"])
        f = {k: float(np.clip(feat.get(v[2], 0.0), 0, 1)) for k, v in SENSES.items()}
        out["_senses"] = {k: {"hz": round(float(r["in:" + k].mean()), 1), "drive": round(f[k] * 200.0, 1),
                              "n": int(len(self.desk.inputs[k]))} for k in SENSES}
        reg = self.region_of[r["_fired"]]
        cnt = np.bincount(reg[reg >= 0], minlength=len(REGIONS))
        out["_regions"] = {name: [int(cnt[i]), int(self.region_total[i])] for i, (name, _) in enumerate(REGIONS)}
        if self.remap is not None:
            sub = self.remap[r["_fired"]]
            sub = sub[sub >= 0]
            bits = np.zeros(self.n_cloud, dtype=np.uint8)
            bits[sub] = 1
            out["_fired_bits"] = np.packbits(bits).tobytes()
            out["_fired_n"] = int(len(sub))
        return out


class StubBrain:
    """
    Same shape, no biology. Appetite and fear are read off two blocks of the
    genome so selection has something to act on; noise matches the measured
    spread of the real MN9 (+/- ~90 Hz) so thresholds behave the same way.
    """
    name = "stub"

    def __init__(self, eco: Ecology, n_types=400, n_free=300, seed=0, n_cloud=0):
        self.n_types = n_types
        self.free = np.arange(n_free)
        self.neurons = 0
        self.eco = eco
        self.n_cloud = n_cloud     # if set, fake firing bitmaps so the page has something to draw

    def sense(self, feat, gains, seed):
        rng = np.random.default_rng(int(seed))
        g = np.ones(self.n_types, dtype=np.float32) if gains is None else gains
        appetite = float(np.mean(g[:100]))
        fear = float(np.mean(g[100:200]))
        mom, liq = feat.get("momentum", 0.0), feat.get("liquidity", 0.0)
        dan, cra = feat.get("danger", 0.0), feat.get("crash", 0.0)
        launch = 130.0 + 200.0 * appetite * (0.7 * mom + 0.3 * liq) - 80.0 * fear * dan
        abort = 15.0 + 450.0 * fear * (0.75 * cra + 0.25 * dan)
        out = {
            "LAUNCH": max(0.0, launch + rng.normal(0, 90)),
            "ABORT": max(0.0, abort + rng.normal(0, 20)),
            "HOLD": max(0.0, 30.0 * dan + rng.normal(0, 5)),
            "BROADCAST": max(0.0, 40.0 * feat.get("social", 0.0) + rng.normal(0, 5)),
            "_net_hz": 40.0,
            "_spikes_per_s": 0.0,
        }
        f = {k: float(np.clip(feat.get(v[2], 0.0), 0, 1)) for k, v in SENSES.items()}
        out["_senses"] = {k: {"hz": round(f[k] * 200.0 * 0.9, 1), "drive": round(f[k] * 200.0, 1), "n": 10} for k in SENSES}
        out["_regions"] = {name: [int(1000 * (0.1 + mom)), 10000] for name, _ in REGIONS}
        if self.n_cloud:
            bits = (rng.random(self.n_cloud) < 0.04 + 0.1 * mom).astype(np.uint8)
            out["_fired_bits"] = np.packbits(bits).tobytes()
            out["_fired_n"] = int(bits.sum())
        return out

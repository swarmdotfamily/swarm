"""
One fly: a wallet, a genome, a life.

The key is generated here and written to build/flies/<address>.json when the
colony persists (the live daemon). In the simulation nothing touches disk.
Keys are never printed. When a fly dies its key file is deleted after its
ETH has gone back to the hive.
"""
import json
import secrets
import time
from pathlib import Path

from .genome import Genome


def _fresh_key():
    return "0x" + secrets.token_hex(32)


def _addr(key):
    from eth_account import Account
    return Account.from_key(key).address


class Fly:
    __slots__ = ("addr", "key", "genome", "born_tick", "born_at", "origin", "parent",
                 "age", "trades", "buys", "sells", "children", "last", "last_rates",
                 "peak_worth", "eth", "tok", "worth", "alive", "cause", "spikes", "fired_n",
                 "spikes_per_s", "senses", "regions")

    def __init__(self, key, genome: Genome, born_tick, origin, parent=None, addr=None):
        self.key = key
        self.addr = addr or _addr(key)
        self.genome = genome
        self.born_tick = born_tick
        self.born_at = time.time()
        self.origin = origin              # "founder" | "hive" | "split"
        self.parent = parent
        self.age = 0
        self.trades = 0
        self.buys = 0
        self.sells = 0
        self.children = 0
        self.last = "BORN"
        self.last_rates = {}
        self.peak_worth = 0
        self.eth = 0
        self.tok = 0
        self.worth = 0
        self.alive = True
        self.cause = None
        self.spikes = b""
        self.fired_n = 0
        self.spikes_per_s = 0.0
        self.senses = {}
        self.regions = {}

    @classmethod
    def hatch(cls, genome, born_tick, origin, parent=None, sim_addr=None):
        if sim_addr is not None:
            return cls(key=None, genome=genome, born_tick=born_tick, origin=origin,
                       parent=parent, addr=sim_addr)
        return cls(key=_fresh_key(), genome=genome, born_tick=born_tick, origin=origin, parent=parent)

    # ---- persistence (live daemon only) ----
    def save(self, d: Path):
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{self.addr}.json").write_text(json.dumps({
            "addr": self.addr, "key": self.key, "born_tick": self.born_tick,
            "born_at": self.born_at, "origin": self.origin, "parent": self.parent,
            "age": self.age, "trades": self.trades, "buys": self.buys, "sells": self.sells,
            "children": self.children, "peak_worth": self.peak_worth}))
        self.genome.save(d / f"{self.addr}.npz")

    @classmethod
    def load(cls, d: Path, addr):
        j = json.loads((d / f"{addr}.json").read_text())
        g = Genome.load(d / f"{addr}.npz")
        f = cls(key=j["key"], genome=g, born_tick=j["born_tick"], origin=j["origin"],
                parent=j.get("parent"), addr=j["addr"])
        f.born_at = j.get("born_at", f.born_at)
        for k in ("age", "trades", "buys", "sells", "children", "peak_worth"):
            setattr(f, k, j.get(k, 0))
        return f

    def erase(self, d: Path):
        for ext in (".json", ".npz"):
            p = d / f"{self.addr}{ext}"
            if p.exists():
                p.unlink()

    # ---- for the site ----
    def summary(self, price_wei=0):
        return {
            "addr": self.addr, "gid": self.genome.gid, "gen": self.genome.gen,
            "origin": self.origin, "parent": self.parent, "age": self.age,
            "eth": self.eth, "tok": self.tok, "worth": self.worth, "peak": self.peak_worth,
            "trades": self.trades, "buys": self.buys, "sells": self.sells,
            "children": self.children, "last": self.last,
            "mn9": round(self.last_rates.get("LAUNCH", 0.0), 1),
            "dnp01": round(self.last_rates.get("ABORT", 0.0), 1),
            "dnp09": round(self.last_rates.get("HOLD", 0.0), 1),
            "pc1": round(self.last_rates.get("BROADCAST", 0.0), 1),
            "fired_n": self.fired_n, "spikes_per_s": round(self.spikes_per_s),
            "senses": self.senses, "regions": self.regions,
            "alive": self.alive, "cause": self.cause,
        }

    def spikes_b64(self):
        """The neurons that fired in the last decision: zlib(packbits) as base64, or ''."""
        if not self.spikes:
            return ""
        import base64, zlib
        return base64.b64encode(zlib.compress(self.spikes, 6)).decode()

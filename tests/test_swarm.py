"""
The rules of life, checked. Stub brain everywhere: these test the ecology and
the money path, not the connectome (flycoin has its own validation).
"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from colony.config import Ecology, WEI                     # noqa: E402
from colony.genome import Genome                           # noqa: E402
from colony.brain import StubBrain, verdict                # noqa: E402
from colony.chain import SimChain, RpcChain, _quote_buy    # noqa: E402
from colony.colony import Colony                           # noqa: E402
from colony.sim import world                               # noqa: E402


def make(seed_eth=0.0, eco=None, seed=0):
    eco = eco or Ecology()
    chain = SimChain(eco, hive_eth=seed_eth)
    brain = StubBrain(eco)
    col = Colony(brain, chain, eco, rng=np.random.default_rng(seed))
    return eco, chain, brain, col


# ------------------------------------------------------------------ genome

def test_mutation_only_touches_free_types():
    rng = np.random.default_rng(1)
    free = np.array([3, 7, 11])
    g = Genome.founder(20, free)
    c = g.mutate(rng, 0.3)
    fixed = np.setdiff1d(np.arange(20), free)
    assert np.all(c.gains[fixed] == 1.0)
    assert np.any(c.gains[free] != 1.0)
    assert c.gen == 1 and c.parents == (g.gid,) and c.gid != g.gid


def test_cross_takes_from_both_parents():
    rng = np.random.default_rng(2)
    free = np.arange(100)
    a = Genome(np.full(100, 2.0), free)
    b = Genome(np.full(100, 0.5), free)
    c = a.cross(b, rng, frac=0.5, sigma=0.0)
    assert 20 < np.sum(c.gains == 0.5) < 80
    assert np.sum(c.gains == 2.0) + np.sum(c.gains == 0.5) == 100
    assert set(c.parents) == {a.gid, b.gid}


def test_genome_roundtrip(tmp_path):
    g = Genome.founder(10, np.array([1, 2])).mutate(np.random.default_rng(0), 0.2)
    g.save(tmp_path / "g.npz")
    h = Genome.load(tmp_path / "g.npz")
    assert h.gid == g.gid and h.gen == g.gen and h.parents == g.parents


# ----------------------------------------------------------------- verdict

def test_verdict_escape_vetoes_eating():
    eco = Ecology()
    assert verdict({"LAUNCH": 300, "ABORT": 200}, eco) == "SELL"
    assert verdict({"LAUNCH": 300, "ABORT": 10}, eco) == "BUY"
    assert verdict({"LAUNCH": 100, "ABORT": 10}, eco) == "HOLD"


# ----------------------------------------------------------------- ecology

def _founders(col, chain, eco, n=6, worth_mult=1.0):
    for i in range(n):
        chain.eth[f"seed{i}"] = int(eco.birth_cost * worth_mult)
        f = col._new_fly(col.founder, "founder"); f.addr = f"seed{i}"; col.flies[f.addr] = f


FLAT = {"momentum": 0, "liquidity": 0, "danger": 0, "crash": 0, "social": 0}


def test_silence_means_no_growth_and_nothing_lost():
    """Founders, empty hive, zero volume: nobody is born from thin air and ETH stays in the colony."""
    eco, chain, brain, col = make(seed_eth=0.0)
    _founders(col, chain, eco)
    col.income_wei = eco.birth_cost * 6
    before = chain.total_eth()
    for _ in range(100):
        st = col.tick(feat=FLAT)
    assert st["births"] == 0
    assert st["population"] == 6
    assert abs(chain.total_eth() - before) <= 1
    # what left the colony: the tiny metabolism (inside the 10% budget) and pons' 1% trade fee
    for f in col.flies.values():
        col._refresh(f)
    kept = sum(f.worth for f in col.flies.values()) + chain.hive + chain.curve_tax
    print("kept", kept / (eco.birth_cost * 6))
    assert kept >= 0.88 * eco.birth_cost * 6
    assert col.burn_wei <= int(col.income_wei * eco.burn_budget_frac)


def test_old_age_returns_the_wallet_to_the_hive():
    eco = Ecology(); eco.lifespan_ticks = 5
    _, chain, brain, col = make(seed_eth=0.0, eco=eco)
    _founders(col, chain, eco, n=1)
    col.income_wei = eco.birth_cost
    for _ in range(6):
        col.tick(feat=FLAT)
    assert col.deaths == 1 and col.dead[-1]["cause"] == "old age"
    assert chain.balance("seed0") == 0
    assert chain.hive + chain.curve_tax >= 0.9 * eco.birth_cost   # nearly all of it came back


def test_burn_never_exceeds_the_budget():
    eco = Ecology(); eco.life_burn_frac = 5.0     # a greedy metabolism: the budget must still hold
    _, chain, brain, col = make(seed_eth=0.0, eco=eco)
    _founders(col, chain, eco, n=4)
    col.income_wei = eco.birth_cost * 4
    for _ in range(100):
        col.tick(feat=FLAT)
    assert col.burn_wei <= int(col.income_wei * eco.burn_budget_frac)
    assert col.burn_wei > 0
    # once the budget is spent, burning stops except for what the tax on our own burns re-earns
    b = col.burn_wei
    for _ in range(50):
        col.tick(feat=FLAT)
    assert col.burn_wei <= int(col.income_wei * eco.burn_budget_frac)
    assert col.burn_wei - b <= 2 * eco.upkeep


def test_exposure_cap_keeps_most_of_a_fly_in_eth():
    eco, chain, brain, col = make(seed_eth=0.0)
    _founders(col, chain, eco, n=3)
    col.income_wei = eco.birth_cost * 3
    hot = {"momentum": 1.0, "liquidity": 1.0, "danger": 0, "crash": 0, "social": 1.0}
    for _ in range(40):
        col.tick(feat=hot)
    for f in col.flies.values():
        col._refresh(f)
        assert f.eth >= (1 - eco.exposure_frac - 0.02) * f.worth, (f.eth, f.worth)


def test_volume_feeds_births():
    eco, chain, brain, col = make(seed_eth=0.0)
    rng = np.random.default_rng(0)
    for _ in range(40):
        world(chain, rng, volume_eth=0.2, sell_bias=0.3)
        col.tick()
    assert col.births > 0
    assert len(col.flies) > 0
    assert chain.hive + chain.curve_tax > 0 or col.births >= 1


def test_eth_is_conserved():
    eco, chain, brain, col = make(seed_eth=0.05)
    rng = np.random.default_rng(3)
    before = chain.total_eth()
    for _ in range(60):
        world(chain, rng, volume_eth=0.1, sell_bias=0.4)
        col.tick()
    assert abs(chain.total_eth() - before) <= 1


def test_rich_fly_splits_and_child_gets_exactly_one_birth():
    eco, chain, brain, col = make(seed_eth=0.0)
    chain.eth["rich"] = eco.birth_cost * 3
    fly = col._new_fly(col.founder, "founder")
    fly.addr = "rich"
    col.flies = {"rich": fly}
    col.tick(feat={"momentum": 0, "liquidity": 0, "danger": 0, "crash": 0, "social": 0})
    assert col.splits == 1
    child = [f for f in col.flies.values() if f.origin == "split"][0]
    assert child.parent == "rich"
    assert chain.balance(child.addr) == eco.birth_cost
    assert child.genome.parents == (fly.genome.gid,)
    assert child.genome.gen == fly.genome.gen + 1


def test_token_rich_fly_sells_to_fund_split():
    eco, chain, brain, col = make(seed_eth=0.0)
    # a fly with almost no ETH but a pile of coin worth several births
    chain.eth["whale"] = eco.gas_reserve * 3
    qr, tr = chain.reserves()
    want = eco.birth_cost * 4
    out, *_ = _quote_buy(want, qr, tr, chain.tax_bps)
    chain.tok["whale"] = out
    chain.qr += want; chain.tr -= out      # as if it had bought earlier
    fly = col._new_fly(col.founder, "founder")
    fly.addr = "whale"
    col.flies = {"whale": fly}
    col.tick(feat={"momentum": 0, "liquidity": 0, "danger": 0, "crash": 0, "social": 0})
    assert col.splits == 1
    assert chain.tokens("whale") < out          # it sold some coin
    child = [f for f in col.flies.values() if f.origin == "split"][0]
    assert chain.balance(child.addr) == eco.birth_cost


def test_metabolism_burns_the_coin():
    eco, chain, brain, col = make(seed_eth=0.0)
    col.income_wei = eco.birth_cost
    chain.eth["f"] = eco.birth_cost
    fly = col._new_fly(col.founder, "founder"); fly.addr = "f"
    col.flies = {"f": fly}
    col.tick(feat={"momentum": 0, "liquidity": 0, "danger": 0, "crash": 0, "social": 0})
    assert chain.burned > 0
    assert col.burned == chain.burned
    assert chain.curve_tax > 0            # the hive gets the tax on every meal back
    assert chain.tokens("f") == 0 or fly.buys > 0   # eaten coin never sits in the wallet


def test_dead_fly_buries_everything_and_is_forgotten():
    eco, chain, brain, col = make(seed_eth=0.0)
    chain.eth["d"] = eco.death_floor - 1
    fly = col._new_fly(col.founder, "founder"); fly.addr = "d"
    col.flies = {"d": fly}
    col.tick(feat={"momentum": 0, "liquidity": 0, "danger": 0, "crash": 0, "social": 0})
    assert col.deaths == 1 and "d" not in col.flies
    assert chain.balance("d") == 0
    assert col.dead[-1]["cause"] == "starved"


def test_population_never_exceeds_cap():
    eco = Ecology(); eco.max_flies = 5
    _, chain, brain, col = make(seed_eth=1.0, eco=eco)
    rng = np.random.default_rng(0)
    for _ in range(30):
        world(chain, rng, 0.3, 0.2)
        st = col.tick()
        assert st["population"] <= 5


# -------------------------------------------------------------- money path

class FakeRpc:
    """Answers reads with a fixed curve; records every write."""
    def __init__(self):
        self.sent = []
        self.qr, self.tr = int(1.68 * WEI), 10**9 * WEI

    def __call__(self, method, params):
        if method == "eth_getBalance":
            return hex(10 * WEI)
        if method == "eth_getTransactionCount":
            return "0x5"
        if method == "eth_gasPrice":
            return hex(10**7)
        if method == "eth_estimateGas":
            return hex(120_000)
        if method == "eth_call":
            data = params[0]["data"]
            if data.startswith("0x0902f1ac"):   # getReserves()
                return "0x" + self.qr.to_bytes(32, "big").hex() + self.tr.to_bytes(32, "big").hex()
            return "0x" + (0).to_bytes(32, "big").hex()
        if method == "eth_sendRawTransaction":
            self.sent.append(params[0])
            return "0x" + "ab" * 32
        raise AssertionError(f"unexpected rpc {method}")


KEY = "0x" + "11" * 32


def test_dry_run_signs_journals_and_never_broadcasts(tmp_path):
    eco = Ecology()
    fake = FakeRpc()
    ch = RpcChain(eco, "http://x", hive="0x" + "aa" * 20, token="0x" + "bb" * 20,
                  curve="0x" + "cc" * 20, live=False, journal_path=tmp_path / "j.jsonl")
    ch._call = fake
    out, h = ch.buy(KEY, 10**15)
    assert h.startswith("dry:") and out > 0
    assert fake.sent == []
    recs = [json.loads(l) for l in (tmp_path / "j.jsonl").read_text().splitlines()]
    assert [r["status"] for r in recs] == ["intent", "dry"]
    assert recs[0]["intent"].startswith("buy")
    assert "key" not in json.dumps(recs).lower() or KEY not in json.dumps(recs)


def test_live_broadcasts_once_and_refuses_duplicates(tmp_path):
    eco = Ecology()
    fake = FakeRpc()
    ch = RpcChain(eco, "http://x", hive="0x" + "aa" * 20, token="0x" + "bb" * 20,
                  curve="0x" + "cc" * 20, live=True, journal_path=tmp_path / "j.jsonl")
    ch._call = fake
    h = ch.feed(KEY)
    assert h.startswith("0x") and len(fake.sent) == 1
    with pytest.raises(RuntimeError):
        ch.feed(KEY)                       # same intent, same tick: refused
    ch.new_tick()
    ch.feed(KEY)                           # next tick is fine
    assert len(fake.sent) == 2


def test_burn_tx_pays_the_dead_address():
    from colony.chain import DEAD
    eco = Ecology()
    fake = FakeRpc()
    captured = {}
    ch = RpcChain(eco, "http://x", hive="0x" + "aa" * 20, token="0x" + "bb" * 20,
                  curve="0x" + "cc" * 20, live=False)
    ch._call = fake
    orig = ch._send
    def spy(key, to, value, data, intent):
        captured.update(to=to, value=value, data=data, intent=intent)
        return orig(key, to, value, data, intent)
    ch._send = spy
    ch.burn(KEY, 10**14)
    assert captured["to"] == ch.curve_addr and captured["value"] == 10**14
    assert DEAD[2:].lower() in captured["data"].hex()

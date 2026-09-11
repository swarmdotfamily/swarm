"""
Ecology simulation: does the colony behave like an ecology?

Runs the exact Colony code against SimChain (pons curve + hive rules) with
outside traders providing volume in regimes. The StubBrain stands in for the
connectome so thousands of ticks take seconds. `--connectome` swaps the real
brain in for a short run (slow, ~7 s per fly per tick).

  py -m colony.sim                      three regimes, ASCII population curves
  py -m colony.sim --connectome --ticks 3 --seed-eth 0.02
"""
import argparse
import json
import numpy as np

from .config import Ecology, BUILD, WEI
from .chain import SimChain
from .brain import StubBrain, ConnectomeBrain
from .colony import Colony


def world(chain, rng, volume_eth, sell_bias):
    """Outside traders: a few buys and sells per tick, volume in ETH."""
    if volume_eth <= 0:
        return
    n = max(1, int(rng.integers(1, 5)))
    for _ in range(n):
        v = int(volume_eth / n * WEI * float(rng.uniform(0.5, 1.5)))
        if rng.random() < sell_bias:
            chain.outside_sell_frac(float(rng.uniform(0.02, 0.10)))
        else:
            chain.outside_buy(v)


REGIMES = {
    # name: (ticks, volume ETH per tick, sell bias)
    "boom":    (120, 0.25, 0.30),
    "grind":   (200, 0.04, 0.45),
    "silence": (250, 0.00, 0.50),
}


def run(brain, eco, seed=0, seed_eth=0.02, regimes=REGIMES, quiet=False):
    rng = np.random.default_rng(seed)
    chain = SimChain(eco, hive_eth=seed_eth)
    col = Colony(brain, chain, eco, rng=np.random.default_rng(seed + 1))
    col.income_wei = int(seed_eth * WEI)
    total0 = chain.total_eth()
    curve = []
    for name, (ticks, vol, bias) in regimes.items():
        for _ in range(ticks):
            world(chain, rng, vol, bias)
            st = col.tick()
            curve.append((name, st["population"], st["births"], st["deaths"], st["hive_eggs"], chain.price()))
        if not quiet:
            st = col.state()
            print(f"  {name:<8} pop {st['population']:>3}  births {st['births']:>4}  deaths {st['deaths']:>4}"
                  f"  eggs {st['hive_eggs']:>3}  max gen {st['max_gen']:>3}  price {chain.price()/1e9:.3f} gwei/tok"
                  f"  hive {chain.hive/1e18:.4f} ETH")
    assert abs(chain.total_eth() - total0) <= 1, "ETH was created or destroyed"
    return col, chain, curve


def ascii_curve(curve, width=100, height=12):
    pops = np.array([c[1] for c in curve], dtype=float)
    if len(pops) > width:
        idx = np.linspace(0, len(pops) - 1, width).astype(int)
        pops = pops[idx]; names = [curve[i][0] for i in idx]
    else:
        names = [c[0] for c in curve]
    top = max(1.0, pops.max())
    rows = []
    for h in range(height, 0, -1):
        lvl = top * h / height
        rows.append("".join("#" if p >= lvl else " " for p in pops))
    print(f"population (max {int(top)})")
    for r in rows:
        print("  |" + r)
    print("  +" + "-" * len(pops))
    marks = "".join(n[0] for n in names)
    print("   " + marks)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--connectome", action="store_true")
    ap.add_argument("--ticks", type=int, default=0, help="override: run only this many boom ticks")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--seed-eth", type=float, default=0.02, help="ETH the hive starts with (the founder eggs)")
    ap.add_argument("--flies", type=int, default=6, help="cap for --connectome runs")
    a = ap.parse_args()

    eco = Ecology()
    if a.connectome:
        eco.max_flies = a.flies
        brain = ConnectomeBrain(eco)
        print(f"connectome brain: {brain.neurons:,} neurons, {len(brain.free)} free cell types")
    else:
        brain = StubBrain(eco)
    regimes = {"boom": (a.ticks, 0.25, 0.3)} if a.ticks else REGIMES
    col, chain, curve = run(brain, eco, seed=a.seed, seed_eth=a.seed_eth, regimes=regimes)
    ascii_curve(curve)
    st = col.state()
    BUILD.mkdir(exist_ok=True)
    (BUILD / "sim-state.json").write_text(json.dumps(st, indent=1, default=str))
    print(f"\nfinal: pop {st['population']}  births {st['births']} (splits {st['splits']})  deaths {st['deaths']}"
          f"  max gen {st['max_gen']}  -> build/sim-state.json")
    if st["flies"]:
        f = st["flies"][0]
        print(f"richest fly {f['addr']} gen {f['gen']} worth {f['worth']/1e18:.5f} ETH  trades {f['trades']}  children {f['children']}")


if __name__ == "__main__":
    main()

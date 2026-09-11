"""
Every number the ecology runs on, in one place, with the reason next to it.

Units: ETH as float in config, wei as int everywhere money moves.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
FLIES_DIR = BUILD / "flies"
ENV = ROOT / ".env"

# the fly brain lives in the flycoin repo; we borrow its graph and simulator
FLYCOIN = Path(os.environ.get("SWARM_FLYCOIN_DIR", str(ROOT.parent / "flycoin")))

WEI = 10**18


def to_wei(eth: float) -> int:
    return int(round(eth * WEI))


def load_env(path=ENV):
    env = dict(os.environ)
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env.setdefault(k.strip(), v.strip())
    return env


class Ecology:
    """
    The rules of life. Same object drives the simulation and the live daemon.
    """
    # A fly is born with this much ETH. It is also the hive's egg price.
    # 0.002 ETH ~ a few dollars: enough for hundreds of L2 transactions.
    birth_cost_eth = 0.002
    # Hive contract: at most one birth per this many seconds (the operator ceiling).
    birth_interval_s = 60
    # Compute ceiling: one shared connectome, ~7 s per decision at 900 steps.
    max_flies = 64

    # Creator tax on every trade, in bps (pons cap 1000). The colony's only income.
    tax_bps = 300
    # A fly lives this many ticks, then returns its wallet to the hive (old age).
    lifespan_ticks = 288          # 2 days at a 10-min tick
    # Metabolism: over its whole life a fly burns this fraction of a birth in SWARM...
    life_burn_frac = 0.10
    # ...and the colony as a whole never burns more than this fraction of all ETH
    # that has ever entered it. Everything else stays ETH in the hive and fly wallets.
    burn_budget_frac = 0.10
    # At most this fraction of a fly's net worth may sit in SWARM (the rest stays ETH).
    exposure_frac = 0.10
    # Net worth (ETH + tokens at curve price) below this fraction of birth -> death.
    death_frac = 0.25
    # Net worth above this many births -> split: BIRTH_COST to a mutated child.
    split_mult = 2.0
    # ETH a fly always keeps for gas; below it, it cannot act and starves.
    gas_reserve_eth = 0.0002

    # Trading. MN9 (eat) -> buy this fraction of the fly's ETH. DNp01 (flee) -> sell all.
    buy_frac = 0.15
    # Curve slippage guard on every trade.
    max_slippage = 0.03

    # Brain thresholds, measured in flycoin/io_map.py at 900 steps.
    steps = 900
    launch_hz = 225.0
    abort_hz = 150.0

    # Genome: log-normal mutation on the on-path cell types only.
    mut_sigma = 0.15
    # Fraction of a child's genome taken from a second parent (0 = clone+mutate).
    cross_frac = 0.5

    # Wall clock between colony ticks.
    tick_s = 600

    @property
    def birth_cost(self):
        return to_wei(self.birth_cost_eth)

    @property
    def gas_reserve(self):
        return to_wei(self.gas_reserve_eth)

    @property
    def upkeep(self):
        return int(self.birth_cost * self.life_burn_frac / self.lifespan_ticks)

    @property
    def death_floor(self):
        return int(self.birth_cost * self.death_frac)

    @property
    def split_at(self):
        return int(self.birth_cost * self.split_mult)


# Robinhood Chain / pons v2 (verified 2026-09-06, see ~/.claude memory pons-protocol)
CHAIN_ID = 4663
RPC = "https://rpc.mainnet.chain.robinhood.com"
EXPLORER = "https://robinhoodchain.blockscout.com"
PONS_FACTORY = "0x7eD598BcEf8bd9Edd8C97A195C6d13f40801EC7e"
PONS_ESCROW = "0xd3AFEB2a57f70eF218Aa82451c51B2fb0416Ac9e"

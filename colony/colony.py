"""
The colony: the loop that is the whole project.

    every tick
      feed          pull creator tax off the curve into the hive
      births        while the hive has an egg and the cap allows: a new fly,
                    genome = cross of two living flies weighted by worth
                    (or the founder genome if nobody is alive)
      each fly      pay upkeep -> die if broke -> smell the market -> think
                    -> BUY / SELL / HOLD -> split if rich
      record        journal every action; state() for the site

Money rules (see ~/.claude/pumpfun-autonomous-playbook.md):
  * intent is journaled before any send (chain layer)
  * a fly can only spend what its own wallet holds; the hive only spawns
  * one action per fly per tick, no re-entry, no double-pay
  * every constant lives in config.Ecology and is printed on the site
"""
import json
import time
from pathlib import Path

import numpy as np

from .config import Ecology, FLIES_DIR, WEI
from .genome import Genome
from .fly import Fly
from .brain import verdict
from .senses import Tape

# ---------------------------------------------------------------- workers
# Decisions are independent, so they run in parallel across CPU cores. With the
# fork start method every worker inherits the loaded connectome by copy-on-write,
# so N workers cost one graph in memory. On platforms without fork (Windows) the
# colony simply thinks sequentially.
_WORKER_BRAIN = None


def _init_worker(brain):
    global _WORKER_BRAIN
    _WORKER_BRAIN = brain


def _sense_job(args):
    addr, feat, gains, seed = args
    return addr, _WORKER_BRAIN.sense(feat, gains, seed)


def make_pool(brain, workers):
    import multiprocessing as mp
    if workers <= 1 or "fork" not in mp.get_all_start_methods():
        return None
    ctx = mp.get_context("fork")
    return ctx.Pool(processes=workers, initializer=_init_worker, initargs=(brain,))


class Colony:
    def __init__(self, brain, chain, eco: Ecology, rng=None, persist=False,
                 flies_dir: Path = FLIES_DIR, journal: Path = None, queen_key=None, workers=1):
        self.brain = brain
        self.workers = int(workers)
        self.pool = make_pool(brain, self.workers)
        self.chain = chain
        self.eco = eco
        self.rng = rng or np.random.default_rng(0)
        self.persist = persist
        self.flies_dir = Path(flies_dir)
        self.journal_path = Path(journal) if journal else None
        self.queen_key = queen_key
        self.flies = {}            # addr -> Fly
        self.dead = []             # summaries
        self.tick_no = 0
        self.births = 0
        self.deaths = 0
        self.splits = 0
        self.burned = 0            # token units eaten
        self.income_wei = 0        # every wei that ever entered the colony from outside
        self.burn_wei = 0          # ETH-value ever spent on burning (capped by burn_budget_frac)
        self.ledger_path = None    # persisted counters (live daemon)
        self.fly_burials = 0       # wei dying flies sent back to the hive (already counted as income)
        self.on_progress = None    # callback(state) fired during a tick so the site never waits for the end
        self.history = []          # (tick, pop, births, deaths, eggs, price) for the chart
        self.tape = Tape()
        self.events = []           # last N events for the site
        self._sim_counter = 0
        self.founder = Genome.founder(brain.n_types, brain.free)
        self.last_feat = {}
        self.last_tick_s = 0.0

    # ------------------------------------------------------------------ util
    def _log(self, kind, **kw):
        rec = {"tick": self.tick_no, "t": time.time(), "kind": kind, **kw}
        self.events.append(rec)
        self.events = self.events[-200:]
        if self.journal_path:
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
            with self.journal_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        return rec

    def _sim_addr(self):
        self._sim_counter += 1
        return f"fly{self._sim_counter:05d}"

    def _is_sim(self):
        return self.chain.__class__.__name__ == "SimChain"

    def _key_or_addr(self, fly):
        return fly.addr if self._is_sim() else fly.key

    def _refresh(self, fly):
        fly.eth = self.chain.balance(fly.addr)
        fly.tok = self.chain.tokens(fly.addr)
        fly.worth = self.chain.worth(fly.addr)
        fly.peak_worth = max(fly.peak_worth, fly.worth)

    def alive_frac(self):
        return len(self.flies) / self.eco.max_flies

    def _sense_all(self, jobs):
        """jobs: [(addr, feat, gains, seed)] -> {addr: rates}. Parallel when a pool exists."""
        if not jobs:
            return {}
        if self.pool is None:
            return {addr: self.brain.sense(feat, gains, seed) for addr, feat, gains, seed in jobs}
        return dict(self.pool.map(_sense_job, jobs, chunksize=1))

    def _progress(self):
        if self.on_progress:
            try:
                self.on_progress(self.state())
            except Exception:
                pass

    # ------------------------------------------------------------ ledger
    def burn_budget_left(self):
        """Wei the colony may still spend on burning: burn_budget_frac of all income, minus burned."""
        return int(self.income_wei * self.eco.burn_budget_frac) - self.burn_wei

    def load_ledger(self, path):
        self.ledger_path = Path(path)
        if self.ledger_path.exists():
            j = json.loads(self.ledger_path.read_text())
            self.income_wei = int(j.get("income_wei", 0)); self.burn_wei = int(j.get("burn_wei", 0))
            self.fly_burials = int(j.get("fly_burials", 0))
            self.burned = int(j.get("burned", 0)); self.tick_no = int(j.get("tick", 0))
            self.births = int(j.get("births", 0)); self.deaths = int(j.get("deaths", 0))
            self.splits = int(j.get("splits", 0)); self.history = j.get("history", [])

    def save_ledger(self):
        if not self.ledger_path:
            return
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.ledger_path.write_text(json.dumps({
            "income_wei": self.income_wei, "burn_wei": self.burn_wei, "burned": self.burned,
            "fly_burials": self.fly_burials, "tick": self.tick_no, "births": self.births, "deaths": self.deaths, "splits": self.splits,
            "history": self.history[-720:]}))

    # ------------------------------------------------------------ genomes
    def _pick_parents(self):
        """Two living flies, chosen with probability proportional to net worth."""
        live = [f for f in self.flies.values() if f.alive]
        if not live:
            return None, None
        w = np.array([max(f.worth, 1) for f in live], dtype=np.float64)
        w = w / w.sum()
        a = live[self.rng.choice(len(live), p=w)]
        if len(live) == 1:
            return a, None
        b = live[self.rng.choice(len(live), p=w)]
        return a, (b if b is not a else None)

    def _child_genome(self, parent=None):
        if parent is not None:
            return parent.genome.mutate(self.rng, self.eco.mut_sigma)
        a, b = self._pick_parents()
        if a is None:
            return self.founder.mutate(self.rng, self.eco.mut_sigma) if self.births else self.founder
        if b is None:
            return a.genome.mutate(self.rng, self.eco.mut_sigma)
        return a.genome.cross(b.genome, self.rng, self.eco.cross_frac, self.eco.mut_sigma)

    # ------------------------------------------------------------- births
    def _new_fly(self, genome, origin, parent=None):
        fly = Fly.hatch(genome, self.tick_no, origin, parent=parent,
                        sim_addr=self._sim_addr() if self._is_sim() else None)
        return fly

    def hive_births(self):
        n = 0
        cap = max(1, self.eco.tick_s // max(1, self.eco.birth_interval_s))
        if not self._is_sim():
            cap = min(cap, 5)                       # leave most of the tick for thinking
        while n < cap and len(self.flies) < self.eco.max_flies:
            if not self._is_sim():
                # respect the contract's birth interval: wait for the next slot (bounded)
                nxt = self.chain._u256(self.chain.hive_addr, "nextBirthAt()")
                wait = nxt - time.time()
                if wait > 90:
                    break
                if wait > 0:
                    time.sleep(wait + 2)
            if not self.chain.hive_can_spawn():
                break
            genome = self._child_genome()
            fly = self._new_fly(genome, "hive")
            if self.persist:
                fly.save(self.flies_dir)          # key on disk BEFORE money moves to it
            h = self.chain.spawn(fly.addr) if self._is_sim() else self.chain.spawn(self.queen_key, fly.addr)
            self.flies[fly.addr] = fly
            self.births += 1
            n += 1
            self._log("born", addr=fly.addr, origin="hive", gid=genome.gid, gen=genome.gen,
                      parents=list(genome.parents), tx=h)
            self._progress()
            if not self._is_sim():
                if not self.chain.live:
                    break                          # dry run: chain state will not change
                time.sleep(4)                      # let the spawn mine before re-reading
        return n

    def split(self, fly):
        need = self.eco.birth_cost + self.eco.gas_reserve * 2
        if fly.eth < need and fly.tok > 0:
            # rich in coin, poor in ETH: sell just enough to fund the birth
            token_value = fly.worth - fly.eth
            frac = min(1.0, (need - fly.eth) * 1.15 / max(token_value, 1))
            self.chain.sell(self._key_or_addr(fly), int(fly.tok * frac))
            self._refresh(fly)
            if fly.eth < self.eco.birth_cost + self.eco.gas_reserve:
                return None
        genome = self._child_genome(parent=fly)
        child = self._new_fly(genome, "split", parent=fly.addr)
        if self.persist:
            child.save(self.flies_dir)
        h = self.chain.transfer(self._key_or_addr(fly), child.addr, self.eco.birth_cost)
        self.flies[child.addr] = child
        fly.children += 1
        self.births += 1
        self.splits += 1
        fly.last = "SPLIT"
        self._log("born", addr=child.addr, origin="split", parent=fly.addr, gid=genome.gid,
                  gen=genome.gen, tx=h)
        return child

    # -------------------------------------------------------------- deaths
    def die(self, fly, cause):
        fly.alive = False
        fly.cause = cause
        txs = []
        try:
            if fly.tok > 0:
                _, h = self.chain.sell(self._key_or_addr(fly), fly.tok)
                txs.append(h)
            eth = self.chain.balance(fly.addr)
            back = eth - (0 if self._is_sim() else self.eco.gas_reserve // 2)
            if back > 0:
                txs.append(self.chain.transfer(self._key_or_addr(fly), "hive", back))
                if not self._is_sim():
                    self.fly_burials += back
        except Exception as e:   # a failed burial must not stop the colony; the key stays until it succeeds
            self._log("burial-failed", addr=fly.addr, err=str(e)[:200])
            fly.alive = True
            fly.cause = None
            return False
        self.chain.forget(fly.addr)
        if self.persist:
            fly.erase(self.flies_dir)
        self.dead.append(fly.summary())
        self.dead = self.dead[-500:]
        del self.flies[fly.addr]
        self.deaths += 1
        self._log("died", addr=fly.addr, cause=cause, age=fly.age, gid=fly.genome.gid,
                  gen=fly.genome.gen, children=fly.children, txs=txs)
        return True

    # ---------------------------------------------------------------- tick
    def tick(self, feat=None):
        t0 = time.time()
        self.tick_no += 1
        self.chain.new_tick()
        eco = self.eco

        # 1. food
        fresh = 0
        try:
            if self._is_sim():
                fresh = self.chain.feed()
                self.income_wei += int(fresh)
            else:
                # on chain: feed only when there is something worth the gas, and count
                # income from the hive's own totals (minus what our dying flies returned)
                on_curve, in_escrow = self.chain.hive_pending()
                if on_curve + in_escrow >= self.eco.birth_cost // 4:
                    self.chain.feed(self.queen_key)
                    if self.chain.live:
                        time.sleep(6)
                fed, buried = self.chain.hive_totals()
                outside = fed + buried - self.fly_burials
                if outside > self.income_wei:
                    fresh = outside - self.income_wei
                    self.income_wei = outside
        except Exception as e:
            self._log("feed-failed", err=str(e)[:200])

        # 2. births from the hive
        self.hive_births()

        # 3. what the market smells like
        if feat is None:
            self.tape.push(self.chain.price(), self.chain.buy_vol, self.chain.sell_vol,
                           self.chain.liquidity_frac())
            feat = self.tape.features(self.alive_frac())
        self.last_feat = feat

        # 4a. every fly ages, eats, and may die (cheap, sequential: wallet reads + small txs)
        thinkers = []
        for addr in list(self.flies):
            fly = self.flies.get(addr)
            if fly is None or not fly.alive:
                continue
            fly.age += 1
            self._refresh(fly)

            # old age: the wallet goes back to the hive, nothing is lost
            if fly.age >= eco.lifespan_ticks:
                self.die(fly, "old age")
                continue

            # metabolism: a small burn of SWARM, coin already held first, else bought
            # straight to the dead address. Hard-capped: the colony never burns more
            # than burn_budget_frac of everything that ever entered it.
            price = self.chain.price()
            units = (eco.upkeep * WEI) // price if price else 0
            if eco.upkeep <= self.burn_budget_left():
                try:
                    if fly.tok >= units > 0:
                        out, h = self.chain.burn_tokens(self._key_or_addr(fly), units)
                        self.burned += out; self.burn_wei += eco.upkeep
                        self._log("eat", addr=addr, units=out, tx=h)
                    elif fly.eth >= eco.upkeep + (0 if self._is_sim() else eco.gas_reserve):
                        out, h = self.chain.burn(self._key_or_addr(fly), eco.upkeep)
                        self.burned += out; self.burn_wei += eco.upkeep
                        self._log("eat", addr=addr, wei=eco.upkeep, burned=out, tx=h)
                except Exception as e:
                    self._log("eat-failed", addr=addr, err=str(e)[:120])
                self._refresh(fly)

            # starvation
            if fly.worth < eco.death_floor or (fly.eth < eco.gas_reserve and fly.tok == 0 and not self._is_sim()):
                self.die(fly, "starved")
                continue

            thinkers.append((addr, feat, fly.genome.gains, int(self.rng.integers(1 << 30))))

        # 4b. every surviving fly thinks - in parallel across workers
        rates_by = self._sense_all(thinkers)

        # 4c. every fly acts on what its command neurons said
        for addr, _, _, _ in thinkers:
            fly = self.flies.get(addr)
            if fly is None or not fly.alive:
                continue
            rates = rates_by[addr]
            fly.last_rates = {k: float(v) for k, v in rates.items() if not k.startswith("_")}
            fly.spikes = rates.get("_fired_bits", b"")
            fly.fired_n = int(rates.get("_fired_n", 0))
            fly.spikes_per_s = float(rates.get("_spikes_per_s", 0.0))
            fly.senses = rates.get("_senses", {})
            fly.regions = rates.get("_regions", {})
            act = verdict(rates, eco)
            fly.last = act

            # act
            try:
                if act == "BUY":
                    room = int(fly.worth * eco.exposure_frac) - (fly.worth - fly.eth)
                    spend = min(int(fly.eth * eco.buy_frac), room)
                    if spend <= 0:
                        fly.last = "FULL"
                    elif fly.eth - spend >= eco.gas_reserve:
                        out, h = self.chain.buy(self._key_or_addr(fly), spend)
                        fly.trades += 1; fly.buys += 1
                        self._log("buy", addr=addr, wei=spend, out=out, mn9=round(rates["LAUNCH"]), tx=h)
                    else:
                        fly.last = "BUY-broke"
                elif act == "SELL":
                    if fly.tok > 0:
                        out, h = self.chain.sell(self._key_or_addr(fly), fly.tok)
                        fly.trades += 1; fly.sells += 1
                        self._log("sell", addr=addr, units=fly.tok, out=out, dnp01=round(rates["ABORT"]), tx=h)
                    else:
                        fly.last = "SELL-empty"
            except Exception as e:
                self._log("trade-failed", addr=addr, act=act, err=str(e)[:200])

            # reproduce
            self._refresh(fly)
            if self.persist:
                fly.save_meta(self.flies_dir)
            self._progress()
            if fly.worth >= eco.split_at and len(self.flies) < eco.max_flies:
                try:
                    self.split(fly)
                except Exception as e:
                    self._log("split-failed", addr=addr, err=str(e)[:200])

        self.last_tick_s = time.time() - t0
        self.history.append([self.tick_no, len(self.flies), self.births, self.deaths,
                             int(self.chain.hive_eggs()), int(self.chain.price())])
        self.history = self.history[-720:]
        self._log("tick", pop=len(self.flies), births=self.births, deaths=self.deaths,
                  fed=fresh, eggs=self.chain.hive_eggs(), feat={k: round(v, 3) for k, v in feat.items()},
                  secs=round(self.last_tick_s, 2))
        self.save_ledger()
        return self.state()

    # --------------------------------------------------------------- state
    def state(self):
        live = sorted(self.flies.values(), key=lambda f: -f.worth)
        gens = [f.genome.gen for f in live]
        return {
            "updated": int(time.time()),
            "tick": self.tick_no,
            "brain": self.brain.name,
            "neurons": getattr(self.brain, "neurons", 0),
            "population": len(live),
            "cap": self.eco.max_flies,
            "births": self.births,
            "splits": self.splits,
            "deaths": self.deaths,
            "max_gen": max(gens) if gens else 0,
            "hive_eggs": self.chain.hive_eggs(),
            "burned": self.burned,
            "income_wei": self.income_wei,
            "burn_wei": self.burn_wei,
            "burn_budget_left_wei": max(0, self.burn_budget_left()),
            "treasury_eth_wei": sum(f.eth for f in live) + self.chain.hive_eggs() * self.eco.birth_cost,
            "treasury_tok": sum(f.tok for f in live),
            "history": self.history,
            "price_wei": self.chain.price(),
            "features": self.last_feat,
            "eco": {k: getattr(self.eco, k) for k in dir(self.eco)
                    if not k.startswith("_") and isinstance(getattr(type(self.eco), k, None), (int, float))},
            "n_cloud": getattr(self.brain, "n_cloud", 0),
            "flies": [dict(f.summary(), spikes=f.spikes_b64()) for f in live],
            "recent_dead": self.dead[-20:],
            "events": self.events[-40:],
            "tick_secs": round(self.last_tick_s, 2),
            "workers": self.workers if self.pool is not None else 1,
        }

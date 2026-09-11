"""
The queen: the process that runs the swarm against Robinhood Chain.

    py -m colony.run              dry run: real reads, signed-but-unsent writes
    SWARM_LIVE=1 py -m colony.run  the only thing that arms real transactions

Every tick it writes build/state.json (what the site shows) and, if
SWARM_RELAY_PUSH is set, pushes the same JSON to the relay over one outbound
websocket - the exact pattern flybrain uses (relay/relay.js, unchanged).

.env keys (values never printed):
    SWARM_QUEEN_SECRET   0x hex key of the queen (deployer of the Hive, calls feed/spawn)
    SWARM_HIVE           Hive contract address
    SWARM_TOKEN          $SWARM token address (Hive.token())
    SWARM_CURVE          pons curve address (Hive.curve())
    SWARM_RPC            Robinhood Chain RPC (default public)
    SWARM_LIVE           0/1
    SWARM_RELAY_PUSH     wss://.../push        (optional)
    SWARM_RELAY_SECRET   shared secret         (optional)
    SWARM_TAX_BPS        creator tax used at hatch (default 500)
    SWARM_TICK_S         override tick seconds
    SWARM_MAX_FLIES      override compute cap
"""
import argparse
import asyncio
import json
import sys
import time

from .config import Ecology, BUILD, FLIES_DIR, load_env, RPC
from .chain import RpcChain
from .brain import ConnectomeBrain, StubBrain
from .colony import Colony
from .fly import Fly


def say(*parts):
    print(time.strftime("%H:%M:%S"), " ".join(str(p) for p in parts), flush=True)


def restore(col: Colony):
    """Flies whose keys are on disk are alive: re-adopt them after a restart."""
    if not FLIES_DIR.exists():
        return 0
    n = 0
    for p in sorted(FLIES_DIR.glob("0x*.json")):
        try:
            fly = Fly.load(FLIES_DIR, p.stem)
            col.flies[fly.addr] = fly
            n += 1
        except Exception as e:
            say("could not restore", p.name, str(e)[:80])
    return n


async def pusher(url, secret, queue):
    import websockets
    while True:
        try:
            async with websockets.connect(f"{url}?secret={secret}", max_size=4 * 1024 * 1024) as ws:
                say("relay connected")
                while True:
                    msg = await queue.get()
                    await ws.send(msg)
        except Exception as e:
            say("relay:", str(e)[:100], "- retry in 5s")
            await asyncio.sleep(5)


async def main_async(a):
    env = load_env()
    eco = Ecology()
    if env.get("SWARM_TICK_S"):
        eco.tick_s = int(env["SWARM_TICK_S"])
    if env.get("SWARM_MAX_FLIES"):
        eco.max_flies = int(env["SWARM_MAX_FLIES"])
    live = env.get("SWARM_LIVE") == "1" and not a.dry
    for k in ("SWARM_QUEEN_SECRET", "SWARM_HIVE", "SWARM_TOKEN", "SWARM_CURVE"):
        if not env.get(k):
            say(f"{k} missing in .env"); sys.exit(1)

    chain = RpcChain(eco, env.get("SWARM_RPC", RPC), env["SWARM_HIVE"], env["SWARM_TOKEN"],
                     env["SWARM_CURVE"], live=live, journal_path=BUILD / "journal.jsonl",
                     tax_bps=int(env.get("SWARM_TAX_BPS", str(eco.tax_bps))))
    brain = StubBrain(eco) if a.stub else ConnectomeBrain(eco)
    col = Colony(brain, chain, eco, persist=True, journal=BUILD / "events.jsonl",
                 queen_key=env["SWARM_QUEEN_SECRET"])
    col.load_ledger(BUILD / "ledger.json")
    if env.get("SWARM_SEED_ETH"):
        col.income_wei = max(col.income_wei, int(float(env["SWARM_SEED_ETH"]) * 10**18))
    n = restore(col)
    say(f"brain {brain.name} ({getattr(brain, 'neurons', 0):,} neurons)  flies restored {n}"
        f"  cap {eco.max_flies}  tick {eco.tick_s}s  {'LIVE' if live else 'DRY RUN'}")

    queue = asyncio.Queue(maxsize=4)
    if env.get("SWARM_RELAY_PUSH") and env.get("SWARM_RELAY_SECRET"):
        asyncio.create_task(pusher(env["SWARM_RELAY_PUSH"], env["SWARM_RELAY_SECRET"], queue))

    BUILD.mkdir(exist_ok=True)
    while True:
        t0 = time.time()
        try:
            st = await asyncio.to_thread(col.tick)
            st["live"] = live
            (BUILD / "state.json").write_text(json.dumps(st, default=str))
            msg = json.dumps({"type": "state", "state": st}, default=str)
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(msg)
            say(f"tick {st['tick']}  pop {st['population']}/{st['cap']}  eggs {st['hive_eggs']}"
                f"  births {st['births']}  deaths {st['deaths']}  gen {st['max_gen']}"
                f"  {st['tick_secs']}s")
        except Exception as e:
            say("tick failed:", str(e)[:200])
        if a.once:
            return
        await asyncio.sleep(max(5.0, eco.tick_s - (time.time() - t0)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="force dry run even if SWARM_LIVE=1")
    ap.add_argument("--stub", action="store_true", help="stub brain (plumbing checks only)")
    ap.add_argument("--once", action="store_true", help="one tick then exit")
    a = ap.parse_args()
    asyncio.run(main_async(a))


if __name__ == "__main__":
    main()

"""
Operator recovery: move the colony's ETH to one wallet, in public.

  python -m colony.recover --to 0xYOUR_WALLET            dry run (signs, sends nothing)
  python -m colony.recover --to 0xYOUR_WALLET --live     the real thing

Run it ON THE QUEEN BOX with the queen stopped (systemctl stop swarm). It uses
the queen key and every fly key; two processes sharing those keys would fight
over nonces, so --live refuses to start while the queen service is active.

What it does, journaling every step to build/recover.jsonl BEFORE sending:

  1. flies   each fly wallet sends its SWARM to the destination as a plain token
             transfer (no market sale) and then all its ETH minus gas. The first
             fly also tops the queen up so it can pay gas for the hive phase.
             Swept key files move to build/recovered/flies/, never deleted.
  2. hive    the Hive has no withdraw; its only exit is spawn(child): 0.002 ETH,
             once a minute, fixed in the contract. So: a fresh child key is saved
             to disk, the queen spawns to it, the child forwards everything minus
             gas to the destination. Pending tax is fed in first and every few
             minutes. Stops when the hive holds less than one birth.
  3. site    when live, every step pushes the colony state with a `recovery`
             block to the relay, so swarm.family shows the recovery as it happens.

Nothing here can send to any address other than --to (and the queen's own
gas top-ups, which stay operator-held).
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from eth_account import Account
from eth_utils import is_address, to_checksum_address

from .config import BUILD, CHAIN_ID, FLIES_DIR, RPC, Ecology, load_env
from .chain import RpcChain, _enc, _sel

REC_DIR = BUILD / "recovered"
QUEEN_MIN = 10**15 // 2          # 0.0005 ETH: below this the queen gets topped up
QUEEN_TOPUP = 3 * 10**15         # 0.003 ETH: ~200 spawns of gas
SPAWN_GAS = 200_000              # measured 123,686
FEED_GAS = 300_000


def say(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def _hex(b):
    h = b.hex() if hasattr(b, "hex") else str(b)
    return h if h.startswith("0x") else "0x" + h


def queen_running():
    try:
        out = subprocess.run(["systemctl", "is-active", "swarm"], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() == "active"
    except Exception:
        return False


class Recovery:
    def __init__(self, env, dest, live):
        self.env = env
        self.eco = Ecology()
        self.dest = to_checksum_address(dest)
        self.live = live
        self.c = RpcChain(self.eco, env.get("SWARM_RPC", RPC), env["SWARM_HIVE"], env["SWARM_TOKEN"],
                          env["SWARM_CURVE"], live=live)
        self.queen_key = env["SWARM_QUEEN_SECRET"]
        self.queen = Account.from_key(self.queen_key).address
        self.journal_path = BUILD / ("recover.jsonl" if live else "recover-dry.jsonl")
        base = BUILD / "state.json"
        self.base = json.loads(base.read_text()) if base.exists() else {}
        self.remaining = {p.stem for p in FLIES_DIR.glob("0x*.json")}
        self.rec = {"to": self.dest, "status": "starting", "started": int(time.time()),
                    "from_flies_wei": 0, "from_hive_wei": 0, "tokens_units": 0,
                    "spawns": 0, "flies_swept": 0, "queen_gas_wei": 0, "rate_eth_per_hour": 0.12}
        for a in (self.c.hive_addr, self.queen):
            if self.dest.lower() == a.lower():
                raise SystemExit("destination must be your own wallet, not the hive or the queen")
        if self.dest in self.remaining:
            raise SystemExit("destination is a fly wallet")

    # ------------------------------------------------------------ plumbing
    def journal(self, rec):
        rec["t"] = time.time()
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        with self.journal_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")

    def gas_price(self):
        return min(int(self.c._call("eth_gasPrice", []), 16) * 12 // 10, self.c.gas_price_cap)

    def send(self, key, to, value, data=b"", gas=None, gp=None, intent=""):
        acct = Account.from_key(key)
        gp = gp or self.gas_price()
        if gas is None:
            gas = int(self.c._call("eth_estimateGas", [{"from": acct.address, "to": to, "value": hex(value),
                                                        "data": "0x" + data.hex()}]), 16) * 12 // 10
        nonce = int(self.c._call("eth_getTransactionCount", [acct.address, "pending"]), 16)
        tx = {"chainId": CHAIN_ID, "nonce": nonce, "to": to_checksum_address(to), "value": int(value),
              "data": "0x" + data.hex(), "gas": int(gas), "gasPrice": int(gp)}
        signed = Account.sign_transaction(tx, key)
        h = _hex(signed.hash)
        rec = {"intent": intent, "from": acct.address, "to": tx["to"], "value": int(value), "gas": gas,
               "gasPrice": gp, "nonce": nonce, "hash": h, "status": "intent", "live": self.live}
        self.journal(rec)
        if not self.live:
            self.journal({**rec, "status": "dry"})
            return "dry:" + h
        self.c._call("eth_sendRawTransaction", [_hex(signed.raw_transaction)])
        self.journal({**rec, "status": "sent"})
        return h

    def wait(self, h, timeout=180):
        if not self.live or str(h).startswith("dry:"):
            return None
        t0 = time.time()
        while time.time() - t0 < timeout:
            r = self.c._call("eth_getTransactionReceipt", [h])
            if r:
                if int(r["status"], 16) != 1:
                    raise RuntimeError(f"reverted: {h}")
                return r
            time.sleep(2)
        raise RuntimeError(f"not mined in {timeout}s: {h}")

    def push(self, status=None):
        if status:
            self.rec["status"] = status
        if not self.live:
            return
        st = dict(self.base)
        st["updated"] = int(time.time())
        st["live"] = True
        st["recovery"] = self.rec
        st["flies"] = [f for f in st.get("flies", []) if f.get("addr") in self.remaining]
        st["population"] = len(self.remaining)
        try:
            hb = self.c.balance(self.c.hive_addr)
            st["hive_eggs"] = hb // self.eco.birth_cost
            st["treasury_eth_wei"] = hb + sum(int(f.get("eth", 0)) for f in st["flies"])
            st["treasury_tok"] = sum(int(f.get("tok", 0)) for f in st["flies"])
        except Exception:
            pass
        (BUILD / "recover-state.json").write_text(json.dumps(st, default=str))
        url, secret = self.env.get("SWARM_RELAY_PUSH"), self.env.get("SWARM_RELAY_SECRET")
        if not (url and secret):
            return
        try:
            from websockets.sync.client import connect
            with connect(f"{url}?secret={secret}", open_timeout=10, max_size=8 * 1024 * 1024) as ws:
                ws.send(json.dumps({"type": "state", "state": st}, default=str))
        except Exception as e:
            say("relay push failed:", str(e)[:100])

    # ------------------------------------------------------------ phase 1
    def sweep_flies(self):
        say(f"flies: {len(self.remaining)} wallets to sweep")
        self.push("sweeping flies")
        (REC_DIR / "flies").mkdir(parents=True, exist_ok=True)
        for addr in sorted(self.remaining):
            p = FLIES_DIR / f"{addr}.json"
            key = json.loads(p.read_text())["key"]
            try:
                gp = self.gas_price()
                tok = self.c.tokens(addr)
                if tok > 0:
                    h = self.send(key, self.c.token_addr, 0,
                                  _enc("transfer(address,uint256)", ["address", "uint256"], [self.dest, tok]),
                                  gp=gp, intent=f"fly {addr}: {tok} SWARM units -> operator")
                    self.wait(h)
                    self.rec["tokens_units"] += tok
                bal = self.c.balance(addr)
                gas = 21_000
                value = bal - gas * gp
                # the queen pays gas for every hive spawn; the first flies refill it
                queen_short = QUEEN_TOPUP - (self.c.balance(self.queen) + self.rec["queen_gas_wei"] * (not self.live))
                if value > 0 and queen_short > 0:
                    h = self.send(key, self.queen, value, gas=gas, gp=gp, intent=f"fly {addr}: ETH -> queen (gas for the hive phase)")
                    self.wait(h)
                    self.rec["queen_gas_wei"] += value
                elif value > 0:
                    h = self.send(key, self.dest, value, gas=gas, gp=gp, intent=f"fly {addr}: ETH -> operator")
                    self.wait(h)
                    self.rec["from_flies_wei"] += value
                self.rec["flies_swept"] += 1
                if self.live:
                    for ext in (".json", ".npz"):
                        src = FLIES_DIR / f"{addr}{ext}"
                        if src.exists():
                            src.rename(REC_DIR / "flies" / src.name)
                    self.remaining.discard(addr)
                say(f"  {addr}  {value / 1e18 if value > 0 else 0:.6f} ETH  {tok / 1e18:,.0f} SWARM")
            except Exception as e:
                say(f"  {addr} failed, key kept in place: {str(e)[:120]}")
                self.journal({"intent": f"fly {addr} sweep", "status": "failed", "err": str(e)[:300]})
            self.push()
            if not self.live and self.rec["flies_swept"] >= 3:
                say("  dry run: stopping after 3 flies")
                break

    # ------------------------------------------------------------ phase 2
    def feed(self):
        on_curve, in_escrow = self.c.hive_pending()
        if on_curve + in_escrow < self.eco.birth_cost:
            return
        h = self.send(self.queen_key, self.c.hive_addr, 0, _sel("feed()"), gas=FEED_GAS,
                      intent=f"hive.feed ({(on_curve + in_escrow) / 1e18:.5f} ETH pending)")
        self.wait(h)

    def drain_hive(self, max_wei=None):
        (REC_DIR / "children").mkdir(parents=True, exist_ok=True)
        self.push("draining the hive")
        self.feed()
        while True:
            hb = self.c.balance(self.c.hive_addr)
            if hb < self.eco.birth_cost:
                self.feed()
                if self.c.balance(self.c.hive_addr) < self.eco.birth_cost:
                    say("hive: less than one birth left, done")
                    break
            if max_wei is not None and self.rec["from_hive_wei"] >= max_wei:
                say("hive: reached --max-eth, done")
                break
            if self.live and self.c.balance(self.queen) < SPAWN_GAS * self.gas_price():
                raise SystemExit("queen is out of gas: send it ~0.003 ETH and rerun with --hive-only")
            wait_s = self.c._u256(self.c.hive_addr, "nextBirthAt()") - time.time()
            if wait_s > 0:
                time.sleep(wait_s + 2)

            child = Account.create()
            ckey = _hex(child.key)
            (REC_DIR / "children" / f"{child.address}.json").write_text(
                json.dumps({"addr": child.address, "key": ckey, "t": time.time()}))
            h = self.send(self.queen_key, self.c.hive_addr, 0,
                          _enc("spawn(address)", ["address"], [child.address]), gas=SPAWN_GAS,
                          intent=f"hive.spawn -> recovery child {child.address}")
            self.wait(h)
            if not self.live:
                say("hive: dry run, one spawn signed, stopping")
                break

            gp = self.gas_price()
            bal = self.c.balance(child.address)
            if self.c.balance(self.queen) < QUEEN_MIN and bal > QUEEN_MIN * 2:
                h2 = self.send(ckey, self.queen, QUEEN_MIN, gas=21_000, gp=gp, intent="child: queen gas top-up")
                self.wait(h2)
                bal = self.c.balance(child.address)
            value = bal - 21_000 * gp
            h3 = self.send(ckey, self.dest, value, gas=21_000, gp=gp, intent=f"child {child.address}: -> operator")
            self.wait(h3)
            self.rec["from_hive_wei"] += value
            self.rec["spawns"] += 1
            left = self.c.balance(self.c.hive_addr)
            say(f"  spawn {self.rec['spawns']}: +{value / 1e18:.6f} ETH  (hive left {left / 1e18:.4f})")
            if self.rec["spawns"] % 10 == 0:
                self.feed()
            self.push()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", required=True, help="your wallet")
    ap.add_argument("--live", action="store_true", help="actually send (default: dry run)")
    ap.add_argument("--flies-only", action="store_true")
    ap.add_argument("--hive-only", action="store_true")
    ap.add_argument("--max-eth", type=float, help="stop the hive phase after this much")
    a = ap.parse_args()
    if not is_address(a.to):
        raise SystemExit("--to is not an address")
    env = load_env()
    if a.live and queen_running():
        raise SystemExit("the queen is running: systemctl stop swarm && systemctl disable swarm, then rerun")

    r = Recovery(env, a.to, a.live)
    hb = r.c.balance(r.c.hive_addr)
    say(f"{'LIVE' if a.live else 'DRY RUN'}  to {r.dest}")
    say(f"hive {hb / 1e18:.4f} ETH (~{hb / r.eco.birth_cost / 60:.1f} h at 0.002 ETH/min), "
        f"{len(r.remaining)} fly wallets, queen gas {r.c.balance(r.queen) / 1e18:.6f} ETH")
    try:
        if not a.hive_only:
            r.sweep_flies()
        if not a.flies_only:
            r.drain_hive(int(a.max_eth * 1e18) if a.max_eth else None)
        r.push("complete")
    except (Exception, KeyboardInterrupt) as e:
        r.push("paused")
        say("stopped:", str(e)[:200])
    say(f"recovered {r.rec['from_flies_wei'] / 1e18:.6f} ETH from flies, "
        f"{r.rec['from_hive_wei'] / 1e18:.6f} ETH from the hive in {r.rec['spawns']} spawns, "
        f"{r.rec['tokens_units'] / 1e18:,.0f} SWARM moved")


if __name__ == "__main__":
    main()

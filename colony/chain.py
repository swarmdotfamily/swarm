"""
Where money moves. Two implementations of one interface:

  SimChain   an in-memory pons curve + hive, for the ecology simulation and
             the tests. Exact integer arithmetic in the same order as pons.

  RpcChain   Robinhood Chain over JSON-RPC. Reads are always real. Writes are
             built and signed locally, journaled with intent BEFORE anything
             is sent, and only broadcast when SWARM_LIVE=1. Without it every
             write returns a "dry:" hash and nothing leaves the machine.

Every fly is a real EOA. The colony holds each key in build/flies/<addr>.json.
Keys are never printed, never logged, never sent anywhere.
"""
import json
import time
from pathlib import Path

from .config import Ecology, WEI, RPC, CHAIN_ID

FEE_BPS = 100
DEAD = "0x000000000000000000000000000000000000dEaD"


def _quote_buy(quote_in, qr, tr, tax_bps):
    fee = quote_in * FEE_BPS // 10_000
    tax = quote_in * tax_bps // 10_000
    net = quote_in - fee - tax
    out = net * tr // (qr + net)
    return out, fee, tax, net


def _quote_sell(tokens_in, qr, tr, tax_bps):
    gross = tokens_in * qr // (tr + tokens_in)
    fee = gross * FEE_BPS // 10_000
    tax = gross * tax_bps // 10_000
    return gross - fee - tax, fee, tax, gross


def price_wei_per_token(qr, tr):
    """Spot price in wei per whole token (1e18 units), from reserves."""
    return qr * WEI // tr if tr else 0


# --------------------------------------------------------------------------
#  simulation
# --------------------------------------------------------------------------

class SimChain:
    """
    pons v2 curve (phantom 1.68 ETH, 1e9 supply, 1% fee) + Hive with the
    contract's rules (birth cost, birth interval in ticks, only spawn pays out).
    """

    def __init__(self, eco: Ecology, tax_bps=None, hive_eth=0.0, birth_interval_ticks=0):
        self.eco = eco
        self.tax_bps = eco.tax_bps if tax_bps is None else tax_bps
        self.qr = int(1.68 * WEI)
        self.tr = 1_000_000_000 * WEI
        self.eth = {}          # addr -> wei
        self.tok = {}          # addr -> token units
        self.hive = int(hive_eth * WEI)
        self.curve_tax = 0
        self.protocol_fees = 0
        self.births = 0
        self.last_birth_tick = -10**9
        self.birth_interval_ticks = birth_interval_ticks
        self.tick_no = 0
        self.buy_vol = 0
        self.sell_vol = 0
        self.log = []
        self.external = 0      # ETH held by the outside world, for conservation checks
        self.burned = 0        # token units eaten by flies (sent to the dead address)
        self.live = False

    # ---- reads ----
    def balance(self, addr):
        return self.eth.get(addr, 0)

    def tokens(self, addr):
        return self.tok.get(addr, 0)

    def reserves(self):
        return self.qr, self.tr

    def price(self):
        return price_wei_per_token(self.qr, self.tr)

    def worth(self, addr):
        t = self.tokens(addr)
        if t:
            out, *_ = _quote_sell(t, self.qr, self.tr, self.tax_bps)
        else:
            out = 0
        return self.balance(addr) + out

    def liquidity_frac(self):
        real = self.qr - int(1.68 * WEI)
        return max(0.0, min(1.0, real / (4.2 * WEI)))

    def hive_eggs(self):
        return self.hive // self.eco.birth_cost

    def hive_can_spawn(self):
        return (self.hive >= self.eco.birth_cost and
                self.tick_no >= self.last_birth_tick + self.birth_interval_ticks)

    # ---- writes (all return a pseudo hash) ----
    def _h(self, kind, **kw):
        h = f"sim:{self.tick_no}:{len(self.log)}:{kind}"
        self.log.append({"tick": self.tick_no, "kind": kind, **kw})
        return h

    def feed(self):
        fresh = self.curve_tax
        self.curve_tax = 0
        self.hive += fresh
        return fresh

    def spawn(self, child):
        assert self.hive_can_spawn(), "hive cannot spawn"
        assert child not in self.eth, "child must be fresh"
        self.hive -= self.eco.birth_cost
        self.eth[child] = self.eco.birth_cost
        self.births += 1
        self.last_birth_tick = self.tick_no
        return self._h("spawn", child=child)

    def transfer(self, frm, to, wei):
        assert self.eth.get(frm, 0) >= wei, "insufficient"
        self.eth[frm] -= wei
        if to == "hive":
            self.hive += wei
        else:
            self.eth[to] = self.eth.get(to, 0) + wei
        return self._h("transfer", frm=frm, to=to, wei=wei)

    def buy(self, addr, quote_in, min_out=0):
        assert self.eth.get(addr, 0) >= quote_in, "insufficient"
        out, fee, tax, net = _quote_buy(quote_in, self.qr, self.tr, self.tax_bps)
        assert out >= min_out, "slippage"
        self.eth[addr] -= quote_in
        self.qr += net
        self.tr -= out
        self.protocol_fees += fee
        self.curve_tax += tax
        self.tok[addr] = self.tok.get(addr, 0) + out
        self.buy_vol += quote_in
        return out, self._h("buy", addr=addr, quote_in=quote_in, out=out)

    def sell(self, addr, tokens_in, min_out=0):
        assert self.tok.get(addr, 0) >= tokens_in, "insufficient tokens"
        out, fee, tax, gross = _quote_sell(tokens_in, self.qr, self.tr, self.tax_bps)
        assert out >= min_out, "slippage"
        self.tok[addr] -= tokens_in
        self.qr -= gross
        self.tr += tokens_in
        self.protocol_fees += fee
        self.curve_tax += tax
        self.eth[addr] = self.eth.get(addr, 0) + out
        self.sell_vol += gross
        return out, self._h("sell", addr=addr, tokens_in=tokens_in, out=out)

    def burn(self, addr, quote_in):
        """Metabolism: buy $SWARM and send it straight to the dead address."""
        out, h = self.buy(addr, quote_in)
        self.tok[addr] -= out
        self.burned += out
        self.log[-1]["kind"] = "burn"
        return out, h

    def burn_tokens(self, addr, units):
        """Metabolism paid in coin already held: straight to the dead address."""
        assert self.tok.get(addr, 0) >= units, "insufficient tokens"
        self.tok[addr] -= units
        self.burned += units
        return units, self._h("burn_tokens", addr=addr, units=units)

    def forget(self, addr):
        """A dead fly's wallet: must be empty of tokens; leftover dust stays on chain."""
        self.tok.pop(addr, None)

    # ---- the outside world ----
    def outside_buy(self, quote_in):
        self.external -= quote_in
        out, fee, tax, net = _quote_buy(quote_in, self.qr, self.tr, self.tax_bps)
        self.qr += net; self.tr -= out
        self.protocol_fees += fee; self.curve_tax += tax
        self.tok["world"] = self.tok.get("world", 0) + out
        self.buy_vol += quote_in

    def outside_sell_frac(self, frac):
        t = int(self.tok.get("world", 0) * frac)
        if t <= 0:
            return
        out, fee, tax, gross = _quote_sell(t, self.qr, self.tr, self.tax_bps)
        self.tok["world"] -= t
        self.qr -= gross; self.tr += t
        self.protocol_fees += fee; self.curve_tax += tax
        self.external += out
        self.sell_vol += gross

    def new_tick(self):
        self.tick_no += 1
        self.buy_vol = 0
        self.sell_vol = 0

    def total_eth(self):
        """Conservation: everything the sim has ever touched, in wei."""
        return (sum(self.eth.values()) + self.hive + self.curve_tax + self.protocol_fees
                + (self.qr - int(1.68 * WEI)) + self.external)


# --------------------------------------------------------------------------
#  Robinhood Chain
# --------------------------------------------------------------------------

def _sel(sig):
    from eth_utils import keccak
    return keccak(text=sig)[:4]


def _enc(sig, types, args):
    from eth_abi import encode
    return _sel(sig) + encode(types, args)


class RpcChain:
    """
    Reads: eth_call against the real curve / token / hive.
    Writes: signed locally by the fly's (or queen's) key; sent only if live.
    """

    def __init__(self, eco: Ecology, rpc, hive, token, curve, live=False,
                 journal_path=None, tax_bps=None, gas_price_cap_gwei=0.5):
        import requests
        self._rq = requests
        self.eco = eco
        self.rpc = rpc or RPC
        from eth_utils import to_checksum_address as cs
        self.hive_addr = cs(hive)
        self.token_addr = cs(token)
        self.curve_addr = cs(curve)
        self.live = bool(live)
        self.tax_bps = eco.tax_bps if tax_bps is None else tax_bps
        self.journal_path = Path(journal_path) if journal_path else None
        self.gas_price_cap = int(gas_price_cap_gwei * 1e9)
        self.tick_no = 0
        self.buy_vol = 0
        self.sell_vol = 0
        self._sent = set()

    # ---- rpc plumbing ----
    def _call(self, method, params):
        r = self._rq.post(self.rpc, json={"jsonrpc": "2.0", "id": 1, "method": method,
                                           "params": params}, timeout=20)
        j = r.json()
        if "error" in j:
            raise RuntimeError(f"{method}: {j['error']}")
        return j["result"]

    def _eth_call(self, to, data):
        return bytes.fromhex(self._call("eth_call", [{"to": to, "data": "0x" + data.hex()}, "latest"])[2:])

    def _u256(self, to, sig, types=(), args=()):
        out = self._eth_call(to, _enc(sig, list(types), list(args)))
        return int.from_bytes(out[:32], "big") if out else 0

    # ---- reads ----
    def balance(self, addr):
        return int(self._call("eth_getBalance", [addr, "latest"]), 16)

    def tokens(self, addr):
        return self._u256(self.token_addr, "balanceOf(address)", ["address"], [addr])

    def reserves(self):
        out = self._eth_call(self.curve_addr, _sel("getReserves()"))
        return int.from_bytes(out[:32], "big"), int.from_bytes(out[32:64], "big")

    def price(self):
        qr, tr = self.reserves()
        return price_wei_per_token(qr, tr)

    def worth(self, addr):
        t = self.tokens(addr)
        qr, tr = self.reserves()
        out = _quote_sell(t, qr, tr, self.tax_bps)[0] if t else 0
        return self.balance(addr) + out

    def liquidity_frac(self):
        qr, _ = self.reserves()
        real = qr - int(1.68 * WEI)
        return max(0.0, min(1.0, real / (4.2 * WEI)))

    def hive_eggs(self):
        return self._u256(self.hive_addr, "eggs()")

    def hive_can_spawn(self):
        nxt = self._u256(self.hive_addr, "nextBirthAt()")
        return self.hive_eggs() > 0 and time.time() >= nxt

    def hive_totals(self):
        """(totalFed, totalBuried) from the Hive: everything that ever entered it."""
        return (self._u256(self.hive_addr, "totalFed()"), self._u256(self.hive_addr, "totalBuried()"))

    def hive_pending(self):
        out = self._eth_call(self.hive_addr, _sel("pending()"))
        return int.from_bytes(out[:32], "big"), int.from_bytes(out[32:64], "big")

    # ---- writes ----
    def _journal(self, rec):
        if self.journal_path:
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
            with self.journal_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, default=str) + "\n")

    def _send(self, key, to, value, data, intent):
        """
        Build, journal intent, sign, and (only if live) broadcast.
        A repeated identical intent inside one process is refused: no double-pays.
        """
        from eth_account import Account
        acct = Account.from_key(key)
        dedupe = (self.tick_no, acct.address, to, value, data.hex())
        if dedupe in self._sent:
            raise RuntimeError("refusing duplicate send in one tick")
        self._sent.add(dedupe)

        nonce = int(self._call("eth_getTransactionCount", [acct.address, "pending"]), 16)
        gas_price = min(int(self._call("eth_gasPrice", []), 16) * 12 // 10, self.gas_price_cap)
        from eth_utils import to_checksum_address
        to = to_checksum_address(to)
        tx = {"chainId": CHAIN_ID, "nonce": nonce, "to": to, "value": value,
              "data": "0x" + data.hex(), "gasPrice": gas_price}
        try:
            gas = int(self._call("eth_estimateGas", [{"from": acct.address, "to": to,
                                                        "value": hex(value), "data": tx["data"]}]), 16)
        except Exception as e:
            self._journal({"t": time.time(), "intent": intent, "from": acct.address, "to": to,
                           "value": value, "status": "estimate-failed", "err": str(e)[:200]})
            raise
        tx["gas"] = gas * 13 // 10
        signed = Account.sign_transaction(tx, key)
        h = signed.hash.hex()
        rec = {"t": time.time(), "intent": intent, "from": acct.address, "to": to,
               "value": value, "nonce": nonce, "gas": tx["gas"], "hash": h,
               "status": "intent", "live": self.live}
        self._journal(rec)
        if not self.live:
            rec["status"] = "dry"
            self._journal(rec)
            return "dry:" + h
        sent = self._call("eth_sendRawTransaction", ["0x" + signed.raw_transaction.hex()])
        rec["status"] = "sent"
        self._journal(rec)
        return sent

    def feed(self, queen_key):
        return self._send(queen_key, self.hive_addr, 0, _sel("feed()"), "hive.feed")

    def spawn(self, queen_key, child):
        return self._send(queen_key, self.hive_addr, 0,
                          _enc("spawn(address)", ["address"], [child]), f"hive.spawn {child}")

    def transfer(self, key, to, wei):
        dest = self.hive_addr if to == "hive" else to
        return self._send(key, dest, wei, b"", f"transfer {wei} -> {to}")

    def buy(self, key, quote_in, min_out=0):
        from eth_account import Account
        addr = Account.from_key(key).address
        qr, tr = self.reserves()
        out, *_ = _quote_buy(quote_in, qr, tr, self.tax_bps)
        floor = max(min_out, int(out * (1 - self.eco.max_slippage)))
        h = self._send(key, self.curve_addr, quote_in,
                       _enc("buy(uint256,uint256,address)", ["uint256", "uint256", "address"],
                            [quote_in, floor, addr]), f"buy {quote_in} wei")
        self.buy_vol += quote_in
        return out, h

    def sell(self, key, tokens_in, min_out=0):
        from eth_account import Account
        addr = Account.from_key(key).address
        qr, tr = self.reserves()
        out, *_ = _quote_sell(tokens_in, qr, tr, self.tax_bps)
        floor = max(min_out, int(out * (1 - self.eco.max_slippage)))
        # approve then sell; approve is idempotent enough to repeat every time
        self._send(key, self.token_addr, 0,
                   _enc("approve(address,uint256)", ["address", "uint256"], [self.curve_addr, tokens_in]),
                   "approve curve")
        h = self._send(key, self.curve_addr, 0,
                       _enc("sell(uint256,uint256,address)", ["uint256", "uint256", "address"],
                            [tokens_in, floor, addr]), f"sell {tokens_in} units")
        self.sell_vol += out
        return out, h

    def burn(self, key, quote_in):
        """Metabolism: one tx, buy(quoteIn, floor, 0x...dEaD). Nothing to approve, nothing to hold."""
        qr, tr = self.reserves()
        out, *_ = _quote_buy(quote_in, qr, tr, self.tax_bps)
        floor = int(out * (1 - self.eco.max_slippage))
        h = self._send(key, self.curve_addr, quote_in,
                       _enc("buy(uint256,uint256,address)", ["uint256", "uint256", "address"],
                            [quote_in, floor, DEAD]), f"burn {quote_in} wei")
        self.buy_vol += quote_in
        return out, h

    def burn_tokens(self, key, units):
        """Metabolism paid in coin already held: one ERC-20 transfer to 0x...dEaD."""
        h = self._send(key, self.token_addr, 0,
                       _enc("transfer(address,uint256)", ["address", "uint256"], [DEAD, units]),
                       f"burn {units} units")
        return units, h

    def forget(self, addr):
        pass

    def new_tick(self):
        self.tick_no += 1
        self.buy_vol = 0
        self.sell_vol = 0
        self._sent.clear()

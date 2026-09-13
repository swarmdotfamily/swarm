"""
Send everything the queen wallet holds, minus gas, to one address.

  python -m colony.sweep_queen --to 0xYOUR_WALLET          dry run (signs, sends nothing)
  python -m colony.sweep_queen --to 0xYOUR_WALLET --live   the real thing

Refuses to run live while the queen or a recovery is active (they spend its gas).
"""
import argparse
import subprocess

from eth_account import Account
from eth_utils import is_address, to_checksum_address

from .config import CHAIN_ID, RPC, Ecology, load_env
from .chain import RpcChain


def busy():
    for unit in ("swarm", "swarm-recover"):
        try:
            out = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=10)
            if out.stdout.strip() == "active":
                return unit
        except Exception:
            pass
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", required=True)
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()
    if not is_address(a.to):
        raise SystemExit("--to is not an address")
    if a.live and busy():
        raise SystemExit(f"{busy()} is running; stop it first")

    env = load_env()
    c = RpcChain(Ecology(), env.get("SWARM_RPC", RPC), env["SWARM_HIVE"], env["SWARM_TOKEN"], env["SWARM_CURVE"])
    key = env["SWARM_QUEEN_SECRET"]
    queen = Account.from_key(key).address
    dest = to_checksum_address(a.to)
    if dest.lower() in (queen.lower(), c.hive_addr.lower()):
        raise SystemExit("destination must be your own wallet")

    quoted = int(c._call("eth_gasPrice", []), 16)
    base = int((c._call("eth_getBlockByNumber", ["latest", False]) or {}).get("baseFeePerGas", "0x0"), 16)
    gp = max(quoted * 12 // 10, base * 2)
    bal = c.balance(queen)
    value = bal - 21_000 * gp
    print(f"queen {queen}  balance {bal / 1e18:.6f} ETH  gas {gp / 1e9:.3f} gwei")
    if value <= 0:
        raise SystemExit("nothing to send above gas")

    nonce = int(c._call("eth_getTransactionCount", [queen, "pending"]), 16)
    tx = {"chainId": CHAIN_ID, "nonce": nonce, "to": dest, "value": value, "data": "0x",
          "gas": 21_000, "gasPrice": gp}
    signed = Account.sign_transaction(tx, key)
    h = signed.hash.hex()
    h = h if h.startswith("0x") else "0x" + h
    print(f"send {value / 1e18:.6f} ETH -> {dest}  tx {h}")
    if not a.live:
        print("dry run: nothing sent")
        return
    raw = signed.raw_transaction.hex()
    c._call("eth_sendRawTransaction", [raw if raw.startswith("0x") else "0x" + raw])
    print("sent. https://robinhoodchain.blockscout.com/tx/" + h)


if __name__ == "__main__":
    main()

// The Hive, read straight off Robinhood Chain. Server-side, read-only: no key
// here can sign anything. Before the contract exists every field is null and
// `deployed` is false - that is the honest state.
//
//   SWARM_HIVE   Hive contract        SWARM_TOKEN  $SWARM      SWARM_CURVE  pons curve
//   SWARM_RPC    defaults to the public Robinhood Chain RPC

const RPC = process.env.SWARM_RPC || 'https://rpc.mainnet.chain.robinhood.com';
const HIVE = process.env.SWARM_HIVE || '';
const TOKEN = process.env.SWARM_TOKEN || '';
const CURVE = process.env.SWARM_CURVE || '';
const DEAD = '0x000000000000000000000000000000000000dEaD';

// keccak-256 selectors, computed once with eth_utils (see DESIGN.md)
const SEL = {
  snapshot: '0x9711715a',    // (balance, eggs, births, totalFed, totalSpawned, totalBuried, lastBirth)
  pending: '0xe20ccec3',     // (onCurve, inEscrow)
  flyCount: '0x9ae89478',
  nextBirthAt: '0xb20d7e0a',
  getReserves: '0x0902f1ac',
  balanceOf: '0x70a08231',
  totalSupply: '0x18160ddd',
};

async function rpc(method, params) {
  const r = await fetch(RPC, { method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }) });
  const j = await r.json();
  if (j.error) throw new Error(j.error.message || 'rpc');
  return j.result;
}
const call = (to, data) => rpc('eth_call', [{ to, data }, 'latest']);
const words = (hex) => { const h = hex.slice(2); const out = []; for (let i = 0; i + 64 <= h.length; i += 64) out.push(BigInt('0x' + h.slice(i, i + 64))); return out; };
const addrArg = (a) => a.toLowerCase().replace('0x', '').padStart(64, '0');
const s = (x) => x.toString();

export const config = { maxDuration: 15 };

export default async function handler(req, res) {
  res.setHeader('Cache-Control', 's-maxage=20, stale-while-revalidate=60');
  if (!HIVE) return res.status(200).json({ deployed: false, hive: null, token: null, curve: null });
  try {
    const [snap, pend, flies, next, reserves, burned, supply, hiveEth] = await Promise.all([
      call(HIVE, SEL.snapshot).then(words),
      call(HIVE, SEL.pending).then(words),
      call(HIVE, SEL.flyCount).then(words),
      call(HIVE, SEL.nextBirthAt).then(words),
      CURVE ? call(CURVE, SEL.getReserves).then(words) : [0n, 0n],
      TOKEN ? call(TOKEN, SEL.balanceOf + addrArg(DEAD)).then(words) : [0n],
      TOKEN ? call(TOKEN, SEL.totalSupply).then(words) : [0n],
      rpc('eth_getBalance', [HIVE, 'latest']).then((h) => BigInt(h)),
    ]);
    const [qr, tr] = reserves;
    return res.status(200).json({
      deployed: true, hive: HIVE, token: TOKEN || null, curve: CURVE || null,
      balance_wei: s(hiveEth), eggs: s(snap[1]), births: s(snap[2]), total_fed_wei: s(snap[3]),
      total_spawned_wei: s(snap[4]), total_buried_wei: s(snap[5]), last_birth: Number(snap[6]),
      pending_on_curve_wei: s(pend[0]), pending_in_escrow_wei: s(pend[1]),
      fly_count: Number(flies[0]), next_birth_at: Number(next[0]),
      quote_reserve_wei: s(qr), token_reserve: s(tr),
      price_wei_per_token: tr ? s((qr * 10n ** 18n) / tr) : '0',
      liquidity_frac: Number(qr - 1_680_000_000_000_000_000n) / 4.2e18,
      burned_units: s(burned[0]), total_supply: s(supply[0]),
    });
  } catch (e) {
    return res.status(200).json({ deployed: true, hive: HIVE, token: TOKEN || null, curve: CURVE || null, error: String(e.message || e).slice(0, 100) });
  }
}

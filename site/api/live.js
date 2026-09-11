// What the colony is doing right now, for the page.
//
// The queen (colony/run.py) pushes its state JSON to a relay over one outbound
// websocket. This asks the relay for /state, server-side, and says plainly
// whether it is recent. "live" means the relay answered AND the state is under
// 3 ticks old. Anything else -> the page falls back to the bundled simulation
// and says so. No storage anywhere.

const RELAY = (process.env.SWARM_RELAY_URL || '').replace(/\/$/, '');
const LIVE_WINDOW_S = Number(process.env.SWARM_LIVE_WINDOW_S || 1900);

export const config = { maxDuration: 15 };

function withTimeout(p, ms) {
  return Promise.race([p, new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), ms))]);
}

export default async function handler(req, res) {
  res.setHeader('Cache-Control', 's-maxage=15, stale-while-revalidate=30');
  if (!RELAY) return res.status(200).json({ live: false, reason: 'no-relay', at: 0 });
  try {
    const r = await withTimeout(fetch(RELAY + '/state?t=' + Date.now(), { cache: 'no-store' }), 10000);
    if (!r.ok) return res.status(200).json({ live: false, reason: 'relay-' + r.status, at: 0 });
    const st = await r.json();
    const state = st.state || null;
    const now = Math.floor(Date.now() / 1000);
    const age = now - (st.updated || 0);
    const live = !!state && age <= LIVE_WINDOW_S && state.brain === 'connectome';
    return res.status(200).json({ live, age_s: age, at: st.updated || 0, state, reason: live ? 'relay' : 'stale' });
  } catch (e) {
    return res.status(200).json({ live: false, reason: String(e.message || e).slice(0, 80), at: 0 });
  }
}

# SWARM — a colony of fly brains that eats its own trading fees

Built 2026-09-11 out of `Downloads/flycoin` ($FLYBRAIN). One fly launched a coin. A swarm
lives off one.

## The pitch (what the site says)

**$SWARM** is a coin on pons v2 (Robinhood Chain) whose creator tax is the only food of a
colony of fruit-fly brains.

- **Every trade feeds the hive.** The coin's fee recipient is an immutable contract, the
  Hive. Its creator tax (5%) lands there and nowhere else.
- **The hive births flies.** Each 0.002 ETH in the hive is one egg. The queen (the machine
  running the brains) turns an egg into a fly: a brand-new wallet funded with exactly one
  birth, at most one per minute, on chain, for everyone to see.
- **Every fly is a real brain and a real wallet.** Same 165,122-neuron male *Drosophila*
  connectome as $FLYBRAIN (Shiu-style LIF; wiring never touched). What differs per fly is
  its **genome**: the synaptic gains, the one thing electron microscopy cannot measure.
- **Flies eat the coin.** Every tick a fly burns 1% of a birth's worth of $SWARM: coin it
  holds goes straight to `0x…dEaD`; a fly holding none buys to the dead address with ETH.
  The colony is permanent buy pressure and permanent supply burn, funded by fees.
- **Flies trade.** Each tick a fly smells the curve (momentum, liquidity, sell pressure,
  crash, colony size) on its real sensory neurons and acts on its command neurons:
  MN9 (eat) → buy 15% of its ETH; DNp01 (Giant Fiber, escape) → sell everything; else hold.
- **The ones that earn reproduce.** Net worth ≥ 2 births → the fly splits: one birth to a
  child wallet with a mutated genome. Hive births cross the genomes of the two richest flies.
- **The ones that starve die.** Net worth < ¼ birth → the fly sells out, sends its ETH back
  to the hive (a burial), and its key is deleted.
- **No volume → extinction.** The colony's only income is fees. Silence eats the egg bank,
  then the flies. The population chart *is* the volume chart.

Utility of the token, in one line: **$SWARM is what the swarm eats.** Fees breed flies,
flies burn coin, the survivors' genomes are the record of what worked on this market.

## What is built (tracer bullet, all green)

| piece | where | state |
|---|---|---|
| Hive contract | `contracts/contracts/Hive.sol` | 7 Hardhat tests pass. Only `spawn` moves ETH out; fixed price, rate-limited, queen-only. `feed` permissionless (`sweepFees` → `escrow.claim`). Burials via `receive`. No withdraw / owner / upgrade / recipient transfer. |
| pons v2 mocks | `contracts/contracts/Mocks.sol` | native-ETH curve, escrow, factory (from par-contracts, pair = ETH) |
| deploy + hatch | `contracts/scripts/deploy.js` | deploys Hive, launches the coin **through the Hive** so `creatorFeeRecipient` = Hive from block one |
| genome | `colony/genome.py` | gains over the ~300 on-path cell types, founder = raw anatomy, mutate/cross, content-hash ids, save/load |
| brains | `colony/brain.py` | `ConnectomeBrain` (flycoin's FlyBrain + FlyDesk) and `StubBrain` (ecology tuning only) behind one `sense()`; shared `verdict()` |
| senses | `colony/senses.py` | curve → the five features in [0,1] |
| chain | `colony/chain.py` | `SimChain` (exact pons integer math, hive rules, conservation check) and `RpcChain` (real reads; writes signed locally, intent journaled, `SWARM_LIVE=1` to broadcast, duplicate-send refusal, slippage floors, gas cap) |
| colony | `colony/colony.py` | the loop: feed → births → per fly: eat, starve, smell, think, act, split |
| ecology sim | `colony/sim.py` | boom / grind / silence regimes, ASCII population curve, `--connectome` for a real-brain run |
| **site** | `site/` | **LIVE https://www.swarm.family** (Vercel `swarm`, ollieagent; also swarm-sage-nine.vercel.app). sexfly.tech-style: black, mono, three.js point clouds. `neurons.bin` = 140,024 measured soma positions (`colony/cloud.py`); every fly streams the bitmap of neurons that fired in its last decision (`ConnectomeBrain.sense` → `_fired_bits` → zlib+b64 in state). Featured brain + CSS-grid of every fly, right rail of readouts incl. **What it feels** (measured Hz on each sensory population LB3c/LB1e, LB4a/LB1d/LB3b, LB1c, LPLC2, ORN_DA1 vs the market drive) and **What is lit** (fired neurons by region: optic lobes, central brain, sensory, descending, ascending, ventral cord, motor), About overlay with the scorecard. Never mention the earlier coin's ticker on the site (user rule). `/api/live` (relay) + `/api/hive` (on-chain reads). Shows a recorded real-brain run, labelled, until the queen connects. |
| daemon | `colony/run.py` | dry-run by default, restores flies from `build/flies/`, writes `build/state.json`, pushes to the flybrain relay protocol |
| tests | `tests/test_swarm.py` | 15 pass: genome, verdict, extinction, growth, conservation, split (ETH-rich and coin-rich), burn, burial, cap, dry-run never broadcasts, live sends once and refuses duplicates, burn tx targets dEaD |

Measured: one connectome decision at 900 steps ≈ 7 s on this PC → cap 64 flies at a 10-min tick.

## Ecology constants (`colony/config.py`, all printed on the site)

**Economy rule (user, 2026-09-11): capital stays in the colony.** 3% creator tax; ≥90% of everything
that ever enters is kept as ETH in the Hive + fly wallets and is recoverable (operator holds fly keys;
Hive eggs spawn to any address, 1/min). Burn is a *small* metabolism hard-capped at 10% of all income.

| | value | why |
|---|---|---|
| creator tax | 3% (`tax_bps=300`) | the food; user's choice |
| birth cost | 0.002 ETH | a few dollars, hundreds of L2 txs |
| birth interval | 60 s | the operator ceiling: ≤ 1440 births/day |
| lifespan | 288 ticks (2 days) | old age → wallet returns to the hive; turnover without loss |
| metabolism | 10% of a birth over a life, SWARM burned, coin-held first | tiny; `upkeep = birth×0.1/288` per tick |
| burn budget | ≤ 10% of all income ever (`burn_budget_frac`) | enforced in `Colony.burn_budget_left()`, persisted in `build/ledger.json` |
| exposure cap | ≤ 10% of a fly's worth in SWARM | coin is only recoverable while it has value |
| death floor | ¼ birth (trading losses) | |
| split at | 2 births | |
| buy size | 15% of ETH, bounded by the exposure cap | |
| tick | 600 s | compute-bound |

Leaks that are NOT recoverable: pons' 1% curve fee on every fly trade, the ≤10% burn, gas.
Silence now means **no growth**, not extinction (income 0 → no eggs; old-age deaths recycle).
Earlier sim-caught bugs (upkeep recycling, coin-rich immortals) are still guarded by tests.

## Honesty scorecard (put on the site)

| claim | truth |
|---|---|
| fees can only become flies | **Absolute.** `Hive` has no withdraw; `spawn` is the only outflow, fixed price, ≤1/min. Verifiable on Blockscout. |
| routing is irrevocable | pons can protocol-propose a new recipient on a timelock; read `pendingCreatorFeeRecipient` and show it. |
| a fly's money is the fly's | **No.** The queen holds every fly key. A fly's ETH is the operator's ETH. The contract caps how fast it can *become* the operator's: one birth per minute. |
| the brain is a fly | A simulated connectome (anatomy → LIF), not an animal. The market→senses mapping is editorial; the response is the wiring's. |
| no dev supply | `hatch` passes no exemptions and makes no opening buy. True only if nobody buys from the queen key afterwards. |
| flies are good traders | Not claimed. Selection acts on worth; the survivors' genomes are the result, whatever it is. Losses shown as loudly as splits. |
| capital is conserved | Yes minus pons 1% trade fee + ≤10% burn. Everything else moves only Hive ↔ fly wallets. |
| the colony is recoverable | Yes: operator holds fly keys; Hive spawns to any named address at 1 birth/min. Stated on the site as a feature. |

## Not built yet (in order)

1. **Deploy on mainnet**: `DEPLOYER_KEY=… npx hardhat run scripts/deploy.js --network robinhood`
   (deployer = queen). Needs ~0.002 ETH on Robinhood Chain. Check `canLaunch` first.
2. **Fund the first eggs**: send 0.01–0.02 ETH to the Hive (counts as a burial → 5–10 eggs).
3. **Queen on a VPS** (2 CPU is enough for ~30 flies at a 10-min tick; the fly VPS
   `193.149.129.108` already has the graph + venv): `SWARM_LIVE=1 python -m colony.run`.
   Keys live only in `build/flies/` on that box.
4. **Relay**: second pm2 instance of `flycoin/relay/relay.js` (port 4672, Caddy
   `swarm.162-252-198-162.sslip.io`, new secret). `run.py` already speaks its protocol.
5. **Site env** on Vercel `swarm` (www.swarm.family): `SWARM_RELAY_URL`, `SWARM_HIVE`, `SWARM_TOKEN`, `SWARM_CURVE`
   (+ `SWARM_RPC` if a paid RPC). The page is already live and flips from SIMULATION to LIVE by itself.
6. Later: adopt-a-fly (hold ≥ X → your address on a fly's page), genome explorer
   (which cell types drifted in the survivors), lower `steps` to raise the cap.

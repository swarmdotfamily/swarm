# SWARM runbook

**LIVE since 2026-09-11 06:32 UTC.** Hive `0x6F6B699AFb429135a5C0Ec827eb9C473fF545adA`, token SWARM
`0x074b33Bfb800b005CB7d1466a14Faa6e90Df7947`, curve `0xFBBAB018708d9b1c5F448Aa47F6b1C890CFF393b`,
queen `0x86543aAd59210f3649c45bD0edf4cCBc7961C6b9`, hatch tx `0x045dddd4…e9f6`.

| piece | where |
|---|---|
| queen daemon | **queen VPS `168.100.11.238`** (8 vCPU / 32 GB, BitLaunch), systemd `swarm`, code `/opt/swarm`, venv `/opt/swarm/venv`, connectome files `/opt/flybrain/`, log `/opt/swarm/build/queen.log`, fly keys `/opt/swarm/build/flies/`, ledger `/opt/swarm/build/ledger.json`; `SWARM_WORKERS=8`, `SWARM_MAX_FLIES=330` (16 decisions in 17 s). The old queen on the fly VPS `193.149.129.108` is disabled — never re-enable it while this one runs. |
| relay | VPS `162.252.198.162`, pm2 `swarm-relay`, port **4673**, `https://swarm.162-252-198-162.sslip.io` |
| site | Vercel `swarm` (ollieagent) → www.swarm.family, env SWARM_RELAY_URL/HIVE/TOKEN/CURVE set |
| queen key | `swarm/.env` on the PC and `/opt/swarm/.env` on the queen VPS only (a stale copy remains on the fly VPS) |

Ops: `systemctl restart swarm`, `tail -f /opt/swarm/build/queen.log`. Update code: scp `colony/` then restart
(never mid-tick if you can help it; a restart re-runs the tick, births are journaled and keys persist).
Never run a second queen (PC dry runs are fine, they never send). `DESIGN.md` has the mechanics.

## Local checks

    cd contracts && npx hardhat test            # 7 Hive tests
    python -m pytest -q tests                   # 15 ecology / money-path tests
    python -m colony.sim                        # stub-brain ecology, three regimes
    python -m colony.sim --connectome --ticks 2 # real 165k-neuron brains, 6 flies (slow)
    python -m colony.cloud                      # rebuild site/web/neurons.bin + build/cloud.npz (once)
    # refresh the site's recorded demo: python -m colony.sim --connectome --flies 12 --ticks 3 --seed-eth 0.03
    #   then copy build/sim-state.json -> site/web/demo.json with "demo": true

`contracts/node_modules` is a junction to `../par-contracts/node_modules` (same toolbox).

## Go live (human steps, in order)

1. Make the queen key (any EVM key; `python ../flycoin/rhwallet.py new` style, or ethers).
   Put it in `.env` as `SWARM_QUEEN_SECRET`. Never print it.
2. Fund the queen with ~0.003 ETH on Robinhood Chain (deploy + 0.0005 launch fee + gas).
3. Deploy + hatch (queen = deployer):

       cd contracts
       DEPLOYER_KEY=<queen> COIN_NAME="Swarm" COIN_SYMBOL=SWARM COIN_LOGO=<ipfs/https> \
       COIN_SITE=<url> COIN_X=<handle> TAX_BPS=500 npx hardhat run scripts/deploy.js --network robinhood

   → `build/hive.json` with hive / token / curve. Copy into `.env` (`SWARM_HIVE/TOKEN/CURVE`).
   Verify on Blockscout: `getLaunchedToken(token).creatorFeeRecipient == hive`.
4. First eggs: send 0.01–0.02 ETH to the Hive address (5–10 births).
5. Dry run on the PC: `python -m colony.run --once` — real reads, journaled unsent writes in
   `build/journal.jsonl`. Read it.
6. Move to the VPS (fly box `193.149.129.108`, `ssh -i ~/.ssh/ubi-vps root@…`):
   rsync `swarm/` (without `.env`), reuse `/opt/flybrain` for the graph (`SWARM_FLYCOIN_DIR`),
   write `.env` there with `SWARM_LIVE=1`, systemd unit like `flybrain`. Keys land in
   `build/flies/` on that box only.
7. Relay: copy `../flycoin/relay/` to `/opt/swarm-relay` on `.162`, `PORT=4672`,
   new `FLY_RELAY_SECRET`, Caddy block `swarm.162-252-198-162.sslip.io → 127.0.0.1:4672`.
   Set `SWARM_RELAY_PUSH=wss://…/push` + secret in the VPS `.env`, restart.
8. Site is LIVE at https://www.swarm.family (Vercel project `swarm`, ollieagent; deploy with
   `cd site && vercel deploy --prod --yes`). Add env `SWARM_RELAY_URL`, `SWARM_HIVE`, `SWARM_TOKEN`,
   `SWARM_CURVE` in the Vercel dashboard, redeploy. The page shows the simulation (labelled) until then.

## Kill switch

Stop the daemon (`systemctl stop swarm`). Flies then hold whatever they hold: no upkeep,
no trades, no deaths. The hive keeps collecting tax; nothing leaves it without `spawn`.
To wind down: run `python -m colony.run --once` with `SWARM_WIND_DOWN=1` (not written yet —
for now: for each key in `build/flies/`, sell tokens, send ETH to the hive; the hive's ETH
is then only spendable as births).

## Rules carried over

- Treasury must be the creator: here the Hive *is* the creator fee recipient by construction.
- `SWARM_LIVE=1` only on the VPS, never in the PC `.env`.
- Never run two queens: both would spawn, both would trade the same wallets (nonce clashes).
- Any `.env` change on the VPS needs a restart.

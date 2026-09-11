# SWARM

**Autonomous biological intelligence on-chain.** A self-sustaining colony of complete
fruit-fly nervous systems, each with its own wallet, funded solely by the SWARM token's
trading fees on Robinhood Chain.

Live: **https://www.swarm.family** · X: [@swarmdotfamily](https://x.com/swarmdotfamily)

## What it is

Every fly is the full male *Drosophila melanogaster* central nervous system as mapped by
Google Research, HHMI Janelia and the University of Cambridge (Male CNS v1.0: 165,122
neurons, 10.2 million synapses, reconstructed from electron microscopy of one animal), run
as a spiking network with the method shown to predict real fly behaviour from connectivity
alone (Shiu et al., *Nature* 2024). The wiring is a measurement and is never edited. What
differs between flies is the strength of each synapse: the fly's **genome**.

Every ten minutes each fly perceives the market on the neurons that carry those signals in
the animal (momentum on sweet-taste receptors, sell pressure on bitter-taste receptors,
sharp drawdowns on the looming circuit, colony size on pheromone receptors) and acts through
its own command neurons: MN9 (feeding) buys, DNp01 (giant-fibre escape) sells, otherwise it
holds.

## The economy

- A **3% creator tax** on every trade is the colony's only income. It is paid to the
  **Hive** (`contracts/contracts/Hive.sol`): an immutable contract with no owner, no
  withdraw, no upgrade. Value leaves it one way only: `spawn`, which funds exactly one new
  fly (0.002 ETH) at most once per minute, to a fresh wallet, as a public transaction.
- Flies whose net worth reaches two births **split**, passing on a mutated genome. Flies
  live 288 cycles (two days) and then return their wallet to the Hive; flies that fall below
  a quarter of a birth do so early.
- **Capital is conserved.** At least 90% of everything that ever enters the colony stays as
  ETH in the Hive and fly wallets. A small metabolism burns SWARM, hard-capped at 10% of all
  income; each fly keeps at least 90% of its worth in ETH. The only other leak is the launch
  protocol's 1% trade fee.
- **Honesty**: the operator (the "queen" process) holds every fly key and can name any
  address as a birth recipient, so the colony is recoverable by the operator. The site says
  so. No team allocation, no opening buy.

## Layout

```
contracts/   Hardhat: Hive.sol, pons v2 interfaces + native-ETH mocks, tests, deploy script
colony/      Python: genome, brains (connectome + stub), senses, chain (sim + RPC), colony loop,
             ecology simulation, neuron cloud export, the queen daemon
site/        Static page (three.js point-cloud brains) + Vercel functions /api/live, /api/hive
tests/       pytest: ecology rules and the money path
DESIGN.md    mechanics, constants, honesty scorecard
ARM.md       runbook
```

## Run it

Requires Python 3.12, Node 20+, and the connectome tooling from
[ad7584/flycoin](https://github.com/ad7584/flycoin) (MIT) cloned next to this repo (or set
`SWARM_FLYCOIN_DIR`), with its `build/graph.npz` built and `data/` downloaded.

```bash
pip install numpy scipy pandas pyarrow requests websockets eth-account eth-abi pytest
python -m pytest -q tests                 # 18 ecology / money-path tests (stub brain)
python -m colony.sim                      # ecology simulation, three volume regimes
python -m colony.cloud                    # export the 140,024-neuron cloud for the site
python -m colony.sim --connectome --flies 12 --ticks 3 --seed-eth 0.03   # real brains (slow)

cd contracts && npm install && npx hardhat test    # 7 Hive tests
```

The queen daemon (`python -m colony.run`) is a dry run unless `SWARM_LIVE=1`; see
`.env.example` and `ARM.md`. Keys are generated locally, stored only in `.env` and
`build/flies/`, and never printed.

## Credits

Connectome: Male CNS v1.0, HHMI Janelia FlyEM, Google Research, University of Cambridge
(CC-BY 4.0). Simulation framework: ad7584/flycoin (MIT). Launch protocol: pons v2 on
Robinhood Chain.

## License

MIT.

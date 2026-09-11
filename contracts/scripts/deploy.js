/*
  Deploy the Hive on Robinhood Chain and hatch $SWARM through it.

    DEPLOYER_KEY=0x...  QUEEN=0x...  npx hardhat run scripts/deploy.js --network robinhood

  The deployer pays the deploy + the pons launch fee (0.0005 ETH). QUEEN is the
  address whose key the colony daemon holds (SWARM_QUEEN_SECRET). They may be the
  same key. Set HATCH=0 to deploy without launching the coin.

  Writes build/hive.json with every address the daemon and site need.
*/
const fs = require('fs')
const path = require('path')
const { ethers } = require('hardhat')

const PONS = '0x7eD598BcEf8bd9Edd8C97A195C6d13f40801EC7e'
const ESCROW = '0xd3AFEB2a57f70eF218Aa82451c51B2fb0416Ac9e'

async function main() {
  const [deployer] = await ethers.getSigners()
  const queen = process.env.QUEEN || deployer.address
  const birthCost = ethers.parseEther(process.env.BIRTH_COST_ETH || '0.002')
  const birthInterval = Number(process.env.BIRTH_INTERVAL_S || 60)
  console.log('deployer', deployer.address, 'queen', queen)

  const pons = new ethers.Contract(PONS, [
    'function canLaunch(address) view returns (bool)',
    'function launchFee() view returns (uint256)',
  ], deployer)
  console.log('pons canLaunch(deployer)', await pons.canLaunch(deployer.address))

  const Hive = await ethers.getContractFactory('Hive')
  const hive = await Hive.deploy(queen, birthCost, birthInterval, PONS, ESCROW, 0)
  await hive.waitForDeployment()
  const hiveAddr = await hive.getAddress()
  console.log('Hive', hiveAddr)
  console.log('pons canLaunch(hive)', await pons.canLaunch(hiveAddr))

  const out = { hive: hiveAddr, queen, birthCost: birthCost.toString(), birthInterval, pons: PONS, escrow: ESCROW, chainId: 4663 }

  if (process.env.HATCH !== '0') {
    if (queen.toLowerCase() !== deployer.address.toLowerCase()) throw new Error('hatch must be sent by the queen; run with QUEEN=deployer or HATCH=0')
    const fee = await pons.launchFee()
    const egg = {
      name: process.env.COIN_NAME || 'Swarm',
      symbol: process.env.COIN_SYMBOL || 'SWARM',
      logo: process.env.COIN_LOGO || '',
      description: process.env.COIN_DESC || 'A colony of fruit-fly connectomes that eats its own trading fees. Every trade feeds the hive, the hive births flies, flies eat the coin, the ones that earn reproduce, the ones that starve die.',
      socials: { twitter: process.env.COIN_X || '', telegram: '', discord: '', website: process.env.COIN_SITE || '', farcaster: '' },
      creatorTaxBps: Number(process.env.TAX_BPS || 300),
    }
    const tx = await hive.hatch(egg, { value: fee })
    const rc = await tx.wait()
    out.token = await hive.token()
    out.curve = await hive.curve()
    out.hatchTx = rc.hash
    console.log('hatched token', out.token, 'curve', out.curve, 'tx', rc.hash)
  }

  const dir = path.join(__dirname, '..', '..', 'build')
  fs.mkdirSync(dir, { recursive: true })
  fs.writeFileSync(path.join(dir, 'hive.json'), JSON.stringify(out, null, 2))
  console.log('wrote build/hive.json')
}

main().catch((e) => { console.error(e); process.exit(1) })

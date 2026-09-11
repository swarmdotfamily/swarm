const { expect } = require('chai')
const { ethers } = require('hardhat')
const { time } = require('@nomicfoundation/hardhat-network-helpers')

const E = (x) => ethers.parseEther(String(x))
const BIRTH = E(0.002)
const INTERVAL = 60
const EGG = { name: 'x', symbol: 'X', logo: '', description: '', socials: { twitter: '', telegram: '', discord: '', website: '', farcaster: '' }, creatorTaxBps: 300 }
const SOCIALS = { twitter: '', telegram: '', discord: '', website: 'https://swarm.example', farcaster: '' }

async function rig() {
  const [deployer, queen, alice, bob, stranger] = await ethers.getSigners()
  const escrow = await (await ethers.getContractFactory('MockEscrow')).deploy()
  const pons = await (await ethers.getContractFactory('MockPonsFactory')).deploy(await escrow.getAddress())
  const hive = await (await ethers.getContractFactory('Hive')).deploy(
    queen.address, BIRTH, INTERVAL, await pons.getAddress(), await escrow.getAddress(), 0)
  const fee = await pons.launchFee()
  const tx = await hive.connect(queen).hatch({ name: 'Swarm', symbol: 'SWARM', logo: 'ipfs://logo', description: 'flies that eat fees', socials: SOCIALS, creatorTaxBps: 300 }, { value: fee })
  await tx.wait()
  const token = await ethers.getContractAt('MockERC20', await hive.token())
  const curve = await ethers.getContractAt('MockCurve', await hive.curve())
  return { deployer, queen, alice, bob, stranger, escrow, pons, hive, token, curve, fee }
}

describe('Hive', () => {
  it('hatches once, with itself as the fee recipient and no buyback', async () => {
    const { hive, pons, queen, fee } = await rig()
    const l = await pons.getLaunchedToken(await hive.token())
    expect(l.creatorFeeRecipient).to.equal(await hive.getAddress())
    expect(l.buybackEnabled).to.equal(false)
    expect(l.pairToken).to.equal(ethers.ZeroAddress)
    expect(l.creatorTaxBps).to.equal(300)
    await expect(hive.connect(queen).hatch(EGG, { value: fee }))
      .to.be.revertedWithCustomError(hive, 'AlreadyHatched')
  })

  it('only the queen hatches, and only with the exact launch fee', async () => {
    const [, queen, alice] = await ethers.getSigners()
    const escrow = await (await ethers.getContractFactory('MockEscrow')).deploy()
    const pons = await (await ethers.getContractFactory('MockPonsFactory')).deploy(await escrow.getAddress())
    const hive = await (await ethers.getContractFactory('Hive')).deploy(
      queen.address, BIRTH, INTERVAL, await pons.getAddress(), await escrow.getAddress(), 0)
    const fee = await pons.launchFee()
    await expect(hive.connect(alice).hatch(EGG, { value: fee }))
      .to.be.revertedWithCustomError(hive, 'NotQueen')
    await expect(hive.connect(queen).hatch(EGG, { value: fee + 1n }))
      .to.be.revertedWithCustomError(hive, 'WrongValue')
    await expect(hive.spawn(alice.address)).to.be.revertedWithCustomError(hive, 'NotQueen')
    await expect(hive.feed()).to.be.revertedWithCustomError(hive, 'NotHatched')
  })

  it('trades leave tax on the curve; anyone can feed it into the hive', async () => {
    const { hive, curve, token, alice, bob, stranger } = await rig()
    await curve.connect(alice).buy(E(1), 0, alice.address, { value: E(1) })
    await curve.connect(bob).buy(E(0.5), 0, bob.address, { value: E(0.5) })
    const half = (await token.balanceOf(alice.address)) / 2n
    await token.connect(alice).approve(await curve.getAddress(), half)
    await curve.connect(alice).sell(half, 0, alice.address)

    const [onCurve, inEscrow] = await hive.pending()
    expect(onCurve).to.be.gt(0n)
    expect(inEscrow).to.equal(0n)

    const before = await ethers.provider.getBalance(await hive.getAddress())
    await expect(hive.connect(stranger).feed()).to.emit(hive, 'Fed')
    const after = await ethers.provider.getBalance(await hive.getAddress())
    expect(after - before).to.equal(onCurve)
    expect(await hive.totalFed()).to.equal(onCurve)
    expect(await hive.totalBuried()).to.equal(0n)   // a claim is food, not a burial
    expect(await hive.eggs()).to.equal(onCurve / BIRTH)
    // a second feed with nothing pending is a no-op, not a revert
    await hive.feed()
    expect(await hive.totalFed()).to.equal(onCurve)
  })

  it('feed survives graduation (sweep reverts, claim still works)', async () => {
    const { hive, curve, alice } = await rig()
    await curve.connect(alice).buy(E(1), 0, alice.address, { value: E(1) })
    await hive.feed()
    await curve.connect(alice).buy(E(1), 0, alice.address, { value: E(1) })
    await curve.setGraduated(true)
    await expect(hive.feed()).to.not.be.reverted
  })

  it('spawn pays exactly one birth to a fresh address, rate-limited, only when an egg exists', async () => {
    const { hive, curve, alice, queen } = await rig()
    const c1 = ethers.Wallet.createRandom().address
    const c2 = ethers.Wallet.createRandom().address
    await expect(hive.connect(queen).spawn(c1)).to.be.revertedWithCustomError(hive, 'NoEgg')

    await curve.connect(alice).buy(E(1), 0, alice.address, { value: E(1) })   // 3% tax = 0.03 ETH = 15 eggs
    await hive.feed()
    expect(await hive.eggs()).to.equal(15n)

    await expect(hive.connect(queen).spawn(c1)).to.emit(hive, 'Born').withArgs(c1, 1n, BIRTH, E(0.03) - BIRTH)
    expect(await ethers.provider.getBalance(c1)).to.equal(BIRTH)
    expect(await hive.flyCount()).to.equal(1n)
    expect(await hive.totalSpawned()).to.equal(BIRTH)

    await expect(hive.connect(queen).spawn(c2)).to.be.revertedWithCustomError(hive, 'TooSoon')
    await time.increase(INTERVAL)
    await expect(hive.connect(queen).spawn(c1)).to.be.revertedWithCustomError(hive, 'BadChild')       // already born
    await expect(hive.connect(queen).spawn(queen.address)).to.be.revertedWithCustomError(hive, 'BadChild')
    await expect(hive.connect(queen).spawn(await hive.getAddress())).to.be.revertedWithCustomError(hive, 'BadChild')
    await expect(hive.connect(queen).spawn(c2)).to.emit(hive, 'Born')
    expect(await hive.births()).to.equal(2n)
  })

  it('the operator ceiling is births per interval and nothing else', async () => {
    const { hive, curve, alice, queen } = await rig()
    await curve.connect(alice).buy(E(2), 0, alice.address, { value: E(2) })
    await hive.feed()
    const bal = await ethers.provider.getBalance(await hive.getAddress())
    // there is no function that moves ETH except spawn
    const names = hive.interface.fragments.filter((f) => f.type === 'function' && f.stateMutability !== 'view').map((f) => f.name)
    expect(names.sort()).to.deep.equal(['feed', 'hatch', 'spawn'].sort())
    // ten intervals -> at most ten births' worth
    let out = 0n
    for (let i = 0; i < 10; i++) {
      await hive.connect(queen).spawn(ethers.Wallet.createRandom().address)
      out += BIRTH
      await time.increase(INTERVAL)
    }
    expect(bal - (await ethers.provider.getBalance(await hive.getAddress()))).to.equal(out)
  })

  it('a dying fly buries its ETH back into the hive and it counts as food for the next egg', async () => {
    const { hive, alice } = await rig()
    await expect(alice.sendTransaction({ to: await hive.getAddress(), value: BIRTH * 3n }))
      .to.emit(hive, 'Buried').withArgs(alice.address, BIRTH * 3n)
    expect(await hive.totalBuried()).to.equal(BIRTH * 3n)
    expect(await hive.eggs()).to.equal(3n)
  })
})

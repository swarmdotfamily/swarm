require('@nomicfoundation/hardhat-toolbox')
require('dotenv').config()

const RPC = (process.env.RH_RPC_URL || 'https://rpc.mainnet.chain.robinhood.com').split(',')[0].trim()

/** @type import('hardhat/config').HardhatUserConfig */
module.exports = {
  solidity: {
    version: '0.8.24',
    settings: { optimizer: { enabled: true, runs: 200 } },
  },
  networks: {
    // Local node pretends to be Robinhood Chain so the site can be pointed at it unchanged.
    // `chains` tells hardhat which hardfork Robinhood Chain runs, which a --fork needs.
    hardhat: {
      chainId: 4663,
      allowUnlimitedContractSize: false,
      chains: { 4663: { hardforkHistory: { cancun: 0 } } },
    },
    localhost: { url: 'http://127.0.0.1:8545', chainId: 4663 },
    fork: { url: 'http://127.0.0.1:8555', chainId: 4663 },
    robinhood: {
      url: RPC,
      chainId: 4663,
      accounts: process.env.DEPLOYER_KEY ? [process.env.DEPLOYER_KEY] : [],
    },
  },
  etherscan: {
    apiKey: { robinhood: 'blockscout' },
    customChains: [
      {
        network: 'robinhood',
        chainId: 4663,
        urls: {
          apiURL: 'https://robinhoodchain.blockscout.com/api',
          browserURL: 'https://robinhoodchain.blockscout.com',
        },
      },
    ],
  },
  sourcify: { enabled: true },
}

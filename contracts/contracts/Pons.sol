// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/*
    The slice of pons v2 the Hive talks to, native-ETH pair only.
    Struct layouts copied from the deployed factory ABI (docs.ponsfamily.com/llms.txt).
*/

struct Socials {
    string twitter;
    string telegram;
    string discord;
    string website;
    string farcaster;
}

struct TokenParams {
    string name;
    string symbol;
    string logo;
    string description;
    Socials socials;
    address creatorFeeRecipient;
    uint16 creatorTaxBps;
    bool buybackEnabled;
    bytes32 expectedEconomics;
    bytes32 salt;
}

struct LaunchedToken {
    address token;
    address curve;
    address deployer;
    address creatorFeeRecipient;
    address pairToken;
    uint256 graduationThreshold;
    uint24 poolFee;
    int24 tickSpacing;
    uint16 creatorTaxBps;
    bool buybackEnabled;
    uint8 phase;
    uint256 sweptQuote;
    uint256 sweptTokens;
    uint256 sweptAt;
    bool exists;
}

interface IPonsLaunchFactory {
    function launchToken(
        TokenParams calldata params,
        uint256 launchConfigId,
        address pairToken,
        address[] calldata snipeTaxExemptions
    ) external payable returns (address token, address curve);

    function previewLaunchEconomics(uint256 launchConfigId, address pairToken) external view returns (bytes32);
    function launchFee() external view returns (uint256);
    function canLaunch(address launcher) external view returns (bool);
    function getLaunchedToken(address token) external view returns (LaunchedToken memory);
}

interface IPonsCurve {
    function sweepFees(uint256 minBuybackTokensOut) external;
    function creatorTaxBalance() external view returns (uint256);
    function getReserves() external view returns (uint256 quote, uint256 token);
}

/// Native-ETH ledger of the fee escrow. (Token-pair launches use claimToken; the Hive never does.)
interface IPonsFeeEscrow {
    function claim() external;
    function balanceOf(address recipient) external view returns (uint256);
}

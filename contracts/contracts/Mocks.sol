// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "./Pons.sol";

/// Enough of pons v2, native-ETH pair, for the Hive to be tested end to end.

contract MockERC20 {
    string public name;
    string public symbol;
    uint8 public constant decimals = 18;
    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    constructor(string memory n, string memory s) {
        name = n;
        symbol = s;
    }

    function mint(address to, uint256 amount) external {
        totalSupply += amount;
        balanceOf[to] += amount;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        return true;
    }

    function transfer(address to, uint256 amount) public returns (bool) {
        require(balanceOf[msg.sender] >= amount, "bal");
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount;
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        require(balanceOf[from] >= amount, "bal");
        require(allowance[from][msg.sender] >= amount, "allow");
        allowance[from][msg.sender] -= amount;
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
        return true;
    }
}

/// The fee escrow's native ledger.
contract MockEscrow {
    mapping(address => uint256) public balanceOf;

    function credit(address recipient) external payable {
        balanceOf[recipient] += msg.value;
    }

    function claim() external {
        uint256 amount = balanceOf[msg.sender];
        require(amount > 0, "nothing to claim");
        balanceOf[msg.sender] = 0;
        (bool ok, ) = msg.sender.call{value: amount}("");
        require(ok, "send");
    }
}

/// A pons curve priced in native ETH. Fees come off the input, then constant product.
contract MockCurve {
    MockEscrow public immutable escrow;
    address public immutable recipient;
    MockERC20 public immutable token;

    uint256 public quoteReserve;      // includes the phantom quote
    uint256 public tokenReserve = 1_000_000_000 ether;
    uint256 public constant FEE_BPS = 100;
    uint256 public taxBps;
    uint256 public creatorTaxBalance;
    uint256 public quoteFeeBalance;
    bool public graduated;

    constructor(MockEscrow _escrow, address _recipient, MockERC20 _token, uint256 phantom, uint256 _taxBps) {
        escrow = _escrow;
        recipient = _recipient;
        token = _token;
        quoteReserve = phantom;
        taxBps = _taxBps;
    }

    function setGraduated(bool g) external { graduated = g; }
    function getReserves() external view returns (uint256, uint256) { return (quoteReserve, tokenReserve); }

    function buy(uint256 quoteIn, uint256 minTokensOut, address to) external payable returns (uint256 out) {
        require(msg.value == quoteIn, "value");
        uint256 fee = (quoteIn * FEE_BPS) / 10_000;
        uint256 tax = (quoteIn * taxBps) / 10_000;
        uint256 net = quoteIn - fee - tax;
        quoteFeeBalance += fee;
        creatorTaxBalance += tax;
        out = (net * tokenReserve) / (quoteReserve + net);
        require(out >= minTokensOut, "slippage");
        quoteReserve += net;
        tokenReserve -= out;
        token.mint(to, out);
    }

    function sell(uint256 tokensIn, uint256 minQuoteOut, address to) external returns (uint256 out) {
        require(token.transferFrom(msg.sender, address(this), tokensIn), "pull");
        uint256 gross = (tokensIn * quoteReserve) / (tokenReserve + tokensIn);
        uint256 fee = (gross * FEE_BPS) / 10_000;
        uint256 tax = (gross * taxBps) / 10_000;
        out = gross - fee - tax;
        require(out >= minQuoteOut, "slippage");
        quoteFeeBalance += fee;
        creatorTaxBalance += tax;
        quoteReserve -= gross;
        tokenReserve += tokensIn;
        (bool ok, ) = to.call{value: out}("");
        require(ok, "send");
    }

    /// Only the recipient may sweep, exactly as pons enforces. Post-graduation only the operator can.
    function sweepFees(uint256) external {
        require(msg.sender == recipient, "not recipient");
        require(!graduated, "operator only");
        uint256 amount = creatorTaxBalance;
        require(amount > 0, "nothing to sweep");
        creatorTaxBalance = 0;
        escrow.credit{value: amount}(recipient);
    }
}

contract MockPonsFactory {
    MockEscrow public immutable escrow;
    uint256 public launchFee = 0.0005 ether;
    bool public gateOpen = true;
    uint256 public phantom = 1.68 ether;
    mapping(address => LaunchedToken) private launches;
    address[] public tokens;

    constructor(MockEscrow _escrow) {
        escrow = _escrow;
    }

    function setGate(bool open) external { gateOpen = open; }
    function canLaunch(address) external view returns (bool) { return gateOpen; }

    function previewLaunchEconomics(uint256, address pair) external pure returns (bytes32) {
        return keccak256(abi.encode("economics", pair));
    }

    function launchToken(
        TokenParams calldata p,
        uint256,
        address pairToken,
        address[] calldata
    ) external payable returns (address token, address curve) {
        require(msg.value == launchFee, "fee");
        require(p.creatorFeeRecipient != address(0), "recipient");
        require(pairToken == address(0), "native only");
        require(p.expectedEconomics == keccak256(abi.encode("economics", pairToken)), "economics");

        MockERC20 t = new MockERC20(p.name, p.symbol);
        MockCurve c = new MockCurve(escrow, p.creatorFeeRecipient, t, phantom, p.creatorTaxBps);

        LaunchedToken storage l = launches[address(t)];
        l.token = address(t);
        l.curve = address(c);
        l.deployer = msg.sender;
        l.creatorFeeRecipient = p.creatorFeeRecipient;
        l.pairToken = pairToken;
        l.creatorTaxBps = p.creatorTaxBps;
        l.buybackEnabled = p.buybackEnabled;
        l.graduationThreshold = 4.2 ether;
        l.exists = true;

        tokens.push(address(t));
        return (address(t), address(c));
    }

    function getLaunchedToken(address token) external view returns (LaunchedToken memory) {
        return launches[token];
    }
}

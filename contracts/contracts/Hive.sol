// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "./Pons.sol";

/*
    THE HIVE - where the swarm's food lands.

    pons v2 pays a coin's creator tax to one address, its creatorFeeRecipient.
    For $SWARM that address is this contract. ETH can leave it in exactly one
    way:

        spawn(child)   the queen pays BIRTH_COST to a brand-new fly wallet,
                       at most one birth per BIRTH_INTERVAL.

    Everything else only puts ETH in:

        feed()         anyone: sweep the curve, claim the escrow, into here
        receive()      a dying fly sends back what it has left (a burial),
                       or anyone donates

    There is no withdraw, no owner, no fee-recipient transfer, no upgrade.
    The queen - the machine that runs the fly brains - can only turn ETH into
    flies, at a published fixed price and a published maximum rate. That is
    the honest ceiling on what an operator could ever take from the hive:
    BIRTH_COST per BIRTH_INTERVAL, in public, as births to addresses it names.
    The queen also holds every fly's key; a fly's ETH is the operator's ETH.
    Say so on the site.

    pons itself can still protocol-propose a new fee recipient on a timelock;
    read pendingCreatorFeeRecipient(token) on the factory and surface it.
*/
contract Hive {
    address public immutable queen;
    uint256 public immutable birthCost;
    uint256 public immutable birthInterval;
    IPonsLaunchFactory public immutable pons;
    IPonsFeeEscrow public immutable escrow;
    uint256 public immutable launchConfigId;

    address public token;
    address public curve;

    uint256 public lastBirth;
    uint256 public births;
    uint256 public totalFed;
    uint256 public totalSpawned;
    uint256 public totalBuried;
    address[] public flies;
    mapping(address => uint256) public bornAt;

    bool private feeding;

    event Hatched(address indexed token, address indexed curve, uint16 creatorTaxBps);
    event Fed(uint256 fresh, uint256 balanceAfter);
    event Born(address indexed child, uint256 indexed n, uint256 cost, uint256 balanceAfter);
    event Buried(address indexed from, uint256 amount);

    error NotQueen();
    error AlreadyHatched();
    error NotHatched();
    error WrongValue();
    error BadChild();
    error TooSoon();
    error NoEgg();
    error TransferFailed();

    modifier onlyQueen() {
        if (msg.sender != queen) revert NotQueen();
        _;
    }

    constructor(
        address _queen,
        uint256 _birthCost,
        uint256 _birthInterval,
        address _pons,
        address _escrow,
        uint256 _launchConfigId
    ) {
        require(_queen != address(0) && _birthCost != 0 && _pons != address(0) && _escrow != address(0), "args");
        queen = _queen;
        birthCost = _birthCost;
        birthInterval = _birthInterval;
        pons = IPonsLaunchFactory(_pons);
        escrow = IPonsFeeEscrow(_escrow);
        launchConfigId = _launchConfigId;
    }

    /* ------------------------------------------------------------------ */
    /*  birth of the coin                                                  */
    /* ------------------------------------------------------------------ */

    /// Launch $SWARM on pons with this contract as the fee recipient. Once.
    /// Native ETH pair, buyback off (so the Hive can sweep its own fees
    /// pre-graduation without pons' operator), no snipe-tax exemptions and
    /// no opening buy: the swarm starts with zero supply.
    struct Egg {
        string name;
        string symbol;
        string logo;
        string description;
        Socials socials;
        uint16 creatorTaxBps;
    }

    function hatch(Egg calldata e) external payable onlyQueen returns (address, address) {
        if (token != address(0)) revert AlreadyHatched();
        uint256 fee = pons.launchFee();
        if (msg.value != fee) revert WrongValue();

        TokenParams memory p;
        p.name = e.name;
        p.symbol = e.symbol;
        p.logo = e.logo;
        p.description = e.description;
        p.socials = e.socials;
        p.creatorFeeRecipient = address(this);
        p.creatorTaxBps = e.creatorTaxBps;
        p.buybackEnabled = false;
        p.expectedEconomics = pons.previewLaunchEconomics(launchConfigId, address(0));
        p.salt = keccak256(abi.encodePacked(address(this), block.chainid, block.number));

        (token, curve) = pons.launchToken{value: fee}(p, launchConfigId, address(0), new address[](0));
        emit Hatched(token, curve, e.creatorTaxBps);
        return (token, curve);
    }

    /* ------------------------------------------------------------------ */
    /*  food in                                                            */
    /* ------------------------------------------------------------------ */

    /// Pull the creator tax into the hive. Anyone may call. Both pons calls
    /// are best-effort: after graduation only pons' operator can sweep the
    /// pool, and the claim still collects whatever they swept.
    function feed() external returns (uint256 fresh) {
        if (curve == address(0)) revert NotHatched();
        uint256 before = address(this).balance;
        feeding = true;
        try IPonsCurve(curve).sweepFees(0) {} catch {}
        try escrow.claim() {} catch {}
        feeding = false;
        fresh = address(this).balance - before;
        totalFed += fresh;
        emit Fed(fresh, address(this).balance);
    }

    /// Burials and donations. Escrow claims arrive here too, flagged apart.
    receive() external payable {
        if (!feeding) {
            totalBuried += msg.value;
            emit Buried(msg.sender, msg.value);
        }
    }

    /* ------------------------------------------------------------------ */
    /*  flies out                                                          */
    /* ------------------------------------------------------------------ */

    /// One egg becomes one fly: BIRTH_COST to a fresh address, rate-limited.
    function spawn(address child) external onlyQueen {
        if (child == address(0) || child == queen || child == address(this) || bornAt[child] != 0) revert BadChild();
        if (block.timestamp < lastBirth + birthInterval) revert TooSoon();
        if (address(this).balance < birthCost) revert NoEgg();

        lastBirth = block.timestamp;
        births += 1;
        bornAt[child] = block.timestamp;
        flies.push(child);
        totalSpawned += birthCost;

        (bool ok, ) = child.call{value: birthCost}("");
        if (!ok) revert TransferFailed();
        emit Born(child, births, birthCost, address(this).balance);
    }

    /* ------------------------------------------------------------------ */
    /*  views                                                              */
    /* ------------------------------------------------------------------ */

    function eggs() external view returns (uint256) {
        return address(this).balance / birthCost;
    }

    function nextBirthAt() external view returns (uint256) {
        return lastBirth + birthInterval;
    }

    function pending() external view returns (uint256 onCurve, uint256 inEscrow) {
        if (curve != address(0)) {
            try IPonsCurve(curve).creatorTaxBalance() returns (uint256 v) { onCurve = v; } catch {}
        }
        try escrow.balanceOf(address(this)) returns (uint256 v) { inEscrow = v; } catch {}
    }

    function flyCount() external view returns (uint256) {
        return flies.length;
    }

    function snapshot()
        external
        view
        returns (
            uint256 balance,
            uint256 eggsNow,
            uint256 births_,
            uint256 totalFed_,
            uint256 totalSpawned_,
            uint256 totalBuried_,
            uint256 lastBirth_
        )
    {
        return (address(this).balance, address(this).balance / birthCost, births, totalFed, totalSpawned, totalBuried, lastBirth);
    }
}

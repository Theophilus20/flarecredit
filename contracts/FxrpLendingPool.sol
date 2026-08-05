// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function balanceOf(address who) external view returns (uint256);
}

interface ICreditRegistry {
    function getScore(address subject)
        external
        view
        returns (uint16 score, uint64 expiry, bytes32 codeHash);
}

interface IFtsoV2 {
    function getFeedById(bytes21 feedId)
        external
        view
        returns (uint256 value, int8 decimals, uint64 timestamp);
}

/// @title FxrpLendingPool
/// @notice Minimal FXRP lending pool on Coston2: C2FLR collateral in, FXRP out.
///         The consumer of the whole system  score >= 700 borrows at 120%
///         collateral instead of 150%. Everything is priced via FTSOv2 feeds.
/// @dev Hackathon-grade: no interest accrual, single liquidation threshold.
contract FxrpLendingPool {
    uint256 public constant BPS = 10_000;
    uint256 public constant DEFAULT_RATIO_BPS = 15_000;    // 150%
    uint256 public constant TRUSTED_RATIO_BPS = 12_000;    // 120%
    uint16 public constant TRUSTED_SCORE = 700;
    uint256 public constant LIQUIDATION_BONUS_BPS = 500;   // 5%

    bytes21 public constant FLR_USD = bytes21(0x01464c522f55534400000000000000000000000000);
    bytes21 public constant XRP_USD = bytes21(0x015852502f55534400000000000000000000000000);

    struct Position {
        uint256 collateralWei; // native C2FLR
        uint256 debtFxrp;      // FXRP smallest units (6 decimals for FXRP)
    }

    event Deposited(address indexed who, uint256 amountWei);
    event Withdrawn(address indexed who, uint256 amountWei);
    event Borrowed(address indexed who, uint256 amountFxrp, uint256 ratioBps);
    event Repaid(address indexed who, uint256 amountFxrp);
    event Liquidated(address indexed who, address indexed by, uint256 debtCovered);

    IERC20 public immutable fxrp;
    ICreditRegistry public immutable creditRegistry;
    IFtsoV2 public immutable ftso;
    uint8 public immutable fxrpDecimals;

    mapping(address => Position) public positions;

    constructor(address _fxrp, address _creditRegistry, address _ftso, uint8 _fxrpDecimals) {
        fxrp = IERC20(_fxrp);
        creditRegistry = ICreditRegistry(_creditRegistry);
        ftso = IFtsoV2(_ftso);
        fxrpDecimals = _fxrpDecimals;
    }

    // ------------------------------------------------------------- deposits
    function deposit() external payable {
        require(msg.value > 0, "zero deposit");
        positions[msg.sender].collateralWei += msg.value;
        emit Deposited(msg.sender, msg.value);
    }

    function withdraw(uint256 amountWei) external {
        Position storage p = positions[msg.sender];
        require(p.collateralWei >= amountWei, "insufficient collateral");
        p.collateralWei -= amountWei;
        require(_healthy(msg.sender), "would undercollateralise");
        (bool ok, ) = msg.sender.call{value: amountWei}("");
        require(ok, "transfer failed");
        emit Withdrawn(msg.sender, amountWei);
    }

    // -------------------------------------------------------------- borrows
    /// @notice The score-gated ratio  the product moment of the demo.
    function collateralRatioBps(address borrower) public view returns (uint256) {
        (uint16 score, uint64 expiry, ) = creditRegistry.getScore(borrower);
        if (score >= TRUSTED_SCORE && expiry > block.timestamp) {
            return TRUSTED_RATIO_BPS;
        }
        return DEFAULT_RATIO_BPS;
    }

    function borrow(uint256 amountFxrp) external {
        Position storage p = positions[msg.sender];
        p.debtFxrp += amountFxrp;
        require(_healthy(msg.sender), "exceeds borrowing power");
        require(fxrp.transfer(msg.sender, amountFxrp), "FXRP transfer failed");
        emit Borrowed(msg.sender, amountFxrp, collateralRatioBps(msg.sender));
    }

    function repay(uint256 amountFxrp) external {
        Position storage p = positions[msg.sender];
        uint256 amount = amountFxrp > p.debtFxrp ? p.debtFxrp : amountFxrp;
        require(fxrp.transferFrom(msg.sender, address(this), amount), "pull failed");
        p.debtFxrp -= amount;
        emit Repaid(msg.sender, amount);
    }

    function liquidate(address who) external {
        require(!_healthy(who), "position healthy");
        Position storage p = positions[who];
        uint256 debt = p.debtFxrp;
        require(fxrp.transferFrom(msg.sender, address(this), debt), "pull failed");
        uint256 seizeWei = _fxrpToWei(debt) * (BPS + LIQUIDATION_BONUS_BPS) / BPS;
        if (seizeWei > p.collateralWei) seizeWei = p.collateralWei;
        p.debtFxrp = 0;
        p.collateralWei -= seizeWei;
        (bool ok, ) = msg.sender.call{value: seizeWei}("");
        require(ok, "transfer failed");
        emit Liquidated(who, msg.sender, debt);
    }

    // ---------------------------------------------------------------- views
    function maxBorrowable(address borrower) external view returns (uint256) {
        Position memory p = positions[borrower];
        uint256 capacityFxrp = _weiToFxrp(p.collateralWei) * BPS / collateralRatioBps(borrower);
        return capacityFxrp > p.debtFxrp ? capacityFxrp - p.debtFxrp : 0;
    }

    // ------------------------------------------------------------- internal
    function _healthy(address who) internal view returns (bool) {
        Position memory p = positions[who];
        if (p.debtFxrp == 0) return true;
        uint256 requiredWei =
            _fxrpToWei(p.debtFxrp) * collateralRatioBps(who) / BPS;
        return p.collateralWei >= requiredWei;
    }

    /// @dev USD value bridging via FTSOv2: wei * FLR/USD == fxrp * XRP/USD.
    function _fxrpToWei(uint256 amountFxrp) internal view returns (uint256) {
        (uint256 flrUsd, int8 flrDec, ) = ftso.getFeedById(FLR_USD);
        (uint256 xrpUsd, int8 xrpDec, ) = ftso.getFeedById(XRP_USD);
        // wei = fxrp * xrpUsd/10^xrpDec / (flrUsd/10^flrDec) * 10^18 / 10^fxrpDecimals
        return amountFxrp * xrpUsd * (10 ** uint8(flrDec)) * 1e18
            / (flrUsd * (10 ** uint8(xrpDec)) * (10 ** fxrpDecimals));
    }

    function _weiToFxrp(uint256 amountWei) internal view returns (uint256) {
        (uint256 flrUsd, int8 flrDec, ) = ftso.getFeedById(FLR_USD);
        (uint256 xrpUsd, int8 xrpDec, ) = ftso.getFeedById(XRP_USD);
        return amountWei * flrUsd * (10 ** uint8(xrpDec)) * (10 ** fxrpDecimals)
            / (xrpUsd * (10 ** uint8(flrDec)) * 1e18);
    }

    receive() external payable {
        positions[msg.sender].collateralWei += msg.value;
        emit Deposited(msg.sender, msg.value);
    }
}

// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @notice Local-testing stand-in for FTSOv2 (used by the deploy test suite
///         only  on Coston2 the pool gets the real FtsoV2 via the registry).
contract MockFtsoV2 {
    mapping(bytes21 => uint256) public price;
    mapping(bytes21 => int8) public dec;

    function setFeed(bytes21 id, uint256 value, int8 decimals_) external {
        price[id] = value;
        dec[id] = decimals_;
    }

    function getFeedById(bytes21 id)
        external
        view
        returns (uint256, int8, uint64)
    {
        return (price[id], dec[id], uint64(block.timestamp));
    }
}

// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title IdentityLinkRegistry
/// @notice Stores only the keccak hash of (flareAddress, xrplAddress, nonce).
///         The XRPL address itself never appears on-chain; the enclave receives
///         the preimage privately and recomputes the hash to check the link.
contract IdentityLinkRegistry {
    event Linked(address indexed flareAddress, bytes32 bindingHash);

    mapping(address => bytes32) public bindingOf;

    function link(bytes32 bindingHash) external {
        require(bindingHash != bytes32(0), "empty binding");
        bindingOf[msg.sender] = bindingHash;
        emit Linked(msg.sender, bindingHash);
    }

    function unlink() external {
        delete bindingOf[msg.sender];
        emit Linked(msg.sender, bytes32(0));
    }
}

// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title CreditRegistry
/// @notice Accepts credit scores only when signed by an attested TEE key that
///         is whitelisted for a specific enclave code hash. Reproducible
///         builds (SOURCE_DATE_EPOCH) make the code hash independently
///         checkable, which is FCC's trust model: trust the code, not the
///         operator. Registration of (codeHash -> teeSigner) is owner-gated
///         here for the hackathon; in production it should be driven by
///         TeeMachineRegistry / TeeExtensionRegistry attestation verification.
contract CreditRegistry {
    struct Score {
        uint16 score;     // 0..1000
        uint64 expiry;    // unix seconds; 30-day validity window in v1
        bytes32 codeHash; // enclave image hash that produced the score
    }

    event ScoreSubmitted(address indexed subject, uint16 score, uint64 expiry, bytes32 codeHash);
    event TeeSignerRegistered(bytes32 indexed codeHash, address signer);

    address public owner;
    mapping(bytes32 => address) public teeSignerOf;
    mapping(address => Score) private _scores;

    modifier onlyOwner() {
        require(msg.sender == owner, "not owner");
        _;
    }

    constructor() {
        owner = msg.sender;
    }

    /// @notice Whitelist the enclave signing key for a given code hash.
    function registerTeeSigner(bytes32 codeHash, address signer) external onlyOwner {
        teeSignerOf[codeHash] = signer;
        emit TeeSignerRegistered(codeHash, signer);
    }

    /// @notice Anyone may relay a score; validity comes from the TEE signature.
    /// @dev digest = keccak256(subject, score, expiry, codeHash), signed as an
    ///      Ethereum signed message by the enclave key.
    function submitScore(
        address subject,
        uint16 score,
        uint64 expiry,
        bytes32 codeHash,
        bytes calldata signature
    ) external {
        require(score <= 1000, "score out of range");
        require(expiry > block.timestamp, "score already expired");
        address signer = teeSignerOf[codeHash];
        require(signer != address(0), "code hash not attested");

        bytes32 digest = keccak256(
            abi.encodePacked(
                "\x19Ethereum Signed Message:\n32",
                keccak256(abi.encodePacked(subject, score, expiry, codeHash))
            )
        );
        require(_recover(digest, signature) == signer, "bad TEE signature");

        _scores[subject] = Score(score, expiry, codeHash);
        emit ScoreSubmitted(subject, score, expiry, codeHash);
    }

    /// @notice Returns (0, 0, 0x0) once the validity window has lapsed.
    function getScore(address subject)
        external
        view
        returns (uint16 score, uint64 expiry, bytes32 codeHash)
    {
        Score memory s = _scores[subject];
        if (s.expiry <= block.timestamp) return (0, 0, bytes32(0));
        return (s.score, s.expiry, s.codeHash);
    }

    function _recover(bytes32 digest, bytes calldata sig) private pure returns (address) {
        require(sig.length == 65, "bad signature length");
        bytes32 r = bytes32(sig[0:32]);
        bytes32 s = bytes32(sig[32:64]);
        uint8 v = uint8(sig[64]);
        if (v < 27) v += 27;
        require(v == 27 || v == 28, "bad v");
        return ecrecover(digest, v, r, s);
    }
}

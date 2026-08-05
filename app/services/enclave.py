"""Bridge to the scoring enclave (FCC / GCP Confidential Space).

Two transport paths, per the architecture:

  A. Direct HTTPS to the Go extension (fce-extension-scaffold exposes an
     endpoint inside Confidential Space). Fastest for the demo.
  B. On-chain via InstructionSender with the CREDIT/SCORE OPType/OPCommand
     pair  the canonical FCC path; the enclave watches for instructions.

Either way the payload is the same: the FDC Merkle proofs + the identity
binding preimage. The enclave verifies proofs against on-chain Merkle roots,
recomputes the binding hash, runs the v1 score model, and returns
(subject, score, expiry, codeHash) signed by its attested TEE key.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
from eth_abi.packed import encode_packed
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import keccak
from web3 import Web3

from ..chains import (
    INSTRUCTION_SENDER_ABI,
    OP_COMMAND_SCORE,
    OP_TYPE_CREDIT,
    w3,
)
from ..config import get_settings


class EnclaveError(RuntimeError):
    pass


async def request_score_https(
    subject: str,
    proofs: list[dict[str, Any]],
    binding: dict[str, str],
) -> dict[str, Any]:
    """POST proofs to the enclave and return its signed score envelope."""
    s = get_settings()
    if not s.enclave_url:
        raise EnclaveError("ENCLAVE_URL not configured")
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{s.enclave_url.rstrip('/')}/score",
            json={"subject": subject, "proofs": proofs, "binding": binding},
        )
    if r.status_code != 200:
        raise EnclaveError(f"enclave returned {r.status_code}: {r.text[:300]}")
    envelope = r.json()
    verify_envelope(envelope)
    return envelope


def request_score_onchain(
    subject: str, proofs: list[dict[str, Any]], binding: dict[str, str]
) -> dict[str, Any]:
    """Route the request through InstructionSender (CREDIT/SCORE)."""
    s = get_settings()
    if not s.backend_private_key:
        raise EnclaveError("BACKEND_PRIVATE_KEY required for on-chain instruction path")
    if int(s.instruction_sender, 16) == 0:
        raise EnclaveError("INSTRUCTION_SENDER not configured")
    acct = Account.from_key(s.backend_private_key)
    sender = w3().eth.contract(
        address=Web3.to_checksum_address(s.instruction_sender),
        abi=INSTRUCTION_SENDER_ABI,
    )
    payload = json.dumps(
        {"subject": subject, "proofs": proofs, "binding": binding}
    ).encode()
    tx = sender.functions.sendInstruction(
        OP_TYPE_CREDIT, OP_COMMAND_SCORE, payload
    ).build_transaction(
        {
            "from": acct.address,
            "nonce": w3().eth.get_transaction_count(acct.address),
            "gas": 500_000,
            "maxFeePerGas": w3().eth.gas_price * 2,
            "maxPriorityFeePerGas": Web3.to_wei(1, "gwei"),
            "chainId": 114,
        }
    )
    signed = acct.sign_transaction(tx)
    tx_hash = w3().eth.send_raw_transaction(signed.raw_transaction)
    w3().eth.wait_for_transaction_receipt(tx_hash, timeout=90)
    return {"txHash": tx_hash.to_0x_hex(), "note": "enclave will emit the signed score"}


def score_digest(subject: str, score: int, expiry: int, code_hash: str) -> bytes:
    """keccak(subject, score, expiry, codeHash)  must match CreditRegistry.sol."""
    return keccak(
        encode_packed(
            ["address", "uint16", "uint64", "bytes32"],
            [subject, score, expiry, Web3.to_bytes(hexstr=code_hash)],
        )
    )


def verify_envelope(envelope: dict[str, Any]) -> None:
    """Check the enclave's signature and code hash before trusting the score.

    CreditRegistry re-checks this on-chain; verifying here too gives the user
    a clear error before they spend gas.
    """
    s = get_settings()
    required = {"subject", "score", "expiry", "codeHash", "signature", "signer"}
    missing = required - envelope.keys()
    if missing:
        raise EnclaveError(f"envelope missing fields: {sorted(missing)}")
    if (
        int(s.expected_code_hash, 16) != 0
        and envelope["codeHash"].lower() != s.expected_code_hash.lower()
    ):
        raise EnclaveError(
            "enclave code hash does not match EXPECTED_CODE_HASH  refusing "
            "(reproducible-build check failed)"
        )
    digest = score_digest(
        envelope["subject"],
        int(envelope["score"]),
        int(envelope["expiry"]),
        envelope["codeHash"],
    )
    recovered = Account.recover_message(
        encode_defunct(digest), signature=envelope["signature"]
    )
    if recovered.lower() != envelope["signer"].lower():
        raise EnclaveError("signature does not recover to the declared TEE signer")

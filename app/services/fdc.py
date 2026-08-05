"""FDC Payment attestation flow (XRPL testnet -> Coston2).

Steps, matching the Flare docs flow:
  1. prepare   POST to the verifier server, get abiEncodedRequest
  2. request   FdcHub.requestAttestation{value: fee}(abiEncodedRequest)
  3. proof     poll the DA layer for the Merkle proof once the round finalises

The Merkle proof is NOT verified on a public contract here. It is forwarded
to the scoring enclave (services/enclave.py), which checks it against the
on-chain Merkle root inside the TEE  that is what keeps history private.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx
from eth_account import Account
from web3 import Web3

from ..chains import fdc_fee_config, fdc_hub, voting_round_for_timestamp, w3
from ..config import get_settings

ATTESTATION_TYPE_PAYMENT = "0x" + "Payment".encode().hex().ljust(64, "0")
SOURCE_ID_TEST_XRP = "0x" + "testXRP".encode().hex().ljust(64, "0")


class FdcError(RuntimeError):
    pass


async def prepare_payment_request(transaction_id: str) -> str:
    """Ask the verifier to encode a Payment attestation request for an XRPL tx."""
    s = get_settings()
    tx = transaction_id.lower().removeprefix("0x")
    body = {
        "attestationType": ATTESTATION_TYPE_PAYMENT,
        "sourceId": SOURCE_ID_TEST_XRP,
        "requestBody": {"transactionId": "0x" + tx, "inUtxo": "0", "utxo": "0"},
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{s.fdc_verifier_url}/verifier/xrp/Payment/prepareRequest",
            json=body,
            headers={"X-API-KEY": s.fdc_verifier_api_key},
        )
    if r.status_code != 200:
        raise FdcError(f"verifier returned {r.status_code}: {r.text[:300]}")
    data = r.json()
    if data.get("status") != "VALID":
        raise FdcError(f"verifier rejected request: {data}")
    return data["abiEncodedRequest"]


def get_request_fee(abi_encoded_request: str) -> int:
    return fdc_fee_config().functions.getRequestFee(
        Web3.to_bytes(hexstr=abi_encoded_request)
    ).call()


def request_attestation(abi_encoded_request: str) -> dict[str, Any]:
    """Submit the request on-chain with the backend relayer key, paying the fee.

    Returns tx hash and the voting round the request landed in  the round id
    is what the DA layer needs to serve the proof.
    """
    s = get_settings()
    if not s.backend_private_key:
        raise FdcError(
            "BACKEND_PRIVATE_KEY not set  either fund a relayer key or submit "
            "the request from the user's wallet in the UI (the frontend "
            "supports both)."
        )
    acct = Account.from_key(s.backend_private_key)
    hub = fdc_hub()
    fee = get_request_fee(abi_encoded_request)
    tx = hub.functions.requestAttestation(
        Web3.to_bytes(hexstr=abi_encoded_request)
    ).build_transaction(
        {
            "from": acct.address,
            "value": fee,
            "nonce": w3().eth.get_transaction_count(acct.address),
            "gas": 300_000,
            "maxFeePerGas": w3().eth.gas_price * 2,
            "maxPriorityFeePerGas": Web3.to_wei(1, "gwei"),
            "chainId": 114,
        }
    )
    signed = acct.sign_transaction(tx)
    tx_hash = w3().eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3().eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    block = w3().eth.get_block(receipt.blockNumber)
    round_id = voting_round_for_timestamp(block.timestamp)
    return {
        "txHash": tx_hash.to_0x_hex(),
        "votingRoundId": round_id,
        "feeWei": str(fee),
    }


async def get_proof(voting_round_id: int, abi_encoded_request: str) -> dict[str, Any]:
    """Fetch the Merkle proof + attested response from the DA layer.

    Rounds finalise ~90–180s after the request; callers should poll.
    """
    s = get_settings()
    payload = {"votingRoundId": voting_round_id, "requestBytes": abi_encoded_request}
    headers = {"X-API-KEY": s.fdc_verifier_api_key}
    async with httpx.AsyncClient(timeout=30) as client:
        # Prefer the JSON endpoint: named fields (sourceAddressHash, amounts,
        # timestamps) let enclaves check the sender without ABI decoding.
        r = await client.post(
            f"{s.da_layer_url}/api/v1/fdc/proof-by-request-round",
            json=payload, headers=headers,
        )
        if r.status_code != 200:
            r = await client.post(
                f"{s.da_layer_url}/api/v1/fdc/proof-by-request-round-raw",
                json=payload, headers=headers,
            )
    if r.status_code in (400, 404):
        return {"ready": False}
    if r.status_code != 200:
        raise FdcError(f"DA layer returned {r.status_code}: {r.text[:300]}")
    data = r.json()
    if not data.get("proof"):
        return {"ready": False}
    return {"ready": True, "proof": data["proof"], "response": data.get("response") or data.get("response_hex")}


async def wait_for_proof(
    voting_round_id: int, abi_encoded_request: str, attempts: int = 20, delay_s: float = 10.0
) -> dict[str, Any]:
    for _ in range(attempts):
        result = await get_proof(voting_round_id, abi_encoded_request)
        if result["ready"]:
            return result
        await asyncio.sleep(delay_s)
    raise FdcError("proof not available yet  round may not have finalised")

"""Reads/writes against our own contracts + FTSOv2 price reads."""
from __future__ import annotations

import time
from typing import Any

from eth_account import Account
from web3 import Web3

from ..chains import (
    CREDIT_REGISTRY_ABI,
    FEED_IDS,
    IDENTITY_REGISTRY_ABI,
    LENDING_POOL_ABI,
    ftso_v2,
    w3,
)
from ..config import get_settings


def _addr_set(a: str) -> bool:
    return int(a, 16) != 0


def credit_registry():
    s = get_settings()
    return w3().eth.contract(
        address=Web3.to_checksum_address(s.credit_registry), abi=CREDIT_REGISTRY_ABI
    )


def identity_registry():
    s = get_settings()
    return w3().eth.contract(
        address=Web3.to_checksum_address(s.identity_registry), abi=IDENTITY_REGISTRY_ABI
    )


def lending_pool():
    s = get_settings()
    return w3().eth.contract(
        address=Web3.to_checksum_address(s.lending_pool), abi=LENDING_POOL_ABI
    )


def get_score(subject: str) -> dict[str, Any]:
    s = get_settings()
    if not _addr_set(s.credit_registry):
        return {"configured": False}
    score, expiry, code_hash = credit_registry().functions.getScore(
        Web3.to_checksum_address(subject)
    ).call()
    now = int(time.time())
    return {
        "configured": True,
        "score": score,
        "expiry": expiry,
        "valid": expiry > now and score > 0,
        "daysRemaining": max(0, (expiry - now) // 86_400),
        "codeHash": "0x" + code_hash.hex(),
    }


def get_binding(subject: str) -> dict[str, Any]:
    s = get_settings()
    if not _addr_set(s.identity_registry):
        return {"configured": False}
    h = identity_registry().functions.bindingOf(
        Web3.to_checksum_address(subject)
    ).call()
    return {
        "configured": True,
        "linked": int.from_bytes(h, "big") != 0,
        "bindingHash": "0x" + h.hex(),
    }


def submit_score(envelope: dict[str, Any]) -> dict[str, Any]:
    """Relay the enclave-signed score to CreditRegistry with the backend key."""
    s = get_settings()
    if not s.backend_private_key:
        raise RuntimeError(
            "BACKEND_PRIVATE_KEY not set  submit from the user's wallet in the UI"
        )
    acct = Account.from_key(s.backend_private_key)
    tx = credit_registry().functions.submitScore(
        Web3.to_checksum_address(envelope["subject"]),
        int(envelope["score"]),
        int(envelope["expiry"]),
        Web3.to_bytes(hexstr=envelope["codeHash"]),
        Web3.to_bytes(hexstr=envelope["signature"]),
    ).build_transaction(
        {
            "from": acct.address,
            "nonce": w3().eth.get_transaction_count(acct.address),
            "gas": 250_000,
            "maxFeePerGas": w3().eth.gas_price * 2,
            "maxPriorityFeePerGas": Web3.to_wei(1, "gwei"),
            "chainId": 114,
        }
    )
    signed = acct.sign_transaction(tx)
    tx_hash = w3().eth.send_raw_transaction(signed.raw_transaction)
    w3().eth.wait_for_transaction_receipt(tx_hash, timeout=90)
    return {"txHash": tx_hash.to_0x_hex()}


def get_prices() -> dict[str, Any]:
    """FTSOv2 block-latency feeds  free to read via eth_call."""
    out = {}
    contract = ftso_v2()
    for pair, feed_id in FEED_IDS.items():
        value, decimals, ts = contract.functions.getFeedById(
            Web3.to_bytes(hexstr=feed_id)
        ).call()
        out[pair] = {
            "price": value / (10**decimals),
            "raw": str(value),
            "decimals": decimals,
            "timestamp": ts,
        }
    return out


def get_lending_state(subject: str) -> dict[str, Any]:
    s = get_settings()
    if not _addr_set(s.lending_pool):
        return {"configured": False}
    pool = lending_pool()
    addr = Web3.to_checksum_address(subject)
    collateral, debt = pool.functions.positions(addr).call()
    return {
        "configured": True,
        "collateralWei": str(collateral),
        "debtFxrp": str(debt),
        "collateralRatioBps": pool.functions.collateralRatioBps(addr).call(),
        "maxBorrowableFxrp": str(pool.functions.maxBorrowable(addr).call()),
        "poolAddress": s.lending_pool,
        "fxrpToken": s.fxrp_token,
    }

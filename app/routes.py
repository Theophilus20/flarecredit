"""API routes. Grouped in one module for hackathon legibility."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .services import fdc, registry
from .services import xrpl_identity as xid
from .services import enclave
from .chains import COSTON2_CHAIN_ID, voting_round_for_timestamp
from .config import get_settings

router = APIRouter(prefix="/api")


# ---- naive in-memory rate limiter (swap for Redis in production) -----------
import time as _time
from collections import defaultdict, deque

_hits: dict[str, deque] = defaultdict(deque)


def rate_limit(bucket: str, key: str, limit: int = 10, window_s: int = 60) -> None:
    """Allow `limit` calls per `window_s` per key; raise 429 beyond that."""
    q = _hits[f"{bucket}:{key}"]
    now = _time.monotonic()
    while q and now - q[0] > window_s:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, "rate limited  slow down")
    q.append(now)


# ----------------------------------------------------------------- identity
class ChallengeIn(BaseModel):
    flareAddress: str = Field(pattern=r"^0x[0-9a-fA-F]{40}$")
    xrplAddress: str = Field(min_length=25, max_length=35)


class VerifyIn(ChallengeIn):
    signature: str
    publicKey: str


@router.post("/identity/challenge")
def identity_challenge(body: ChallengeIn):
    rate_limit("challenge", body.flareAddress.lower(), limit=5, window_s=60)
    return xid.make_challenge(body.flareAddress, body.xrplAddress)


@router.post("/identity/verify")
def identity_verify(body: VerifyIn):
    rate_limit("verify", body.flareAddress.lower(), limit=5, window_s=60)
    try:
        return xid.verify_signature(
            body.flareAddress, body.xrplAddress, body.signature, body.publicKey
        )
    except xid.IdentityError as e:
        raise HTTPException(400, str(e))


@router.get("/identity/{address}")
def identity_status(address: str):
    try:
        return registry.get_binding(address)
    except Exception as e:  # RPC not reachable etc.
        raise HTTPException(502, f"chain read failed: {e}")


# -------------------------------------------------------------- attestations
class PrepareIn(BaseModel):
    transactionId: str = Field(min_length=64, max_length=66)


class RequestIn(BaseModel):
    abiEncodedRequest: str


class ProofIn(BaseModel):
    votingRoundId: int
    abiEncodedRequest: str


@router.post("/fdc/prepare")
async def fdc_prepare(body: PrepareIn):
    rate_limit("prepare", body.transactionId.lower(), limit=3, window_s=300)
    try:
        encoded = await fdc.prepare_payment_request(body.transactionId)
        return {"abiEncodedRequest": encoded, "feeWei": str(fdc.get_request_fee(encoded))}
    except fdc.FdcError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"verifier/chain unreachable: {e}")


@router.post("/fdc/request")
def fdc_request(body: RequestIn):
    try:
        return fdc.request_attestation(body.abiEncodedRequest)
    except fdc.FdcError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"attestation request failed: {e}")


@router.post("/fdc/proof")
async def fdc_proof(body: ProofIn):
    try:
        return await fdc.get_proof(body.votingRoundId, body.abiEncodedRequest)
    except fdc.FdcError as e:
        raise HTTPException(502, str(e))


@router.get("/fdc/round-for/{timestamp}")
def fdc_round(timestamp: int):
    return {"votingRoundId": voting_round_for_timestamp(timestamp)}


# --------------------------------------------------------------------- score
class ComputeIn(BaseModel):
    subject: str = Field(pattern=r"^0x[0-9a-fA-F]{40}$")
    proofs: list[dict[str, Any]]
    binding: dict[str, str]  # {xrplAddress, nonce}
    transport: str = "https"  # "https" | "onchain"
    autoSubmit: bool = False


@router.post("/score/compute")
async def score_compute(body: ComputeIn):
    try:
        if body.transport == "onchain":
            return enclave.request_score_onchain(body.subject, body.proofs, body.binding)
        envelope = await enclave.request_score_https(body.subject, body.proofs, body.binding)
        result: dict[str, Any] = {"envelope": envelope}
        if body.autoSubmit:
            result["submission"] = registry.submit_score(envelope)
        return result
    except enclave.EnclaveError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, str(e))


class EnvelopeIn(BaseModel):
    subject: str
    score: int
    expiry: int
    codeHash: str
    signature: str
    signer: str


@router.post("/score/submit")
def score_submit(body: EnvelopeIn):
    envelope = body.model_dump()
    try:
        enclave.verify_envelope(envelope)
        return registry.submit_score(envelope)
    except enclave.EnclaveError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, str(e))


@router.get("/score/{address}")
def score_read(address: str):
    try:
        return registry.get_score(address)
    except Exception as e:
        raise HTTPException(502, f"chain read failed: {e}")


# -------------------------------------------------------------------- market
@router.get("/market/prices")
def market_prices():
    try:
        return registry.get_prices()
    except Exception as e:
        raise HTTPException(502, f"FTSO read failed: {e}")


@router.get("/lending/{address}")
def lending_state(address: str):
    try:
        return registry.get_lending_state(address)
    except Exception as e:
        raise HTTPException(502, f"chain read failed: {e}")


# -------------------------------------------------------------------- config
@router.get("/config")
def public_config():
    """Everything the frontend needs; no secrets cross this boundary."""
    s = get_settings()
    return {
        "chainId": COSTON2_CHAIN_ID,
        "rpc": s.coston2_rpc,
        "contracts": {
            "identityRegistry": s.identity_registry,
            "creditRegistry": s.credit_registry,
            "lendingPool": s.lending_pool,
            "fxrpToken": s.fxrp_token,
            "instructionSender": s.instruction_sender,
        },
        "hasRelayer": bool(s.backend_private_key),
        "hasEnclave": bool(s.enclave_url),
    }


@router.get("/health")
def health():
    return {"ok": True}

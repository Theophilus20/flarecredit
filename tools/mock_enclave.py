"""Mock scoring enclave  stands in for the FCC Go extension during weeks 1–4.

Same HTTP + signing contract as the real Confidential Space extension:
POST /score {subject, proofs, binding} -> signed envelope.

It now enforces the same integrity rules the real enclave must:

  RULE 1 (ownership): a proof only counts if its Payment was SENT FROM the
          XRPL address in the binding  checked via
          keccak(xrplAddress) == responseBody.sourceAddressHash.
          Someone else's tx hash scores nothing.
  RULE 2 (success):  the payment must have status 0 (success on XRPL).

What it does NOT do (real enclave must): verify the Merkle proof against the
Relay contract's round root, and recompute bindingHash against the on-chain
IdentityLinkRegistry value. See enclave-go/ for the real implementation.

Wire-up:
  1. python tools/mock_enclave.py                # prints signer + code hash
  2. CreditRegistry.registerTeeSigner(codeHash, signer)  (deploy.py does this)
  3. ENCLAVE_URL=http://localhost:9090, EXPECTED_CODE_HASH in .env
"""
import json
import os
import time

from eth_abi.packed import encode_packed
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import keccak
from fastapi import FastAPI, Request
import uvicorn

DEV_KEY = os.environ.get("MOCK_TEE_KEY") or "0x" + "42" * 32
CODE_HASH = "0x" + keccak(b"flarecredit-mock-enclave-v1").hex()
SIGNER = Account.from_key(DEV_KEY)

app = FastAPI(title="Mock FCC scoring enclave")


def address_hash(xrpl_address: str) -> str:
    """FDC standard address hash: keccak256 of the address string."""
    return "0x" + keccak(xrpl_address.encode()).hex()


def response_body(proof: dict) -> dict:
    """DA layer JSON shapes vary slightly; dig out responseBody defensively."""
    resp = proof.get("response") or {}
    if isinstance(resp, str):  # raw ABI hex fallback  can't inspect fields
        return {}
    return resp.get("responseBody") or resp.get("response_body") or resp


def tx_id_of(proof: dict) -> str:
    resp = proof.get("response") or {}
    if isinstance(resp, str):
        return ""
    rb = resp.get("requestBody") or resp.get("request_body") or {}
    return str(rb.get("transactionId", "")).lower()


def filter_owned(proofs: list, binding: dict) -> tuple[list, list]:
    """Anti-gaming rules  keep in lockstep with enclave-go/main.go.

    RULE 1 (ownership):  payment must be SENT FROM the bound XRPL address.
    RULE 2 (success):    XRPL status must be 0.
    RULE 3 (dedup):      the same transactionId counts once, no matter how
                         many times its proof is resubmitted.
    RULE 4 (self-pay):   payments where the receiver hash equals the sender
                         hash are ignored (no farming by paying yourself).
    """
    want = address_hash(binding.get("xrplAddress", "")).lower()
    owned, rejected, seen_tx = [], [], set()
    for p in proofs:
        body = response_body(p)
        src = str(body.get("sourceAddressHash", "")).lower()
        dst = str(body.get("receivingAddressHash", "")).lower()
        status = int(body.get("status", 0) or 0)
        txid = tx_id_of(p)
        if not body:
            rejected.append("opaque response (raw hex)  cannot verify sender")
        elif src != want:
            rejected.append("sender is not the bound XRPL address")
        elif status != 0:
            rejected.append("payment did not succeed on XRPL")
        elif txid and txid in seen_tx:
            rejected.append("duplicate transaction  counted once")
        elif dst and dst == src:
            rejected.append("self-payment  does not count toward score")
        else:
            if txid:
                seen_tx.add(txid)
            owned.append(body)
    return owned, rejected


VOLUME_CAP_PER_COUNTERPARTY_DROPS = 100 * 1_000_000  # 100 XRP


def score_model(owned: list) -> dict:
    """v1  dumb and explainable, computed from attested fields only.

    base 400
    + tx count      min(40 per unique tx, 200)
    + volume        per-counterparty capped at 100 XRP, 35 pts / 25 XRP, max 200
                    (RULE 5: two wallets ping-ponging the same funds saturate
                    a single counterparty's cap instead of farming volume)
    + wallet age    100 if the oldest attested tx is > 30 days old
    + clean repay   100 (real model reads lending-pool Repaid/Liquidated events)
    """
    tx_component = min(len(owned) * 40, 200)

    per_counterparty: dict[str, int] = {}
    for b in owned:
        dst = str(b.get("receivingAddressHash", "?")).lower()
        amt = int(b.get("receivedAmount", 0) or 0)
        per_counterparty[dst] = min(
            per_counterparty.get(dst, 0) + max(amt, 0),
            VOLUME_CAP_PER_COUNTERPARTY_DROPS,
        )
    total_drops = sum(per_counterparty.values())
    volume_component = min(int(total_drops / (25 * 1_000_000)) * 35, 200)

    oldest = min((int(b.get("blockTimestamp", 0) or 0) for b in owned), default=0)
    wallet_age = 100 if oldest and (time.time() - oldest) > 30 * 86_400 else 0

    clean_repayment = 100
    total = min(400 + tx_component + volume_component + wallet_age + clean_repayment, 1000)
    return {
        "score": total,
        "breakdown": {
            "base": 400, "transactions": tx_component, "volume": volume_component,
            "walletAge": wallet_age, "cleanRepayment": clean_repayment,
        },
    }


@app.post("/score")
async def score(req: Request):
    body = await req.json()
    subject = body["subject"]
    binding = body.get("binding", {})

    # Real enclave inserts here: Merkle-verify each proof against the Relay
    # round root, and check keccak(flare, xrpl, nonce) == bindingOf(subject).
    owned, rejected = filter_owned(body.get("proofs", []), binding)
    result = score_model(owned)
    expiry = int(time.time()) + 30 * 86_400

    digest = keccak(encode_packed(
        ["address", "uint16", "uint64", "bytes32"],
        [subject, result["score"], expiry, bytes.fromhex(CODE_HASH[2:])],
    ))
    sig = Account.sign_message(encode_defunct(digest), SIGNER.key)
    return {
        "subject": subject,
        "score": result["score"],
        "expiry": expiry,
        "codeHash": CODE_HASH,
        "signature": sig.signature.to_0x_hex(),
        "signer": SIGNER.address,
        "breakdown": result["breakdown"],
        "proofsCounted": len(owned),
        "proofsRejected": rejected,
    }


if __name__ == "__main__":
    print(json.dumps({"signer": SIGNER.address, "codeHash": CODE_HASH}, indent=2))
    uvicorn.run(app, host="0.0.0.0", port=9090)

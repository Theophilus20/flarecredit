"""XRPL <-> Flare identity linking.

Flow (architecture step 1):
  1. /challenge  backend issues a nonce bound to (flareAddress, xrplAddress)
  2. user signs the challenge message with their XRPL key (GemWallet signMessage)
  3. /verify    backend checks the signature against the XRPL public key,
                 checks the public key actually derives the claimed XRPL
                 address, then returns bindingHash = keccak(flare, xrpl, nonce)
  4. the UI stores ONLY the hash on-chain via IdentityLinkRegistry.link()

The raw XRPL address never touches the chain, so the XRPL<->Flare link is not
public. The preimage (xrpl address + nonce) is handed to the enclave, which
can recompute and check the hash privately.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

from eth_abi.packed import encode_packed
from eth_utils import keccak
from xrpl.core import keypairs

from ..config import get_settings

CHALLENGE_TTL_S = 10 * 60
COSTON2_CHAIN_ID = 114  # domain-binding: sig is only valid for this chain


@dataclass
class Challenge:
    nonce: str
    flare_address: str
    xrpl_address: str
    issued_at: float


# In-memory store; swap for Redis in production.
_challenges: dict[str, Challenge] = {}


class IdentityError(RuntimeError):
    pass


def make_challenge(flare_address: str, xrpl_address: str) -> dict:
    nonce = secrets.token_hex(16)
    _challenges[flare_address.lower()] = Challenge(
        nonce=nonce,
        flare_address=flare_address,
        xrpl_address=xrpl_address,
        issued_at=time.time(),
    )
    return {
        "nonce": nonce,
        "message": challenge_message(flare_address, xrpl_address, nonce),
        "expiresInSeconds": CHALLENGE_TTL_S,
    }


def challenge_message(flare_address: str, xrpl_address: str, nonce: str) -> str:
    """Domain-bound challenge.

    Following the OpenZeppelin / EIP-712 pattern, the signed message commits to
    its full context  chainId and the IdentityLinkRegistry address  not just a
    nonce. A nonce alone stops same-chain replay; binding chainId + contract
    address also stops cross-chain and cross-contract replay of the signature.
    """
    s = get_settings()
    return (
        "FlareCredit identity link\n"
        f"domain:flarecredit-v1\n"
        f"chainId:{COSTON2_CHAIN_ID}\n"
        f"contract:{s.identity_registry.lower()}\n"
        f"flare:{flare_address.lower()}\n"
        f"xrpl:{xrpl_address}\n"
        f"nonce:{nonce}"
    )


def binding_hash(flare_address: str, xrpl_address: str, nonce: str) -> str:
    packed = encode_packed(
        ["address", "string", "string"],
        [flare_address, xrpl_address, nonce],
    )
    return "0x" + keccak(packed).hex()


def verify_signature(
    flare_address: str,
    xrpl_address: str,
    signature_hex: str,
    public_key_hex: str,
) -> dict:
    """Verify a GemWallet signMessage() result and mint the binding hash."""
    ch = _challenges.get(flare_address.lower())
    if ch is None:
        raise IdentityError("no active challenge  request one first")
    if time.time() - ch.issued_at > CHALLENGE_TTL_S:
        del _challenges[flare_address.lower()]
        raise IdentityError("challenge expired  request a new one")
    if ch.xrpl_address != xrpl_address:
        raise IdentityError("challenge was issued for a different XRPL address")

    # 1. The signing key must derive the claimed XRPL classic address.
    derived = keypairs.derive_classic_address(public_key_hex)
    if derived != xrpl_address:
        raise IdentityError("public key does not match the claimed XRPL address")

    # 2. The signature must be valid over the exact challenge message.
    message = challenge_message(flare_address, xrpl_address, ch.nonce)
    valid = keypairs.is_valid_message(
        message.encode("utf-8"),
        bytes.fromhex(signature_hex.removeprefix("0x")),
        public_key_hex,
    )
    if not valid:
        raise IdentityError("invalid signature over challenge message")

    h = binding_hash(flare_address, xrpl_address, ch.nonce)
    del _challenges[flare_address.lower()]
    return {
        "bindingHash": h,
        "nonce": ch.nonce,
        "note": "call IdentityLinkRegistry.link(bindingHash) from the Flare wallet; "
        "keep (xrplAddress, nonce) to disclose to the enclave only",
    }
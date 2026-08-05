"""Deploy the full FlareCredit stack to Coston2 and update .env in place.

Usage:
    export DEPLOYER_PRIVATE_KEY=0x...   # fresh key funded at https://faucet.flare.network/coston2
    python tools/deploy.py

Deploys IdentityLinkRegistry, CreditRegistry, MockFXRP, FxrpLendingPool
(wired to the real FtsoV2 resolved via FlareContractRegistry), funds the pool
with 1,000,000 mock FXRP, and registers the mock enclave's TEE signer so the
whole demo works immediately. Re-run any time; it simply deploys fresh copies.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from eth_account import Account
from eth_utils import keccak
from web3 import Web3

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

RPC = os.environ.get("COSTON2_RPC", "https://coston2-api.flare.network/ext/C/rpc")
FLARE_CONTRACT_REGISTRY = "0xaD67FE66660Fb8dFE9d6b1b4240d8650e30F6019"
REGISTRY_ABI = [{
    "name": "getContractAddressByName", "type": "function", "stateMutability": "view",
    "inputs": [{"name": "_name", "type": "string"}],
    "outputs": [{"name": "", "type": "address"}],
}]

# Must match tools/mock_enclave.py defaults.
MOCK_TEE_KEY = os.environ.get("MOCK_TEE_KEY") or "0x" + "42" * 32
MOCK_CODE_HASH = keccak(b"flarecredit-mock-enclave-v1")


def main() -> None:
    key = os.environ.get("DEPLOYER_PRIVATE_KEY") or os.environ.get("BACKEND_PRIVATE_KEY")
    if not key:
        sys.exit("Set DEPLOYER_PRIVATE_KEY (fund it at https://faucet.flare.network/coston2)")

    w3 = Web3(Web3.HTTPProvider(RPC, request_kwargs={"timeout": 30}))
    if not w3.is_connected():
        sys.exit(f"Cannot reach Coston2 RPC at {RPC}")
    acct = Account.from_key(key)
    bal = w3.eth.get_balance(acct.address)
    print(f"deployer {acct.address}  balance {w3.from_wei(bal, 'ether'):.2f} C2FLR")
    if bal < w3.to_wei(1, "ether"):
        sys.exit("Balance too low  grab C2FLR from https://faucet.flare.network/coston2")

    artifacts = json.loads((ROOT / "contracts" / "artifacts.json").read_text())
    nonce = w3.eth.get_transaction_count(acct.address)

    def send(build):
        nonlocal nonce
        tx = build({
            "from": acct.address, "nonce": nonce, "chainId": 114,
            "maxFeePerGas": w3.eth.gas_price * 2,
            "maxPriorityFeePerGas": w3.to_wei(1, "gwei"),
        })
        tx.setdefault("gas", int(w3.eth.estimate_gas(tx) * 1.2))
        signed = acct.sign_transaction(tx)
        h = w3.eth.send_raw_transaction(signed.raw_transaction)
        r = w3.eth.wait_for_transaction_receipt(h, timeout=120)
        assert r.status == 1, f"tx reverted: {h.hex()}"
        nonce += 1
        return r

    def deploy(name, *args):
        a = artifacts[name]
        c = w3.eth.contract(abi=a["abi"], bytecode=a["bytecode"])
        r = send(lambda base: c.constructor(*args).build_transaction(base))
        print(f"  {name:<22} {r.contractAddress}")
        return w3.eth.contract(address=r.contractAddress, abi=a["abi"])

    # Real FtsoV2 for the pool, resolved via the registry.
    reg = w3.eth.contract(address=Web3.to_checksum_address(FLARE_CONTRACT_REGISTRY), abi=REGISTRY_ABI)
    ftso_v2 = reg.functions.getContractAddressByName("FtsoV2").call()
    print(f"FtsoV2 (via registry)    {ftso_v2}")

    print("deploying…")
    identity = deploy("IdentityLinkRegistry")
    credit = deploy("CreditRegistry")
    fxrp = deploy("MockFXRP")
    pool = deploy("FxrpLendingPool", fxrp.address, credit.address, ftso_v2, 6)

    print("funding pool with 1,000,000 FXRP…")
    send(lambda b: fxrp.functions.mint(pool.address, 1_000_000 * 10**6).build_transaction(b))

    tee_signer = Account.from_key(MOCK_TEE_KEY).address
    print(f"registering mock TEE signer {tee_signer} for codeHash 0x{MOCK_CODE_HASH.hex()[:16]}…")
    send(lambda b: credit.functions.registerTeeSigner(MOCK_CODE_HASH, tee_signer).build_transaction(b))

    # ---- write .env -------------------------------------------------------
    env_path = ROOT / ".env"
    env = env_path.read_text() if env_path.exists() else (ROOT / ".env.example").read_text()
    updates = {
        "IDENTITY_REGISTRY": identity.address,
        "CREDIT_REGISTRY": credit.address,
        "LENDING_POOL": pool.address,
        "FXRP_TOKEN": fxrp.address,
        "ENCLAVE_URL": os.environ.get("ENCLAVE_URL") or "http://localhost:9090",
        "EXPECTED_CODE_HASH": "0x" + MOCK_CODE_HASH.hex(),
        "BACKEND_PRIVATE_KEY": key,
    }
    for k, v in updates.items():
        if re.search(rf"^{k}=.*$", env, flags=re.M):
            env = re.sub(rf"^{k}=.*$", f"{k}={v}", env, flags=re.M)
        else:
            env += f"\n{k}={v}"
    env_path.write_text(env)

    print("\n.env updated. Next:")
    print("  terminal 1: python tools/mock_enclave.py")
    print("  terminal 2: uvicorn app.main:app --reload")
    print("  open http://localhost:8000  full flow is live")
    explorer = "https://coston2-explorer.flare.network/address/"
    for label, addr in [("identity", identity.address), ("credit", credit.address),
                        ("fxrp", fxrp.address), ("pool", pool.address)]:
        print(f"  {label:<9} {explorer}{addr}")


if __name__ == "__main__":
    main()

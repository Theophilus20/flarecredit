"""Chain plumbing for Flare Coston2.

- Resolves protocol contracts (FdcHub, fee config, FtsoV2) through the
  FlareContractRegistry, which lives at the same address on every Flare
  network, so nothing here is hardcoded to a deployment that can rot.
- Holds minimal ABIs for our own contracts (see /contracts).
"""
from __future__ import annotations

from functools import lru_cache

from web3 import Web3

from .config import get_settings

# Same address on Flare, Songbird, Coston and Coston2.
FLARE_CONTRACT_REGISTRY = "0xaD67FE66660Fb8dFE9d6b1b4240d8650e30F6019"

# FDC voting rounds on Coston2: 90s rounds anchored at this timestamp.
FIRST_VOTING_ROUND_START_TS = 1_658_430_000
VOTING_ROUND_SECONDS = 90

COSTON2_CHAIN_ID = 114

# FTSOv2 feed ids (bytes21): 0x01 category + ascii pair, zero padded.
FEED_IDS = {
    "FLR/USD": "0x01464c522f55534400000000000000000000000000",
    "XRP/USD": "0x015852502f55534400000000000000000000000000",
    "BTC/USD": "0x014254432f55534400000000000000000000000000",
}

REGISTRY_ABI = [
    {
        "name": "getContractAddressByName",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "_name", "type": "string"}],
        "outputs": [{"name": "", "type": "address"}],
    }
]

FDC_HUB_ABI = [
    {
        "name": "requestAttestation",
        "type": "function",
        "stateMutability": "payable",
        "inputs": [{"name": "_data", "type": "bytes"}],
        "outputs": [],
    }
]

FDC_FEE_ABI = [
    {
        "name": "getRequestFee",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "_data", "type": "bytes"}],
        "outputs": [{"name": "", "type": "uint256"}],
    }
]

FTSOV2_ABI = [
    {
        "name": "getFeedById",
        "type": "function",
        "stateMutability": "payable",
        "inputs": [{"name": "_feedId", "type": "bytes21"}],
        "outputs": [
            {"name": "value", "type": "uint256"},
            {"name": "decimals", "type": "int8"},
            {"name": "timestamp", "type": "uint64"},
        ],
    }
]

IDENTITY_REGISTRY_ABI = [
    {
        "name": "bindingOf",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "", "type": "address"}],
        "outputs": [{"name": "", "type": "bytes32"}],
    },
    {
        "name": "link",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "bindingHash", "type": "bytes32"}],
        "outputs": [],
    },
]

CREDIT_REGISTRY_ABI = [
    {
        "name": "getScore",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "subject", "type": "address"}],
        "outputs": [
            {"name": "score", "type": "uint16"},
            {"name": "expiry", "type": "uint64"},
            {"name": "codeHash", "type": "bytes32"},
        ],
    },
    {
        "name": "submitScore",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "subject", "type": "address"},
            {"name": "score", "type": "uint16"},
            {"name": "expiry", "type": "uint64"},
            {"name": "codeHash", "type": "bytes32"},
            {"name": "signature", "type": "bytes"},
        ],
        "outputs": [],
    },
]

LENDING_POOL_ABI = [
    {
        "name": "collateralRatioBps",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "borrower", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "positions",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "", "type": "address"}],
        "outputs": [
            {"name": "collateralWei", "type": "uint256"},
            {"name": "debtFxrp", "type": "uint256"},
        ],
    },
    {
        "name": "maxBorrowable",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "borrower", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]

# The fce-extension-scaffold generates the InstructionSender; keep this ABI in
# sync with your generated contract (OPType/OPCommand pair = CREDIT/SCORE).
INSTRUCTION_SENDER_ABI = [
    {
        "name": "sendInstruction",
        "type": "function",
        "stateMutability": "payable",
        "inputs": [
            {"name": "opType", "type": "uint16"},
            {"name": "opCommand", "type": "uint16"},
            {"name": "payload", "type": "bytes"},
        ],
        "outputs": [{"name": "requestId", "type": "uint256"}],
    }
]

OP_TYPE_CREDIT = 1
OP_COMMAND_SCORE = 1


@lru_cache
def w3() -> Web3:
    return Web3(Web3.HTTPProvider(get_settings().coston2_rpc, request_kwargs={"timeout": 20}))


@lru_cache
def protocol_address(name: str) -> str:
    """Resolve an enshrined protocol contract by name via the registry."""
    registry = w3().eth.contract(
        address=Web3.to_checksum_address(FLARE_CONTRACT_REGISTRY), abi=REGISTRY_ABI
    )
    return registry.functions.getContractAddressByName(name).call()


def fdc_hub():
    return w3().eth.contract(address=protocol_address("FdcHub"), abi=FDC_HUB_ABI)


def fdc_fee_config():
    return w3().eth.contract(
        address=protocol_address("FdcRequestFeeConfigurations"), abi=FDC_FEE_ABI
    )


def ftso_v2():
    return w3().eth.contract(address=protocol_address("FtsoV2"), abi=FTSOV2_ABI)


def voting_round_for_timestamp(ts: int) -> int:
    return (ts - FIRST_VOTING_ROUND_START_TS) // VOTING_ROUND_SECONDS

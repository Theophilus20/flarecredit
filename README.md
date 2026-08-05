# FlareCredit

**Prove you're creditworthy without doxxing your wallet.**

Private credit scores for XRP holders, computed inside a TEE on Flare, consumed
by an FXRP lending pool that cuts collateral from 150% to 120% for scores ≥ 700.

Uses all four enshrined protocols: **FDC** (XRPL history proofs), **FCC**
(scoring enclave in GCP Confidential Space), **FTSO** (pricing), **FAssets**
(FXRP lending).

## Stack

- **Backend**: Python / FastAPI  FDC verifier + DA-layer orchestration, XRPL
  signature verification, enclave relay, chain reads via web3.py
- **Frontend**: zero-build static app served by the backend  viem +
  GemWallet via ESM, MetaMask on Coston2. Wallet-state buttons
  (connect/sign/link with reset actions), light/dark theme, notifications,
  session persistence, skeleton loading, and a live score-factor breakdown
- **Contracts**: `contracts/`  IdentityLinkRegistry, CreditRegistry,
  FxrpLendingPool (Solidity 0.8.20)
- **Enclave**: your Go extension on fce-extension-scaffold;
  `tools/mock_enclave.py` implements the identical HTTP + signing contract for
  local development

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # fill in addresses as you deploy

# terminal 1  mock enclave (until Confidential Space is live)
python tools/mock_enclave.py    # prints {signer, codeHash}

# terminal 2  app
uvicorn app.main:app --reload   # http://localhost:8000
```

On Windows (cmd): use `set VAR=value` instead of `export`, and
`.venv\Scripts\activate` to activate the venv. `--reload` watches `.py`
files only  restart manually after editing `.env` or swapping a service file.
After any restart, `curl localhost:8000/api/config` should show your real
contract addresses (not `0x000…`) before signing.

## Deploy to Coston2 (one command)

```bash
export DEPLOYER_PRIVATE_KEY=0x...   # fresh key, fund at https://faucet.flare.network/coston2
python tools/deploy.py
```

Deploys all four contracts (pool wired to the real FtsoV2 via the
FlareContractRegistry), funds the pool with 1M mock FXRP, registers the mock
enclave's TEE signer, and writes every address into `.env`. Contract ABIs and
bytecode are pre-compiled in `contracts/artifacts.json` (solc 0.8.26,
optimizer 200 runs) no toolchain needed.

Week 5: deploy the Go extension to Confidential Space, register it in
TeeExtensionRegistry/TeeMachineRegistry, point `ENCLAVE_URL` at it, set
`EXPECTED_CODE_HASH` to the reproducible-build hash, and call
`registerTeeSigner` with the attested key.

## How the privacy model works

- **Identity**: only `keccak(flareAddr, xrplAddr, nonce)` goes on-chain. The
  preimage is disclosed to the enclave alone, which recomputes and checks it.
  The GemWallet challenge is **domain-bound** (EIP-712-style): it commits to
  chainId + the IdentityLinkRegistry address plus a single-use nonce, so a
  captured signature can't be replayed on another chain or a copied contract.
- **History**: FDC Merkle proofs are fetched from the DA layer and forwarded
  to the TEE never verified by a public contract. Only Merkle *roots* exist
  on-chain.
- **Output**: the enclave signs `(subject, score, expiry, codeHash)`.
  `CreditRegistry` accepts a score only if the signature recovers to the key
  whitelisted for that exact code hash  trust the code, not the operator.

## Anti-gaming (it's a loan product  every input is adversarial)

The enclave enforces these rules; see `SECURITY.md` for the full threat model
(14 vectors mapped, plus honest v1 limitations):

- **Ownership**  a proof only counts if the payment was *sent from* the bound
  XRPL address (`keccak(xrplAddr) == sourceAddressHash`); a stranger's tx
  scores nothing.
- **Merkle verification**  the Go enclave verifies each proof against the
  on-chain FDC round root before scoring (mock skips this).
- **Dedup**  the same transaction counts once, however many times its proof
  is resubmitted.
- **No self-payments**  payments where receiver == sender are discarded.
- **Volume cap**  capped per counterparty (100 XRP), so ping-ponging funds
  between two wallets can't farm the volume component.
- **Wallet-age gate**  age points require the oldest attested tx to be
  > 30 days old; a fresh wallet can't instantly score.
- **Signed scores**  `CreditRegistry` accepts a score only from the TEE key
  whitelisted for that exact code hash; tampered signatures revert.

The frontend also blocks recomputing an unchanged proof set while a score is
still valid (allowed again after a new attestation or once the score expires),
rate-limits identity/verifier calls, and de-dupes proofs on load.

## API surface

| Route | Purpose |
|---|---|
| `POST /api/identity/challenge` | issue XRPL signing challenge |
| `POST /api/identity/verify` | verify GemWallet signature → bindingHash |
| `GET /api/identity/{addr}` | on-chain link status |
| `POST /api/fdc/prepare` | verifier → abiEncodedRequest + fee |
| `POST /api/fdc/request` | pay fee, submit to FdcHub, return round id |
| `POST /api/fdc/proof` | poll DA layer for Merkle proof |
| `POST /api/score/compute` | proofs + binding → enclave → signed envelope |
| `POST /api/score/submit` | relay envelope to CreditRegistry |
| `GET /api/score/{addr}` | current score / validity |
| `GET /api/market/prices` | FTSOv2 FLR/XRP/BTC vs USD |
| `GET /api/lending/{addr}` | position, ratio, borrowing power |
| `GET /api/config` | public config for the frontend (no secrets) |

## Judging-criteria map

- **Usefulness**: undercollateralized lending is DeFi's unsolved problem; the
  pool is a working consumer, not an oracle demo
- **Integration quality**: FDC + FCC + FTSO + FAssets  impossible elsewhere
- **New work**: everything in this repo, built during the program
- **Roadmap**: richer scoring, BTC/DOGE history via additional FDC sources,
  institutional selective-disclosure reports

## Notes / known gaps

- The mock enclave signs with a dev key; only the real Go enclave in Confidential
  Space does Merkle verification + code-hash attestation. Testnet only until then.
- `INSTRUCTION_SENDER_ABI` in `app/chains.py` must be kept in sync with the
  contract your fce-extension-scaffold generates
- Challenge store + rate limiter are in-memory  swap for Redis before
  multi-instance deploys
- The lending pool skips interest accrual; add it post-hackathon
- Repayment flag is static in v1; the Go enclave TODO reads Repaid/Liquidated
  events so liquidated borrowers lose the component (see SECURITY.md #3)
- One XRPL history can back multiple Flare bindings (privacy/uniqueness
  trade-off)  documented in SECURITY.md with v2 mitigations
- Verify current verifier/DA-layer URLs and API-key policy against the Flare
  docs before demo day  testnet endpoints occasionally rotate

## Repository layout

```
app/            FastAPI backend (routes, services, chain plumbing)
contracts/      Solidity + pre-compiled artifacts.json
enclave-go/     real FCC scoring enclave (+ reproducible-build Dockerfile)
tools/          deploy.py (one-command Coston2 deploy), mock_enclave.py
static/         zero-build frontend (index.html, app.js, styles.css)
SECURITY.md     threat model  14 vectors + v1 limitations
```
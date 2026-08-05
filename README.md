# FlareCredit

**Prove you're creditworthy without exposing your wallet.**

FlareCredit verifies your XRPL payment history with Flare's Data Connector, calculates your credit score inside a confidential enclave, and publishes only the score on chain. Your XRPL address and transaction history remain private. A score of 700 or higher reduces the collateral required for FXRP loans from 150% to 120%.

Built on Flare's four core protocols: **FDC** for payment verification, **FCC** for confidential scoring, **FTSOv2** for live price feeds, and **FAssets** for FXRP lending.

Deployed on **Coston2 testnet** (chain ID 114).

---

## Deployed contracts  Coston2 testnet

All contracts are verifiable on the
[Coston2 explorer](https://coston2-explorer.flare.network).

| Contract | Address | Role |
|---|---|---|
| `IdentityLinkRegistry` | `0x4ddf14F3ed14889E4A56C3b8C70304A0832cc93F` | Stores the hashed XRPL↔Flare binding |
| `CreditRegistry` | `0x1A52F314aBb135CE58A26D9318601Ac671Db94FC` | Holds TEE-signed scores, whitelists enclave code hashes |
| `FxrpLendingPool` | `0x80c4C5fB122362dE3845938c0deeA095382a83f1` | Score-gated FXRP lending, priced by FTSOv2 |
| `MockFXRP` | `0x42A3941Ca9f665252E8708EE1307A01DC9E4DAF2` | The borrowable asset (stand-in for FAssets FXRP) |

Protocol contracts (`FdcHub`, `FtsoV2`, `FdcVerification`) are resolved at
runtime through the **FlareContractRegistry** at
`0xaD67FE66660Fb8dFE9d6b1b4240d8650e30F6019`  the same address on every Flare
network  so no protocol address is hardcoded.

Live addresses are also shown in the app under **Settings → Deployment**, each
linking through to the explorer.

---

## What's in here

```
app/                    FastAPI backend
  main.py               entrypoint + page routing
  routes.py             JSON API (identity, FDC, scoring, lending)
  chains.py             web3 plumbing, protocol registry, ABIs
  config.py             settings from .env
  fc_support.py         support contact endpoint (Resend)
  services/
    xrpl_identity.py    domain-bound challenge + signature verification
    fdc.py              attestation request + DA-layer proof polling
    enclave.py          enclave relay + signed-envelope verification
    registry.py         contract reads/writes, FTSO prices

contracts/              Solidity + pre-compiled artifacts.json
  IdentityLinkRegistry.sol
  CreditRegistry.sol
  FxrpLendingPool.sol
  MockFXRP.sol

enclave-go/             real FCC scoring enclave + reproducible-build Dockerfile

tools/
  deploy.py             one-command Coston2 deployment
  mock_enclave.py       stand-in enclave for local development

static/                 marketing site
  fc-landing.html       /
  fc-docs*.html         /docs and 15 sub-pages
  fc-support.html       /support
  fc-site.css  fc-theme.js  fc-hero.svg
  app/                  the dashboard, served at /app

docs/architecture.puml  architecture diagram source
SECURITY.md             threat model + data durability
```

---

## Running it locally

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
copy .env.example .env          # then fill it in
```

Two processes:

```bash
# 1. mock enclave (until Confidential Space is deployed)
python tools/mock_enclave.py     # prints {signer, codeHash}

# 2. the app
uvicorn app.main:app --reload    # http://localhost:8000
```

| Route | Page |
|---|---|
| `/` | Landing |
| `/docs` | Documentation (multi-page) |
| `/support` | Support + contact form |
| `/app` | Dashboard |
| `/api-docs` | Swagger (moved, `/docs` is the docs site) |
| `/__routes` | Build/health check |

On Windows use `set VAR=value` rather than `export`. `--reload` only watches
`.py` files, so restart manually after editing `.env`.

---

## Deploying the contracts

```bash
set DEPLOYER_PRIVATE_KEY=0x...   # fund at https://faucet.flare.network/coston2
python tools/deploy.py
```

Deploys all four contracts (the pool wired to the real FtsoV2 via the
FlareContractRegistry), funds the pool with mock FXRP, registers the enclave's
TEE signer, and writes every address into `.env`. Contract ABIs and bytecode
are pre-compiled in `contracts/artifacts.json`  no Solidity toolchain needed.

---

## How the privacy model works

- **Identity**  only `keccak(flare, xrpl, nonce)` goes on-chain. The challenge
  is domain-bound (EIP-712 style: chainId + registry address + single-use
  nonce), so a captured signature can't be replayed elsewhere.
- **History**  FDC Merkle proofs are fetched from the DA layer and forwarded
  to the enclave, never verified by a public contract. Only roots exist
  on-chain.
- **Scoring**  the enclave signs `(subject, score, expiry, codeHash)`.
  `CreditRegistry` accepts a score only if the signature recovers to the key
  whitelisted for that exact code hash  trust the code, not the operator.

---

## Scoring model (v1)

Deliberately simple and fully explainable  the app shows the breakdown factor
by factor.

| Factor | Max | Earned by |
|---|---|---|
| Base | 400 | A valid enclave-signed score existing |
| Transaction history | 200 | 40 points per unique attested payment |
| Volume | 200 | 35 points per 25 XRP moved, capped at 100 XRP per counterparty |
| Wallet age | 100 | Oldest attested transaction older than 30 days |
| Repayment record | 100 | Clean repayment history in the pool |

Maximum 1000; **700** unlocks the reduced collateral ratio. Scores carry a
30-day validity window, after which the pool falls back to 150%.

---

## Anti-gaming

Every piece of data is verified before it contributes to a credit score. The enclave enforces the following protections:

- **Ownership**  a proof counts only if the payment was sent *from* the bound
  XRPL address; someone else's transaction scores nothing.
- **Merkle verification**  proofs checked against the on-chain FDC root.
- **Dedup**  one transaction counts once, however often it's resubmitted.
- **No self-payments**  receiver == sender is discarded.
- **Volume cap**  100 XRP per counterparty, so wash trading can't farm volume.
- **Wallet-age gate**  age points need the oldest attested tx > 30 days.
- **Signed scores only**  tampered signatures revert on-chain.

The frontend prevents unnecessary recalculations, rate limits verification requests, and automatically removes duplicate proofs.

Full threat model, including limitations: **`SECURITY.md`**.

---

## Support email (Resend)

```
RESEND_API_KEY=re_xxxxxxxx
SUPPORT_FROM=FlareCredit <support@yourdomain.com>
SUPPORT_TO=you@yourdomain.com
```

Each submission sends a team notification (reply-to = the sender) and a
branded auto-reply. Includes rate limiting and a bot honeypot.

---

## Known limitations

- **The production enclave is still in development.** Version one uses a development signing key for testing. Full confidential scoring and code attestation will be available with the Go enclave running in Flare Confidential Computing.
- **The backend is designed for a single server.** Multi-server deployments will use Redis for shared challenges and rate limiting.
- **Interest has not been implemented yet.** Loans do not currently accrue interest.
- **Repayment history is not yet included.** Version one does not adjust credit scores based on loan repayments or liquidations.
- **One payment history can currently be linked to more than one Flare wallet.** We're evaluating solutions that preserve privacy while preventing duplicate bindings.
- **FDC proofs should be backed up.** They are not stored on chain, and if they are lost after the verification window has passed, some older transactions may no longer be available for verification. FlareCredit includes backup and restore to help prevent this.
- **Some Flare testnet services may change.** Testnet verifier and Data Availability endpoints can be updated over time and should be confirmed before demonstrations.

---

## Roadmap

- **Production Confidential Computing** with full enclave attestation.
- **Repayment-based scoring** that rewards successful repayments and reflects liquidations.
- **Support for BTC and DOGE payment history** through additional Flare Data Connector attestation types.
- **Selective disclosure for institutions**, allowing borrowers to share only the credit information required for lending.

---


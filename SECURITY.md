# FlareCredit threat model

Security is built into every part of FlareCredit. This guide explains how potential attacks are prevented, where the protections are enforced, and what is still planned for future versions.

## Security Measures

| | Attack | Protection | Where | Tested |
|---|---|---|---|---|
| 1 | Use someone else's XRPL payment history | Only transactions sent from the linked XRPL address are accepted  `keccak(xrplAddress) == sourceAddressHash` | Enclave | ✔ |
| 2 | Modify or fake payment history | Every payment is verified against Flare's Data Connector before it is scored  `FdcVerification.verifyPayment`, a read-only `eth_call` against the on-chain Merkle root | Enclave | Spec |
| 3 | Link a wallet you don't own | The wallet owner must sign a one time challenge before linking  single-use nonce with a 10-minute TTL, and the public key must derive the claimed address | Backend | ✔ |
| 3b | Reuse a captured signature | Every challenge is tied to a specific chain, contract, and one time nonce  the signed message commits to `chainId` and the `IdentityLinkRegistry` address | Backend | ✔ |
| 4 | Submit the same transaction multiple times | Duplicate transactions are counted only once  deduplicated by `transactionId` | Enclave | ✔ |
| 5 | Send funds back and forth to inflate activity | Volume from the same counterparty is capped  100 XRP per `receivingAddressHash` | Enclave | ✔ |
| 6 | Send funds to yourself | Self payments are ignored  proofs where `receivingAddressHash == sourceAddressHash` are discarded | Enclave | ✔ |
| 7 | Create a new wallet to earn a high score | Wallet age and activity limits prevent new wallets from scoring highly  age points require the oldest attested transaction to be older than 30 days | Enclave | ✔ |
| 8 | Forge a credit score | Only scores signed by the approved enclave are accepted  `CreditRegistry` recovers the signature over `keccak(subject, score, expiry, codeHash)` | Smart Contract | ✔ |
| 9 | Run modified scoring software | Only approved enclave code can generate valid scores  code-hash whitelisting with reproducible builds (`SOURCE_DATE_EPOCH`, `-trimpath`, pinned base images) | Smart Contract | Design |
| 10 | Use a score for a different wallet | Every score is tied to a specific wallet  the subject address is inside the signed digest | Smart Contract | ✔ |
| 11 | Reuse an expired score | Expired scores are ignored and the loan falls back to standard collateral  `getScore` returns zero past expiry, so the pool reverts to 150% | Smart Contract | ✔ |
| 12 | Borrow more than allowed | Every borrow and withdrawal is checked against live prices from FTSOv2  `FLR/USD` and `XRP/USD` feeds, with liquidation at a 5% bonus | Smart Contract | ✔ |
| 13 | Spam wallet verification | Rate limits protect verification endpoints  5 per minute per address, 3 per 5 minutes per transaction hash | Backend |  |
| 14 | Count failed XRPL payments | Failed payments are ignored  proofs with `status != 0` are discarded | Enclave | ✔ |
| 15 | Recalculate the same score repeatedly | Scores are only recalculated when new verified activity is available or the current score expires | Frontend | ✔ |
| 16 | Spam the support form | Rate limits and a hidden honeypot field block automated submissions 3 per 15 minutes per email address | Backend |  |

## Data durability

FDC does not store transaction proofs on chain. Only a Merkle root for each voting round is recorded. The proofs are available from Flare's Data Availability layer for a limited time and are kept by the client. This helps protect user privacy, but it also means proofs should be preserved if they need to be verified later.

| Data | Stored in | Lost if you clear your browser? |
|---|---|---|
| Credit score | `CreditRegistry` (on chain) | ✘ No |
| Identity link | `IdentityLinkRegistry` (on chain) | ✘ No |
| Loan position | `FxrpLendingPool` (on chain) | ✘ No |
| Wallet linking data | Browser local storage | ✔ Yes |
| FDC proofs | Browser local storage | ✔ Yes |

### What happens if I clear my browser?

Your credit score, identity link, and any active loans remain available because they are stored on chain.

What you lose is the local data used to generate your score. To create a new score, you'll need to link your wallet again and verify your payment history.

### Why is this important?

FDC proofs are only available for a limited time. If you lose them after that period, some older transactions may no longer be available to verify again.

### Back up your data

FlareCredit lets you export a backup containing your wallet linking data, FDC proofs, your latest signed score, and your score history.

If you restore a backup, FlareCredit checks that the file is valid, removes duplicate proofs, and warns you if the backup belongs to a different Flare wallet. The app also reminds you to create a backup whenever new proofs are available or after a new credit score is generated.

### Keep your backup safe

Your backup contains the same private information used to calculate your credit score, including your XRPL address, wallet linking data, and FDC proofs. It is never uploaded to FlareCredit's servers. Only you control it, so store it somewhere secure.

## Known limitations

FlareCredit is still evolving. These are the current limitations of version one.

### One payment history can be linked to more than one Flare wallet

Today, the same XRPL payment history can be linked to multiple Flare wallets. Future versions will add protections to ensure one payment history can only be linked once, while preserving as much privacy as possible.

### Fake activity across multiple wallets

The current scoring system limits repeated activity between the same wallets, but someone could still spread transactions across many wallets to increase their score. Future versions will place greater emphasis on the quality and history of counterparties.

### Repayment history is not fully included

Version one does not yet reduce a score after a liquidation or reward successful loan repayments. Future versions will use real lending history to make scores more accurate.

### Older scores can still be submitted

A previously issued score can currently replace a newer one if it is still valid. Future versions will only accept the most recent score.

### Browser data is stored locally

Wallet linking data and FDC proofs are stored in your browser. If they are lost, you'll need to link your wallet again and, where possible, verify your payment history again.

### Development scoring environment

Version one uses a development signing key for testing. Production deployments will use Flare Confidential Computing to generate and sign credit scores.

### Older payment history counts the same as recent activity

Today, older verified payments contribute the same as recent ones. Future versions will place more emphasis on recent payment activity.

## Trust boundaries

Different parts of FlareCredit only see the information they need.

| Component | What it can see |
|---|---|
| Blockchain | Credit scores, identity hashes, Merkle roots, and loan positions. It never sees your XRPL address or transaction history. |
| Backend | Processes wallet linking and verification requests, but cannot create or modify credit scores. |
| Confidential enclave | Processes the information needed to calculate your credit score inside a secure environment. |
| Lending pool | Reads only your credit score and its expiry. It never sees your payment history. |

## Reporting a security issue

If you discover a security issue, please email flarecreditco@gmail.com with "Security" in the subject line, or use the Security Disclosure option on the support page. We'll review your report promptly and work with you through a responsible disclosure process.

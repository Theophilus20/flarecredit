# FlareCredit scoring enclave (FCC extension)

The real enclave. Same HTTP contract as `tools/mock_enclave.py`, plus the two
checks the mock can't do:

1. **Merkle verification**  each Payment proof is checked via a read-only
   `eth_call` to the enshrined `FdcVerification.verifyPayment`, resolved
   through the FlareContractRegistry. Nothing about the user's history is
   written on-chain.
2. **Binding verification**  `keccak(flare, xrpl, nonce)` must equal
   `IdentityLinkRegistry.bindingOf(subject)`.

Then: sender-ownership rule, status rule, v1 score model (identical to the
mock  keep them in lockstep), and the score envelope signed as
`keccak(subject, uint16 score, uint64 expiry, bytes32 codeHash)`.

## Build & run locally

```bash
go mod tidy
IDENTITY_REGISTRY=0x... CODE_HASH=0x... TEE_KEY_HEX=0x... go run .
# POST http://localhost:9090/score  same payload as the mock
```

## Confidential Space deployment (week 5)

1. `docker build -t gcr.io/PROJECT/flarecredit-enclave .`  record the image
   digest; that digest is your reproducible code hash
2. Create a Confidential Space VM with the image; the workload generates its
   TEE key at boot (leave `TEE_KEY_HEX` unset) and logs `{signer, codeHash}`
3. Verify the attestation token, then
   `CreditRegistry.registerTeeSigner(codeHash, signer)`
4. Register the workload in TeeExtensionRegistry / TeeMachineRegistry per
   fce-extension-scaffold docs, wire the InstructionSender listener to
   `handleScore` for the CREDIT/SCORE opcode pair
5. Point the backend's `ENCLAVE_URL` + `EXPECTED_CODE_HASH` at it  nothing
   else in the stack changes

## Sync warning

The `PaymentResponse` struct field order must match the current FDC Payment
attestation spec  verify against the deployed `FdcVerification` interface
before demo day (`sourceAddressesRoot` was added in a later spec revision).

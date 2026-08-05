// FlareCredit scoring enclave  the real FCC extension.
//
// Runs inside GCP Confidential Space. Implements the same HTTP contract as
// tools/mock_enclave.py, plus the two verifications the mock skips:
//
//  1. Each FDC Payment proof is verified against the on-chain Merkle root,
//     via an eth_call to FdcVerification.verifyPayment (read-only: nothing
//     about the user's history touches the public chain).
//  2. The binding preimage keccak(flare, xrpl, nonce) must equal the hash the
//     user stored in IdentityLinkRegistry  proving the attested history
//     belongs to the caller.
//
// Then the same rules as the mock: proofs must be SENT FROM the bound XRPL
// address (sourceAddressHash) and have status 0, scored by the v1 model, and
// the result signed as keccak(subject, score, expiry, codeHash) with the
// enclave key.
//
// Integration with fce-extension-scaffold: mount this handler as the
// CREDIT/SCORE OPCommand processor; the scaffold's instruction listener can
// call handleScore with the decoded payload. Direct HTTPS also works.
//
// Env:
//   RPC_URL            Coston2 RPC (default public endpoint)
//   IDENTITY_REGISTRY  deployed IdentityLinkRegistry address
//   CODE_HASH          reproducible image hash (from the build; see Dockerfile)
//   TEE_KEY_HEX        optional fixed key for local testing; in Confidential
//                      Space leave unset  a fresh key is generated in-enclave
//                      and its address printed for registerTeeSigner.
package main

import (
	"bytes"
	"crypto/ecdsa"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"log"
	"math/big"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/ethereum/go-ethereum/accounts/abi"
	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/crypto"
	"github.com/ethereum/go-ethereum/ethclient"
)

// Same address on every Flare network.
const flareContractRegistry = "0xaD67FE66660Fb8dFE9d6b1b4240d8650e30F6019"

// ---------------------------------------------------------------- FDC types
// Payment attestation response, per the FDC spec. Field order matters for
// ABI encoding  keep in sync with the current Payment attestation type.

type PaymentRequestBody struct {
	TransactionId [32]byte `abi:"transactionId"`
	InUtxo        *big.Int `abi:"inUtxo"`
	Utxo          *big.Int `abi:"utxo"`
}

type PaymentResponseBody struct {
	BlockNumber                  uint64   `abi:"blockNumber"`
	BlockTimestamp               uint64   `abi:"blockTimestamp"`
	SourceAddressHash            [32]byte `abi:"sourceAddressHash"`
	SourceAddressesRoot          [32]byte `abi:"sourceAddressesRoot"`
	ReceivingAddressHash         [32]byte `abi:"receivingAddressHash"`
	IntendedReceivingAddressHash [32]byte `abi:"intendedReceivingAddressHash"`
	SpentAmount                  *big.Int `abi:"spentAmount"`
	IntendedSpentAmount          *big.Int `abi:"intendedSpentAmount"`
	ReceivedAmount               *big.Int `abi:"receivedAmount"`
	IntendedReceivedAmount       *big.Int `abi:"intendedReceivedAmount"`
	StandardPaymentReference     [32]byte `abi:"standardPaymentReference"`
	OneToOne                     bool     `abi:"oneToOne"`
	Status                       uint8    `abi:"status"`
}

type PaymentResponse struct {
	AttestationType     [32]byte            `abi:"attestationType"`
	SourceId            [32]byte            `abi:"sourceId"`
	VotingRound         uint64              `abi:"votingRound"`
	LowestUsedTimestamp uint64              `abi:"lowestUsedTimestamp"`
	RequestBody         PaymentRequestBody  `abi:"requestBody"`
	ResponseBody        PaymentResponseBody `abi:"responseBody"`
}

type PaymentProof struct {
	MerkleProof [][32]byte      `abi:"merkleProof"`
	Data        PaymentResponse `abi:"data"`
}

// ------------------------------------------------------------- HTTP payloads

type scoreRequest struct {
	Subject string `json:"subject"`
	Proofs  []struct {
		Proof    []string        `json:"proof"`
		Response json.RawMessage `json:"response"`
	} `json:"proofs"`
	Binding struct {
		XrplAddress string `json:"xrplAddress"`
		Nonce       string `json:"nonce"`
	} `json:"binding"`
}

type scoreEnvelope struct {
	Subject        string         `json:"subject"`
	Score          uint16         `json:"score"`
	Expiry         uint64         `json:"expiry"`
	CodeHash       string         `json:"codeHash"`
	Signature      string         `json:"signature"`
	Signer         string         `json:"signer"`
	Breakdown      map[string]int `json:"breakdown"`
	ProofsCounted  int            `json:"proofsCounted"`
	ProofsRejected []string       `json:"proofsRejected"`
}

// -------------------------------------------------------------------- server

type enclave struct {
	client           *ethclient.Client
	key              *ecdsa.PrivateKey
	codeHash         [32]byte
	identityRegistry common.Address
	fdcVerification  common.Address
	verifyABI        abi.ABI
	bindingABI       abi.ABI
}

func main() {
	rpc := envOr("RPC_URL", "https://coston2-api.flare.network/ext/C/rpc")
	client, err := ethclient.Dial(rpc)
	must(err)

	var key *ecdsa.PrivateKey
	if k := os.Getenv("TEE_KEY_HEX"); k != "" {
		key, err = crypto.HexToECDSA(strings.TrimPrefix(k, "0x"))
		must(err)
	} else {
		// In Confidential Space: fresh key per attested boot; the operator
		// never sees it. Register its address after verifying the attestation.
		key, err = crypto.GenerateKey()
		must(err)
	}

	var codeHash [32]byte
	ch, err := hex.DecodeString(strings.TrimPrefix(envOr("CODE_HASH", ""), "0x"))
	must(err)
	copy(codeHash[:], ch)

	e := &enclave{
		client:           client,
		key:              key,
		codeHash:         codeHash,
		identityRegistry: common.HexToAddress(mustEnv("IDENTITY_REGISTRY")),
		fdcVerification:  resolve(client, "FdcVerification"),
	}
	e.verifyABI = mustABI(`[{"name":"verifyPayment","type":"function","stateMutability":"view",
	  "inputs":[{"name":"_proof","type":"tuple","components":[
	    {"name":"merkleProof","type":"bytes32[]"},
	    {"name":"data","type":"tuple","components":[
	      {"name":"attestationType","type":"bytes32"},{"name":"sourceId","type":"bytes32"},
	      {"name":"votingRound","type":"uint64"},{"name":"lowestUsedTimestamp","type":"uint64"},
	      {"name":"requestBody","type":"tuple","components":[
	        {"name":"transactionId","type":"bytes32"},{"name":"inUtxo","type":"uint256"},{"name":"utxo","type":"uint256"}]},
	      {"name":"responseBody","type":"tuple","components":[
	        {"name":"blockNumber","type":"uint64"},{"name":"blockTimestamp","type":"uint64"},
	        {"name":"sourceAddressHash","type":"bytes32"},{"name":"sourceAddressesRoot","type":"bytes32"},
	        {"name":"receivingAddressHash","type":"bytes32"},{"name":"intendedReceivingAddressHash","type":"bytes32"},
	        {"name":"spentAmount","type":"int256"},{"name":"intendedSpentAmount","type":"int256"},
	        {"name":"receivedAmount","type":"int256"},{"name":"intendedReceivedAmount","type":"int256"},
	        {"name":"standardPaymentReference","type":"bytes32"},{"name":"oneToOne","type":"bool"},
	        {"name":"status","type":"uint8"}]}]}]}],
	  "outputs":[{"name":"_proved","type":"bool"}]}]`)
	e.bindingABI = mustABI(`[{"name":"bindingOf","type":"function","stateMutability":"view",
	  "inputs":[{"name":"","type":"address"}],"outputs":[{"name":"","type":"bytes32"}]}]`)

	addr := crypto.PubkeyToAddress(key.PublicKey)
	log.Printf(`{"signer":"%s","codeHash":"0x%x"}`, addr.Hex(), codeHash)
	log.Printf("register with: CreditRegistry.registerTeeSigner(codeHash, signer)")

	http.HandleFunc("/score", e.handleScore)
	http.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) { fmt.Fprint(w, "ok") })
	log.Fatal(http.ListenAndServe(":9090", nil))
}

// ------------------------------------------------------------------ handler

func (e *enclave) handleScore(w http.ResponseWriter, r *http.Request) {
	var req scoreRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, err.Error(), 400)
		return
	}
	subject := common.HexToAddress(req.Subject)

	// ---- verification 2: binding preimage matches the on-chain hash --------
	if err := e.checkBinding(subject, req.Binding.XrplAddress, req.Binding.Nonce); err != nil {
		http.Error(w, "binding check failed: "+err.Error(), 400)
		return
	}
	wantSource := crypto.Keccak256Hash([]byte(req.Binding.XrplAddress))

	var counted []PaymentResponseBody
	var rejected []string
	seenTx := map[[32]byte]bool{}
	for _, p := range req.Proofs {
		resp, err := parseResponse(p.Response)
		if err != nil {
			rejected = append(rejected, "unparseable response: "+err.Error())
			continue
		}
		// ---- verification 1: Merkle proof against the on-chain round root --
		ok, err := e.verifyPayment(p.Proof, resp)
		if err != nil || !ok {
			rejected = append(rejected, "Merkle verification failed")
			continue
		}
		// ---- anti-gaming rules (keep in lockstep with mock_enclave.py) ------
		if resp.ResponseBody.SourceAddressHash != wantSource {
			rejected = append(rejected, "sender is not the bound XRPL address")
			continue
		}
		if resp.ResponseBody.Status != 0 {
			rejected = append(rejected, "payment did not succeed on XRPL")
			continue
		}
		if seenTx[resp.RequestBody.TransactionId] {
			rejected = append(rejected, "duplicate transaction  counted once")
			continue
		}
		if resp.ResponseBody.ReceivingAddressHash == wantSource {
			rejected = append(rejected, "self-payment  does not count toward score")
			continue
		}
		seenTx[resp.RequestBody.TransactionId] = true
		counted = append(counted, resp.ResponseBody)
	}

	score, breakdown := scoreModel(counted)
	expiry := uint64(time.Now().Unix()) + 30*86_400

	sig, signer, err := e.sign(subject, score, expiry)
	if err != nil {
		http.Error(w, err.Error(), 500)
		return
	}
	json.NewEncoder(w).Encode(scoreEnvelope{
		Subject: req.Subject, Score: score, Expiry: expiry,
		CodeHash:  "0x" + hex.EncodeToString(e.codeHash[:]),
		Signature: sig, Signer: signer,
		Breakdown: breakdown, ProofsCounted: len(counted), ProofsRejected: rejected,
	})
}

// ------------------------------------------------------------- verifications

func (e *enclave) verifyPayment(proofHashes []string, data PaymentResponse) (bool, error) {
	proof := PaymentProof{Data: data}
	for _, h := range proofHashes {
		var b [32]byte
		raw, err := hex.DecodeString(strings.TrimPrefix(h, "0x"))
		if err != nil || len(raw) != 32 {
			return false, fmt.Errorf("bad proof hash %q", h)
		}
		copy(b[:], raw)
		proof.MerkleProof = append(proof.MerkleProof, b)
	}
	input, err := e.verifyABI.Pack("verifyPayment", proof)
	if err != nil {
		return false, err
	}
	out, err := e.client.CallContract(r0(), callMsg(e.fdcVerification, input), nil)
	if err != nil {
		return false, err
	}
	res, err := e.verifyABI.Unpack("verifyPayment", out)
	if err != nil {
		return false, err
	}
	return res[0].(bool), nil
}

func (e *enclave) checkBinding(subject common.Address, xrpl, nonce string) error {
	input, _ := e.bindingABI.Pack("bindingOf", subject)
	out, err := e.client.CallContract(r0(), callMsg(e.identityRegistry, input), nil)
	if err != nil {
		return err
	}
	var onchain [32]byte
	copy(onchain[:], out)
	// keccak(abi.encodePacked(address, string, string))  matches backend + registry.
	preimage := append(subject.Bytes(), []byte(xrpl)...)
	preimage = append(preimage, []byte(nonce)...)
	if crypto.Keccak256Hash(preimage) != onchain {
		return fmt.Errorf("preimage does not match on-chain binding")
	}
	return nil
}

// ------------------------------------------------------------------- scoring

// v1  identical to tools/mock_enclave.py. Keep the two in lockstep.
func scoreModel(owned []PaymentResponseBody) (uint16, map[string]int) {
	txC := min(len(owned)*40, 200)

	// Volume capped per counterparty (100 XRP): ping-ponging funds between
	// two wallets saturates one cap instead of farming the volume component.
	cap := big.NewInt(100 * 1_000_000)
	perCounterparty := map[[32]byte]*big.Int{}
	oldest := uint64(0)
	for _, b := range owned {
		if b.ReceivedAmount != nil && b.ReceivedAmount.Sign() > 0 {
			cur, ok := perCounterparty[b.ReceivingAddressHash]
			if !ok {
				cur = new(big.Int)
				perCounterparty[b.ReceivingAddressHash] = cur
			}
			cur.Add(cur, b.ReceivedAmount)
			if cur.Cmp(cap) > 0 {
				cur.Set(cap)
			}
		}
		if oldest == 0 || b.BlockTimestamp < oldest {
			oldest = b.BlockTimestamp
		}
	}
	totalDrops := new(big.Int)
	for _, v := range perCounterparty {
		totalDrops.Add(totalDrops, v)
	}
	steps := new(big.Int).Div(totalDrops, big.NewInt(25*1_000_000))
	volC := min(int(steps.Int64())*35, 200)

	age := 0
	if oldest != 0 && time.Now().Unix()-int64(oldest) > 30*86_400 {
		age = 100
	}
	repay := 100 // TODO: read Repaid/Liquidated events from the lending pool
	total := min(400+txC+volC+age+repay, 1000)
	return uint16(total), map[string]int{
		"base": 400, "transactions": txC, "volume": volC,
		"walletAge": age, "cleanRepayment": repay,
	}
}

// ------------------------------------------------------------------- signing

func (e *enclave) sign(subject common.Address, score uint16, expiry uint64) (string, string, error) {
	// keccak(abi.encodePacked(subject, uint16, uint64, bytes32))  matches
	// CreditRegistry.sol and app/services/enclave.py exactly.
	packed := subject.Bytes()
	packed = append(packed, byte(score>>8), byte(score))
	for i := 7; i >= 0; i-- {
		packed = append(packed, byte(expiry>>(8*i)))
	}
	packed = append(packed, e.codeHash[:]...)
	digest := crypto.Keccak256(packed)

	prefixed := crypto.Keccak256(append(
		[]byte("\x19Ethereum Signed Message:\n32"), digest...))
	sig, err := crypto.Sign(prefixed, e.key)
	if err != nil {
		return "", "", err
	}
	sig[64] += 27
	return "0x" + hex.EncodeToString(sig),
		crypto.PubkeyToAddress(e.key.PublicKey).Hex(), nil
}

// --------------------------------------------------------------------- utils

func parseResponse(raw json.RawMessage) (PaymentResponse, error) {
	// DA layer JSON uses camelCase names mirroring the ABI struct; decode the
	// hex/string number fields defensively.
	var j struct {
		AttestationType     string `json:"attestationType"`
		SourceId            string `json:"sourceId"`
		VotingRound         any    `json:"votingRound"`
		LowestUsedTimestamp any    `json:"lowestUsedTimestamp"`
		RequestBody         struct {
			TransactionId string `json:"transactionId"`
			InUtxo        any    `json:"inUtxo"`
			Utxo          any    `json:"utxo"`
		} `json:"requestBody"`
		ResponseBody struct {
			BlockNumber                  any    `json:"blockNumber"`
			BlockTimestamp               any    `json:"blockTimestamp"`
			SourceAddressHash            string `json:"sourceAddressHash"`
			SourceAddressesRoot          string `json:"sourceAddressesRoot"`
			ReceivingAddressHash         string `json:"receivingAddressHash"`
			IntendedReceivingAddressHash string `json:"intendedReceivingAddressHash"`
			SpentAmount                  any    `json:"spentAmount"`
			IntendedSpentAmount          any    `json:"intendedSpentAmount"`
			ReceivedAmount               any    `json:"receivedAmount"`
			IntendedReceivedAmount       any    `json:"intendedReceivedAmount"`
			StandardPaymentReference     string `json:"standardPaymentReference"`
			OneToOne                     bool   `json:"oneToOne"`
			Status                       any    `json:"status"`
		} `json:"responseBody"`
	}
	var out PaymentResponse
	if err := json.Unmarshal(raw, &j); err != nil {
		return out, err
	}
	out.AttestationType = b32(j.AttestationType)
	out.SourceId = b32(j.SourceId)
	out.VotingRound = u64(j.VotingRound)
	out.LowestUsedTimestamp = u64(j.LowestUsedTimestamp)
	out.RequestBody = PaymentRequestBody{
		TransactionId: b32(j.RequestBody.TransactionId),
		InUtxo:        bigOf(j.RequestBody.InUtxo),
		Utxo:          bigOf(j.RequestBody.Utxo),
	}
	rb := &out.ResponseBody
	rb.BlockNumber = u64(j.ResponseBody.BlockNumber)
	rb.BlockTimestamp = u64(j.ResponseBody.BlockTimestamp)
	rb.SourceAddressHash = b32(j.ResponseBody.SourceAddressHash)
	rb.SourceAddressesRoot = b32(j.ResponseBody.SourceAddressesRoot)
	rb.ReceivingAddressHash = b32(j.ResponseBody.ReceivingAddressHash)
	rb.IntendedReceivingAddressHash = b32(j.ResponseBody.IntendedReceivingAddressHash)
	rb.SpentAmount = bigOf(j.ResponseBody.SpentAmount)
	rb.IntendedSpentAmount = bigOf(j.ResponseBody.IntendedSpentAmount)
	rb.ReceivedAmount = bigOf(j.ResponseBody.ReceivedAmount)
	rb.IntendedReceivedAmount = bigOf(j.ResponseBody.IntendedReceivedAmount)
	rb.StandardPaymentReference = b32(j.ResponseBody.StandardPaymentReference)
	rb.OneToOne = j.ResponseBody.OneToOne
	rb.Status = uint8(u64(j.ResponseBody.Status))
	return out, nil
}

func b32(s string) (out [32]byte) {
	raw, _ := hex.DecodeString(strings.TrimPrefix(s, "0x"))
	copy(out[:], raw)
	return
}
func u64(v any) uint64      { return bigOf(v).Uint64() }
func bigOf(v any) *big.Int {
	switch x := v.(type) {
	case float64:
		return big.NewInt(int64(x))
	case string:
		n := new(big.Int)
		if strings.HasPrefix(x, "0x") {
			n.SetString(x[2:], 16)
		} else {
			n.SetString(x, 10)
		}
		return n
	default:
		return big.NewInt(0)
	}
}

func resolve(c *ethclient.Client, name string) common.Address {
	regABI := mustABI(`[{"name":"getContractAddressByName","type":"function","stateMutability":"view",
	  "inputs":[{"name":"_name","type":"string"}],"outputs":[{"name":"","type":"address"}]}]`)
	input, _ := regABI.Pack("getContractAddressByName", name)
	out, err := c.CallContract(r0(), callMsg(common.HexToAddress(flareContractRegistry), input), nil)
	must(err)
	return common.BytesToAddress(out[12:32])
}

func mustABI(s string) abi.ABI {
	a, err := abi.JSON(bytes.NewReader([]byte(s)))
	must(err)
	return a
}
func envOr(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}
func mustEnv(k string) string {
	v := os.Getenv(k)
	if v == "" {
		log.Fatalf("missing env %s", k)
	}
	return v
}
func must(err error) {
	if err != nil {
		log.Fatal(err)
	}
}
func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

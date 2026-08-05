/* FlareCredit frontend  persistence, notifications, live factors, theming. */

import {
  createPublicClient, createWalletClient, custom, http,
  parseEther, parseUnits, formatEther, formatUnits, defineChain,
} from "https://esm.sh/viem@2.21.19";
import * as gem from "https://esm.sh/@gemwallet/api@3.8.0";

/* ------------------------------------------------ config */
const CFG = await (await fetch("/api/config")).json();

const coston2 = defineChain({
  id: CFG.chainId,
  name: "Coston2",
  nativeCurrency: { name: "Coston2 Flare", symbol: "C2FLR", decimals: 18 },
  rpcUrls: { default: { http: [CFG.rpc] } },
  blockExplorers: { default: { name: "Coston2 Explorer", url: "https://coston2-explorer.flare.network" } },
  testnet: true,
});
const publicClient = createPublicClient({ chain: coston2, transport: http(CFG.rpc) });

const IDENTITY_ABI = [
  { name: "link", type: "function", stateMutability: "nonpayable",
    inputs: [{ name: "bindingHash", type: "bytes32" }], outputs: [] },
  { name: "unlink", type: "function", stateMutability: "nonpayable", inputs: [], outputs: [] },
];
const REGISTRY_ABI = [{ name: "submitScore", type: "function", stateMutability: "nonpayable",
  inputs: [{ name: "subject", type: "address" }, { name: "score", type: "uint16" },
    { name: "expiry", type: "uint64" }, { name: "codeHash", type: "bytes32" },
    { name: "signature", type: "bytes" }], outputs: [] }];
const POOL_ABI = [
  { name: "deposit", type: "function", stateMutability: "payable", inputs: [], outputs: [] },
  { name: "borrow", type: "function", stateMutability: "nonpayable",
    inputs: [{ name: "amountFxrp", type: "uint256" }], outputs: [] },
  { name: "repay", type: "function", stateMutability: "nonpayable",
    inputs: [{ name: "amountFxrp", type: "uint256" }], outputs: [] }];
const ERC20_ABI = [{ name: "approve", type: "function", stateMutability: "nonpayable",
  inputs: [{ name: "spender", type: "address" }, { name: "amount", type: "uint256" }],
  outputs: [{ name: "", type: "bool" }] }];

/* ------------------------------------------------ persisted state
   Keys are namespaced per Flare address so switching accounts is clean.
   The binding preimage (xrplAddress + nonce) lives ONLY in this browser 
   it is the secret that lets the enclave check ownership. */
const store = {
  key: (k) => `fc:${S.account?.toLowerCase() ?? "anon"}:${k}`,
  get(k, d = null) { try { return JSON.parse(localStorage.getItem(this.key(k))) ?? d; } catch { return d; } },
  set(k, v) { localStorage.setItem(this.key(k), JSON.stringify(v)); },
  clearAccount() {
    const prefix = `fc:${S.account?.toLowerCase()}:`;
    Object.keys(localStorage).filter((k) => k.startsWith(prefix)).forEach((k) => localStorage.removeItem(k));
  },
};

const S = {
  account: null, wallet: null,
  binding: null,     // {bindingHash, nonce, xrplAddress}
  proofs: [],        // collected FDC proofs
  pending: [],
  envelope: null,    // last signed score envelope (has breakdown)
  maxBorrow: 0,
  lending: null,
};

/* ------------------------------------------------ tiny utils */
const $ = (id) => document.getElementById(id);
const shortX = (a) => a.slice(0, 5) + '…' + a.slice(-5);
function markStep(btnId, doneLabel, resetId) {
  const b = $(btnId);
  b.textContent = doneLabel; b.classList.add("done"); b.disabled = true;
  if (resetId) $(resetId)?.classList.remove("hidden");
}
function unmarkStep(btnId, label, resetId) {
  const b = $(btnId);
  b.textContent = label; b.classList.remove("done"); b.disabled = false;
  if (resetId) $(resetId)?.classList.add("hidden");
}

const toast = (msg, ms = 4200) => {
  const t = $("toast"); t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.add("hidden"), ms);
};
const STATUS_PERSIST = new Set(["s1", "s2", "s3", "attest-status"]); // state, not toasts
function confirmModal({ title, body, confirmText = "Confirm", danger = false }) {
  return new Promise((resolve) => {
    const back = $("modal-backdrop"), modal = back.querySelector(".modal");
    $("modal-title").textContent = title;
    $("modal-body").textContent = body;
    $("modal-confirm").textContent = confirmText;
    modal.classList.toggle("danger", danger);
    back.classList.remove("hidden");
    const done = (v) => {
      back.classList.add("hidden");
      $("modal-confirm").onclick = $("modal-cancel").onclick = back.onclick = null;
      resolve(v);
    };
    $("modal-confirm").onclick = () => done(true);
    $("modal-cancel").onclick = () => done(false);
    back.onclick = (e) => { if (e.target === back) done(false); };
  });
}

const setStatus = (id, msg, ok = true, sticky = false) => {
  const el = $(id); el.textContent = msg; el.className = "status " + (ok ? "ok" : "err");
  clearTimeout(el._h);
  if (!STATUS_PERSIST.has(id) && !sticky) {
    el._h = setTimeout(() => { el.textContent = ""; el.className = "status"; }, 8000);
  }
};
const api = async (path, body) => {
  const r = await fetch("/api" + path, body && {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    if (r.status === 429) throw new Error("too many requests  wait a moment and try again");
    const d = data.detail;
    throw new Error(typeof d === "string" ? d : (d ? JSON.stringify(d) : `request failed (${r.status})`));
  }
  return data;
};

/* ------------------------------------------------ theme */
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  localStorage.setItem("fc:theme", t);
  $("btn-theme").innerHTML = `<svg><use href="#i-${t === "dark" ? "sun" : "moon"}"/></svg>`;
}
$("btn-theme").addEventListener("click", () =>
  applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
applyTheme(localStorage.getItem("fc:theme") || "light");

/* ------------------------------------------------ notifications */
function notifs() { return store.get("notifs", []); }
function notify(msg) {
  const list = notifs();
  list.unshift({ msg, t: Date.now() });
  store.set("notifs", list.slice(0, 30));
  store.set("unread", true);
  renderNotifs();
}
function renderNotifs() {
  const list = notifs();
  $("bell-dot").classList.toggle("hidden", !store.get("unread", false));
  $("notif-panel").innerHTML = list.length
    ? list.map((n) => {
        const mins = Math.round((Date.now() - n.t) / 60000);
        const when = mins < 1 ? "now" : mins < 60 ? `${mins}m` : `${Math.round(mins / 60)}h`;
        return `<div class="notif"><span>${n.msg}</span><time>${when}</time></div>`;
      }).join("")
    : `<div class="muted small" style="padding:10px 12px">No activity yet</div>`;
}
$("btn-bell").addEventListener("click", (e) => {
  e.stopPropagation();
  $("notif-panel").classList.toggle("hidden");
  store.set("unread", false);
  renderNotifs();
});
document.addEventListener("click", (e) => {
  if (!e.target.closest(".bell-wrap")) $("notif-panel").classList.add("hidden");
});

/* ------------------------------------------------ search filter */
$("search-input").addEventListener("input", (e) => {
  const q = e.target.value.trim().toLowerCase();
  document.querySelectorAll(".factor, .proof").forEach((el) => {
    el.classList.toggle("hidden", q && !el.textContent.toLowerCase().includes(q));
  });
});

/* ------------------------------------------------ navigation */
document.querySelectorAll(".nav-btn[data-view]").forEach((b) =>
  b.addEventListener("click", () => showView(b.dataset.view)));
function bindGoto(scope = document) {
  scope.querySelectorAll("[data-goto]").forEach((b) =>
    b.addEventListener("click", () => showView(b.dataset.goto)));
}
// read dataset at click time so updateRing() can retarget the task button
$("btn-task")?.addEventListener("click", () => showView($("btn-task").dataset.goto));
function showView(name) {
  document.querySelectorAll(".view").forEach((v) => v.classList.add("hidden"));
  $("view-" + name).classList.remove("hidden");
  document.querySelectorAll(".nav-btn[data-view]").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === name));
  // Search only filters the dashboard's factors/attestations  hide elsewhere.
  $("search-wrap").style.visibility = name === "dashboard" ? "visible" : "hidden";
  if (name === "lending") refreshLending();
}
bindGoto();

$("factor-tabs").addEventListener("click", (e) => {
  if (!e.target.classList.contains("tab")) return;
  document.querySelectorAll("#factor-tabs .tab").forEach((t) => t.classList.remove("active"));
  e.target.classList.add("active");
  renderFactors(e.target.textContent);
});

/* ------------------------------------------------ wallet connect / disconnect */
async function connect(silent = false) {
  if (!window.ethereum) { if (!silent) toast("MetaMask not detected  install it to connect"); return; }
  if (S.connecting) return; // re-entrancy guard: one MetaMask flow at a time
  S.connecting = true;
  S.wallet = createWalletClient({ chain: coston2, transport: custom(window.ethereum) });
  try {
    let accounts = await window.ethereum.request({ method: "eth_accounts" });
    if (!accounts.length) {
      if (silent) return; // don't pop MetaMask uninvited on page load
      accounts = await S.wallet.requestAddresses();
    }
    S.account = accounts[0];
    const chainHex = await window.ethereum.request({ method: "eth_chainId" });
    if (parseInt(chainHex, 16) !== coston2.id) {
      if (silent) {
        // NEVER pop MetaMask on page load  just inform. The prompt only
        // ever appears from an explicit Connect click.
        toast("MetaMask is on another network  click your address and reconnect, or switch to Coston2.");
      } else {
        try { await S.wallet.switchChain({ id: coston2.id }); }
        catch { try { await S.wallet.addChain({ chain: coston2 }); await S.wallet.switchChain({ id: coston2.id }); } catch {} }
      }
    }
    onConnected();
  } catch (e) { if (!silent) toast("Connect failed: " + e.message); }
  finally { S.connecting = false; }
}

function onConnected() {
  const short = S.account.slice(0, 6) + "…" + S.account.slice(-4);
  $("wallet-chip").textContent = `⛓ ${short} · Coston2`;
  $("hello").textContent = "Hello, " + short + "!";
  const wb = $("btn-wallet");
  wb.textContent = short;
  wb.classList.add("connected");
  $("wallet-full").textContent = S.account;
  setStatus("s1", "");
  markStep("btn-connect", "Connected · " + short, "reset-connect");

  // Restore session for this account
  S.binding = store.get("binding");
  S.proofs = store.get("proofs", []);
  // de-dupe any historical double entries by tx hash
  const seen = new Set();
  S.proofs = S.proofs.filter((p) => {
    const k = p.txid.toLowerCase().replace(/^0x/, "");
    if (seen.has(k)) return false;
    seen.add(k); return true;
  });
  store.set("proofs", S.proofs);
  S.envelope = store.get("envelope");
  if (S.binding) {
    markStep("btn-sign", "Signed · " + shortX(S.binding.xrplAddress), "reset-sign");
    $("btn-link").disabled = false;
  }
  $("btn-score").disabled = S.proofs.length === 0;
  $("btn-task").classList.remove("hidden");
  renderProofs(); renderNotifs(); renderFactors(activeTab());

  // Show a real rendered skeleton over the whole main area until ALL chain
  // reads settle, then reveal everything at once, already correct.
  S.booting = true;
  showSkeleton(true);
  Promise.allSettled([refreshScore(), refreshPrices(), refreshIdentity(), refreshLending()])
    .then(() => {
      S.booting = false; updateRing(); renderFactors(activeTab());
      renderBackupStats(); maybePromptBackup(); showSkeleton(false);
    });
}

/* ----------- skeleton overlay: animated placeholder layout ---------------- */
const SK_CSS = `
#fc-skeleton{position:absolute;inset:0;z-index:35;background:var(--panel);
  display:flex;gap:24px;padding:64px 4px 8px;}
#fc-skeleton .c-l{flex:1.55;min-width:0}
#fc-skeleton .c-r{flex:1;min-width:280px}
#fc-skeleton .b{border-radius:20px;margin-bottom:14px;position:relative;overflow:hidden;
  background:var(--input);}
#fc-skeleton .b::after{content:"";position:absolute;inset:0;transform:translateX(-100%);
  background:linear-gradient(90deg,transparent,var(--border),transparent);
  animation:fcsk 1.2s infinite;}
#fc-skeleton .b.sm{border-radius:14px}
#fc-skeleton .row{display:flex;gap:12px}
#fc-skeleton .row .b{flex:1}
@keyframes fcsk{to{transform:translateX(100%)}}
@media (prefers-reduced-motion:reduce){#fc-skeleton .b::after{animation:none}}
@media (max-width:900px){#fc-skeleton{flex-direction:column}}`;

const SK_HTML = `
<div class="c-l">
  <div class="b" style="height:150px"></div>
  <div class="b" style="height:70px"></div>
  <div class="b sm" style="height:20px;width:38%;margin:20px 0 12px"></div>
  ${'<div class="b sm" style="height:64px"></div>'.repeat(5)}
</div>
<div class="c-r">
  <div class="row"><div class="b" style="height:76px"></div><div class="b" style="height:76px"></div></div>
  <div class="b sm" style="height:20px;width:46%;margin:16px 0 12px"></div>
  <div class="b" style="height:180px"></div>
  <div class="b" style="height:148px;margin-top:18px"></div>
  <div class="b" style="height:60px"></div>
</div>`;

function showSkeleton(on) {
  let ov = $("fc-skeleton");
  if (on) {
    if (!$("fc-skeleton-style")) {
      const st = document.createElement("style");
      st.id = "fc-skeleton-style"; st.textContent = SK_CSS;
      document.head.appendChild(st);
    }
    if (!ov) {
      ov = document.createElement("div");
      ov.id = "fc-skeleton";
      ov.setAttribute("aria-hidden", "true");
      const main = document.querySelector(".main");
      main.style.position = "relative";
      ov.innerHTML = SK_HTML;
      main.appendChild(ov);
    }
    ov.style.display = "flex";
  } else if (ov) {
    ov.style.display = "none";
  }
}

function disconnect() {
  // MetaMask has no programmatic "log out"; revoke permissions where supported,
  // then clear our session so the app treats you as disconnected.
  try {
    window.ethereum?.request({
      method: "wallet_revokePermissions",
      params: [{ eth_accounts: {} }],
    });
  } catch {}
  S.account = null; S.wallet = null; S.binding = null; S.proofs = []; S.envelope = null;
  sessionStorage.setItem("fc:disconnected", "1");
  location.reload();
}
$("btn-connect").addEventListener("click", () => connect(false));

// OpenSea-style wallet button: Connect when signed out; when connected it
// shows the address and opens a menu with Copy / Disconnect.
$("btn-wallet").addEventListener("click", (e) => {
  e.stopPropagation();
  if (!S.account) return connect(false);
  $("wallet-menu").classList.toggle("hidden");
});
$("btn-copy-addr").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(S.account); toast("Address copied"); } catch {}
  $("wallet-menu").classList.add("hidden");
});
$("btn-disconnect").addEventListener("click", disconnect);
$("reset-connect")?.addEventListener("click", disconnect);
// Re-sign: clear the stored binding and let the user sign again.
$("reset-link")?.addEventListener("click", async () => {
  const ok = await confirmModal({
    title: "Unlink XRPL identity?",
    body: "This sends an on-chain transaction to remove your binding. You'll need to link again before computing new scores.",
    confirmText: "Unlink", danger: true,
  });
  if (!ok) return;
  try {
    const hash = await S.wallet.writeContract({
      account: S.account, chain: coston2,
      address: CFG.contracts.identityRegistry, abi: IDENTITY_ABI,
      functionName: "unlink", args: [],
    });
    S.linkedOnChain = false;
    // Recover the binding from storage if it isn't in memory this session.
    if (!S.binding) S.binding = store.get("binding");
    unmarkStep("btn-link", "Link on Coston2", "reset-link");
    if (S.binding) {
      $("btn-link").disabled = false; // can re-link the same binding
    } else {
      // No signed binding available  send them back to step 2.
      $("btn-link").disabled = true;
      unmarkStep("btn-sign", "Sign with GemWallet", "reset-sign");
      setStatus("s3", "sign with GemWallet again, then link", false);
    }
    updateRing();
    notify("Identity unlinked on Coston2");
    toast("Unlinked  tx " + hash.slice(0, 14) + "…");
  } catch (e) { setStatus("s3", e.shortMessage || e.message, false); }
});
$("reset-sign")?.addEventListener("click", () => {
  S.binding = null; localStorage.removeItem(store.key("binding"));
  unmarkStep("btn-sign", "Sign with GemWallet", "reset-sign");
  $("btn-link").disabled = true;
  setStatus("s2", "");
  toast("Signature cleared  sign again to refresh your binding.");
});
document.addEventListener("click", (e) => {
  if (!e.target.closest(".wallet-wrap")) $("wallet-menu").classList.add("hidden");
});
window.ethereum?.on?.("chainChanged", () => location.reload());
window.ethereum?.on?.("accountsChanged", (accs) => {
  // Fires on connect approval too  only reload on a REAL account switch,
  // otherwise the page reloads mid-connect and re-prompts MetaMask.
  const next = accs?.[0]?.toLowerCase();
  if (S.account && next && next !== S.account.toLowerCase()) location.reload();
  if (S.account && !next) location.reload(); // disconnected from MetaMask side
});

function updateRing() {
  if (S.booting) return; // boot renders once, after all reads settle
  // Before a score exists: setup progress (20/40/60/80).
  // Once a score exists: ring = score out of 1000  100% ONLY at a full 1000.
  let pct = 0, title = "Get your credit score",
      sub = "connect wallets to begin", cta = "Continue", goto = "identity";
  if (S.account) { title = "Link your XRPL identity"; pct = 20; sub = "sign the XRPL challenge"; }
  if (S.binding) { title = "Link your XRPL identity"; pct = 40; sub = "store the hashed binding on-chain"; }
  if (S.linkedOnChain) {
    title = "Attest your XRPL history"; pct = 60;
    sub = "attest payments sent from your linked address";
    cta = "Attest history"; goto = "attestations";
  }
  if (S.linkedOnChain && S.proofs.length > 0) {
    title = "Compute your score"; pct = 80;
    sub = `${S.proofs.length} proof(s) ready  compute your score`;
    cta = "Compute score"; goto = "attestations";
  }
  if (S.scoreValid) {
    title = "Your credit score";
    pct = Math.round(S.scoreValue / 10);           // 580 -> 58%
    sub = `score ${S.scoreValue} / 1000  attest more history to improve`;
    if (S.scoreValue >= 700) { cta = "Open lending"; goto = "lending"; }
    else { cta = "Improve score"; goto = "attestations"; }
  } else if (S.scoreValue > 0) {
    title = "Score expired";
    sub = `previous score ${S.scoreValue} lapsed  recompute to restore it`;
    cta = "Recompute"; goto = "attestations";
  }
  const C = 138.2;
  $("task-ring").style.strokeDashoffset = C * (1 - pct / 100);
  $("task-pct").textContent = pct + "%";
  $("task-sub").textContent = sub;
  const ttl = $("task-title"); if (ttl) ttl.textContent = title;
  const btn = $("btn-task");
  btn.textContent = cta;
  btn.dataset.goto = goto;
  // Only offer the next action once a wallet is actually connected.
  btn.classList.toggle("hidden", !S.account);
}

/* ------------------------------------------------ identity link */
$("btn-sign").addEventListener("click", async () => {
  if (!S.account) { toast("Connect MetaMask first"); return; }
  try {
    const installed = await gem.isInstalled();
    if (!installed.result.isInstalled) { toast("GemWallet not detected  install the XRPL wallet extension"); return; }
    const addr = (await gem.getAddress()).result?.address;
    if (!addr) throw new Error("GemWallet returned no address");
    S.xrplShort = shortX(addr);

    const ch = await api("/identity/challenge", { flareAddress: S.account, xrplAddress: addr });
    const signed = await gem.signMessage(ch.message);
    if (!signed.result?.signedMessage) throw new Error("signature rejected");
    const pubkey = (await gem.getPublicKey()).result?.publicKey;

    const verified = await api("/identity/verify", {
      flareAddress: S.account, xrplAddress: addr,
      signature: signed.result.signedMessage, publicKey: pubkey,
    });
    S.binding = { ...verified, xrplAddress: addr };
    store.set("binding", S.binding);   // persists across refreshes
    markStep("btn-sign", "Signed · " + shortX(addr), "reset-sign");
    setStatus("s2", ""); // button now carries the state
    $("binding-note").textContent =
      `bindingHash ${verified.bindingHash} · preimage saved in this browser; only the enclave ever sees it.`;
    $("btn-link").disabled = false;
    notify("XRPL identity signature verified");
    updateRing();
  } catch (e) { setStatus("s2", e.message, false); }
});

$("btn-link").addEventListener("click", async () => {
  try {
    const hash = await S.wallet.writeContract({
      account: S.account, chain: coston2,
      address: CFG.contracts.identityRegistry, abi: IDENTITY_ABI,
      functionName: "link", args: [S.binding.bindingHash],
    });
    markStep("btn-link", "Linked on Coston2", "reset-link");
    setStatus("s3", ""); // button now carries the state
    S.linkedOnChain = true;
    notify("Identity linked on Coston2 (hash only)");
    updateRing();
    toast("Identity linked privately  this persists on-chain; no need to repeat it.");
  } catch (e) { setStatus("s3", e.message, false); }
});

async function refreshIdentity() {
  try {
    const st = await api("/identity/" + S.account);
    if (st.linked) {
      S.linkedOnChain = true;
      markStep("btn-link", "Linked on Coston2", "reset-link");
      updateRing();
    }
  } catch { /* contracts not deployed yet */ }
}

/* ------------------------------------------------ FDC attestations */
$("btn-attest").addEventListener("click", async () => {
  const txid = $("xrpl-tx").value.trim();
  if (!/^(0x)?[0-9a-fA-F]{64}$/.test(txid)) {
    setStatus("attest-status", "enter a full 64-hex-char XRPL tx hash", false); return;
  }
  const norm = (h) => h.toLowerCase().replace(/^0x/, "");
  if (S.proofs.some((p) => norm(p.txid) === norm(txid))) {
    setStatus("attest-status", "that transaction is already attested  duplicates don't add score", false); return;
  }
  if (S.pending.some((p) => norm(p.txid) === norm(txid))) {
    setStatus("attest-status", "that transaction is already in progress  its proof arrives in ~2–3 min, no need to resubmit", false); return;
  }
  try {
    setStatus("attest-status", "preparing request at verifier…");
    const prep = await api("/fdc/prepare", { transactionId: txid });
    setStatus("attest-status", `fee ${formatEther(BigInt(prep.feeWei))} C2FLR  submitting to FdcHub…`);
    const req = await api("/fdc/request", { abiEncodedRequest: prep.abiEncodedRequest });
    const item = { ...req, abiEncodedRequest: prep.abiEncodedRequest, txid };
    S.pending.push(item);
    $("tile-pending").textContent = S.pending.length;
    notify(`Attestation requested  round ${req.votingRoundId}`);
    setStatus("attest-status", `round ${req.votingRoundId}  polling DA layer for proof (~2 min)…`);
    pollProof(item);
  } catch (e) { setStatus("attest-status", e.message, false); }
});

const POLL_EVERY_MS = 10_000;
const POLL_MAX = 25;

function startCountdown(item) {
  stopCountdown();
  item.deadline = Date.now() + POLL_MAX * POLL_EVERY_MS;
  const tick = () => {
    const left = Math.max(0, Math.round((item.deadline - Date.now()) / 1000));
    const m = String(Math.floor(left / 60)).padStart(1, "0");
    const s = String(left % 60).padStart(2, "0");
    setStatus("attest-status",
      `round ${item.votingRoundId}  waiting for proof · ${m}:${s} remaining`, true, true);
  };
  tick();
  S._countdown = setInterval(tick, 1000);
}
function stopCountdown() {
  if (S._countdown) { clearInterval(S._countdown); S._countdown = null; }
}

async function pollProof(item, attempt = 0) {
  if (attempt === 0) startCountdown(item);
  if (attempt >= POLL_MAX) {
    stopCountdown();
    S.pending = S.pending.filter((p) => p !== item);
    $("tile-pending").textContent = S.pending.length;
    setStatus("attest-status",
      "proof didn't arrive in time  the round may have been missed. Request the attestation again.", false);
    return;
  }
  try {
    const res = await api("/fdc/proof", {
      votingRoundId: item.votingRoundId, abiEncodedRequest: item.abiEncodedRequest,
    });
    if (!res.ready) return setTimeout(() => pollProof(item, attempt + 1), POLL_EVERY_MS);
    stopCountdown();
    S.pending = S.pending.filter((p) => p !== item);
    const dup = S.proofs.some((p) =>
      p.txid.toLowerCase().replace(/^0x/, "") === item.txid.toLowerCase().replace(/^0x/, ""));
    if (dup) { $("tile-pending").textContent = S.pending.length; return; }
    S.proofs.push({ txid: item.txid, votingRoundId: item.votingRoundId, proof: res.proof, response: res.response });
    store.set("proofs", S.proofs);
    $("tile-pending").textContent = S.pending.length;
    renderProofs(); renderFactors(activeTab()); renderBackupStats(); maybePromptBackup();
    $("btn-score").disabled = false;
    updateRing();
    notify("FDC proof received ✓");
    setStatus("attest-status", "proof received ✓  ready for the enclave");
  } catch (e) { stopCountdown(); setStatus("attest-status", e.message, false); }
}

function renderProofs() {
  $("proof-list").innerHTML = S.proofs.map((p) => `
    <div class="proof">
      <strong>Payment proof</strong> · round ${p.votingRoundId} · XRPL tx ${p.txid.slice(0, 12)}…
      <br/><span class="muted">Merkle proof held locally  forwarded only to the TEE.</span>
    </div>`).join("");
}

/* ------------------------------------------------ enclave scoring */
function proofFingerprint() {
  return S.proofs.map((p) => p.txid.toLowerCase().replace(/^0x/, "")).sort().join(",");
}

$("btn-score").addEventListener("click", async () => {
  if (!S.binding) { setStatus("score-status", "link your XRPL identity first (Identity page)", false); return; }
  // Recompute rules: same proof set + still-valid registered score = refuse
  // with an error. Allowed again when (a) a NEW attestation is added, or
  // (b) the registered score has expired and needs renewing.
  const sameProofs = S.envelope && store.get("scoredFp") === proofFingerprint();
  const scoreExpired = !S.scoreValid ||
    (S.envelope && Date.now() / 1000 > Number(S.envelope.expiry));
  if (sameProofs && !scoreExpired) {
    setStatus("score-status",
      `score ${S.envelope.score} is already registered from these exact proofs  ` +
      `attest a new payment to improve it, or recompute after it expires`, false);
    return;
  }
  const transport = document.querySelector("input[name=transport]:checked").value;
  try {
    setStatus("score-status", "verifying proofs + scoring inside the enclave…");
    const res = await api("/score/compute", {
      subject: S.account,
      proofs: S.proofs.map((p) => ({ proof: p.proof, response: p.response })),
      binding: { xrplAddress: S.binding.xrplAddress, nonce: S.binding.nonce },
      transport,
    });
    if (res.envelope) {
      S.envelope = res.envelope;
      store.set("envelope", S.envelope);
      const rej = res.envelope.proofsRejected?.length
        ? ` (${res.envelope.proofsRejected.length} proof(s) rejected: ${res.envelope.proofsRejected.join("; ")})` : "";
      setStatus("score-status", `TEE-signed score ${res.envelope.score}${rej}  submitting…`);
      await submitScore();
      store.set("scoredFp", proofFingerprint());
      setStatus("score-status",
        `score ${res.envelope.score} registered ✓  tip: download a backup from Settings so a ` +
        `cleared browser can't cost you these proofs`);
      renderBackupStats(); maybePromptBackup();
      renderFactors(activeTab());
    } else {
      setStatus("score-status", "instruction sent on-chain: " + res.txHash);
    }
  } catch (e) {
    const msg = /connection|fetch|502|unreachable/i.test(e.message)
      ? "can't reach the scoring enclave  make sure it's running (python tools/mock_enclave.py) " +
        "and ENCLAVE_URL is set in .env"
      : e.message;
    setStatus("score-status", msg, false);
  }
});

async function submitScore() {
  const e = S.envelope;
  if (CFG.hasRelayer) {
    await api("/score/submit", {
      subject: e.subject, score: e.score, expiry: e.expiry,
      codeHash: e.codeHash, signature: e.signature, signer: e.signer,
    });
  } else {
    await S.wallet.writeContract({
      account: S.account, chain: coston2,
      address: CFG.contracts.creditRegistry, abi: REGISTRY_ABI,
      functionName: "submitScore",
      args: [e.subject, e.score, BigInt(e.expiry), e.codeHash, e.signature],
    });
  }
  notify(`Score ${e.score} registered on CreditRegistry`);
  toast("Score registered on CreditRegistry");
  refreshScore(); refreshLending();
}

/* ------------------------------------------------ dashboard data */
async function refreshScore() {
  try {
    const s = await api("/score/" + S.account);
    S.scoreValid = !!(s.configured && s.valid);
    S.scoreValue = s.score || 0;
    updateRing();
    if (s.configured && s.valid) {
      $("tile-score").textContent = s.score;
      $("tile-validity").textContent = `valid ${s.daysRemaining} days`;
      pushScorePoint(s.score);
    } else {
      $("tile-score").textContent = "";
      $("tile-validity").textContent = s.configured ? "not computed" : "deploy contracts";
    }
  } catch { /* offline */ }
}

async function refreshPrices() {
  try {
    const p = await api("/market/prices");
    $("prices").innerHTML = Object.entries(p).map(([pair, d]) =>
      `<span class="p">$${d.price.toFixed(4)}<small>${pair} · FTSOv2</small></span>`).join("");
  } catch { $("prices").innerHTML = `<span class="muted small">FTSOv2 unreachable  check RPC</span>`; }
}

/* ------------------------------------------------ lending */
async function refreshLending() {
  if (!S.account) return;
  try {
    const l = await api("/lending/" + S.account);
    if (!l.configured) { setStatus("lend-status", "deploy FxrpLendingPool + set LENDING_POOL", false); return; }
    S.lending = l;
    S.maxBorrow = +formatUnits(BigInt(l.maxBorrowableFxrp), 6);
    const ratio = l.collateralRatioBps / 100;
    $("pos-ratio").textContent = ratio + "%";
    $("pos-ratio").className = "big " + (ratio <= 120 ? "flare-text" : "");
    $("pos-collateral").textContent = (+formatEther(BigInt(l.collateralWei))).toFixed(2);
    $("pos-debt").textContent = (+formatUnits(BigInt(l.debtFxrp), 6)).toFixed(2);
    $("pos-max").textContent = S.maxBorrow.toFixed(2);
    $("borrow-hint").textContent = ratio <= 120
      ? `score ≥ 700 → 120% ratio · up to ${S.maxBorrow.toFixed(2)} FXRP`
      : `at 150% ratio · up to ${S.maxBorrow.toFixed(2)} FXRP · reach score 700 to unlock 120%`;
  } catch (e) { setStatus("lend-status", e.message, false); }
}

const lendTx = (fn) => async () => {
  if (!S.account) { toast("Connect MetaMask first"); return; }
  try { await fn(); setStatus("lend-status", "confirmed ✓"); refreshLending(); }
  catch (e) { setStatus("lend-status", e.shortMessage || e.message, false); }
};

$("btn-deposit").addEventListener("click", lendTx(async () => {
  const v = parseFloat($("amt-deposit").value);
  if (!(v > 0)) throw new Error("enter a deposit amount");
  await S.wallet.writeContract({
    account: S.account, chain: coston2, address: CFG.contracts.lendingPool,
    abi: POOL_ABI, functionName: "deposit", value: parseEther(String(v)),
  });
  notify(`Deposited ${v} C2FLR collateral`);
}));

$("btn-borrow").addEventListener("click", lendTx(async () => {
  const v = parseFloat($("amt-borrow").value);
  if (!(v > 0)) throw new Error("enter a borrow amount");
  if (v > S.maxBorrow) throw new Error(
    `exceeds your borrowing power (${S.maxBorrow.toFixed(2)} FXRP at your current score/collateral)`);
  await S.wallet.writeContract({
    account: S.account, chain: coston2, address: CFG.contracts.lendingPool,
    abi: POOL_ABI, functionName: "borrow", args: [parseUnits(String(v), 6)],
  });
  notify(`Borrowed ${v} FXRP`);
}));

$("btn-repay").addEventListener("click", lendTx(async () => {
  const v = parseFloat($("amt-repay").value);
  if (!(v > 0)) throw new Error("enter a repay amount");
  const amt = parseUnits(String(v), 6);
  await S.wallet.writeContract({
    account: S.account, chain: coston2, address: CFG.contracts.fxrpToken,
    abi: ERC20_ABI, functionName: "approve", args: [CFG.contracts.lendingPool, amt],
  });
  await S.wallet.writeContract({
    account: S.account, chain: coston2, address: CFG.contracts.lendingPool,
    abi: POOL_ABI, functionName: "repay", args: [amt],
  });
  notify(`Repaid ${v} FXRP  repayment record improved`);
}));

/* ------------------------------------------------ factors  live data
   Each factor shows the actual points it contributes (from the enclave's
   signed breakdown) out of its maximum, with a progress bar. */
function activeTab() { return document.querySelector("#factor-tabs .tab.active")?.textContent || "All factors"; }

function factorData() {
  const b = S.envelope?.breakdown || {};
  return [
    { icon: "i-clock", name: "Wallet age", tag: "XRPL",
      sub: "oldest attested tx > 30 days old", pts: b.walletAge ?? null, max: 100 },
    { icon: "i-pulse", name: "Transaction history", tag: "XRPL",
      sub: `${S.proofs.length} FDC Payment proof(s) collected`, pts: b.transactions ?? null, max: 200 },
    { icon: "i-scale", name: "Volume", tag: "XRPL",
      sub: "attested XRP moved, per-counterparty capped", pts: b.volume ?? null, max: 200 },
    { icon: "i-bank", name: "Repayment record", tag: "Lending",
      sub: S.lending ? `${(+formatUnits(BigInt(S.lending.debtFxrp || 0), 6)).toFixed(2)} FXRP outstanding` : "FXRP pool, Coston2",
      pts: b.cleanRepayment ?? null, max: 100 },
    { icon: "i-shield", name: "TEE attestation", tag: "Flare",
      sub: S.envelope ? `codeHash ${S.envelope.codeHash.slice(0, 14)}…` : "no signed score yet",
      pts: S.envelope ? 400 : null, max: 400, label: "base" },
  ];
}

function renderFactors(filter = "All factors") {
  $("factors").innerHTML = factorData()
    .filter((f) => filter === "All factors" || f.tag === filter)
    .map((f) => {
      const has = f.pts !== null;
      const pct = has ? Math.min(100, (f.pts / f.max) * 100) : 0;
      return `
      <div class="factor">
        <div class="f-icon"><svg class="ic"><use href="#${f.icon}"/></svg></div>
        <div class="f-body"><strong>${f.name}</strong><span class="muted small">${f.sub}</span></div>
        <div class="f-bar"><i style="width:${pct}%"></i></div>
        <div class="f-points">${has ? f.pts : ""}<small>/ ${f.max} ${f.label || "pts"}</small></div>
      </div>`;
    }).join("");
  // re-apply active search filter
  $("search-input").dispatchEvent(new Event("input"));
}

/* ------------------------------------------------ chart */
function scoreHistory() { return store.get("history", []); }
function pushScorePoint(v) {
  const h = scoreHistory();
  if (h[h.length - 1]?.v !== v) { h.push({ v, t: Date.now() }); store.set("history", h.slice(-14)); }
  drawChart();
}
function drawChart() {
  const svg = $("chart"), W = 300, H = 150, PAD = 16;
  const data = scoreHistory().map((p) => p.v);
  if (!data.length) data.push(0);
  const min = Math.min(...data, 400) - 20, max = Math.max(...data, 1000) + 20;
  const xs = data.map((_, i) => PAD + i * (W - 2 * PAD) / Math.max(data.length - 1, 1));
  const ys = data.map((v) => H - PAD - (v - min) / (max - min) * (H - 2 * PAD));
  let d = `M ${xs[0]} ${ys[0]}`;
  for (let i = 1; i < xs.length; i++) {
    const cx = (xs[i - 1] + xs[i]) / 2;
    d += ` C ${cx} ${ys[i - 1]}, ${cx} ${ys[i]}, ${xs[i]} ${ys[i]}`;
  }
  svg.innerHTML =
    [0, 1, 2, 3, 4].map((i) => { const y = PAD + i * (H - 2 * PAD) / 4;
      return `<line x1="${PAD}" x2="${W - PAD}" y1="${y}" y2="${y}" stroke="var(--border)"/>`; }).join("") +
    `<path d="${d}" fill="none" stroke="var(--flare)" stroke-width="2.4" stroke-linecap="round"/>` +
    xs.map((x, i) => `
      <circle cx="${x}" cy="${ys[i]}" r="4" fill="var(--card)" stroke="var(--flare)" stroke-width="2.4"/>
      <g transform="translate(${Math.min(Math.max(x - 15, 2), W - 32)},${ys[i] - 24})">
        <rect width="30" height="16" rx="6" fill="var(--card)" stroke="var(--border)"/>
        <text x="15" y="11" text-anchor="middle" font-size="8.5" font-weight="700" fill="var(--ink)">${data[i] || ""}</text>
      </g>`).join("");
}


/* ================================================================
   Backup & recovery
   On-chain state (score, identity link, lending position) always
   survives. The binding preimage and FDC proofs live only in this
   browser  and old XRPL transactions can fall outside the verifier's
   history window, so re-attesting is not always possible. These let a
   user carry that state to another browser or recover after a wipe.
   ================================================================ */
const BACKUP_VERSION = 1;

function renderBackupStats() {
  const el = $("backup-stats");
  if (!el) return;
  const linked = S.linkedOnChain ? "Linked" : "Not linked";
  el.innerHTML = `
    <div class="backup-stat"><b>${S.proofs.length}</b>proof${S.proofs.length === 1 ? "" : "s"} stored</div>
    <div class="backup-stat"><b>${S.binding ? "Yes" : "No"}</b>binding preimage</div>
    <div class="backup-stat"><b>${linked}</b>on-chain identity</div>`;
}

function exportBackup() {
  if (!S.account) { setStatus("backup-status", "connect your wallet first", false); return; }
  if (!S.binding && S.proofs.length === 0) {
    setStatus("backup-status", "nothing to back up yet  link your identity and attest a payment first", false);
    return;
  }
  const payload = {
    format: "flarecredit-backup",
    version: BACKUP_VERSION,
    exportedAt: new Date().toISOString(),
    flareAddress: S.account,
    binding: S.binding,          // { bindingHash, nonce, xrplAddress }
    proofs: S.proofs,            // [{ txid, votingRoundId, proof, response }]
    envelope: S.envelope || null,
    history: store.get("history", []),
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  const stamp = new Date().toISOString().slice(0, 10);
  a.href = url;
  a.download = `flarecredit-backup-${S.account.slice(0, 6)}-${stamp}.json`;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  store.set("backedUpFp", proofFingerprint());
  setStatus("backup-status",
    `backup downloaded  ${S.proofs.length} proof(s) and your binding preimage. Keep it private.`);
  notify("Backup downloaded");
}

async function importBackup(file) {
  try {
    const text = await file.text();
    let data;
    try { data = JSON.parse(text); }
    catch { throw new Error("that file isn't valid JSON"); }

    if (data.format !== "flarecredit-backup") throw new Error("not a FlareCredit backup file");
    if (Number(data.version) > BACKUP_VERSION) throw new Error("backup was made by a newer version of the app");
    if (!S.account) throw new Error("connect your wallet before restoring");
    if (data.flareAddress && data.flareAddress.toLowerCase() !== S.account.toLowerCase()) {
      const ok = await confirmModal({
        title: "Different wallet",
        body: `This backup belongs to ${data.flareAddress.slice(0, 10)}… but you're connected as ` +
              `${S.account.slice(0, 10)}…. Restoring it here will not work  scores are bound to a ` +
              `specific Flare address. Continue anyway?`,
        confirmText: "Restore anyway", danger: true,
      });
      if (!ok) { setStatus("backup-status", "restore cancelled", false); return; }
    }

    // merge proofs, de-duplicated by transaction id
    const norm = (h) => String(h).toLowerCase().replace(/^0x/, "");
    const seen = new Set(S.proofs.map((p) => norm(p.txid)));
    let added = 0;
    for (const pr of (data.proofs || [])) {
      if (!pr || !pr.txid || !pr.proof) continue;
      if (seen.has(norm(pr.txid))) continue;
      seen.add(norm(pr.txid)); S.proofs.push(pr); added++;
    }
    store.set("proofs", S.proofs);

    let restoredBinding = false;
    if (data.binding && data.binding.xrplAddress && data.binding.nonce && !S.binding) {
      S.binding = data.binding;
      store.set("binding", S.binding);
      markStep("btn-sign", "Signed · " + shortX(S.binding.xrplAddress), "reset-sign");
      $("btn-link").disabled = false;
      restoredBinding = true;
    }
    if (data.envelope && !S.envelope) { S.envelope = data.envelope; store.set("envelope", S.envelope); }
    if (Array.isArray(data.history) && data.history.length && !store.get("history", []).length) {
      store.set("history", data.history);
    }

    renderProofs(); renderFactors(activeTab()); renderBackupStats(); drawChart();
    $("btn-score").disabled = S.proofs.length === 0;
    updateRing();

    const bits = [];
    if (added) bits.push(`${added} proof(s)`);
    if (restoredBinding) bits.push("binding preimage");
    setStatus("backup-status",
      bits.length ? `restored ${bits.join(" and ")} ✓` : "nothing new to restore  everything was already here");
    if (bits.length) notify("Backup restored");
  } catch (e) {
    setStatus("backup-status", e.message, false);
  }
}

$("btn-export")?.addEventListener("click", exportBackup);
$("btn-import")?.addEventListener("click", () => $("import-file").click());
$("import-file")?.addEventListener("change", (e) => {
  const f = e.target.files?.[0];
  if (f) importBackup(f);
  e.target.value = "";   // allow re-selecting the same file
});

/* Nudge the user to back up once they have state worth losing. */
function maybePromptBackup() {
  if (!S.account || S.proofs.length === 0) return;
  if (store.get("backedUpFp") === proofFingerprint()) return;   // already backed up this set
  const card = $("backup-card");
  if (!card || card.querySelector(".backup-warn")) return;
  const w = document.createElement("div");
  w.className = "backup-warn";
  w.innerHTML = `<span><b>Not backed up.</b> You have ${S.proofs.length} proof(s) stored only in ` +
                `this browser. Download a backup so a cleared cache can't cost you them.</span>`;
  card.appendChild(w);
}

/* ------------------------------------------------ settings + boot */
/* Settings: human-readable deployment info instead of a raw JSON dump */
(function renderDeployment() {
  const label = {
    identityRegistry: "Identity registry",
    creditRegistry: "Credit registry",
    lendingPool: "FXRP lending pool",
    fxrpToken: "FXRP token",
    instructionSender: "Instruction sender",
  };
  const explorer = "https://coston2-explorer.flare.network/address/";
  const short = (a) => a.slice(0, 8) + "…" + a.slice(-6);
  const rows = [
    `<div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--border)">
       <span class="muted small">Network</span>
       <span style="font-weight:700;font-size:12.5px">Flare Coston2 · testnet</span>
     </div>`,
    ...Object.entries(CFG.contracts)
      .filter(([, a]) => a && !/^0x0+$/.test(a))
      .map(([k, a]) => `
        <div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--border)">
          <span class="muted small">${label[k] || k}</span>
          <a href="${explorer}${a}" target="_blank" rel="noopener"
             style="font-weight:700;font-size:12.5px;color:var(--flare);text-decoration:none">
            ${short(a)} ↗</a>
        </div>`),
  ];
  $("config-dump").innerHTML = rows.join("");
})();

renderFactors(); drawChart(); renderNotifs(); refreshPrices(); renderBackupStats();
if (!sessionStorage.getItem("fc:disconnected")) connect(true);
sessionStorage.removeItem("fc:disconnected");

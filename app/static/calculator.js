const fields = ["bet_type", "back_stake", "back_odds", "lay_odds", "commission_percent", "cashback", "lay_stake_override", "rtp", "spin_count", "wagering_multiplier", "max_cashout"];

const UNMATCHED = new Set(["normal", "acca", "bet_builder"]);
const CASINO = new Set(["casino_wager", "free_spins"]);

let lastLay = "";
let lastLiability = "";
let lastMatchedLay = "2.10";

function pound(value) {
  const amount = Number(value);
  if (Number.isNaN(amount)) return "–";
  const sign = amount < 0 ? "−" : "";
  return `${sign}£${Math.abs(amount).toFixed(2)}`;
}

function paint(id, value) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = pound(value);
  el.classList.remove("pnl-pos", "pnl-neg", "pnl-zero");
  const amount = Number(value);
  if (amount > 0) el.classList.add("pnl-pos");
  else if (amount < 0) el.classList.add("pnl-neg");
  else el.classList.add("pnl-zero");
}

function currentType() {
  const el = document.getElementById("bet_type");
  return el ? el.value : "qualifying";
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function syncVisibility() {
  const type = currentType();
  const unmatched = UNMATCHED.has(type);
  const casino = CASINO.has(type);
  const lay = document.getElementById("lay_odds");
  const cashback = document.getElementById("cashback-field");
  const manual = document.getElementById("manual-expected");
  const layField = document.getElementById("lay-odds-field");
  const exchangeField = document.getElementById("exchange-field");
  const advanced = document.getElementById("matched-advanced");
  const results = document.getElementById("results-panel");
  const unmatchedHint = document.getElementById("unmatched-hint");
  const casinoHint = document.getElementById("casino-hint");
  const rtpField = document.getElementById("rtp-field");
  const spinCount = document.getElementById("spin-count-field");
  const wagering = document.getElementById("wagering-field");
  const maxCashout = document.getElementById("max-cashout-field");
  const oddsField = document.getElementById("odds-field");
  const outcomeTable = document.getElementById("outcome-table");

  if (cashback) cashback.classList.toggle("is-hidden", type !== "money_back");
  if (manual) manual.classList.add("is-hidden");
  if (layField) layField.classList.toggle("is-hidden", unmatched || casino);
  if (exchangeField) exchangeField.classList.toggle("is-hidden", unmatched || casino);
  if (advanced) advanced.classList.toggle("is-hidden", unmatched || casino);
  if (results) results.classList.toggle("is-unmatched", unmatched || casino);
  if (unmatchedHint) unmatchedHint.classList.toggle("is-hidden", !unmatched || casino);
  if (casinoHint) casinoHint.classList.toggle("is-hidden", !casino);
  if (rtpField) rtpField.classList.toggle("is-hidden", !casino);
  if (spinCount) spinCount.classList.toggle("is-hidden", type !== "free_spins");
  if (wagering) wagering.classList.toggle("is-hidden", type !== "free_spins");
  if (maxCashout) maxCashout.classList.toggle("is-hidden", type !== "free_spins");
  const spinInput = document.getElementById("spin_count");
  const wagerInput = document.getElementById("wagering_multiplier");
  const capInput = document.getElementById("max_cashout");
  const rtpInput = document.getElementById("rtp");
  if (spinInput) spinInput.disabled = type !== "free_spins";
  if (wagerInput) wagerInput.disabled = type !== "free_spins";
  if (capInput) capInput.disabled = type !== "free_spins";
  if (rtpInput) rtpInput.disabled = !casino;
  const backOdds = document.getElementById("back_odds");
  if (backOdds) {
    backOdds.disabled = casino;
    backOdds.required = !casino;
  }
  if (oddsField) oddsField.classList.toggle("is-hidden", casino);
  if (outcomeTable) outcomeTable.classList.toggle("is-hidden", casino);
  document.querySelectorAll(".casino-only").forEach((el) => el.classList.toggle("is-hidden", !casino));
  syncLogButton();

  const selections = document.getElementById("selections-field");
  if (selections) selections.classList.remove("is-hidden");

  if (type === "free_bet_snr" || type === "free_bet_sr") {
    setText("stake-label", "Free bet stake");
    setText("odds-label", "Back odds");
  } else if (type === "casino_wager") {
    setText("stake-label", "Amount to wager");
    setText("odds-label", "Back odds");
  } else if (type === "free_spins") {
    setText("stake-label", "Value each");
    setText("odds-label", "Back odds");
  } else if (unmatched) {
    setText("stake-label", "Stake");
    setText("odds-label", type === "acca" ? "Combined odds" : type === "bet_builder" ? "Builder odds" : "Odds");
  } else {
    setText("stake-label", "Back stake");
    setText("odds-label", "Back odds");
  }

  if (type === "acca") {
    setText("selections-label", "Legs");
    const market = document.getElementById("market");
    if (market) market.placeholder = "Team A, Team B, Team C…";
  } else if (type === "bet_builder") {
    setText("selections-label", "Builder selections");
    const market = document.getElementById("market");
    if (market) market.placeholder = "Anytime scorer, over 2.5, BTTS…";
  } else if (casino) {
    setText("selections-label", "Game");
    const market = document.getElementById("market");
    if (market) market.placeholder = "Double Bubble";
  } else {
    setText("selections-label", "Selections");
    const market = document.getElementById("market");
    if (market) market.placeholder = "Match odds / Liverpool";
  }

  setText("back-win-label", unmatched ? "If it wins" : "If back wins");
  setText("lay-win-label", unmatched ? "If it loses" : "If lay wins");
  setText("expected-label", casino ? "Expected profit (RTP)" : unmatched ? "Pending (unmatched)" : "Expected profit");

  if (lay) {
    if (unmatched || casino) {
      if (lay.value && Number(lay.value) > 1) lastMatchedLay = lay.value;
      lay.value = "";
    } else if (!lay.value) {
      lay.value = lastMatchedLay || "2.10";
    }
  }
}

function casinoWagerStake() {
  return Number((document.getElementById("back_stake") || {}).value) || 0;
}

function syncCasinoBoxes(from) {
  const cash = document.getElementById("casino_cashout");
  const profit = document.getElementById("casino_profit");
  if (!cash || !profit) return;
  const spins = currentType() === "free_spins";
  const stake = casinoWagerStake();
  if (from === "cashout" && cash.value !== "") {
    const c = Number(cash.value);
    if (!Number.isNaN(c)) profit.value = (spins ? c : c - stake).toFixed(2);
  } else if (from === "profit" && profit.value !== "") {
    const p = Number(profit.value);
    if (!Number.isNaN(p)) cash.value = (spins ? p : p + stake).toFixed(2);
  }
  syncLogButton();
}

function syncLogButton() {
  const btn = document.getElementById("log-bet-submit");
  if (!btn) return;
  const casino = CASINO.has(currentType());
  const cash = document.getElementById("casino_cashout");
  const profit = document.getElementById("casino_profit");
  const filled = casino && ((cash && cash.value !== "") || (profit && profit.value !== ""));
  btn.textContent = filled ? "Log with winnings" : "Log pending bet";
}

function payload() {
  const data = {};
  for (const name of fields) {
    const el = document.getElementById(name);
    data[name] = el && !el.disabled ? el.value : "";
  }
  if (!Number(data.lay_stake_override)) data.lay_stake_override = "";
  if (currentType() === "free_spins") data.spin_value = data.back_stake;
  return data;
}

async function refresh() {
  const error = document.getElementById("calc-error");
  if (!error) return;
  try {
    const response = await fetch("/api/calculate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload()),
    });
    const data = await response.json();
    if (!response.ok) {
      error.textContent = data.error || "Could not calculate.";
      error.classList.remove("is-hidden");
      return;
    }
    error.classList.add("is-hidden");
    lastLay = data.lay_stake;
    lastLiability = data.liability;
    const outLay = document.getElementById("out-lay");
    const outLiab = document.getElementById("out-liability");
    if (outLay) outLay.textContent = pound(data.lay_stake);
    if (outLiab) outLiab.textContent = pound(data.liability);
    paint("out-expected", data.expected_profit);
    if (data.expected_return !== undefined) paint("out-return", data.expected_return);
    paint("back-bookie", data.if_back_wins.bookie);
    paint("back-exchange", data.if_back_wins.exchange);
    paint("back-total", data.if_back_wins.total);
    paint("lay-bookie", data.if_lay_wins.bookie);
    paint("lay-exchange", data.if_lay_wins.exchange);
    paint("lay-total", data.if_lay_wins.total);
  } catch (err) {
    error.textContent = err.message;
    error.classList.remove("is-hidden");
  }
}

const exchange = document.getElementById("exchange_id");
const commission = document.getElementById("commission_percent");
if (exchange && commission) {
  const selected = exchange.selectedOptions[0];
  if (selected && selected.dataset.commission !== undefined) {
    commission.value = selected.dataset.commission;
  }
}
if (exchange) {
  exchange.addEventListener("change", () => {
    const option = exchange.selectedOptions[0];
    if (option && commission && option.dataset.commission !== undefined) {
      commission.value = option.dataset.commission;
    }
    refresh();
  });
}

for (const name of fields) {
  const el = document.getElementById(name);
  if (el) {
    el.addEventListener("input", () => {
      if (name === "bet_type") {
        syncVisibility();
        applyCasinoDefaults();
      }
      if (name === "back_stake" || name === "bet_type") {
        const cash = document.getElementById("casino_cashout");
        if (cash && cash.value !== "") syncCasinoBoxes("cashout");
        else syncCasinoBoxes("profit");
      }
      refresh();
    });
  }
}
function applyCasinoDefaults() {
  const form = document.getElementById("calc-form");
  if (!form || !form.dataset.casinoOffer) return;
  const type = currentType();
  const stake = document.getElementById("back_stake");
  const rtp = document.getElementById("rtp");
  const spins = document.getElementById("spin_count");
  const wager = document.getElementById("wagering_multiplier");
  const cap = document.getElementById("max_cashout");
  const market = document.getElementById("market");
  if (type === "casino_wager") {
    if (stake && form.dataset.casinoWager) stake.value = form.dataset.casinoWager;
    if (rtp && form.dataset.casinoRtp) rtp.value = form.dataset.casinoRtp;
    if (market && form.dataset.spinGame && !market.value) market.value = form.dataset.spinGame;
  } else if (type === "free_spins") {
    if (stake && form.dataset.spinValue) stake.value = form.dataset.spinValue;
    if (rtp && form.dataset.spinRtp) rtp.value = form.dataset.spinRtp;
    if (spins && form.dataset.spinCount) spins.value = form.dataset.spinCount;
    if (wager && form.dataset.wagering) wager.value = form.dataset.wagering;
    if (cap && form.dataset.maxCashout) cap.value = form.dataset.maxCashout;
    if (market && form.dataset.spinGame) market.value = form.dataset.spinGame;
  }
}

const betType = document.getElementById("bet_type");
if (betType) {
  betType.addEventListener("change", () => {
    syncVisibility();
    applyCasinoDefaults();
    refresh();
  });
}

function syncOfferFields() {
  const pick = document.getElementById("offer_pick");
  if (!pick || pick.value === "__new__") return;
  const option = pick.selectedOptions[0];
  const bookie = document.getElementById("bookie_id");
  if (option?.dataset.bookie && bookie) {
    bookie.value = option.dataset.bookie;
  }
}
window.syncOfferFields = syncOfferFields;

const offerPick = document.getElementById("offer_pick");
if (offerPick) {
  offerPick.addEventListener("change", syncOfferFields);
  syncOfferFields();
}

const calcForm = document.getElementById("calc-form");
if (calcForm) {
  calcForm.addEventListener("submit", (event) => {
    if (offerPick?.value !== "__new__") return;
    const name = document.getElementById("new_offer_name");
    if (name && !name.value.trim()) {
      event.preventDefault();
      name.focus();
    }
  });
}

syncVisibility();
if (!calcForm?.dataset.keepCommission && exchange?.selectedOptions[0]?.dataset.commission && commission) {
  commission.value = exchange.selectedOptions[0].dataset.commission;
}
refresh();

const casinoCashout = document.getElementById("casino_cashout");
const casinoProfit = document.getElementById("casino_profit");
if (casinoCashout) casinoCashout.addEventListener("input", () => syncCasinoBoxes("cashout"));
if (casinoProfit) casinoProfit.addEventListener("input", () => syncCasinoBoxes("profit"));
syncLogButton();

async function copyAmount(value, button) {
  if (value === "" || value === undefined || Number.isNaN(Number(value))) return;
  const text = Number(value).toFixed(2);
  try {
    await navigator.clipboard.writeText(text);
    const original = button.textContent;
    button.textContent = "Copied";
    setTimeout(() => { button.textContent = original; }, 1200);
  } catch {
    button.textContent = "Copy failed";
  }
}

const copyLay = document.getElementById("copy-lay");
const copyLiability = document.getElementById("copy-liability");
if (copyLay) copyLay.addEventListener("click", () => copyAmount(lastLay, copyLay));
if (copyLiability) copyLiability.addEventListener("click", () => copyAmount(lastLiability, copyLiability));

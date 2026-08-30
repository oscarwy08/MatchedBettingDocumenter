const fields = ["bet_type", "back_stake", "back_odds", "lay_odds", "commission_percent", "cashback", "lay_stake_override"];

let lastLay = "";
let lastLiability = "";

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
  return document.getElementById("bet_type").value;
}

function syncVisibility() {
  const type = currentType();
  const cashback = document.getElementById("cashback-field");
  const manual = document.getElementById("manual-expected");
  const lay = document.getElementById("lay_odds");
  if (cashback) cashback.classList.toggle("is-hidden", type !== "money_back");
  if (manual) manual.classList.toggle("is-hidden", type !== "other");
  if (lay && (type === "normal" || type === "acca" || type === "bet_builder") && !lay.value) {
    lay.placeholder = "Leave blank if unmatched";
  }
}

function payload() {
  const data = {};
  for (const name of fields) {
    const el = document.getElementById(name);
    data[name] = el ? el.value : "";
  }
  return data;
}

async function refresh() {
  const error = document.getElementById("calc-error");
  if (currentType() === "other") {
    error.classList.add("is-hidden");
    return;
  }
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
    document.getElementById("out-lay").textContent = pound(data.lay_stake);
    document.getElementById("out-liability").textContent = pound(data.liability);
    paint("out-expected", data.expected_profit);
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
  if (el) el.addEventListener("input", refresh);
}
const betType = document.getElementById("bet_type");
if (betType) {
  betType.addEventListener("change", () => {
    syncVisibility();
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

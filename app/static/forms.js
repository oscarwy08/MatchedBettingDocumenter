(function () {
  const OFFER_SHOW = {
    welcome: ["deposit", "free_funds"],
    reload: ["reload"],
    risk_free: ["deposit", "free_funds"],
    acca_insurance: ["free_funds"],
    extra_place: [],
    price_boost: [],
    other: ["deposit", "free_funds"],
  };
  const OFFER_HINT = {
    welcome: "Deposit goes on your bankroll once. Free bets are what they give you to convert.",
    reload: "How often it comes round, what you put in, and what you get back.",
    risk_free: "Stake it — they refund you if it loses.",
    acca_insurance: "The free bet or refund if the acca loses.",
    extra_place: "Just a name and bookie. Attach the extra-place bets to this offer.",
    price_boost: "Just a name and bookie. Attach the boosted bets to this offer.",
    other: "Use this when it is not a welcome, reload, or insurance offer.",
  };
  const FREE_LABEL = {
    welcome: "Free bets they give you",
    risk_free: "Refund if it loses",
    acca_insurance: "Insurance / free bet",
    other: "Free bets they give you",
  };
  const DEPOSIT_LABEL = {
    welcome: "You deposited",
    risk_free: "You staked / deposited",
    other: "You deposited",
  };

  function syncOfferForm(select) {
    const form = select.closest("form") || document;
    const type = select.value;
    const show = new Set(OFFER_SHOW[type] || OFFER_SHOW.other);
    form.querySelectorAll("[data-offer-field]").forEach((el) => {
      el.classList.toggle("is-hidden", !show.has(el.dataset.offerField));
    });
    form.querySelectorAll("[data-offer-hint]").forEach((el) => {
      el.textContent = OFFER_HINT[type] || "";
    });
    form.querySelectorAll("[data-free-label]").forEach((el) => {
      el.textContent = FREE_LABEL[type] || FREE_LABEL.other;
    });
    form.querySelectorAll("[data-deposit-label]").forEach((el) => {
      el.textContent = DEPOSIT_LABEL[type] || DEPOSIT_LABEL.other;
    });
    if (type === "reload") {
      const freq = form.querySelector("[name$='reload_frequency']");
      if (freq && !freq.value) freq.value = "weekly";
    }
  }

  function bindOfferForms() {
    document.querySelectorAll(".js-offer-type").forEach((select) => {
      select.addEventListener("change", () => syncOfferForm(select));
      syncOfferForm(select);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindOfferForms);
  } else {
    bindOfferForms();
  }
  window.syncOfferForm = syncOfferForm;
})();

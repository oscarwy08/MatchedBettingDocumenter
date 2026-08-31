(() => {
  const pop = document.getElementById("whats-new");
  if (!pop) return;
  const form = pop.querySelector("form");
  const football = pop.querySelector("input[name='football_token']");
  const racingUser = pop.querySelector("input[name='racing_user']");
  const racingPassword = pop.querySelector("input[name='racing_password']");
  const enter = pop.querySelector("[data-whats-new-primary]");

  pop.addEventListener("mousedown", (event) => {
    if (event.target === pop) event.preventDefault();
  });

  document.addEventListener(
    "keydown",
    (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
      }
    },
    true,
  );

  function allFilled() {
    if (!football || !racingUser || !racingPassword) return true;
    return Boolean(
      football.value.trim() && racingUser.value.trim() && racingPassword.value.trim(),
    );
  }

  function syncEnter() {
    if (!enter || enter.getAttribute("name") !== "action" || enter.value !== "save") return;
    enter.disabled = !allFilled();
  }

  if (football) {
    [football, racingUser, racingPassword].forEach((field) => {
      field.addEventListener("input", syncEnter);
      field.addEventListener("change", syncEnter);
    });
    syncEnter();
  }

  if (form) {
    form.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      if (event.target && event.target.tagName === "TEXTAREA") return;
      if (event.target && event.target.closest("button")) return;
      event.preventDefault();
      if (allFilled() && enter && !enter.disabled) enter.click();
    });
    form.addEventListener("submit", (event) => {
      const submitter = event.submitter;
      if (submitter && submitter.value === "skip") return;
      if (submitter && submitter.value === "ok") return;
      if (!allFilled()) event.preventDefault();
    });
  }

  const first = football || enter;
  if (first) first.focus();
})();

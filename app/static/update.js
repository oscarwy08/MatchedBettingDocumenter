(() => {
  const bar = document.getElementById("update-bar");
  if (!bar) return;
  const skipKey = "mbd-skip-update";
  const pollMs = 60000;

  function resetButtons() {
    const nowBtn = bar.querySelector("[data-now]");
    const laterBtn = bar.querySelector("[data-later]");
    nowBtn.disabled = false;
    laterBtn.disabled = false;
    nowBtn.textContent = "Update now";
  }

  function hide() {
    bar.hidden = true;
    bar.classList.remove("is-on");
    resetButtons();
  }

  function show(latest) {
    if (localStorage.getItem(skipKey) === latest) return;
    bar.querySelector("[data-latest]").textContent = latest;
    bar.hidden = false;
    requestAnimationFrame(() => bar.classList.add("is-on"));
  }

  async function check() {
    try {
      const res = await fetch("/api/update-status", { headers: { Accept: "application/json" } });
      if (!res.ok) return;
      const data = await res.json();
      if (data.available) show(data.latest);
      else hide();
    } catch {
      /* local only; ignore */
    }
  }

  bar.querySelector("[data-later]").addEventListener("click", () => {
    const latest = bar.querySelector("[data-latest]").textContent.trim();
    if (latest) localStorage.setItem(skipKey, latest);
    hide();
  });

  bar.querySelector("[data-now]").addEventListener("click", async () => {
    const nowBtn = bar.querySelector("[data-now]");
    const laterBtn = bar.querySelector("[data-later]");
    nowBtn.disabled = true;
    laterBtn.disabled = true;
    nowBtn.textContent = "Updating…";
    const latest = bar.querySelector("[data-latest]").textContent.trim();
    try {
      const res = await fetch("/api/update-apply", {
        method: "POST",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify({ latest }),
      });
      const data = await res.json();
      if (!data.ok) {
        nowBtn.textContent = data.error || "Update failed";
        laterBtn.disabled = false;
        if (/already up to date/i.test(data.error || "")) {
          setTimeout(hide, 2500);
        }
        return;
      }
      nowBtn.textContent = "Restarting…";
      for (let i = 0; i < 90; i += 1) {
        await new Promise((resolve) => setTimeout(resolve, 500));
        try {
          const ping = await fetch("/api/update-status", { cache: "no-store" });
          if (ping.ok) {
            location.reload();
            return;
          }
        } catch {
          /* server still coming up */
        }
      }
      nowBtn.textContent = "Updated. Run Start again if the page does not load.";
    } catch {
      nowBtn.textContent = "Update failed";
      laterBtn.disabled = false;
    }
  });

  check();
  setInterval(check, pollMs);
})();

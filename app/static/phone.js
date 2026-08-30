(() => {
  const pop = document.getElementById("phone-pop");
  const openBtn = document.getElementById("phone-open");
  if (!pop || !openBtn) return;

  const close = () => {
    pop.hidden = true;
    openBtn.setAttribute("aria-expanded", "false");
  };
  const open = () => {
    pop.hidden = false;
    openBtn.setAttribute("aria-expanded", "true");
  };

  openBtn.addEventListener("click", open);
  pop.addEventListener("click", (event) => {
    if (event.target === pop) close();
  });
  pop.querySelectorAll("[data-phone-close]").forEach((btn) => btn.addEventListener("click", close));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !pop.hidden) close();
  });

  const copyBtn = pop.querySelector("[data-phone-copy]");
  const urlEl = pop.querySelector("[data-phone-url]");
  if (copyBtn && urlEl) {
    copyBtn.addEventListener("click", async () => {
      const text = (urlEl.getAttribute("href") || urlEl.textContent || "").trim();
      const label = copyBtn.textContent;
      const copied = () => {
        copyBtn.textContent = "Copied";
        setTimeout(() => { copyBtn.textContent = label; }, 1400);
      };
      try {
        await navigator.clipboard.writeText(text);
        copied();
        return;
      } catch {
        /* HTTP on the LAN often blocks clipboard; select the link instead. */
      }
      const range = document.createRange();
      range.selectNodeContents(urlEl);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      try {
        if (document.execCommand("copy")) copied();
        else copyBtn.textContent = "Select the link, then copy";
      } catch {
        copyBtn.textContent = "Select the link, then copy";
      }
    });
  }
})();

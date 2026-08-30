(() => {
  const bell = document.getElementById("notify-bell");
  const drop = document.getElementById("notify-drop");
  const countEl = document.getElementById("notify-count");
  const listEl = document.getElementById("notify-drop-list");
  const clearBtn = document.getElementById("notify-clear");
  const toast = document.getElementById("notify-toast");
  if (!bell || !drop || !countEl || !listEl || !toast) return;

  const pollMs = 5000;
  let lastSeen = Number(sessionStorage.getItem("mbd-notify-seen") || "0");
  let primed = sessionStorage.getItem("mbd-notify-primed") === "1";
  let hideTimer = 0;

  function setCount(n) {
    countEl.textContent = n > 99 ? "99+" : String(n);
    countEl.hidden = n < 1;
    bell.classList.toggle("has-unread", n > 0);
    if (clearBtn) clearBtn.hidden = n < 1;
  }

  function render(items) {
    const open = (items || []).filter((item) => item.unread).slice(0, 8);
    if (!open.length) {
      listEl.innerHTML = '<p class="empty">No notifications</p>';
      return;
    }
    listEl.innerHTML = open
      .map(
        (item) =>
          `<a class="notify-drop-item is-unread" href="${item.href}" data-notify-id="${item.id}">` +
          `<strong>${escapeHtml(item.title)}</strong>` +
          `<span>${escapeHtml(item.body)}</span></a>`
      )
      .join("");
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function hideToast() {
    toast.hidden = true;
    toast.classList.remove("is-on");
  }

  function showToast(item) {
    toast.querySelector("[data-title]").textContent = item.title;
    toast.querySelector("[data-body]").textContent = item.body;
    toast.dataset.href = item.href || "";
    toast.dataset.id = String(item.id || "");
    toast.hidden = false;
    requestAnimationFrame(() => toast.classList.add("is-on"));
    clearTimeout(hideTimer);
    hideTimer = window.setTimeout(hideToast, 5000);
  }

  async function markRead(id, allItems) {
    if (!id && !allItems) return;
    try {
      await fetch("/notifications/read", {
        method: "POST",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify(allItems ? { all: true } : { id: Number(id) }),
      });
    } catch {
      /* local only */
    }
  }

  async function check() {
    try {
      const res = await fetch("/api/notifications", { headers: { Accept: "application/json" } });
      if (!res.ok) return;
      const data = await res.json();
      setCount(data.unread || 0);
      render(data.items || []);
      const newest = data.latest_id || 0;
      if (!primed) {
        primed = true;
        lastSeen = newest;
        sessionStorage.setItem("mbd-notify-primed", "1");
        sessionStorage.setItem("mbd-notify-seen", String(lastSeen));
        return;
      }
      const fresh = (data.items || []).filter((item) => item.id > lastSeen).reverse();
      fresh.forEach(showToast);
      if (newest > lastSeen) {
        lastSeen = newest;
        sessionStorage.setItem("mbd-notify-seen", String(lastSeen));
      }
    } catch {
      /* local only */
    }
  }

  if (clearBtn) {
    clearBtn.addEventListener("click", async (event) => {
      event.stopPropagation();
      await markRead(null, true);
      await check();
    });
  }

  bell.addEventListener("click", () => {
    const open = drop.hidden;
    drop.hidden = !open;
    bell.setAttribute("aria-expanded", open ? "true" : "false");
  });

  document.addEventListener("click", (event) => {
    if (!drop.hidden && !event.target.closest(".notify-wrap")) {
      drop.hidden = true;
      bell.setAttribute("aria-expanded", "false");
    }
  });

  document.addEventListener("click", (event) => {
    const link = event.target.closest("[data-notify-id]");
    if (!link) return;
    markRead(link.getAttribute("data-notify-id"));
  });

  toast.addEventListener("click", () => {
    const href = toast.dataset.href;
    markRead(toast.dataset.id);
    hideToast();
    if (href) window.location.href = href;
  });

  check();
  setInterval(check, pollMs);
})();

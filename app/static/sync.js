(function () {
  const SKIP = ["/sync", "/friends", "/settings", "/api/"];

  function shouldWatch(form) {
    if (!form || (form.method || "get").toLowerCase() !== "post") return false;
    const action = form.getAttribute("action") || location.pathname;
    let path = action;
    try {
      path = new URL(action, location.origin).pathname;
    } catch (e) {}
    return !SKIP.some((prefix) => path.startsWith(prefix));
  }

  function message(state) {
    const name = state.peer_name || "the other computer";
    const localBets = (state.local && state.local.bets) || 0;
    const remoteBets = (state.remote && state.remote.bets) || 0;
    if (state.shrink) {
      return `Replace ${localBets} bets with ${remoteBets} from ${name}? Fetch the latest log first, or save this copy anyway.`;
    }
    if (state.action === "conflict") {
      return `This computer and ${name} both changed. Fetch the latest log first, or save this copy anyway.`;
    }
    return `${name} has a newer log. Fetch it first, or save this copy anyway.`;
  }

  function showModal(text, onFetch, onSave, onCancel) {
    let overlay = document.getElementById("sync-stale");
    if (!overlay) {
      overlay = document.createElement("aside");
      overlay.id = "sync-stale";
      overlay.className = "sync-stale";
      overlay.innerHTML =
        '<div class="sync-stale-card" role="dialog" aria-labelledby="sync-stale-title">' +
        '<p class="update-pop-kicker">Out of date</p>' +
        '<h2 id="sync-stale-title">Another computer has a different log</h2>' +
        '<p class="sync-stale-copy"></p>' +
        '<div class="update-pop-actions">' +
        '<button type="button" class="btn btn-primary" data-fetch>Fetch latest</button>' +
        '<button type="button" class="btn" data-save>Save this copy anyway</button>' +
        '<button type="button" class="btn" data-cancel>Cancel</button>' +
        "</div></div>";
      document.body.appendChild(overlay);
    }
    overlay.querySelector(".sync-stale-copy").textContent = text;
    overlay.hidden = false;
    const fetchBtn = overlay.querySelector("[data-fetch]");
    const saveBtn = overlay.querySelector("[data-save]");
    const cancelBtn = overlay.querySelector("[data-cancel]");
    const done = () => {
      overlay.hidden = true;
    };
    fetchBtn.onclick = () => {
      done();
      onFetch();
    };
    saveBtn.onclick = () => {
      done();
      onSave();
    };
    cancelBtn.onclick = () => {
      done();
      onCancel();
    };
  }

  async function freshness() {
    const res = await fetch("/api/sync/freshness", { headers: { Accept: "application/json" } });
    if (!res.ok) return { needs_confirm: false };
    return res.json();
  }

  document.addEventListener("submit", async (event) => {
    const form = event.target;
    if (!shouldWatch(form) || form.dataset.syncOk === "1") return;
    event.preventDefault();
    let state;
    try {
      state = await freshness();
    } catch (e) {
      form.dataset.syncOk = "1";
      form.submit();
      return;
    }
    if (!state.needs_confirm) {
      form.dataset.syncOk = "1";
      form.submit();
      return;
    }
    showModal(
      message(state),
      async () => {
        try {
          const res = await fetch("/api/sync/pull", {
            method: "POST",
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: JSON.stringify({ peer_id: state.peer_id, force: true }),
          });
          const body = await res.json();
          if (!res.ok) throw new Error(body.error || "Pull failed");
        } catch (err) {
          alert(err.message || "Could not fetch the other computer.");
          return;
        }
        try {
          sessionStorage.setItem("mbd-restore-form", JSON.stringify({ action: form.action, values: Object.fromEntries(new FormData(form)) }));
        } catch (e) {}
        location.reload();
      },
      () => {
        let extra = form.querySelector('input[name="sync_force"]');
        if (!extra) {
          extra = document.createElement("input");
          extra.type = "hidden";
          extra.name = "sync_force";
          form.appendChild(extra);
        }
        extra.value = "1";
        form.dataset.syncOk = "1";
        form.submit();
      },
      () => {}
    );
  });
})();

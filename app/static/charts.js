(() => {
  const gbp = (value) => {
    const amount = Number(value) || 0;
    const sign = amount < 0 ? "−" : "";
    return `${sign}£${Math.abs(amount).toLocaleString("en-GB", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })}`;
  };

  const css = (name, fallback) =>
    getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;

  const colours = () => ({
    profit: css("--profit", "#2f7a45"),
    loss: css("--loss", "#b42318"),
    muted: css("--muted", "#6f685c"),
    line: css("--line", "#d9d0c0"),
    ink: css("--ink", "#1c1913"),
    gold: css("--gold", "#c4922a"),
    card: css("--card", "#fffdf8"),
  });

  const formatValue = (value, unit) => (unit === "bets" ? String(Math.round(Number(value) || 0)) : gbp(value));

  function axisMoney(value, unit) {
    if (unit === "bets") return String(Math.round(Number(value) || 0));
    const amount = Number(value) || 0;
    const sign = amount < 0 ? "−" : "";
    const abs = Math.abs(amount);
    if (abs >= 100) return `${sign}£${Math.round(abs).toLocaleString("en-GB")}`;
    if (abs >= 10) return `${sign}£${abs.toFixed(0)}`;
    return `${sign}£${abs.toFixed(2)}`;
  }

  function niceTicks(min, max, count = 4) {
    const lo = Math.min(min, 0);
    const hi = Math.max(max, 0);
    if (lo === hi) return [0];
    const raw = (hi - lo) / count;
    const pow = Math.pow(10, Math.floor(Math.log10(raw) || 0));
    const nice = [1, 2, 2.5, 5, 10].map((n) => n * pow).find((n) => n >= raw) || raw;
    const start = Math.floor(lo / nice) * nice;
    const end = Math.ceil(hi / nice) * nice;
    const ticks = [];
    for (let value = start; value <= end + nice * 0.001; value += nice) {
      ticks.push(value);
    }
    return ticks;
  }

  function setPnlClass(el, value) {
    el.classList.remove("pnl-pos", "pnl-neg", "pnl-zero");
    const amount = Number(value) || 0;
    el.classList.add(amount > 0 ? "pnl-pos" : amount < 0 ? "pnl-neg" : "pnl-zero");
  }

  function labelStep(count) {
    if (count <= 8) return 1;
    return Math.ceil(count / 7);
  }

  function ensureTip(plot) {
    plot.classList.add("is-hoverable");
    let tip = plot.querySelector(".chart-tip");
    if (!tip) {
      tip = document.createElement("div");
      tip.className = "chart-tip";
      tip.hidden = true;
      plot.appendChild(tip);
    }
    return tip;
  }

  function placeTip(plot, tip, clientX, clientY) {
    const rect = plot.getBoundingClientRect();
    const left = Math.min(rect.width - 12, Math.max(12, clientX - rect.left + 14));
    const top = Math.min(rect.height - 12, Math.max(12, clientY - rect.top - 36));
    tip.style.left = `${left}px`;
    tip.style.top = `${top}px`;
  }

  function drawArea(plot, payload) {
    const values = payload.values || [];
    const labels = payload.labels || [];
    const width = plot.clientWidth || 640;
    const height = 240;
    const pad = { top: 16, right: 16, bottom: 30, left: 52 };
    const innerW = Math.max(40, width - pad.left - pad.right);
    const innerH = height - pad.top - pad.bottom;
    const ticks = niceTicks(Math.min(0, ...values), Math.max(0, ...values));
    const min = ticks[0];
    const max = ticks[ticks.length - 1];
    const span = max - min || 1;
    const xAt = (i) => {
      if (values.length <= 1) return pad.left + innerW / 2;
      return pad.left + (i / (values.length - 1)) * innerW;
    };
    const yAt = (v) => pad.top + (1 - (v - min) / span) * innerH;
    const tone = (values[values.length - 1] || 0) < 0 ? colours().loss : colours().profit;
    const points = values.map((v, i) => `${xAt(i).toFixed(1)},${yAt(v).toFixed(1)}`);
    const zeroY = yAt(0);
    const area = values.length
      ? `M ${xAt(0).toFixed(1)} ${zeroY.toFixed(1)} L ${points.join(" L ")} L ${xAt(values.length - 1).toFixed(1)} ${zeroY.toFixed(1)} Z`
      : "";
    const step = labelStep(labels.length);
    const xTicks = labels
      .map((label, i) => {
        if (i !== 0 && i !== labels.length - 1 && i % step !== 0) return "";
        return `<text class="chart-axis" x="${xAt(i).toFixed(1)}" y="${height - 8}" text-anchor="middle">${escapeXml(label)}</text>`;
      })
      .join("");
    const yGrid = ticks
      .map((tick) => {
        const y = yAt(tick);
        return `
          <line x1="${pad.left}" y1="${y.toFixed(1)}" x2="${width - pad.right}" y2="${y.toFixed(1)}" stroke="${colours().line}" stroke-dasharray="3 4"/>
          <text class="chart-axis chart-axis-y" x="${pad.left - 8}" y="${(y + 4).toFixed(1)}" text-anchor="end">${escapeXml(axisMoney(tick, payload.unit))}</text>
        `;
      })
      .join("");
    const id = `fill-${Math.random().toString(36).slice(2, 8)}`;
    plot.innerHTML = `
      <svg viewBox="0 0 ${width} ${height}" width="100%" height="${height}" role="img" aria-label="${escapeXml(payload.title || "Chart")}">
        <defs>
          <linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="${tone}" stop-opacity="0.35"/>
            <stop offset="100%" stop-color="${tone}" stop-opacity="0.02"/>
          </linearGradient>
        </defs>
        ${yGrid}
        ${area ? `<path d="${area}" fill="url(#${id})"/>` : ""}
        ${points.length ? `<polyline fill="none" stroke="${tone}" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round" points="${points.join(" ")}"/>` : ""}
        <line class="chart-hover-line" x1="0" y1="${pad.top}" x2="0" y2="${height - pad.bottom}" stroke="${colours().muted}" stroke-dasharray="3 3" style="display:none"/>
        <circle class="chart-hover-dot" r="4.5" fill="${tone}" stroke="${colours().card}" stroke-width="2" style="display:none"/>
        <g class="chart-axis">${xTicks}</g>
      </svg>`;
    const xs = values.map((_, i) => xAt(i));
    bindLineHover(plot, payload, xs, values.map((v) => yAt(v)));
  }

  function bindLineHover(plot, payload, xs, ys) {
    const tip = ensureTip(plot);
    const svg = plot.querySelector("svg");
    const line = plot.querySelector(".chart-hover-line");
    const dot = plot.querySelector(".chart-hover-dot");
    const hide = () => {
      tip.hidden = true;
      if (line) line.style.display = "none";
      if (dot) dot.style.display = "none";
    };
    if (!xs.length) {
      plot.onmousemove = null;
      plot.onmouseleave = null;
      hide();
      return;
    }
    plot.onmousemove = (event) => {
      const rect = svg.getBoundingClientRect();
      const x = ((event.clientX - rect.left) / rect.width) * svg.viewBox.baseVal.width;
      let best = 0;
      let dist = Infinity;
      xs.forEach((px, i) => {
        const gap = Math.abs(px - x);
        if (gap < dist) {
          dist = gap;
          best = i;
        }
      });
      const px = xs[best];
      const py = ys[best];
      line.style.display = "";
      dot.style.display = "";
      line.setAttribute("x1", px.toFixed(1));
      line.setAttribute("x2", px.toFixed(1));
      dot.setAttribute("cx", px.toFixed(1));
      dot.setAttribute("cy", py.toFixed(1));
      const label = (payload.labels || [])[best] || "";
      tip.hidden = false;
      tip.innerHTML = `<span>${escapeXml(label)}</span><strong>${escapeXml(formatValue(payload.values[best], payload.unit))}</strong>`;
      placeTip(plot, tip, event.clientX, event.clientY);
    };
    plot.onmouseleave = hide;
  }

  function drawBars(plot, payload) {
    const values = payload.values || [];
    const labels = payload.labels || [];
    const width = plot.clientWidth || 640;
    const rowH = 36;
    const height = Math.max(80, values.length * rowH + 28);
    const pad = { top: 20, right: 72, bottom: 8, left: 120 };
    const innerW = Math.max(40, width - pad.left - pad.right);
    const maxAbs = Math.max(1, ...values.map((v) => Math.abs(v)));
    const scaleTicks = niceTicks(-maxAbs, maxAbs, 4);
    const scale = Math.max(Math.abs(scaleTicks[0]), Math.abs(scaleTicks[scaleTicks.length - 1]), maxAbs);
    const zeroX = pad.left + innerW / 2;
    const xAt = (value) => zeroX + (value / scale) * (innerW / 2);
    const axis = scaleTicks
      .map((tick) => {
        const x = xAt(tick);
        return `
          <line x1="${x.toFixed(1)}" y1="${pad.top}" x2="${x.toFixed(1)}" y2="${height - pad.bottom}" stroke="${colours().line}" stroke-dasharray="3 4"/>
          <text class="chart-axis" x="${x.toFixed(1)}" y="14" text-anchor="middle">${escapeXml(axisMoney(tick, payload.unit))}</text>
        `;
      })
      .join("");
    const rows = values
      .map((value, i) => {
        const y = pad.top + i * rowH + 8;
        const mag = Math.abs(xAt(value) - zeroX);
        const x = value >= 0 ? zeroX : xAt(value);
        const fill = value < 0 ? colours().loss : colours().profit;
        const label = labels[i] || "—";
        const count = payload.counts ? ` · ${payload.counts[i]}` : "";
        return `
          <g class="chart-bar" data-i="${i}">
            <text class="chart-bar-label" x="${pad.left - 10}" y="${y + 14}" text-anchor="end">${escapeXml(trimLabel(label))}</text>
            <rect x="${x.toFixed(1)}" y="${y}" width="${Math.max(2, mag).toFixed(1)}" height="18" rx="4" fill="${fill}"/>
            <text class="chart-bar-value" x="${(value >= 0 ? x + mag + 8 : x - 8).toFixed(1)}" y="${y + 14}" text-anchor="${value >= 0 ? "start" : "end"}">${escapeXml(formatValue(value, payload.unit))}${count}</text>
          </g>
        `;
      })
      .join("");
    plot.innerHTML = `
      <svg viewBox="0 0 ${width} ${height}" width="100%" height="${height}" role="img" aria-label="${escapeXml(payload.title || "Chart")}">
        ${axis}
        <line x1="${zeroX.toFixed(1)}" y1="${pad.top}" x2="${zeroX.toFixed(1)}" y2="${height - pad.bottom}" stroke="${colours().line}"/>
        ${rows || ""}
      </svg>`;
    bindBarHover(plot, payload);
  }

  function bindBarHover(plot, payload) {
    const tip = ensureTip(plot);
    plot.querySelectorAll(".chart-bar").forEach((row) => {
      row.addEventListener("mousemove", (event) => {
        const i = Number(row.getAttribute("data-i"));
        const extra = payload.counts ? ` · ${payload.counts[i]}` : "";
        tip.hidden = false;
        tip.innerHTML = `<span>${escapeXml((payload.labels || [])[i] || "")}</span><strong>${escapeXml(formatValue(payload.values[i], payload.unit))}${extra}</strong>`;
        placeTip(plot, tip, event.clientX, event.clientY);
      });
      row.addEventListener("mouseleave", () => {
        tip.hidden = true;
      });
    });
    plot.onmousemove = null;
    plot.onmouseleave = () => {
      tip.hidden = true;
    };
  }

  function trimLabel(text) {
    const raw = String(text || "");
    return raw.length > 22 ? `${raw.slice(0, 20)}…` : raw;
  }

  function escapeXml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function render(plot, payload) {
    if (!plot) return;
    if (payload.kind === "bar") drawBars(plot, payload);
    else drawArea(plot, payload);
  }

  function applyHeadline(root, payload) {
    const total = root.querySelector("[data-chart-total]");
    const pending = root.querySelector("[data-chart-pending]");
    if (total) {
      total.textContent = formatValue(payload.total, payload.unit);
      setPnlClass(total, payload.unit === "bets" ? 0 : payload.total);
    }
    if (pending) pending.textContent = gbp(payload.pending);
  }

  function live(root, extra = {}) {
    if (!root) return;
    const plot = root.querySelector("[data-chart-plot]");
    const seed = root.querySelector("[data-chart-seed], script[type='application/json']");
    let current = { range: "1W", labels: [], values: [], total: 0, pending: 0 };
    try {
      current = JSON.parse(seed ? seed.textContent : "{}");
    } catch {
      /* keep default */
    }
    const draw = () => {
      applyHeadline(root, current);
      render(plot, current);
    };
    draw();
    root.querySelectorAll("[data-range]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const range = btn.getAttribute("data-range");
        root.querySelectorAll("[data-range]").forEach((el) => el.classList.toggle("on", el === btn));
        const params = new URLSearchParams({
          view: extra.view || current.view || "profit_time",
          range,
          ...extra,
        });
        try {
          const res = await fetch(`/api/charts?${params}`, { headers: { Accept: "application/json" } });
          if (!res.ok) return;
          current = await res.json();
          draw();
        } catch {
          /* local only */
        }
      });
    });
    window.addEventListener("resize", draw);
    document.addEventListener("mbd-theme", draw);
  }

  function dashboard() {
    live(document.getElementById("profit-chart"));
  }

  function account() {
    document.querySelectorAll("[data-account-chart]").forEach((root) => {
      live(root, {
        view: root.getAttribute("data-view") || "profit_time",
        account_id: root.getAttribute("data-account-id") || "",
      });
    });
  }

  function seeded() {
    document.querySelectorAll("[data-seeded-chart]").forEach((root) => {
      const plot = root.querySelector("[data-chart-plot]");
      const seed = root.querySelector("[data-chart-seed], script[type='application/json']");
      let payload = { labels: [], values: [], kind: "bar" };
      try {
        payload = JSON.parse(seed ? seed.textContent : "{}");
      } catch {
        /* keep empty */
      }
      const draw = () => {
        applyHeadline(root, payload);
        render(plot, payload);
      };
      draw();
      window.addEventListener("resize", draw);
      document.addEventListener("mbd-theme", draw);
    });
  }

  function visualiser() {
    const root = document.getElementById("visualiser");
    if (!root) return;
    const plot = document.querySelector("[data-chart-plot]");
    const title = document.querySelector("[data-vis-title]");
    const sub = document.querySelector("[data-vis-sub]");
    const empty = document.querySelector("[data-vis-empty]");
    const tableBody = document.querySelector("[data-vis-table] tbody");
    const form = root.querySelector("[data-vis-filters]");
    let view = "profit_time";
    let payload = { labels: [], values: [], total: 0, kind: "area" };

    const draw = () => {
      applyHeadline(document.querySelector(".vis-chart"), payload);
      if (title) title.textContent = payload.title || "Chart";
      if (sub) {
        const bits = [];
        if (payload.from && payload.to) bits.push(`${payload.from} – ${payload.to}`);
        if (payload.pending) bits.push(`${gbp(payload.pending)} potential`);
        sub.textContent = bits.join(" · ");
      }
      if (empty) empty.classList.toggle("is-hidden", !payload.empty);
      render(plot, payload);
      if (tableBody) {
        tableBody.innerHTML = (payload.labels || [])
          .map((label, i) => {
            const value = payload.values[i];
            const extra = payload.counts ? ` <span class="notes">(${payload.counts[i]})</span>` : "";
            const cls = payload.unit === "bets" ? "" : value > 0 ? "pnl-pos" : value < 0 ? "pnl-neg" : "pnl-zero";
            return `<tr><td>${escapeXml(label)}${extra}</td><td class="num ${cls}">${formatValue(value, payload.unit)}</td></tr>`;
          })
          .join("");
      }
    };

    const load = async () => {
      const data = new FormData(form);
      const params = new URLSearchParams();
      params.set("view", view);
      for (const [key, value] of data.entries()) {
        if (String(value).trim()) params.set(key, String(value).trim());
      }
      try {
        const res = await fetch(`/api/charts?${params}`, { headers: { Accept: "application/json" } });
        if (!res.ok) return;
        payload = await res.json();
        draw();
      } catch {
        /* local only */
      }
    };

    root.querySelectorAll("[data-view]").forEach((btn) => {
      btn.addEventListener("click", () => {
        view = btn.getAttribute("data-view");
        root.querySelectorAll("[data-view]").forEach((el) => el.classList.toggle("on", el === btn));
        load();
      });
    });
    form.addEventListener("change", load);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      load();
    });
    window.addEventListener("resize", draw);
    document.addEventListener("mbd-theme", draw);
    load();
  }

  window.MBDCharts = { dashboard, visualiser, account, seeded };
})();

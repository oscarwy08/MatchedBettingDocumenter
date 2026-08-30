(() => {
  const parse = (text) => {
    const raw = (text || "").trim();
    if (!raw || raw === "—" || raw === "–") return { kind: "empty", n: 0, s: "" };
    const uk = raw.match(/^(\d{2})\/(\d{2})\/(\d{4})(?:\s+(\d{2}):(\d{2}))?/);
    if (uk) {
      return {
        kind: "num",
        n: Date.parse(`${uk[3]}-${uk[2]}-${uk[1]}T${uk[4] || "00"}:${uk[5] || "00"}`),
        s: raw,
      };
    }
    const money = raw.replace(/[£,\s]/g, "").replace("−", "-");
    if (/^-?\d+(?:\.\d+)?$/.test(money)) return { kind: "num", n: Number(money), s: raw };
    return { kind: "text", n: 0, s: raw.toLowerCase() };
  };

  const valueOf = (cell) => parse(cell.getAttribute("data-sort") || cell.textContent);

  const compare = (a, b, dir) => {
    if (a.kind === "empty" && b.kind !== "empty") return 1;
    if (b.kind === "empty" && a.kind !== "empty") return -1;
    if (a.kind === "num" && b.kind === "num") return (a.n - b.n) * dir;
    return a.s.localeCompare(b.s, "en-GB") * dir;
  };

  document.querySelectorAll("table").forEach((table) => {
    if (table.classList.contains("outcome-table") || table.closest(".spreadsheet")) return;
    const head = table.querySelector("thead tr");
    const body = table.querySelector("tbody");
    if (!head || !body || head.querySelector(".th-sort")) return;
    [...head.children].forEach((th, index) => {
      if (!(th.textContent || "").trim()) return;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "th-sort";
      button.setAttribute("aria-label", `Sort by ${th.textContent.trim()}`);
      while (th.firstChild) button.appendChild(th.firstChild);
      const arrows = document.createElement("span");
      arrows.className = "th-arrows";
      arrows.setAttribute("aria-hidden", "true");
      button.appendChild(arrows);
      th.appendChild(button);
      button.addEventListener("click", () => {
        const next = th.dataset.sortDir === "desc" ? "asc" : "desc";
        [...head.children].forEach((other) => {
          delete other.dataset.sortDir;
        });
        th.dataset.sortDir = next;
        const dir = next === "asc" ? 1 : -1;
        const rows = [...body.rows];
        rows.sort((left, right) => compare(valueOf(left.cells[index] || left.cells[0]), valueOf(right.cells[index] || right.cells[0]), dir));
        rows.forEach((row) => body.appendChild(row));
      });
    });
  });
})();

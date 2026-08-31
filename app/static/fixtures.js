(function () {
  const root = document.querySelector("[data-event-picker]");
  if (!root) return;
  const input = root.querySelector("[name=event]");
  const list = root.querySelector("[data-event-suggest]");
  const source = root.querySelector("[name=fixture_source]");
  const fixtureId = root.querySelector("[name=fixture_id]");
  const starts = document.querySelector("[name=starts_at]");
  const ends = document.querySelector("[name=ends_at]");
  if (!input || !list || !source || !fixtureId) return;

  let picked = source.value && fixtureId.value ? input.value : "";
  let timer = 0;

  function clearLink() {
    source.value = "";
    fixtureId.value = "";
    picked = "";
  }

  function hide() {
    list.hidden = true;
    list.innerHTML = "";
  }

  function load(query) {
    fetch("/api/fixtures?q=" + encodeURIComponent(query || ""))
      .then(function (response) {
        return response.json();
      })
      .then(function (data) {
        render((data && data.items) || []);
      })
      .catch(function () {
        hide();
      });
  }

  function render(items) {
    list.innerHTML = "";
    if (!items.length) {
      hide();
      return;
    }
    items.forEach(function (item) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "event-suggest-item";
      const title = document.createElement("strong");
      title.textContent = item.label || "";
      const hint = document.createElement("small");
      hint.textContent = item.hint || "";
      button.appendChild(title);
      button.appendChild(hint);
      button.addEventListener("click", function () {
        input.value = item.label || "";
        if (starts) starts.value = item.starts_at || "";
        if (ends) ends.value = item.ends_at || "";
        source.value = item.source || "";
        fixtureId.value = item.fixture_id || "";
        picked = input.value;
        hide();
      });
      list.appendChild(button);
    });
    list.hidden = false;
  }

  input.addEventListener("input", function () {
    if (picked && input.value !== picked) clearLink();
    window.clearTimeout(timer);
    timer = window.setTimeout(function () {
      load(input.value.trim());
    }, 160);
  });

  input.addEventListener("focus", function () {
    load(input.value.trim());
  });

  document.addEventListener("click", function (event) {
    if (!root.contains(event.target)) hide();
  });
})();

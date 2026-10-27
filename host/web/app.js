// pvm-jig web panel: tabs and rows built from /api/items; values only while editing (BINT asserted).
// All unit conversions happen on the server: the page sends the text in the item's units.

const $ = (sel, el = document) => el.querySelector(sel);
const rows = {};       // key -> {item, el, input, state}
let held = false;
let queue = Promise.resolve();  // one request at a time, in order (fast taps on +/-)

async function api(path, body) {
  const opt = body === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)};
  const r = await fetch(path, opt);
  const d = await r.json().catch(() => ({error: r.statusText}));
  if (!r.ok) throw Object.assign(new Error(d.error || r.statusText), d);
  return d;
}

function serial(fn) {
  queue = queue.then(fn, fn);
  return queue;
}

let toastTimer;
function toast(text) {
  const t = $("#toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 5000);
}

// ---- build ----
async function load() {
  for (const key of Object.keys(rows)) delete rows[key];
  $("#tabs").innerHTML = "";
  $("#groups").innerHTML = "";
  build((await api("/api/items")).groups);
}

function build(groups) {
  const tabs = $("#tabs"), main = $("#groups"), tpl = $("#row");
  groups.forEach((g, n) => {
    const tab = document.createElement("button");
    tab.textContent = g.name.charAt(0).toUpperCase() + g.name.slice(1);
    const sec = document.createElement("section");
    sec.hidden = n > 0;
    tab.classList.toggle("active", n === 0);
    tab.onclick = () => {
      tabs.querySelectorAll("button").forEach(b => b.classList.toggle("active", b === tab));
      main.querySelectorAll("section").forEach(s => (s.hidden = s !== sec));
    };
    tabs.append(tab);
    for (const item of g.items) {
      const el = tpl.content.firstElementChild.cloneNode(true);
      const name = $(".name", el), info = $(".info", el), level = item.danger && item.danger.level;
      name.textContent = item.title + (level === "high" || level === "medium" ? " !" : "");
      if (level) name.classList.add(level);
      info.innerHTML = "";
      info.append(item.help || "(no description yet)");
      if (item.danger) {
        const risk = document.createElement("div");
        risk.className = "risk";
        risk.textContent = "\n" + item.danger.level.toUpperCase() + " RISK: " + item.danger.text;
        info.append(risk);
      }
      info.append((item.range ? "\nSafe range: " + item.range : "") + "\n" + item.where);
      name.onclick = () => (info.hidden = !info.hidden);
      const row = {item, el, input: $(".value", el), state: $(".state", el)};
      row.state.textContent = how(item.live, item.when);
      el.querySelectorAll(".step").forEach(b => (b.onclick = () => step(row, +b.dataset.d)));
      row.input.onchange = () => send(row, row.input.value.trim());
      row.input.onkeydown = e => {
        if (e.key === "Enter") row.input.blur();
        if (e.key === "ArrowUp" || e.key === "ArrowDown") {
          e.preventDefault();
          const hex = item.unit === "hex" || item.unit === "osd-hex";
          step(row, (e.key === "ArrowUp" ? 1 : -1) * (e.shiftKey ? (hex ? 16 : 10) : 1));
        }
      };
      rows[item.key] = row;
      sec.append(el);
    }
    main.append(sec);
  });
}

function how(live, when) {
  return live ? "live" : when ? "live if " + when : "on save (MCU reload)";
}

// ---- values ----
function fill(values) {
  for (const [key, v] of Object.entries(values)) {
    const row = rows[key];
    if (!row) continue;
    row.text = v.text;
    row.input.value = v.text;
    row.state.textContent = v.pending ? "changed" : how(v.live);
    row.state.classList.toggle("changed", v.pending);
  }
}

function step(row, d) {
  if (!held) return;
  const hex = row.item.unit === "hex" || row.item.unit === "osd-hex";
  const v = parseInt(row.input.value, hex ? 16 : 10);
  if (Number.isNaN(v)) return;
  const n = v + d;
  const text = hex ? (n < 0 ? "-" + (-n).toString(16) : n.toString(16)).toUpperCase() : String(n);
  row.input.value = text;
  send(row, text);
}

function send(row, text, force = false) {
  return serial(async () => {
    try {
      const r = await api("/api/set", {key: row.item.key, text, force});
      row.text = r.text;
      row.input.value = r.text;
      row.state.textContent = "changed" + (r.live ? "" : " (applied on save)");
      row.state.classList.add("changed");
      pending(r.pending);
    } catch (e) {
      if (e.unsafe && confirm(e.message + (e.danger ? "\n\n" + e.danger : "") + "\n\nWrite it anyway?")) {
        send(row, text, true);  // queued after this request; awaiting it here would wait on itself
        return;
      }
      row.input.value = row.text || "";
      if (!e.unsafe) toast(e.message);
    }
  });
}

// ---- session ----
function setHeld(on) {
  held = on;
  $("#edit").hidden = on;
  $("#save").hidden = !on;
  $("#revert").hidden = !on;
  $("#settings-open").disabled = on;
  for (const row of Object.values(rows)) {
    row.input.disabled = !on;
    row.el.querySelectorAll(".step").forEach(b => (b.disabled = !on));
    if (!on) {
      row.input.value = "";
      row.state.textContent = how(row.item.live, row.item.when);
      row.state.classList.remove("changed");
    }
  }
  $("#banner").hidden = !on;
  if (on) pending(0);
}

function pending(n) {
  $("#banner").textContent = "Editing: BINT asserted, the monitor's MCU is off the bus. "
    + (n ? n + " unsaved change(s). " : "") + "Save writes the EEPROM and reloads (short blank); Revert drops the changes.";
  $("#save").textContent = n ? "Save (" + n + ")" : "Save";
}

function action(path, busy) {
  return serial(async () => {
    const buttons = ["#edit", "#save", "#revert"].map(s => $(s));
    buttons.forEach(b => (b.disabled = true));
    toast(busy);
    try {
      const r = await api(path, {});
      if (path === "/api/hold") {
        setHeld(true);
        fill(r.values);
        $("#note").hidden = true;
        toast("Editing. Context: " + Object.entries(r.context || {}).map(kv => kv.join(" ")).join(", "));
      } else {
        setHeld(false);
        toast(path === "/api/save" ? "Saved " + r.saved + " item(s); the monitor reloads." : "Reverted.");
      }
    } catch (e) {
      toast(e.message);
    } finally {
      buttons.forEach(b => (b.disabled = false));
    }
  });
}

// ---- settings: serial port (auto or fixed) and model ----
let config = null;

async function fillSettings() {
  config = await api("/api/config");
  const port = $("#port");
  port.innerHTML = "";
  port.append(new Option("Auto (" + (config.detected || "no USB serial port found") + ")", ""));
  for (const p of config.ports) {
    const desc = p.description && p.description !== "n/a" ? " · " + p.description : "";
    port.append(new Option(p.device + desc, p.device));
  }
  if (config.port && !config.ports.some(p => p.device === config.port)) {
    port.append(new Option(config.port + " (not present)", config.port));
  }
  port.value = config.port;
  $("#port-info").textContent = "Auto picks the first USB serial port each time it connects, "
    + "so it follows the Nano when it comes back under another name.";
  const model = $("#model-select");
  model.innerHTML = "";
  for (const m of config.models) model.append(new Option(m.name + " (" + m.id + ")", m.id));
  if (!config.models.some(m => m.id === config.model)) model.append(new Option(config.model, config.model));
  model.value = config.model;
  $("#watcher").checked = config.watcher;
  $("#auto-apply").checked = config.auto_apply;
  $("#delay").value = config.delay;
}

$("#settings-open").onclick = async () => {
  try {
    await fillSettings();
    $("#settings").showModal();
  } catch (e) {
    toast(e.message);
  }
};
$("#rescan").onclick = () => fillSettings().catch(e => toast(e.message));
$("#apply").onclick = () => serial(async () => {
  const body = {port: $("#port").value, model: $("#model-select").value, watcher: $("#watcher").checked,
    auto_apply: $("#auto-apply").checked, delay: parseFloat($("#delay").value) || 1.5};
  try {
    const before = config.model;
    await api("/api/config", body);
    $("#settings").close();
    if (body.model !== before) await load();
    toast("Settings saved.");
    if (profView) loadProfiles();
  } catch (e) {
    toast(e.message);
  }
  poll();
});

// ---- profiles: baseline, presets and one per core, managed apart from Edit ----
// A card is named by a ref {kind: "baseline" | "preset" | "core", name}.
let profView = false, prof = null;
const profOpen = new Set();  // ids of the expanded cards, kept across re-renders
const refId = ref => ref.kind + ":" + ref.name;
const refBody = ref => (ref.kind === "preset" ? {preset: ref.name} : {core: ref.kind === "core" ? ref.name : ""});

function showProfiles(on) {
  profView = on;
  $("#groups").hidden = on;
  $("#tabs").hidden = on;
  $("#profiles-view").hidden = !on;
  $("#profiles-open").textContent = on ? "Panel" : "Profiles";
  if (on) loadProfiles();
}

async function loadProfiles() {
  try {
    prof = await api("/api/profiles");
    renderProfiles();
  } catch (e) {
    toast(e.message);
  }
}

function renderProfiles() {
  renderHead();
  const list = $("#prof-list");
  list.innerHTML = "";
  list.append(card({kind: "baseline", name: ""}, prof.baseline));
  for (const [name, vals] of Object.entries(prof.presets)) list.append(card({kind: "preset", name}, vals));
  for (const [name, vals] of Object.entries(prof.cores)) list.append(card({kind: "core", name}, vals));
}

function renderHead() {
  const running = prof.core && prof.core !== "MENU" ? prof.core : "";
  $("#prof-status").textContent = "Running: " + (running || "menu")
    + " · uses: " + (prof.active ? "baseline + " + prof.active : "baseline")
    + " · watcher " + (prof.watcher ? "on" : "off") + ", auto-apply " + (prof.auto_apply ? "on" : "off");
  $("#prof-last").textContent = prof.last_apply ? "Last: " + prof.last_apply : "";
  $("#cap-core").textContent = "Copy monitor → " + (running || "core");
  $("#cap-core").disabled = !running || held;
  $("#cap-base").disabled = held;
  $("#cap-preset").disabled = held;
  document.querySelectorAll(".apply, .copy-here").forEach(b => (b.disabled = held));
  $("#prof-editing").hidden = !held;
}

function button(text, cls, onclick) {
  const b = document.createElement("button");
  b.textContent = text;
  if (cls) b.className = cls;
  b.onclick = e => {
    e.preventDefault();  // inside <summary>: don't toggle the card
    onclick();
  };
  return b;
}

function card(ref, vals) {
  const base = ref.kind === "baseline", label = base ? "Baseline" : ref.name;
  const running = ref.kind === "core" && ref.name === prof.active;
  const d = document.createElement("details");
  d.className = "prof";
  d.open = profOpen.has(refId(ref)) || running;
  d.ontoggle = () => (d.open ? profOpen.add(refId(ref)) : profOpen.delete(refId(ref)));
  const sum = document.createElement("summary");
  sum.append(Object.assign(document.createElement("span"), {className: "prof-name", textContent: label}));
  if (ref.kind === "preset") sum.append(Object.assign(document.createElement("span"), {className: "tag", textContent: "preset"}));
  if (running) sum.append(Object.assign(document.createElement("span"), {className: "badge", textContent: "running"}));
  const keys = Object.keys(vals);
  const differ = base ? keys : keys.filter(k => !prof.baseline[k] || prof.baseline[k].raw !== vals[k].raw);
  const count = document.createElement("span");
  count.className = "muted";
  count.textContent = base ? keys.length + " items" : keys.length + " items, " + differ.length + " differ";
  sum.append(count, Object.assign(document.createElement("span"), {className: "spacer"}));
  if (ref.kind === "preset") {
    const copy = button("Copy monitor → here", "copy-here", () => {
      if (confirm("Replace the preset " + ref.name + " with the current settings?")) {
        profCall("/api/profiles/capture", {target: "preset", name: ref.name});
      }
    });
    copy.disabled = held;
    sum.append(copy);
  }
  const apply = button("Apply → monitor", "apply", () => profCall("/api/profiles/apply", refBody(ref)));
  apply.disabled = held;
  apply.title = base ? "Write the baseline alone to the monitor now"
    : "Write the baseline + " + label + " to the monitor now (until the next core change)";
  sum.append(apply);
  if (!base) {
    sum.append(button("Delete", "", () => {
      if (confirm("Delete " + (ref.kind === "preset" ? "the preset " : "the profile for ") + label + "?")) {
        profCall("/api/profiles/delete", refBody(ref), "Deleted " + label + ".");
      }
    }));
  }
  d.append(sum);
  const body = document.createElement("div");
  body.className = "prof-body";
  const show = !base && $("#only-diff").checked ? differ : keys;
  for (const item of prof.items) {
    if (show.includes(item.key)) body.append(profRow(ref, item, vals[item.key]));
  }
  if (!body.children.length) {
    body.append(Object.assign(document.createElement("p"), {className: "prof-empty",
      textContent: keys.length ? "Same as the baseline." : "Empty: use “Copy monitor → baseline”."}));
  }
  d.append(body);
  return d;
}

function profRow(ref, item, v) {
  const row = document.createElement("div");
  row.className = "prow";
  const label = document.createElement("span");
  const level = item.danger && item.danger.level;
  label.className = "label" + (level ? " " + level : "");
  label.textContent = item.title + (level === "high" || level === "medium" ? " !" : "");
  label.title = item.help + (item.range ? "\nSafe range: " + item.range : "");
  const input = document.createElement("input");
  input.className = "value";
  input.value = v.text;
  input.onchange = () => profSet(ref, item, input, input.value.trim(), v);
  input.onkeydown = e => e.key === "Enter" && input.blur();
  const x = button("×", "x", () => profCall("/api/profiles/remove", {...refBody(ref), key: item.key}));
  x.title = ref.kind === "baseline" ? "Remove from the baseline" : "Remove (use the baseline value)";
  row.append(label, input, x);
  return row;
}

function profSet(ref, item, input, text, old, force = false) {
  serial(async () => {
    try {
      const r = await api("/api/profiles/set", {...refBody(ref), key: item.key, text, force});
      old.text = r.text;
      old.raw = r.raw;
      input.value = r.text;
    } catch (e) {
      if (e.unsafe && confirm(e.message + (e.danger ? "\n\n" + e.danger : "") + "\n\nStore it anyway?")) {
        profSet(ref, item, input, text, old, true);
        return;
      }
      input.value = old.text;
      if (!e.unsafe) toast(e.message);
    }
  });
}

function result(text) {
  const p = $("#prof-result");
  p.textContent = text;
  p.hidden = !text;
}

function profCall(path, body, done) {
  return serial(async () => {
    try {
      const r = await api(path, body);
      if (path === "/api/profiles/capture") {
        result(r.profile + ": " + (r.new ? "created from the EEPROM (" + r.items + " items)."
          : r.changes.length ? r.changes.join(", ") : "no change from the previous values.")
          + (r.unsafe.length ? " Outside the safe range: " + r.unsafe.join(", ") + "." : ""));
        profOpen.add(refId({kind: r.kind, name: r.name}));
      } else if (r.result) result(r.result);
      else if (done) toast(done);
    } catch (e) {
      result("Not done: " + e.message);
    }
    await loadProfiles();
  });
}

$("#profiles-open").onclick = () => showProfiles(!profView);
$("#only-diff").onchange = renderProfiles;
$("#cap-core").onclick = () => profCall("/api/profiles/capture", {target: "core"});
$("#cap-base").onclick = () => {
  if (confirm("Replace the baseline with the current settings?")) {
    profCall("/api/profiles/capture", {target: "baseline"});
  }
};
$("#cap-preset").onclick = () => {
  const name = (prompt("Name of the new preset (the current settings are copied into it):") || "").trim();
  if (!name) return;
  if (prof.presets[name] && !confirm("The preset " + name + " exists. Replace it with the current settings?")) return;
  profCall("/api/profiles/capture", {target: "preset", name});
};

$("#edit").onclick = () => action("/api/hold", "Asserting BINT, waiting for jig mode…");
$("#save").onclick = () => action("/api/save", "Saving…");
$("#revert").onclick = () => action("/api/revert", "Reverting…");

// ---- status poll (only while the page is visible, so a hidden tab lets the idle timeout run) ----
async function poll() {
  if (document.visibilityState !== "visible") return;
  try {
    const s = await api("/api/status");
    $("#model").textContent = s.model;
    $("#core").textContent = s.core ? "· " + s.core : "";
    const dot = $("#dot");
    dot.className = "dot" + (!s.connected ? "" : s.held ? " edit" : s.pvm ? " on" : "");
    const where = s.port ? s.port + (s.port_setting === "auto" ? " (auto)" : "") : s.port_setting;
    dot.title = !s.connected ? "Nano not connected (" + s.port_setting + "): " + s.error
      : (s.held ? "editing" : s.pvm ? "PVM on" : "Nano connected, PVM off or not wired") + " · " + where;
    if (s.held !== held) {
      setHeld(s.held);
      if (s.held) fill((await api("/api/values")).values);
    }
    if (s.held) pending(s.pending);
    $("#note").textContent = s.note || "";
    $("#note").hidden = !s.note;
    if (profView && prof) {
      if (s.core !== prof.core || s.last_apply !== prof.last_apply) await loadProfiles();
      else renderHead();  // editing state may have changed; the list stays (values being typed)
    }
  } catch (e) {
    $("#dot").className = "dot";
    $("#dot").title = "server not reachable";
  }
}

document.addEventListener("visibilitychange", poll);

(async () => {
  try {
    await load();
  } catch (e) {
    toast("Can't load the items: " + e.message);
  }
  await poll();
  setInterval(poll, 3000);
})();

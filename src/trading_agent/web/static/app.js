"use strict";

/* ------------------------------------------------------------------ helpers */
const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const fmt = {
  money: (n, d = 2) =>
    n == null ? "–" : n.toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: d, maximumFractionDigits: d }),
  pct: (n, d = 1) => (n == null ? "–" : `${n > 0 ? "+" : ""}${n.toFixed(d)}%`),
  plain: (n, d = 1) => (n == null ? "–" : `${n.toFixed(d)}%`),
  day: (iso) => new Date(`${iso.slice(0, 10)}T00:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric" }),
  dayYear: (iso) => new Date(`${iso.slice(0, 10)}T00:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }),
  monthYear: (iso) => new Date(`${iso.slice(0, 10)}T00:00:00`).toLocaleDateString("en-US", { month: "short", year: "2-digit" }),
  time: (iso) => new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }),
};
const tone = (n) => (n > 0 ? "pos" : n < 0 ? "neg" : "");

const LABELS = {
  style: { momentum: "Momentum", swing: "Swing", mean_reversion: "Mean reversion", long_term: "Long-term" },
  risk: { conservative: "Conservative", moderate: "Moderate", aggressive: "Aggressive" },
  goal: { growth: "Growth", income: "Income", capital_preservation: "Capital preservation", speculation: "Speculation" },
  algo: { random_forest: "Random Forest", xgboost: "XGBoost" },
};

function keep(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    localStorage.setItem(key, value);
  } catch {
    return null;
  }
}

let toastTimer;
function toast(message, isError = false) {
  const el = $("#toast");
  el.textContent = message;
  el.className = `show${isError ? " error" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.className = ""), isError ? 6000 : 3200);
}

async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(`/api${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* empty body */
  }
  if (!res.ok) {
    const d = data?.detail;
    const msg = typeof d === "string" ? d : Array.isArray(d) ? d.map((e) => e.msg).join("; ") : res.statusText;
    throw new Error(msg || "Request failed");
  }
  return data;
}

async function run(button, label, task) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = label;
  try {
    return await task();
  } catch (err) {
    toast(err.message, true);
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

/* -------------------------------------------------------------------- state */
const state = { meta: null, profiles: [], profile: null, notes: [], chartSymbol: null, newProfile: false };

const universe = () => (state.profile?.preferred_assets?.length ? state.profile.preferred_assets : state.meta.default_symbols);

/* -------------------------------------------------------------------- charts */
function drawChart(el, series, opts = {}) {
  const W = 640, H = opts.height || 250, m = { l: 56, r: 12, t: 12, b: 26 };
  const base = series[0].points;
  const n = base.length;
  if (n < 2) {
    el.innerHTML = `<p class="muted">Not enough data to chart.</p>`;
    return;
  }
  const vals = series.flatMap((s) => s.points.map((p) => p.y)).filter((v) => v != null);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = (hi - lo) * 0.07 || 1;
  lo -= pad;
  hi += pad;
  const X = (i) => m.l + ((W - m.l - m.r) * i) / (n - 1);
  const Y = (v) => m.t + (H - m.t - m.b) * (1 - (v - lo) / (hi - lo));
  const f = opts.fmt || ((v) => v.toFixed(0));
  const long = (new Date(base[n - 1].x) - new Date(base[0].x)) / 864e5 > 240;
  const xl = (iso) => (long ? fmt.monthYear(iso) : fmt.day(iso));

  let grid = "";
  for (let k = 0; k <= 4; k++) {
    const v = lo + ((hi - lo) * k) / 4;
    grid += `<line x1="${m.l}" x2="${W - m.r}" y1="${Y(v)}" y2="${Y(v)}"/><text x="${m.l - 8}" y="${Y(v) + 4}" text-anchor="end">${esc(f(v))}</text>`;
  }
  let xt = "";
  for (let k = 0; k <= 4; k++) {
    const i = Math.round(((n - 1) * k) / 4);
    xt += `<text x="${X(i)}" y="${H - 6}" text-anchor="${k === 0 ? "start" : k === 4 ? "end" : "middle"}">${esc(xl(base[i].x))}</text>`;
  }
  const paths = series
    .map((s) => {
      let d = "", pen = false;
      s.points.forEach((p, i) => {
        if (p.y == null) { pen = false; return; }
        d += `${pen ? "L" : "M"}${X(i).toFixed(1)},${Y(p.y).toFixed(1)}`;
        pen = true;
      });
      return `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="${s.width || 1.8}" ${s.dash ? `stroke-dasharray="${s.dash}"` : ""} stroke-linejoin="round" stroke-linecap="round"/>`;
    })
    .join("");

  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(opts.label || "Line chart")}">
      <g class="grid">${grid}</g><g class="axis">${xt}</g>${paths}
      <line class="cross" x1="0" x2="0" y1="${m.t}" y2="${H - m.b}" stroke="#14202a" stroke-opacity=".35" visibility="hidden"/>
      <rect class="hit" x="${m.l}" y="${m.t}" width="${W - m.l - m.r}" height="${H - m.t - m.b}" fill="transparent"/>
    </svg><div class="chart-tip" hidden></div>`;

  const svg = $("svg", el), cross = $(".cross", el), tip = $(".chart-tip", el);
  const move = (e) => {
    const r = svg.getBoundingClientRect();
    const x = ((e.clientX - r.left) / r.width) * W;
    const i = Math.max(0, Math.min(n - 1, Math.round(((x - m.l) / (W - m.l - m.r)) * (n - 1))));
    cross.setAttribute("x1", X(i)); cross.setAttribute("x2", X(i)); cross.setAttribute("visibility", "visible");
    tip.hidden = false;
    tip.innerHTML = `<b>${esc(fmt.dayYear(base[i].x))}</b>` + series.map((s) => (s.points[i].y == null ? "" : `<br>${esc(s.name)} ${esc(f(s.points[i].y))}`)).join("");
    tip.style.left = `${(X(i) / W) * r.width}px`;
    tip.style.top = `${(Y(base[i].y ?? lo) / H) * r.height}px`;
  };
  $(".hit", el).addEventListener("mousemove", move);
  $(".hit", el).addEventListener("mouseleave", () => { cross.setAttribute("visibility", "hidden"); tip.hidden = true; });
}

/* ------------------------------------------------------------ desk (tickets) */
function ruler(r) {
  const band = state.meta.neutral_band * 100;
  const bar = state.profile.min_confidence_pct;
  const p = r.prob_up * 100;
  return `<div class="ruler" role="img" aria-label="The model estimates a ${p.toFixed(0)}% chance of a rise. Your confidence bar is ${bar}%.">
    <div class="ruler-track">
      <span class="zone sell" style="width:${50 - band}%"></span><span class="zone mid" style="width:${2 * band}%"></span><span class="zone buy" style="width:${50 - band}%"></span>
      <i class="tick" style="left:${100 - bar}%"></i><i class="tick" style="left:${bar}%"></i>
      <b class="marker" style="left:${p}%"><em>${p.toFixed(0)}%</em></b>
    </div>
    <div class="ruler-scale"><span>Falls</span><span>Ticks mark your ${bar}% bar</span><span>Rises</span></div>
  </div>`;
}

const checkItem = (c) =>
  `<li class="${c.passed ? "pass" : "fail"}"><span class="mk" aria-hidden="true">${c.passed ? "✓" : "✗"}</span><span class="sr">${c.passed ? "Passed" : "Failed"}: </span><span>${esc(c.name)}. ${esc(c.detail)}</span></li>`;

function ticket(r) {
  const kind = r.action === "BUY" ? "buy" : r.action === "SELL" ? "sell" : "";
  const verb = r.action === "BUY" ? "Buy" : "Sell";
  const stratOk = r.strategy_status === "Matched";
  const riskOk = r.risk_status === "Within limit";
  const decided = r.status === "approved" || r.status === "rejected";

  let footer = "";
  if (r.status === "pending") {
    footer = `<div class="t-actions">
      <input type="text" maxlength="500" placeholder="Add a note (optional)" aria-label="Note for ${esc(r.symbol)}">
      <button class="${kind}" data-act="approve" data-id="${r.id}">Approve paper trade</button>
      <button data-act="reject" data-id="${r.id}">Reject</button>
    </div>`;
  } else if (decided) {
    footer = `<div class="t-result"><span class="badge ${r.status === "approved" ? "buy" : "hold"}">${r.status === "approved" ? "Approved, paper trade filled" : "Rejected"}</span>
      ${r.comment ? `<span class="muted">“${esc(r.comment)}”</span>` : ""}</div>`;
  }

  return `<article class="ticket ${kind} ${decided ? "done" : ""}">
    <div class="t-head">
      <div class="t-line"><span class="t-sym">${esc(r.symbol)}</span>
        <span class="badge ${kind}">${verb} ${r.suggested_qty} shares</span>
        ${r.replay ? `<span class="badge hold">Replay of ${fmt.day(r.as_of)}</span>` : ""}</div>
      <div class="t-price num">${fmt.money(r.price)}<small>Close, ${fmt.day(r.as_of)}</small></div>
    </div>
    <p class="t-summary">${esc(r.summary)}</p>
    ${ruler(r)}
    <div class="verdicts">
      <div class="verdict"><span class="k">Signal</span><span class="v">${esc(r.signal)}</span></div>
      <div class="verdict"><span class="k">Confidence</span><span class="v num">${(r.confidence * 100).toFixed(0)}%</span></div>
      <div class="verdict"><span class="k">Strategy</span><span class="v ${stratOk ? "ok" : "bad"}">${esc(r.strategy_status)}</span></div>
      <div class="verdict"><span class="k">Risk</span><span class="v ${riskOk ? "ok" : "bad"}">${esc(r.risk_status)}${r.risk_label ? `, ${esc(r.risk_label.toLowerCase())}` : ""}</span></div>
      ${r.stop_loss_price ? `<div class="verdict"><span class="k">Stop-loss</span><span class="v num">${fmt.money(r.stop_loss_price)}</span></div>` : ""}
    </div>
    <details class="why"><summary>Why this call</summary>
      <ul>${r.reasons.map((t) => `<li><span class="mk"></span><span>${esc(t)}</span></li>`).join("")}</ul>
      <h3>Strategy rules (${esc(LABELS.style[state.profile.trading_style])})</h3><ul>${r.strategy_checks.map(checkItem).join("")}</ul>
      <h3>Risk limits</h3><ul>${r.risk_checks.map(checkItem).join("")}</ul>
    </details>
    ${footer}
  </article>`;
}

function holdRow(r) {
  return `<div class="row"><span class="sym">${esc(r.symbol)}</span><span class="why-short">${esc(r.summary)}</span><span class="p num">${(r.prob_up * 100).toFixed(0)}% rise</span></div>`;
}

async function viewDesk() {
  const p = state.profile;
  if (!p) return viewEmpty();
  const [recs, pf, notes] = await Promise.all([
    api(`/recommendations?profile_id=${p.id}&limit=80`),
    api(`/portfolio/${p.id}`),
    api(`/notifications?profile_id=${p.id}`),
  ]);
  state.notes = notes;
  updateBell();

  const seen = new Set();
  const latest = [];
  for (const r of recs) {
    if (r.status === "expired" || seen.has(r.symbol)) continue;
    seen.add(r.symbol);
    latest.push(r);
  }
  const pending = latest.filter((r) => r.status === "pending");
  const decided = latest.filter((r) => r.status === "approved" || r.status === "rejected");
  const holds = latest.filter((r) => r.status === "info");
  const syms = universe();
  if (!state.chartSymbol || !syms.includes(state.chartSymbol)) state.chartSymbol = syms[0];

  const figures = `<div class="figures">
      <div class="figure"><div class="label">Portfolio value</div><div class="value num">${fmt.money(pf.equity, 0)}</div><div class="sub">Started at ${fmt.money(pf.starting_cash, 0)}</div></div>
      <div class="figure"><div class="label">Total return</div><div class="value num ${tone(pf.total_return_pct)}">${fmt.pct(pf.total_return_pct, 2)}</div><div class="sub">${fmt.money(pf.total_pnl, 0)}</div></div>
      <div class="figure"><div class="label">Cash</div><div class="value num">${fmt.money(pf.cash, 0)}</div><div class="sub">${fmt.plain((pf.cash / pf.equity) * 100, 0)} of portfolio</div></div>
      <div class="figure"><div class="label">Below peak</div><div class="value num">${fmt.plain(pf.drawdown_pct)}</div><div class="sub">Your limit is ${p.max_drawdown_pct}%</div></div>
      <div class="figure"><div class="label">Awaiting your decision</div><div class="value num">${pending.length}</div><div class="sub">${holds.length} on hold</div></div>
    </div>`;

  const left = latest.length
    ? `${pending.length ? `<div class="section-title"><h2>Needs your decision</h2></div>${pending.map(ticket).join("")}` : `<div class="empty"><h2>Nothing needs your decision</h2><p>No recommendation clears your strategy and risk limits right now.</p></div>`}
       ${decided.length ? `<div class="section-title"><h2>Decided</h2></div>${decided.map(ticket).join("")}` : ""}
       ${holds.length ? `<div class="section-title"><h2>On hold</h2><span class="muted">No action suggested</span></div><div class="rows">${holds.map(holdRow).join("")}</div>` : ""}`
    : `<div class="empty"><h2>No recommendations yet</h2><p>Analyse ${syms.length} symbols against your profile to see what the agents suggest.</p><button class="primary" id="emptyGenerate">Generate recommendations</button></div>`;

  const positions = pf.positions.length
    ? `<table><thead><tr><th>Symbol</th><th class="r">Shares</th><th class="r">Price</th><th class="r">P&amp;L</th></tr></thead><tbody>${pf.positions
        .map((x) => `<tr><td><b>${esc(x.symbol)}</b></td><td class="r num">${x.qty}</td><td class="r num">${fmt.money(x.price)}</td><td class="r num ${tone(x.pnl)}">${fmt.pct(x.pnl_pct)}</td></tr>`)
        .join("")}</tbody></table>`
    : `<p class="muted">No open positions. Approve a recommendation to open one.</p>`;

  const unread = notes.filter((n) => !n.is_read).length;
  const alerts = notes.length
    ? `<ul class="alerts">${notes.slice(0, 5).map((n) => `<li class="${n.is_read ? "" : "unread"}"><div class="msg">${esc(n.message)}</div><div class="when">${fmt.time(n.created_at)}</div></li>`).join("")}</ul>${unread ? `<button class="ghost" id="readAll" style="margin-top:8px">Mark all read</button>` : ""}`
    : `<p class="muted">Alerts appear here when a recommendation matches your preferences.</p>`;

  $("#view").innerHTML = `
    <div class="page-head">
      <div><h1>Desk</h1><p>Review what the agents found, then approve or reject. Nothing happens without you.</p></div>
      <button class="primary" id="generate">Generate recommendations</button>
    </div>
    ${figures}
    <div class="desk">
      <section class="stack" aria-label="Recommendations">${left}</section>
      <aside class="stack">
        <div class="panel"><h2>Price</h2>
          <div class="chart-tabs">${syms.map((s) => `<button aria-pressed="${s === state.chartSymbol}" data-sym="${esc(s)}">${esc(s)}</button>`).join("")}</div>
          <div class="chart" id="chart"><p class="muted">Loading…</p></div>
          <div class="legend"><span><i style="background:#14202a"></i>Close</span><span><i style="background:#1d4f7c"></i>20-day average</span><span><i style="background:#8a97a3"></i>50-day average</span></div>
        </div>
        <div class="panel"><h2>Positions</h2>${positions}</div>
        <div class="panel" id="alertsPanel"><h2>Alerts</h2>${alerts}</div>
      </aside>
    </div>`;

  bindDesk();
  loadChart(state.chartSymbol);
}

async function loadChart(symbol) {
  const el = $("#chart");
  if (!el) return;
  try {
    const d = await api(`/market/${encodeURIComponent(symbol)}?days=180`);
    if (state.chartSymbol !== symbol) return;
    const pts = (key) => d.bars.map((b) => ({ x: b.date, y: b[key] }));
    drawChart(el, [
      { name: "Close", color: "#14202a", points: pts("close") },
      { name: "20-day", color: "#1d4f7c", width: 1.4, points: pts("sma20") },
      { name: "50-day", color: "#8a97a3", width: 1.4, dash: "5 4", points: pts("sma50") },
    ], { fmt: (v) => fmt.money(v, 0), label: `${symbol} price, last 180 trading days` });
  } catch (err) {
    el.innerHTML = `<p class="muted">${esc(err.message)}</p>`;
  }
}

function bindDesk() {
  const gen = async (btn) => {
    const out = await run(btn, "Analysing…", () => api("/recommendations/generate", { method: "POST", body: { profile_id: state.profile.id } }));
    if (!out) return;
    toast(out.errors.length ? `Done. Skipped: ${out.errors.join("; ")}` : `Analysed ${out.recommendations.length} symbols`, out.errors.length > 0);
    render();
  };
  $("#generate")?.addEventListener("click", (e) => gen(e.currentTarget));
  $("#emptyGenerate")?.addEventListener("click", (e) => gen(e.currentTarget));
  $("#readAll")?.addEventListener("click", async () => {
    await api(`/notifications/read?profile_id=${state.profile.id}`, { method: "POST" });
    render();
  });
  $$(".chart-tabs button").forEach((b) =>
    b.addEventListener("click", () => {
      state.chartSymbol = b.dataset.sym;
      $$(".chart-tabs button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      loadChart(state.chartSymbol);
    }));
  $$("[data-act]").forEach((b) =>
    b.addEventListener("click", async () => {
      const comment = $("input", b.closest(".t-actions")).value.trim() || null;
      const act = b.dataset.act;
      const out = await run(b, act === "approve" ? "Filling…" : "Saving…", () =>
        api(`/recommendations/${b.dataset.id}/${act}`, { method: "POST", body: { comment } }));
      if (out) {
        toast(act === "approve" ? `Paper trade filled for ${out.symbol}` : `Rejected ${out.symbol}`);
        render();
      }
    }));
}

/* ------------------------------------------------------------------ profile */
function viewEmpty() {
  $("#view").innerHTML = `<div class="page-head"><div><h1>Welcome</h1></div></div>
    <div class="empty"><h2>Set up your investor profile</h2>
      <p>The agents compare every market signal with your risk limits, trading style and goals. Create a profile to get recommendations.</p>
      <button class="primary" id="startProfile">Create profile</button></div>`;
  $("#startProfile").addEventListener("click", () => { state.newProfile = true; location.hash = "#/profile"; });
}

function limitField(id, label, value, unit, hint, step = "0.5", min = "0") {
  return `<div class="field"><label for="${id}">${label}</label><div class="suffix"><input type="number" id="${id}" value="${value ?? ""}" step="${step}" min="${min}" required><span>${unit}</span></div><div class="hint">${hint}</div></div>`;
}

function viewProfile() {
  const blank = { name: "", risk_level: "moderate", trading_style: "swing", goals: ["growth"], preferred_assets: [], notify_enabled: true, ...state.meta.presets.moderate };
  const p = state.newProfile || !state.profile ? blank : state.profile;
  const creating = state.newProfile || !state.profile;
  const opt = (map, cur) => Object.entries(map).map(([k, v]) => `<option value="${k}" ${k === cur ? "selected" : ""}>${v}</option>`).join("");

  $("#view").innerHTML = `
    <div class="page-head"><div><h1>${creating ? "New investor profile" : "Profile"}</h1><p>These settings decide which signals reach you and how large a trade can be.</p></div>
      ${creating ? "" : `<button id="newProfile">Add another investor</button>`}</div>
    <form class="form" id="profileForm" novalidate>
      <div class="field"><label for="f_name">Name</label><input type="text" id="f_name" value="${esc(p.name)}" maxlength="60" required></div>
      <div class="field"><label for="f_assets">Preferred assets</label><input type="text" id="f_assets" value="${esc(p.preferred_assets.join(", "))}" placeholder="${esc(state.meta.default_symbols.join(", "))}">
        <div class="hint">Comma-separated tickers. Leave empty to watch the defaults.</div></div>
      <div class="field"><label for="f_risk">Risk level</label><select id="f_risk">${opt(LABELS.risk, p.risk_level)}</select>
        <div class="hint">Picking a level fills the limits below. Change any limit to override it.</div></div>
      <div class="field"><label for="f_style">Trading style</label><select id="f_style">${opt(LABELS.style, p.trading_style)}</select>
        <div class="hint">Momentum follows trends, swing buys pullbacks, mean reversion buys dips, long-term ignores short swings.</div></div>
      <div class="field full"><span class="lbl">Goals</span><div class="checks">${Object.entries(LABELS.goal).map(([k, v]) => `<label><input type="checkbox" name="goal" value="${k}" ${p.goals.includes(k) ? "checked" : ""}>${v}</label>`).join("")}</div></div>
      <fieldset><legend>Risk limits</legend>
        ${limitField("f_minconf", "Minimum confidence", p.min_confidence_pct, "%", "Signals below this stay on hold.", "1", "50")}
        ${limitField("f_maxpos", "Largest position", p.max_position_pct, "%", "Share of portfolio in one stock.")}
        ${limitField("f_stop", "Stop-loss", p.stop_loss_pct, "%", "Used to size trades and set exit prices.")}
        ${limitField("f_rpt", "Risk per trade", p.risk_per_trade_pct, "%", "Most you accept losing if the stop is hit.")}
        ${limitField("f_vol", "Highest volatility", p.max_volatility_pct, "%", "Annualised. Blocks buys in wilder stocks.", "1")}
        ${limitField("f_dd", "Portfolio drawdown", p.max_drawdown_pct, "%", "Stops new buys when the portfolio falls this far below its peak.", "1")}
      </fieldset>
      <div class="field full"><div class="checks"><label><input type="checkbox" id="f_notify" ${p.notify_enabled ? "checked" : ""}>Alert me when a recommendation needs a decision</label></div></div>
      <div class="field full"><div><button class="primary" type="submit">${creating ? "Create profile" : "Save changes"}</button></div></div>
    </form>`;

  $("#f_risk").addEventListener("change", (e) => {
    const pre = state.meta.presets[e.target.value];
    const map = { f_minconf: "min_confidence_pct", f_maxpos: "max_position_pct", f_stop: "stop_loss_pct", f_rpt: "risk_per_trade_pct", f_vol: "max_volatility_pct", f_dd: "max_drawdown_pct" };
    for (const [id, key] of Object.entries(map)) $(`#${id}`).value = pre[key];
  });
  $("#newProfile")?.addEventListener("click", () => { state.newProfile = true; render(); });
  $("#profileForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const num = (id) => parseFloat($(`#${id}`).value);
    const body = {
      name: $("#f_name").value.trim(),
      risk_level: $("#f_risk").value,
      trading_style: $("#f_style").value,
      goals: $$('input[name="goal"]:checked').map((x) => x.value),
      preferred_assets: $("#f_assets").value.split(/[,\s]+/).filter(Boolean),
      min_confidence_pct: num("f_minconf"), max_position_pct: num("f_maxpos"), stop_loss_pct: num("f_stop"),
      risk_per_trade_pct: num("f_rpt"), max_volatility_pct: num("f_vol"), max_drawdown_pct: num("f_dd"),
      notify_enabled: $("#f_notify").checked,
    };
    const btn = $("button[type=submit]");
    const saved = await run(btn, "Saving…", () => api(creating ? "/profiles" : `/profiles/${state.profile.id}`, { method: creating ? "POST" : "PUT", body }));
    if (!saved) return;
    state.newProfile = false;
    keep("profileId", String(saved.id));
    await loadProfiles(saved.id);
    toast(creating ? "Profile created" : "Profile saved");
    location.hash = "#/desk";
  });
}

/* ----------------------------------------------------------------- backtest */
function viewBacktest() {
  if (!state.profile) return viewEmpty();
  $("#view").innerHTML = `
    <div class="page-head"><div><h1>Backtest</h1><p>Replays your profile over past prices, using a model that only saw earlier data.</p></div></div>
    <div class="panel"><div class="controls">
      <div class="field"><label for="bt_months">Window</label><select id="bt_months"><option value="6">Last 6 months</option><option value="12" selected>Last 12 months</option><option value="24">Last 24 months</option></select></div>
      <div class="field"><span class="lbl">Exits</span><div class="checks"><label><input type="checkbox" id="bt_stop" checked>Apply stop-loss</label></div></div>
      <button class="primary" id="bt_run">Run backtest</button>
    </div><p class="muted" style="margin-top:10px">Symbols: ${esc(universe().join(", "))}. Takes a few seconds.</p></div>
    <div id="btResult"></div>`;

  $("#bt_run").addEventListener("click", async (e) => {
    const out = await run(e.currentTarget, "Running…", () =>
      api("/backtest", { method: "POST", body: { profile_id: state.profile.id, months: +$("#bt_months").value, use_stop_loss: $("#bt_stop").checked } }));
    if (!out) return;
    const m = out.metrics;
    const row = (k, a, b) => `<tr><td>${k}</td><td class="r num">${a}</td><td class="r num">${b}</td></tr>`;
    const oos = out.model_oos;
    $("#btResult").innerHTML = `
      <div class="split">
        <div class="panel"><h2>Portfolio value</h2><div class="chart" id="btChart"></div>
          <div class="legend"><span><i style="background:#1d4f7c"></i>Agent (${esc(LABELS.style[state.profile.trading_style])}, ${esc(LABELS.risk[state.profile.risk_level].toLowerCase())})</span><span><i style="background:#8a97a3"></i>Equal-weight buy and hold</span></div></div>
        <div class="panel"><h2>${fmt.dayYear(out.start)} to ${fmt.dayYear(out.end)}</h2>
          <table class="metric-table"><thead><tr><th>Measure</th><th class="r">Agent</th><th class="r">Buy and hold</th></tr></thead><tbody>
            ${row("Total return", `<span class="${tone(m.total_return_pct)}">${fmt.pct(m.total_return_pct, 2)}</span>`, `<span class="${tone(m.benchmark_return_pct)}">${fmt.pct(m.benchmark_return_pct, 2)}</span>`)}
            ${row("Worst drop from peak", fmt.plain(m.max_drawdown_pct, 2), fmt.plain(m.benchmark_max_drawdown_pct, 2))}
            ${row("Volatility (yearly)", fmt.plain(m.volatility_pct, 1), "–")}
            ${row("Sharpe ratio", m.sharpe ?? "–", "–")}
            ${row("Trades", m.n_trades, "–")}
            ${row("Winning exits", m.win_rate_pct == null ? "–" : fmt.plain(m.win_rate_pct, 0), "–")}
            ${row("Time invested", fmt.plain(m.exposure_pct, 0), "100%")}
            ${row("Final value", fmt.money(m.final_equity, 0), "–")}
          </tbody></table>
          ${oos ? `<p class="muted" style="margin-top:10px">Out-of-sample model check: ${(oos.accuracy * 100).toFixed(1)}% of ${oos.n} calls were right, against ${(oos.baseline_accuracy * 100).toFixed(1)}% for always guessing the common outcome${oos.roc_auc ? ` (AUC ${oos.roc_auc})` : ""}.</p>` : ""}
          <ul class="notes">${out.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul></div>
      </div>
      <div class="panel" style="margin-top:24px"><h2>Recent trades</h2>
        ${out.trades.length ? `<table><thead><tr><th>Date</th><th>Symbol</th><th>Side</th><th class="r">Shares</th><th class="r">Price</th><th class="r">P&amp;L</th><th>Reason</th></tr></thead><tbody>${out.trades.slice(0, 12).map((t) => `<tr><td>${fmt.day(t.date)}</td><td><b>${esc(t.symbol)}</b></td><td>${t.side === "BUY" ? "Buy" : "Sell"}</td><td class="r num">${t.qty}</td><td class="r num">${fmt.money(t.price)}</td><td class="r num ${tone(t.pnl)}">${t.pnl == null ? "–" : fmt.money(t.pnl, 0)}</td><td>${esc(t.reason)}</td></tr>`).join("")}</tbody></table>` : `<p class="muted">The agent made no trades in this window.</p>`}
      </div>`;
    drawChart($("#btChart"), [
      { name: "Agent", color: "#1d4f7c", points: out.curve.map((c) => ({ x: c.date, y: c.equity })) },
      { name: "Buy and hold", color: "#8a97a3", dash: "5 4", width: 1.5, points: out.curve.map((c) => ({ x: c.date, y: c.benchmark })) },
    ], { fmt: (v) => fmt.money(v, 0), label: "Portfolio value over the backtest window", height: 270 });
  });
}

/* ----------------------------------------------------------------- feedback */
async function viewFeedback() {
  if (!state.profile) return viewEmpty();
  const id = state.profile.id;
  const [fb, recs] = await Promise.all([api(`/feedback/${id}`), api(`/recommendations?profile_id=${id}&limit=200`)]);
  const s = fb.stats;
  const rows = recs.filter((r) => r.action !== "HOLD" && r.status !== "expired");
  const outcome = (r) =>
    r.outcome_return == null
      ? `<span class="muted">Not scored yet</span>`
      : `<span class="num ${r.outcome_correct ? "pos" : "neg"}">${fmt.pct(r.outcome_return * 100)}</span> <span class="badge ${r.outcome_correct ? "buy" : "sell"}">${r.outcome_correct ? "Right call" : "Wrong call"}</span>`;

  $("#view").innerHTML = `
    <div class="page-head"><div><h1>Feedback</h1><p>Your decisions and what the market did next. This is how the agents learn what suits you.</p></div>
      <button id="score">Score past recommendations</button></div>
    <div class="figures">
      <div class="figure"><div class="label">Recommendations</div><div class="value num">${s.total}</div><div class="sub">${s.pending} awaiting a decision</div></div>
      <div class="figure"><div class="label">You approved</div><div class="value num">${s.approval_rate == null ? "–" : fmt.plain(s.approval_rate, 0)}</div><div class="sub">${s.approved} approved, ${s.rejected} rejected</div></div>
      <div class="figure"><div class="label">Calls that were right</div><div class="value num">${s.hit_rate == null ? "–" : fmt.plain(s.hit_rate, 0)}</div><div class="sub">${s.scored} scored</div></div>
      <div class="figure"><div class="label">Average result of a call</div><div class="value num ${tone(s.avg_signal_return_pct)}">${fmt.pct(s.avg_signal_return_pct, 2)}</div><div class="sub">Over ${state.meta.horizon_days} trading days</div></div>
      <div class="figure"><div class="label">Approved vs rejected</div><div class="value num">${s.hit_rate_approved == null ? "–" : fmt.plain(s.hit_rate_approved, 0)} / ${s.hit_rate_rejected == null ? "–" : fmt.plain(s.hit_rate_rejected, 0)}</div><div class="sub">Share right in each group</div></div>
    </div>
    <div class="split">
      <div class="panel"><h2>Decisions and outcomes</h2>
        ${rows.length ? `<table><thead><tr><th>Date</th><th>Symbol</th><th>Call</th><th class="r">Confidence</th><th>Your decision</th><th>Outcome</th></tr></thead><tbody>${rows.map((r) => `<tr><td>${fmt.day(r.as_of)}${r.replay ? ' <span class="badge hold">Replay</span>' : ""}</td><td><b>${esc(r.symbol)}</b></td><td>${r.action === "BUY" ? "Buy" : "Sell"}</td><td class="r num">${(r.confidence * 100).toFixed(0)}%</td><td>${r.status === "pending" ? '<span class="muted">Waiting</span>' : r.status === "approved" ? "Approved" : "Rejected"}${r.comment ? `<div class="muted">${esc(r.comment)}</div>` : ""}</td><td>${outcome(r)}</td></tr>`).join("")}</tbody></table>` : `<p class="muted">No recommendations to review yet. Generate some from the Desk.</p>`}
      </div>
      <div class="panel"><h2>Suggestions for you</h2>
        ${fb.suggestions.length ? fb.suggestions.map((x) => `<div class="suggestion"><h3>${esc(x.title)}</h3><p>${esc(x.detail)}</p></div>`).join("") : `<p class="muted">Suggestions appear after you have decided on at least 5 recommendations. They are advice only; your limits never change unless you edit them.</p>`}
      </div>
    </div>`;

  $("#score").addEventListener("click", async (e) => {
    const out = await run(e.currentTarget, "Scoring…", () => api(`/feedback/${id}/evaluate`, { method: "POST" }));
    if (out) {
      toast(out.updated ? `Scored ${out.updated} recommendations` : "Nothing new to score yet. Outcomes need the full horizon to pass.");
      render();
    }
  });
}

/* ---------------------------------------------------------------- app shell */
function updateBell() {
  const unread = state.notes.filter((n) => !n.is_read).length;
  const c = $("#bellCount");
  c.hidden = unread === 0;
  c.textContent = unread;
}

async function loadProfiles(selectId) {
  state.profiles = await api("/profiles");
  const stored = selectId ?? Number(keep("profileId"));
  state.profile = state.profiles.find((p) => p.id === stored) || state.profiles[0] || null;
  if (state.profile) keep("profileId", String(state.profile.id));
  const sel = $("#profileSelect");
  sel.innerHTML = state.profiles.length
    ? state.profiles.map((p) => `<option value="${p.id}" ${p.id === state.profile?.id ? "selected" : ""}>${esc(p.name)}</option>`).join("")
    : `<option>No profile yet</option>`;
  sel.disabled = !state.profiles.length;
}

const routes = { desk: viewDesk, profile: viewProfile, backtest: viewBacktest, feedback: viewFeedback };

async function render() {
  const name = location.hash.replace(/^#\//, "") || "desk";
  const route = routes[name] ? name : "desk";
  $$("#nav a").forEach((a) => (a.dataset.route === route ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
  if (route !== "profile") state.newProfile = false;
  try {
    await routes[route]();
  } catch (err) {
    $("#view").innerHTML = `<div class="empty"><h2>Something went wrong</h2><p>${esc(err.message)}</p><button id="retry">Try again</button></div>`;
    $("#retry").addEventListener("click", render);
  }
}

function paintShell() {
  const m = state.meta;
  const synthetic = m.data_source === "synthetic";
  const badge = $("#sourceBadge");
  badge.textContent = synthetic ? "Demo data" : "Live data";
  badge.className = `badge ${synthetic ? "warn" : ""}`;
  $("#banner").innerHTML = synthetic
    ? `<div class="notice"><b>Demo data.</b> Yahoo Finance could not be reached, so prices are simulated. Do not use these signals for real decisions.</div>`
    : "";
  $("#disclaimer").textContent = m.disclaimer;
  const mm = m.model;
  $("#railFoot").innerHTML = mm
    ? `<b>${esc(LABELS.algo[mm.algorithm] || mm.algorithm)}</b> model<br>Trained on ${mm.symbols.length} symbols to ${fmt.dayYear(mm.data_end)}${mm.metrics ? `<br>Hold-out accuracy ${(mm.metrics.accuracy * 100).toFixed(0)}%` : ""}`
    : `The model trains on first use.`;
}

async function init() {
  $("#view").innerHTML = `<p class="muted" style="padding:24px 0">Loading…</p>`;
  try {
    state.meta = await api("/meta");
    await loadProfiles();
  } catch (err) {
    $("#view").innerHTML = `<div class="empty"><h2>Cannot reach the API</h2><p>${esc(err.message)}</p></div>`;
    return;
  }
  paintShell();
  $("#profileSelect").addEventListener("change", (e) => {
    state.profile = state.profiles.find((p) => p.id === +e.target.value);
    keep("profileId", e.target.value);
    state.chartSymbol = null;
    render();
  });
  $("#bellBtn").addEventListener("click", async () => {
    location.hash = "#/desk";
    await render();
    $("#alertsPanel")?.scrollIntoView({ behavior: "smooth", block: "center" });
  });
  window.addEventListener("hashchange", render);
  render().then(async () => {
    // model may have been trained lazily by the first request
    state.meta.model = (await api("/meta").catch(() => state.meta)).model;
    paintShell();
  });
}

init();

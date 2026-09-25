(() => {
"use strict";

// ---------- constants ----------
const AREA_ORDER = ["Meal prep","Kitchen","Bathrooms","Bedrooms","Living areas","Laundry","Outdoor","Spare rooms","Whole house"];
const FREQ_LABEL = {visit:"Every visit", weekly:"Weekly", fortnightly:"Every 2 weeks", monthly:"Monthly"};
const FREQ_GAP = {weekly:5, fortnightly:12, monthly:26}; // min days since last done before it's due again
const DAY_LABEL = {any:"Tue or Fri", tue:"Tuesdays", fri:"Fridays"};
const SLOT_LABEL = {breakfast:"Breakfast · overnight oats", main:"Main · 3 a day", dessert:"Dessert"};
const AISLES = ["Produce","Meat","Dairy & eggs","Frozen","Bakery","Pantry","Baking","Spices"];
const POLL_MS = 20000;
const CHECK_SVG = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8.5l3.2 3L13 4.5" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>';

// ---------- dates (local, YYYY-MM-DD) ----------
const pad = n => String(n).padStart(2,"0");
const iso = d => `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}`;
const parse = s => { const [y,m,d] = s.split("-").map(Number); return new Date(y, m-1, d); };
const addDays = (s,n) => { const d = parse(s); d.setDate(d.getDate()+n); return iso(d); };
const dow = s => parse(s).getDay();
const diffDays = (a,b) => Math.round((parse(b) - parse(a)) / 86400000);
const today = () => iso(new Date());
const mondayOf = s => addDays(s, -((dow(s)+6) % 7));
const isVisitDay = s => dow(s) === 2 || dow(s) === 5;
const nextVisit = from => { for (let i=0;i<7;i++){ const d = addDays(from,i); if (isVisitDay(d)) return d; } return from; };
const stepVisit = (s,dir) => { for (let i=1;i<8;i++){ const d = addDays(s, dir*i); if (isVisitDay(d)) return d; } return s; };
const sessionOf = s => dow(s) === 2 ? "tue" : "fri";
const fmt = (s, opts) => parse(s).toLocaleDateString("en-US", opts);
const fmtLong = s => fmt(s, {weekday:"long", month:"short", day:"numeric"});
const fmtShort = s => fmt(s, {weekday:"short", month:"short", day:"numeric"});
const fmtDay = s => fmt(s, {month:"short", day:"numeric"});

// ---------- state ----------
const S = {
  me:null, isOwner:false, booted:false, loginError:"",
  tasks:{}, visits:{}, settings:null, plans:{}, planLoaded:{}, users:null, loadedState:false,
  tab:"visit", date: nextVisit(today()), week:null, session:null, shopFilter:"all",
  gen:{running:false, status:"", titles:[], error:"", jobId:null},
  confirm:null, copyText:null, showAccount:false, accountMsg:"",
};
S.week = mondayOf(S.date); S.session = sessionOf(S.date);
{ const h = (location.hash || "").slice(1); if (["visit","meals","shopping","setup"].includes(h)) S.tab = h; }

const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const settings = () => S.settings || {kcal:500, protein:50, tue:{breakfast:3,main:9,dessert:3,covers:"Wed, Thu, Fri"}, fri:{breakfast:4,main:12,dessert:4,covers:"Sat, Sun, Mon, Tue"}, store:"", likes:"", dislikes:"", pantry:""};
function toast(msg){
  const el = document.getElementById("toast");
  el.textContent = msg; el.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => el.hidden = true, 2600);
}

// ---------- API ----------
class ApiError extends Error { constructor(status, message, body){ super(message); this.status = status; this.body = body; } }
async function api(method, path, body){
  const opts = {method, credentials:"same-origin", headers:{"X-HRS":"1"}};
  if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  let res;
  try { res = await fetch(path, opts); }
  catch (_) { throw new ApiError(0, "Can't reach the server. Check your connection."); }
  let data = null;
  try { data = await res.json(); } catch (_) {}
  if (res.status === 401 && path !== "/api/login") { S.me = null; stopPolling(); render(); throw new ApiError(401, "Please sign in again."); }
  if (!res.ok) throw new ApiError(res.status, (data && data.error) || `Request failed (${res.status}).`, data);
  return data;
}
// Fire a change, show a toast on failure and resync from the server either way.
async function mutate(method, path, body, okMsg){
  try { const r = await api(method, path, body); if (okMsg) toast(okMsg); return r; }
  catch (e) { lastSig = ""; if (e.status !== 401) toast(e.message); throw e; }  // force a re-render from server state
  finally { refresh(); }
}

async function loadState(){
  const j = await api("GET", "/api/state");
  S.me = j.me; S.isOwner = j.me.role === "homeowner";
  const m = {}; for (const t of j.tasks) m[t.id] = t;
  S.tasks = m; S.visits = j.visits; S.settings = j.settings; S.loadedState = true;
}
async function loadPlan(week){
  const j = await api("GET", "/api/plans/" + week);
  S.plans[week] = j.plan; S.planLoaded[week] = true;
}
async function loadUsers(){ S.users = (await api("GET", "/api/users")).users; }
let lastSig = "";
async function refresh(){
  if (!S.me) return;
  try {
    await loadState();
    if (S.tab === "meals" || S.tab === "shopping") await loadPlan(S.week);
    if (S.tab === "setup" && S.isOwner) await loadUsers();
    const sig = JSON.stringify([S.tasks, S.visits, S.settings, S.plans[S.week], S.users, S.me]);
    if (sig !== lastSig) { lastSig = sig; render(); }
  } catch (e) { if (e.status !== 401) console.warn(e); }
}
let pollTimer = null;
function startPolling(){ stopPolling(); pollTimer = setInterval(() => { if (document.visibilityState === "visible") refresh(); }, POLL_MS); }
function stopPolling(){ clearInterval(pollTimer); pollTimer = null; }
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") refresh(); });

// ---------- task logic ----------
function doneDatesByTask(){
  const map = {};
  for (const [date, v] of Object.entries(S.visits)) for (const id of Object.keys((v && v.done) || {})) (map[id] ||= []).push(date);
  for (const k in map) map[k].sort();
  return map;
}
function lastDoneBefore(list, date){
  if (!list) return null;
  let last = null;
  for (const d of list) { if (d < date) last = d; else break; }
  return last;
}
function isDue(t, date, doneMap){
  if (t.active === false) return false;
  const d = dow(date);
  if (t.day === "tue" && d !== 2) return false;
  if (t.day === "fri" && d !== 5) return false;
  const freq = t.freq || "visit";
  if (freq === "visit") return true;
  if (S.visits[date] && S.visits[date].done && S.visits[date].done[t.id]) return true;
  if (freq === "weekly" && t.day && t.day !== "any") return true;
  const last = lastDoneBefore(doneMap[t.id], date);
  return !last || diffDays(last, date) >= (FREQ_GAP[freq] || 0);
}
const areaRank = a => { const i = AREA_ORDER.indexOf(a); return i < 0 ? 100 : i; };
const sortedTasks = () => Object.values(S.tasks).sort((a,b) => areaRank(a.area) - areaRank(b.area) || String(a.area).localeCompare(String(b.area)) || (a.order||0) - (b.order||0));
const visitOf = date => (S.visits[date] ||= {date, note:"", done:{}, extras:{}});

// ---------- meal logic ----------
function macros(r){
  const n = Math.max(1, Number(r.portions) || 1);
  const k = (r.ingredients||[]).reduce((s,i) => s + (Number(i.kcal)||0), 0) / n;
  const p = (r.ingredients||[]).reduce((s,i) => s + (Number(i.protein)||0), 0) / n;
  return {kcal: Math.round(k), protein: Math.round(p)};
}
function macroPill(r){
  const st = settings(), m = macros(r);
  const ok = Math.abs(m.kcal - st.kcal) <= st.kcal * 0.07 && m.protein >= st.protein - 3;
  return `<span class="pill ${ok ? "ok" : "warn"}">${m.kcal} kcal</span><span class="pill ${ok ? "ok" : "warn"}">${m.protein} g protein</span><span class="pill">per portion</span>${ok ? "" : `<span class="pill warn">off target</span>`}`;
}

// ---------- rendering ----------
const app = document.getElementById("app");
function render(){
  const a = document.activeElement;
  const keep = a && a.id && app.contains(a) && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName) ? {id:a.id, v:a.value, s:a.selectionStart, e:a.selectionEnd} : null;
  const drafts = {};
  app.querySelectorAll("[data-keep]").forEach(el => { if (el.id) drafts[el.id] = el.value; });

  if (!S.booted) { app.innerHTML = `<div class="empty">Loading your run sheet…</div>`; return; }
  if (!S.me) { app.innerHTML = renderLogin(); restore(drafts, keep); return; }

  const tabs = [["visit","Visit"],["meals","Meals"]];
  if (S.isOwner) tabs.push(["shopping","Shopping"],["setup","Setup"]);
  if (!tabs.some(t => t[0] === S.tab)) S.tab = "visit";
  app.innerHTML = `
    <header>
      <div class="top">
        <div class="brand"><h1>House Run Sheet</h1><small>Tuesdays &amp; Fridays · 6 hours each</small></div>
        <div class="acct"><span>${esc(S.me.displayName)}</span><button class="linkish" data-action="account">Account</button><button class="linkish" data-action="logout">Sign out</button></div>
      </div>
      ${S.showAccount ? renderAccount() : ""}
      <nav class="tabs" role="tablist">${tabs.map(([k,l]) => `<button class="tab" role="tab" data-action="tab" data-tab="${k}" aria-selected="${S.tab===k}">${l}</button>`).join("")}</nav>
    </header>
    <main>${({visit:renderVisit, meals:renderMeals, shopping:renderShopping, setup:renderSetup}[S.tab])()}</main>`;
  restore(drafts, keep);
}
function restore(drafts, keep){
  for (const [id, v] of Object.entries(drafts)) { const el = document.getElementById(id); if (el) el.value = v; }
  if (keep) {
    const el = document.getElementById(keep.id);
    if (el) { if (el.tagName !== "SELECT") el.value = keep.v; el.focus(); try { if (keep.s != null) el.setSelectionRange(keep.s, keep.e); } catch(_){} }
  }
}

function renderLogin(){
  return `<div class="login-wrap"><form class="card login" data-form="login">
    <h1>House Run Sheet</h1>
    <p class="muted small" style="margin:0">Sign in with the username and password Owen gave you.</p>
    <label class="field" for="loginUser"><span>Username</span><input type="text" id="loginUser" data-keep autocomplete="username" autocapitalize="none" required></label>
    <label class="field" for="loginPass"><span>Password</span><input type="password" id="loginPass" autocomplete="current-password" required></label>
    ${S.loginError ? `<div class="err" role="alert">${esc(S.loginError)}</div>` : ""}
    <button class="btn primary" type="submit">Sign in</button>
  </form></div>`;
}
function renderAccount(){
  return `<form class="card mt10" data-form="password" style="display:grid;gap:10px;max-width:420px">
    <div class="label">Change your password</div>
    <label class="field" for="pwCur"><span>Current password</span><input type="password" id="pwCur" autocomplete="current-password"></label>
    <label class="field" for="pwNew"><span>New password (10+ characters)</span><input type="password" id="pwNew" autocomplete="new-password"></label>
    ${S.accountMsg ? `<div class="small">${esc(S.accountMsg)}</div>` : ""}
    <div class="row"><button class="btn primary" type="submit">Change password</button><button class="btn ghost" type="button" data-action="account">Close</button></div>
  </form>`;
}

// ---- Visit tab ----
function renderVisit(){
  const date = S.date, t0 = today(), next = nextVisit(t0);
  const visit = S.visits[date] || {}, done = visit.done || {};
  const doneMap = doneDatesByTask();
  const due = sortedTasks().filter(t => isDue(t, date, doneMap));
  const extras = Object.entries(visit.extras || {}).sort((a,b) => String(a[1].createdAt||"").localeCompare(String(b[1].createdAt||"")));
  const total = due.length + extras.length;
  const doneCount = due.filter(t => done[t.id]).length + extras.filter(([,x]) => x.done).length;
  const rel = date === t0 ? "Today" : date === next ? "Next visit" : date < t0 ? "Past visit" : "Coming up";
  const sess = sessionOf(date), st = settings();

  const groups = {};
  for (const t of due) (groups[t.area || "Other"] ||= []).push(t);

  const taskRow = t => {
    const isDone = !!done[t.id];
    let pill = "";
    if (t.freq && t.freq !== "visit") {
      const last = lastDoneBefore(doneMap[t.id], date);
      const lastTxt = last ? `last ${fmtDay(last)}` : "first time";
      pill = `<span class="pill oat">${esc(FREQ_LABEL[t.freq])}${t.day && t.day !== "any" ? " · " + esc(DAY_LABEL[t.day]) : ""}</span>` + (t.freq !== "weekly" || t.day === "any" ? `<span class="pill">${lastTxt}</span>` : "");
    }
    const link = t.link === "meals" ? `<div><button class="linkish" data-action="open-meals">Open this visit's recipes</button></div>` : "";
    return `<div class="task ${isDone ? "done" : ""}">
      <button class="check" role="checkbox" aria-checked="${isDone}" aria-label="${esc(t.title)}" data-action="toggle" data-id="${esc(t.id)}">${CHECK_SVG}</button>
      <div><div class="t">${esc(t.title)}</div>${t.notes ? `<div class="n">${esc(t.notes)}</div>` : ""}${link}</div>
      <div class="side">${pill}</div>
    </div>`;
  };
  const extraRow = ([id, x]) => `<div class="task ${x.done ? "done" : ""}">
      <button class="check" role="checkbox" aria-checked="${!!x.done}" aria-label="${esc(x.title)}" data-action="toggle-extra" data-id="${esc(id)}">${CHECK_SVG}</button>
      <div><div class="t">${esc(x.title)}</div>${x.notes ? `<div class="n">${esc(x.notes)}</div>` : ""}</div>
      <div class="side"><span class="pill oat">Just this visit</span>${S.isOwner ? `<button class="linkish" data-action="remove-extra" data-id="${esc(id)}">Remove</button>` : ""}</div>
    </div>`;

  return `<div class="section">
    <div class="card">
      <div class="visit-head">
        <button class="icon-btn" data-action="prev-visit" aria-label="Previous visit">‹</button>
        <div>
          <h2>${esc(fmtLong(date))}</h2>
          <div class="visit-meta"><span class="pill ${rel === "Today" || rel === "Next visit" ? "ok" : ""}">${rel}</span><span class="small muted">Meal prep covers ${esc(st[sess].covers)}</span></div>
        </div>
        <button class="icon-btn" data-action="next-visit" aria-label="Next visit">›</button>
      </div>
      <div class="row small mt14"><span class="mono">${doneCount} of ${total} done</span><span class="spacer"></span>${date !== next ? `<button class="linkish" data-action="goto-next">Jump to next visit</button>` : ""}</div>
      <div class="progress" aria-hidden="true"><i style="width:${total ? Math.round(doneCount/total*100) : 0}%"></i></div>
    </div>

    ${(extras.length || S.isOwner) ? `<div class="group">
      <div class="group-h"><span class="label">Added for this visit</span><span class="mono small muted">${extras.filter(([,x]) => x.done).length}/${extras.length}</span></div>
      ${extras.map(extraRow).join("")}
      ${S.isOwner ? `<form class="task" data-form="extra" style="grid-template-columns:1fr auto">
        <input type="text" id="extraTitle" data-keep maxlength="200" placeholder="Add a one-off job, e.g. Sort the hall closet" aria-label="One-off job for this visit">
        <button class="btn" type="submit">Add</button>
      </form>` : ""}
    </div>` : ""}

    ${!S.loadedState ? `<div class="empty">Loading tasks…</div>` : due.length === 0 ? `<div class="card empty">No regular tasks yet.${S.isOwner ? ` Add them in <button class="linkish" data-action="tab" data-tab="setup">Setup</button>.` : ""}</div>` :
      Object.entries(groups).map(([area, list]) => `<div class="group">
        <div class="group-h"><span class="label">${esc(area)}</span><span class="mono small muted">${list.filter(t => done[t.id]).length}/${list.length}</span></div>
        ${list.map(taskRow).join("")}
      </div>`).join("")}

    <div class="card">
      <label class="field" for="visitNote"><span>Notes for ${S.isOwner ? "you" : "Owen"}: anything running low, broken or needing a decision</span>
      <textarea id="visitNote" maxlength="4000" placeholder="e.g. Out of dish soap. Spare room blind is sticking.">${esc(visit.note || "")}</textarea></label>
    </div>
  </div>`;
}

// ---- week navigation shared by Meals + Shopping ----
function weekNav(){
  const w = S.week, plan = S.plans[w];
  return `<div class="card visit-head">
    <button class="icon-btn" data-action="prev-week" aria-label="Previous week">‹</button>
    <div><div class="label">Week of</div><h2 style="font-size:24px">${esc(fmt(w,{month:"long", day:"numeric"}))}</h2>
    <div class="visit-meta"><span class="small muted">Tue ${esc(fmtDay(addDays(w,1)))} and Fri ${esc(fmtDay(addDays(w,4)))}</span>${S.planLoaded[w] && !plan ? `<span class="pill">no menu yet</span>` : ""}</div></div>
    <button class="icon-btn" data-action="next-week" aria-label="Next week">›</button>
  </div>`;
}

// ---- Meals tab ----
function renderMeals(){
  const plan = S.plans[S.week], st = settings();
  let body;
  if (!S.planLoaded[S.week]) body = `<div class="empty">Loading menu…</div>`;
  else if (!plan) body = `<div class="card empty">No menu for this week yet.${S.isOwner ? "" : " Owen will add one before the visit."}</div>`;
  else {
    const sess = S.session, s = plan.sessions && plan.sessions[sess];
    body = `<div class="chips" role="group" aria-label="Prep session">
      ${["tue","fri"].map(k => `<button class="chip" data-action="session" data-s="${k}" aria-pressed="${sess===k}">${k === "tue" ? "Tuesday prep" : "Friday prep"} · covers ${esc((plan.sessions[k] && plan.sessions[k].covers) || st[k].covers)}</button>`).join("")}
    </div>` + (s ? `
    <div class="card">
      <div class="label">Prep order for ${esc(fmtLong(s.date || addDays(S.week, sess==="tue"?1:4)))}</div>
      <ol class="timeline">${(s.timeline||[]).map(x => `<li>${esc(x)}</li>`).join("")}</ol>
    </div>
    ${["breakfast","main","dessert"].map(slot => recipeCard(plan, sess, slot)).join("")}` : `<div class="empty">This session is missing from the menu.</div>`);
  }
  return `<div class="section">${weekNav()}${body}${S.isOwner && S.planLoaded[S.week] ? genPanel(plan) : ""}</div>`;
}
function recipeCard(plan, sess, slot){
  const r = plan.sessions[sess].recipes && plan.sessions[sess].recipes[slot];
  if (!r) return "";
  return `<article class="recipe">
    <div class="row"><span class="label">${esc(SLOT_LABEL[slot])}</span><span class="spacer"></span><span class="pill mono">× ${esc(r.portions)} portions</span>
      ${S.isOwner ? `<button class="star" data-action="fav" data-s="${sess}" data-slot="${slot}" aria-pressed="${!!r.fav}">${r.fav ? "★ Favourite" : "☆ Favourite"}</button>` : ""}</div>
    <h3>${esc(r.title)}</h3>
    ${r.blurb ? `<div class="blurb">${esc(r.blurb)}</div>` : ""}
    <div class="macros">${macroPill(r)}</div>
    <div class="recipe-body">
      <div><div class="label mb4">Ingredients for the whole batch</div>
        <table class="ing"><tbody>${(r.ingredients||[]).map(i => `<tr><td>${esc(i.item)}</td><td>${esc(i.amount)}</td></tr>`).join("")}</tbody></table>
      </div>
      <div><div class="label mb6">Method</div>
        <ol class="steps">${(r.steps||[]).map(x => `<li>${esc(x)}</li>`).join("")}</ol>
        ${r.portionNote ? `<div class="store"><b>Portions:</b> ${esc(r.portionNote)}</div>` : ""}
        ${r.storage ? `<div class="store mt8"><b>Storage:</b> ${esc(r.storage)}</div>` : ""}
      </div>
    </div>
  </article>`;
}
function genPanel(plan){
  const g = S.gen, confirming = S.confirm === "regen";
  return `<div class="card" id="genCard">
    <h3 class="h19">${plan ? "Replace this week's menu" : "Create this week's menu"}</h3>
    <p class="small muted" style="margin:6px 0 12px">Claude writes the oats, main and dessert for both prep days plus one combined shopping list, sharing ingredients between Tuesday and Friday to cut waste. It uses your Setup preferences, last week's leftovers and avoids recent repeats. Takes about a minute.</p>
    <label class="field" for="genNote"><span>Anything for this week? (optional)</span>
      <input type="text" id="genNote" data-keep maxlength="500" placeholder="e.g. Asian flavours, no pork, use up the Parmesan" ${g.running ? "disabled" : ""}></label>
    <div class="row mt12">
      ${g.running ? `<button class="btn" data-action="gen-stop">Stop</button>`
        : plan && !confirming ? `<button class="btn" data-action="gen-ask">Replace menu…</button>`
        : plan && confirming ? `<span class="small">This overwrites the current menu and shopping list.</span><button class="btn danger" data-action="gen-go">Replace it</button><button class="btn ghost" data-action="gen-cancel">Cancel</button>`
        : `<button class="btn primary" data-action="gen-go">Create menu &amp; shopping list</button>`}
    </div>
    <div class="gen-status mt10" id="genStatus" ${g.status || g.error ? "" : "hidden"}>${genStatusHtml()}</div>
  </div>`;
}
function genStatusHtml(){
  const g = S.gen;
  if (g.error) return `<div class="notice warn">${esc(g.error)}</div>`;
  return `<div class="${g.running ? "muted" : ""}">${esc(g.status)}</div>${g.titles && g.titles.length ? `<ul class="gen-titles">${g.titles.map(t => `<li>${esc(t)}</li>`).join("")}</ul>` : ""}`;
}
function updateGenStatus(){
  const el = document.getElementById("genStatus");
  if (el) { el.hidden = !(S.gen.status || S.gen.error); el.innerHTML = genStatusHtml(); }
}

// ---- Shopping tab ----
function renderShopping(){
  const plan = S.plans[S.week];
  if (!S.planLoaded[S.week]) return `<div class="section">${weekNav()}<div class="empty">Loading…</div></div>`;
  if (!plan) return `<div class="section">${weekNav()}<div class="card empty">No menu for this week yet. Create one on the <button class="linkish" data-action="tab" data-tab="meals">Meals</button> tab.</div></div>`;
  const got = plan.got || {}, f = S.shopFilter;
  const items = (plan.shopping || []).filter(i => f === "all" || (f === "tue" ? i.for !== "fri" : i.for === "fri"));
  const fresh = items.filter(i => !i.stock), stock = items.filter(i => i.stock);
  const rank = a => { const i = AISLES.indexOf(a); return i < 0 ? 99 : i; };
  const groupBy = list => { const g = {}; for (const i of list) (g[i.aisle || "Other"] ||= []).push(i); return Object.entries(g).sort((a,b) => rank(a[0]) - rank(b[0])); };
  const row = i => `<div class="shop-item ${got[i.id] ? "done" : ""}">
    <button class="check" role="checkbox" aria-checked="${!!got[i.id]}" aria-label="${esc(i.item)}" data-action="got" data-id="${esc(i.id)}">${CHECK_SVG}</button>
    <div class="t">${esc(i.item)}${i.for === "both" ? "" : ` <span class="pill">${i.for === "tue" ? "Tue" : "Fri"}</span>`}</div>
    <div class="b">${esc(i.buy)}</div></div>`;
  const left = fresh.filter(i => !got[i.id]).length;
  return `<div class="section">${weekNav()}
    <div class="row">
      <div class="chips" role="group" aria-label="Which order">
        ${[["all","Everything"],["tue","Deliver by Tue"],["fri","Friday-only items"]].map(([k,l]) => `<button class="chip" data-action="shop-filter" data-f="${k}" aria-pressed="${f===k}">${l}</button>`).join("")}
      </div><span class="spacer"></span>
      <button class="btn" data-action="copy">Copy list</button>
    </div>
    <p class="small muted" style="margin:0">One order before Tuesday covers the whole week (buy the Friday meat and freeze it if the use-by is tight). Or split: "Deliver by Tue" first, then the Friday-only items. <span class="mono">${left}</span> fresh items left to order.</p>
    ${S.copyText != null ? `<div class="card"><label class="field" for="copyBox"><span>Copying was blocked. Select all and copy this instead.</span><textarea id="copyBox" class="copybox" readonly>${esc(S.copyText)}</textarea></label></div>` : ""}
    ${groupBy(fresh).map(([aisle, list]) => `<div class="group"><div class="group-h"><span class="label">${esc(aisle)}</span><span class="mono small muted">${list.filter(i => got[i.id]).length}/${list.length}</span></div>${list.map(row).join("")}</div>`).join("")}
    ${stock.length ? `<div class="group"><div class="group-h"><span class="label">Pantry basics: only if you've run out</span><span class="mono small muted">${stock.length}</span></div>${stock.map(row).join("")}</div>` : ""}
    ${(plan.leftovers||[]).length ? `<div class="card"><div class="label">Expected leftovers</div><ul style="margin:8px 0 0;padding-left:20px">${plan.leftovers.map(x => `<li>${esc(x)}</li>`).join("")}</ul><p class="small muted" style="margin:8px 0 0">Next week's menu is planned to use these up first.</p></div>` : ""}
  </div>`;
}
function shoppingText(){
  const plan = S.plans[S.week]; if (!plan) return "";
  const got = plan.got || {}, f = S.shopFilter;
  const items = (plan.shopping||[]).filter(i => !got[i.id] && (f === "all" || (f === "tue" ? i.for !== "fri" : i.for === "fri")));
  const out = [`Groceries – week of ${fmt(S.week,{month:"short", day:"numeric"})}`];
  const aisles = [...new Set(items.filter(i => !i.stock).map(i => i.aisle || "Other"))];
  for (const a of aisles) { out.push("", a.toUpperCase()); for (const i of items.filter(i => !i.stock && (i.aisle||"Other") === a)) out.push(`- ${i.item}: ${i.buy}`); }
  const st = items.filter(i => i.stock);
  if (st.length) { out.push("", "PANTRY (if out)"); for (const i of st) out.push(`- ${i.item}: ${i.buy}`); }
  return out.join("\n");
}

// ---- Setup tab ----
function renderSetup(){
  const tasks = sortedTasks(), st = settings();
  const areas = [...new Set([...AREA_ORDER, ...tasks.map(t => t.area).filter(Boolean)])];
  const freqSel = (id, v) => `<select id="${id}" data-field="freq" aria-label="How often">${Object.entries(FREQ_LABEL).map(([k,l]) => `<option value="${k}" ${v===k?"selected":""}>${l}</option>`).join("")}</select>`;
  const daySel = (id, v) => `<select id="${id}" data-field="day" aria-label="Which day">${Object.entries(DAY_LABEL).map(([k,l]) => `<option value="${k}" ${v===k?"selected":""}>${k === "any" ? "Either day" : l}</option>`).join("")}</select>`;
  const groups = {};
  for (const t of tasks) (groups[t.area || "Other"] ||= []).push(t);
  const row = t => `<div class="trow" data-task="${esc(t.id)}">
      <input type="text" class="title-in" id="tt-${esc(t.id)}" data-field="title" maxlength="200" value="${esc(t.title)}" aria-label="Task">
      <input type="text" id="ta-${esc(t.id)}" data-field="area" maxlength="60" value="${esc(t.area)}" list="areaList" aria-label="Area">
      ${freqSel("tf-" + t.id, t.freq || "visit")}
      ${daySel("td-" + t.id, t.day || "any")}
      ${S.confirm === "del:" + t.id
        ? `<span class="row"><button class="btn danger" data-action="del-yes" data-id="${esc(t.id)}">Delete</button><button class="btn ghost" data-action="del-no">Keep</button></span>`
        : `<button class="btn ghost" data-action="del-ask" data-id="${esc(t.id)}" aria-label="Delete task">Delete</button>`}
      <input type="text" class="notes" id="tn-${esc(t.id)}" data-field="notes" maxlength="500" value="${esc(t.notes || "")}" placeholder="Notes for the housekeeper (optional)" aria-label="Notes">
    </div>`;
  const num = (id, v, label) => `<label class="field" for="${id}"><span>${label}</span><input type="number" id="${id}" min="0" max="40" value="${esc(v)}"></label>`;
  const users = S.users || [];
  const userRow = u => `<div class="urow">
      <div><div class="t">${esc(u.displayName)} ${u.active ? "" : `<span class="pill warn">signed out · no access</span>`}</div><div class="small muted mono">${esc(u.username)}</div></div>
      <span class="pill ${u.role === "homeowner" ? "ok" : "oat"}">${u.role === "homeowner" ? "Homeowner" : "Housekeeper"}</span>
      ${u.id === S.me.id ? `<span class="small muted">You</span>` : `<span class="row">
        ${S.confirm === "pw:" + u.id
          ? `<input type="password" id="rpw-${u.id}" placeholder="New password (10+)" autocomplete="new-password" style="width:180px"><button class="btn" data-action="pw-set" data-id="${u.id}">Set</button><button class="btn ghost" data-action="pw-cancel">Cancel</button>`
          : `<button class="btn ghost" data-action="pw-ask" data-id="${u.id}">Reset password</button><button class="btn ghost" data-action="user-active" data-id="${u.id}" data-active="${u.active ? "0" : "1"}">${u.active ? "Remove access" : "Restore access"}</button>`}
      </span>`}
    </div>`;
  return `<div class="section">
    <div class="group">
      <div class="group-h"><span class="label">People</span><span class="mono small muted">${users.length}</span></div>
      ${users.map(userRow).join("")}
      <form class="urow" data-form="add-user" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr))">
        <input type="text" id="nuName" data-keep maxlength="80" placeholder="Name, e.g. Maria" aria-label="Name">
        <input type="text" id="nuUser" data-keep maxlength="64" placeholder="Username" autocapitalize="none" aria-label="Username">
        <select id="nuRole" data-keep aria-label="Role"><option value="housekeeper">Housekeeper</option><option value="homeowner">Homeowner</option></select>
        <input type="password" id="nuPass" placeholder="Temporary password (10+)" autocomplete="new-password" aria-label="Temporary password">
        <button class="btn primary" type="submit">Add person</button>
      </form>
    </div>
    <p class="small muted" style="margin:0">Housekeepers see the Visit and Meals tabs, can tick jobs off and leave notes. Homeowners also get Shopping and Setup. Give each person their own login; they can change their password under Account.</p>

    <div class="card">
      <h3 class="h19">Regular tasks</h3>
      <p class="small muted" style="margin:6px 0 12px">Every-visit tasks show on both days. Weekly, every-2-weeks and monthly tasks show when they're due and stay on the list until ticked. Pick a day to pin a task to Tuesdays or Fridays. One-off jobs for a single day are added on the Visit tab.</p>
      <form data-form="add-task" class="grid2" style="align-items:end">
        <label class="field" for="newTitle"><span>New task</span><input type="text" id="newTitle" data-keep maxlength="200" placeholder="e.g. Clean the garage fridge"></label>
        <label class="field" for="newArea"><span>Area</span><input type="text" id="newArea" data-keep maxlength="60" list="areaList" placeholder="Kitchen"></label>
        <label class="field" for="newFreq"><span>How often</span><select id="newFreq" data-keep>${Object.entries(FREQ_LABEL).map(([k,l]) => `<option value="${k}">${l}</option>`).join("")}</select></label>
        <label class="field" for="newDay"><span>Day</span><select id="newDay" data-keep>${Object.entries(DAY_LABEL).map(([k,l]) => `<option value="${k}">${k === "any" ? "Either day" : l}</option>`).join("")}</select></label>
        <button class="btn primary" type="submit">Add task</button>
      </form>
      <datalist id="areaList">${areas.map(a => `<option value="${esc(a)}"></option>`).join("")}</datalist>
    </div>
    ${Object.entries(groups).map(([area, list]) => `<div class="group"><div class="group-h"><span class="label">${esc(area)}</span><span class="mono small muted">${list.length}</span></div>${list.map(row).join("")}</div>`).join("") || `<div class="card empty">No tasks yet.</div>`}

    <form class="card" data-form="settings" style="display:grid;gap:14px">
      <h3 class="h19">Meal plan settings</h3>
      <div class="grid2">
        <label class="field" for="sKcal"><span>Calories per portion</span><input type="number" id="sKcal" value="${esc(st.kcal)}" min="200" max="1500"></label>
        <label class="field" for="sProt"><span>Protein per portion (g)</span><input type="number" id="sProt" value="${esc(st.protein)}" min="10" max="150"></label>
      </div>
      ${["tue","fri"].map(k => `<div><div class="label mb6">${k === "tue" ? "Tuesday" : "Friday"} prep: portions</div>
        <div class="grid3">${num("s-"+k+"-b", st[k].breakfast, "Oats")}${num("s-"+k+"-m", st[k].main, "Main")}${num("s-"+k+"-d", st[k].dessert, "Dessert")}</div>
        <label class="field mt8" for="s-${k}-c"><span>Days it covers</span><input type="text" id="s-${k}-c" maxlength="80" value="${esc(st[k].covers)}"></label></div>`).join("")}
      <label class="field" for="sStore"><span>Where you order from</span><input type="text" id="sStore" maxlength="1000" value="${esc(st.store)}" placeholder="e.g. Whole Foods via Amazon, Ralphs on Instacart"></label>
      <label class="field" for="sLikes"><span>Flavours and meals you like</span><textarea id="sLikes" maxlength="1000" placeholder="e.g. Mexican, Thai curries, pesto pasta, anything with peanut butter">${esc(st.likes)}</textarea></label>
      <label class="field" for="sDislikes"><span>Never use</span><textarea id="sDislikes" maxlength="1000" placeholder="e.g. mushrooms, tofu, olives">${esc(st.dislikes)}</textarea></label>
      <label class="field" for="sPantry"><span>Always in the pantry (left off shopping lists)</span><textarea id="sPantry" maxlength="1000" placeholder="e.g. olive oil, salt, pepper, cumin, vanilla whey">${esc(st.pantry)}</textarea></label>
      <div><button class="btn primary" type="submit">Save settings</button></div>
    </form>
  </div>`;
}

// ---------- menu generation (server-side job) ----------
async function generate(){
  if (S.gen.running) return;
  const week = S.week, note = (document.getElementById("genNote") || {}).value || "";
  S.confirm = null;
  S.gen = {running:true, status:"Thinking about this week's menu… (the first words usually take 20–60 seconds)", titles:[], error:"", jobId:null};
  render();
  let jobId;
  try { jobId = (await api("POST", `/api/plans/${week}/generate`, {note})).jobId; }
  catch (e) {
    if (e.status === 409 && e.body && e.body.jobId) jobId = e.body.jobId;
    else { S.gen = {running:false, status:"", titles:[], error:e.message, jobId:null}; render(); return; }
  }
  S.gen.jobId = jobId;
  while (true) {
    await new Promise(r => setTimeout(r, 2000));
    let j;
    try { j = await api("GET", `/api/jobs/${jobId}`); } catch (e) { if (e.status === 401) return; continue; }
    if (j.status === "running" || j.status === "cancelling") {
      if (j.progressChars) S.gen.status = `Writing the menu… ${j.progressChars.toLocaleString()} characters so far`;
      S.gen.titles = j.titles || [];
      updateGenStatus();
      continue;
    }
    if (j.status === "done") {
      S.gen = {running:false, status:"Saved. Recipes are on this tab and the shopping list is ready.", titles:[], error:"", jobId:null};
      S.week = j.week; S.session = "tue";
      await loadPlan(j.week).catch(() => {});
    } else if (j.status === "cancelled") {
      S.gen = {running:false, status:"", titles:[], error:"", jobId:null};
    } else {
      S.gen = {running:false, status:"", titles:[], error:j.error || "Something went wrong while writing the menu. Try again.", jobId:null};
    }
    render();
    return;
  }
}

// ---------- events ----------
function syncWeek(){ S.week = mondayOf(S.date); S.session = sessionOf(S.date); }
async function showWeek(){ S.copyText = null; S.confirm = null; render(); if (!S.planLoaded[S.week]) { await loadPlan(S.week).catch(() => {}); render(); } }

app.addEventListener("click", async e => {
  const b = e.target.closest("[data-action]"); if (!b || b.disabled) return;
  const a = b.dataset.action, id = b.dataset.id;
  switch (a) {
    case "tab":
      S.tab = b.dataset.tab; S.confirm = null; S.copyText = null;
      try { history.replaceState(null, "", "#" + S.tab); } catch(_){}
      render(); window.scrollTo(0,0); refresh();
      break;
    case "account": S.showAccount = !S.showAccount; S.accountMsg = ""; render(); break;
    case "logout":
      try { await api("POST", "/api/logout"); } catch(_){}
      S.me = null; S.loginError = ""; stopPolling(); render();
      break;
    case "prev-visit": S.date = stepVisit(S.date,-1); syncWeek(); render(); break;
    case "next-visit": S.date = stepVisit(S.date,1); syncWeek(); render(); break;
    case "goto-next": S.date = nextVisit(today()); syncWeek(); render(); break;
    case "open-meals": S.tab = "meals"; syncWeek(); window.scrollTo(0,0); showWeek(); break;
    case "prev-week": S.week = addDays(S.week,-7); showWeek(); break;
    case "next-week": S.week = addDays(S.week,7); showWeek(); break;
    case "session": S.session = b.dataset.s; render(); break;
    case "toggle": {
      const v = visitOf(S.date), done = !v.done[id];
      if (done) v.done[id] = new Date().toISOString(); else delete v.done[id];
      render();
      mutate("PUT", `/api/visits/${S.date}/tasks/${encodeURIComponent(id)}`, {done}).catch(() => {});
      break;
    }
    case "toggle-extra": {
      const x = visitOf(S.date).extras[id]; if (!x) break;
      x.done = !x.done; render();
      mutate("PATCH", `/api/visits/${S.date}/extras/${id}`, {done:x.done}).catch(() => {});
      break;
    }
    case "remove-extra":
      delete visitOf(S.date).extras[id]; render();
      mutate("DELETE", `/api/visits/${S.date}/extras/${id}`).catch(() => {});
      break;
    case "fav": {
      const {s, slot} = b.dataset, r = S.plans[S.week].sessions[s].recipes[slot];
      r.fav = !r.fav; render();
      mutate("PATCH", `/api/plans/${S.week}/recipes/${s}/${slot}`, {fav:r.fav}).catch(() => {});
      break;
    }
    case "got": {
      const plan = S.plans[S.week]; plan.got ||= {};
      const got = !plan.got[id]; plan.got[id] = got; render();
      mutate("PATCH", `/api/plans/${S.week}/shopping/${encodeURIComponent(id)}`, {got}).catch(() => {});
      break;
    }
    case "shop-filter": S.shopFilter = b.dataset.f; S.copyText = null; render(); break;
    case "copy": {
      const txt = shoppingText();
      try { await navigator.clipboard.writeText(txt); S.copyText = null; toast("Shopping list copied"); }
      catch (_) { S.copyText = txt; render(); const box = document.getElementById("copyBox"); if (box) { box.focus(); box.select(); } }
      break;
    }
    case "gen-ask": S.confirm = "regen"; render(); break;
    case "gen-cancel": S.confirm = null; render(); break;
    case "gen-go": generate(); break;
    case "gen-stop": if (S.gen.jobId) api("POST", `/api/jobs/${S.gen.jobId}/cancel`).catch(() => {}); S.gen.status = "Stopping…"; updateGenStatus(); break;
    case "del-ask": S.confirm = "del:" + id; render(); break;
    case "del-no": S.confirm = null; render(); break;
    case "del-yes":
      S.confirm = null; delete S.tasks[id]; render();
      mutate("DELETE", `/api/tasks/${encodeURIComponent(id)}`, undefined, "Task deleted").catch(() => {});
      break;
    case "pw-ask": S.confirm = "pw:" + id; render(); break;
    case "pw-cancel": S.confirm = null; render(); break;
    case "pw-set": {
      const pw = (document.getElementById("rpw-" + id) || {}).value || "";
      try { await mutate("PATCH", `/api/users/${id}`, {password:pw}, "Password reset. They'll need to sign in again."); S.confirm = null; render(); } catch(_){}
      break;
    }
    case "user-active":
      mutate("PATCH", `/api/users/${id}`, {active: b.dataset.active === "1"}, b.dataset.active === "1" ? "Access restored" : "Access removed").catch(() => {});
      break;
  }
});

app.addEventListener("submit", async e => {
  const f = e.target.closest("form[data-form]"); if (!f) return;
  e.preventDefault();
  const kind = f.dataset.form;
  const val = id => (document.getElementById(id) || {}).value || "";
  if (kind === "login") {
    S.loginError = "";
    try {
      const j = await api("POST", "/api/login", {username: val("loginUser").trim(), password: val("loginPass")});
      S.me = j.me; S.isOwner = j.me.role === "homeowner";
      await refresh(); startPolling(); render();
    } catch (err) { S.loginError = err.message; render(); }
  }
  if (kind === "password") {
    try { await api("PUT", "/api/me/password", {current: val("pwCur"), new: val("pwNew")}); S.accountMsg = "Password changed."; }
    catch (err) { S.accountMsg = err.message; }
    render();
  }
  if (kind === "extra") {
    const el = document.getElementById("extraTitle"), title = el.value.trim(); if (!title) return;
    el.value = "";
    mutate("POST", `/api/visits/${S.date}/extras`, {title}, "Added to " + fmtShort(S.date)).catch(() => {});
  }
  if (kind === "add-task") {
    const title = val("newTitle").trim(); if (!title) { document.getElementById("newTitle").focus(); return; }
    document.getElementById("newTitle").value = "";
    mutate("POST", "/api/tasks", {title, area: val("newArea").trim() || "Whole house", freq: val("newFreq"), day: val("newDay")}, "Task added").catch(() => {});
  }
  if (kind === "add-user") {
    try {
      await mutate("POST", "/api/users", {displayName: val("nuName").trim(), username: val("nuUser").trim(), role: val("nuRole"), password: val("nuPass")}, "Person added");
      for (const id of ["nuName","nuUser","nuPass"]) document.getElementById(id).value = "";
    } catch(_){}
  }
  if (kind === "settings") {
    const n = id => Number(val(id));
    const t = id => val(id).trim();
    mutate("PUT", "/api/settings", {
      kcal:n("sKcal"), protein:n("sProt"),
      tue:{breakfast:n("s-tue-b"), main:n("s-tue-m"), dessert:n("s-tue-d"), covers:t("s-tue-c")},
      fri:{breakfast:n("s-fri-b"), main:n("s-fri-m"), dessert:n("s-fri-d"), covers:t("s-fri-c")},
      store:t("sStore"), likes:t("sLikes"), dislikes:t("sDislikes"), pantry:t("sPantry"),
    }, "Settings saved").catch(() => {});
  }
});

// inline task edits save on change
app.addEventListener("change", e => {
  const el = e.target, row = el.closest("[data-task]");
  if (row && el.dataset.field) {
    const id = row.dataset.task, field = el.dataset.field, v = el.value.trim();
    if (field === "title" && !v) { el.value = S.tasks[id].title; return; }
    if (S.tasks[id] && S.tasks[id][field] === v) return;
    S.tasks[id][field] = v;
    mutate("PATCH", `/api/tasks/${encodeURIComponent(id)}`, {[field]: v}, "Saved").catch(() => {});
  }
});

// visit note: save after a pause and on blur
let noteTimer = null;
function saveNote(){
  clearTimeout(noteTimer);
  const el = document.getElementById("visitNote"); if (!el) return;
  const v = el.value, visit = visitOf(S.date);
  if (v !== (visit.note || "")) { visit.note = v; mutate("PUT", `/api/visits/${S.date}/note`, {note:v}).catch(() => {}); }
}
app.addEventListener("input", e => { if (e.target.id === "visitNote") { clearTimeout(noteTimer); noteTimer = setTimeout(saveNote, 900); } });
app.addEventListener("focusout", e => { if (e.target.id === "visitNote") saveNote(); });

// ---------- boot ----------
(async () => {
  try {
    const j = await api("GET", "/api/me");
    S.me = j.me; S.isOwner = j.me.role === "homeowner";
  } catch (_) { S.me = null; }
  S.booted = true;
  if (S.me) { await refresh(); startPolling(); }
  render();
})();
})();

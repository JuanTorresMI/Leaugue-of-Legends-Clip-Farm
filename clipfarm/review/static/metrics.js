// Metrics dashboard: platform tabs, strong/weak tiers, growth chart, "what's working" insights.
const fmt = (n) => (n === null || n === undefined ? "—" : Number(n).toLocaleString());
const pct = (n) => (n === null || n === undefined ? "—" : `${Number(n).toFixed(0)}%`);
const hrs = (mins) => (mins ? `${(mins / 60).toLocaleString(undefined, { maximumFractionDigits: 1 })}h` : "—");
const when = (s) => (s ? s.replace("T", " ").slice(0, 16) : "—");
const esc = (s) => (s || "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const TIER_META = {
  strong: { label: "Strong", icon: "🔥" },
  average: { label: "Average", icon: "➖" },
  weak: { label: "Weak", icon: "⚠️" },
};

let STATE = { videos: [], sort: {}, dir: {} };

// ---------- summary cards ----------
function card(val, label, hint) {
  return `<div class="stat" ${hint ? `title="${esc(hint)}"` : ""}>
    <div class="stat-val">${val}</div><div class="stat-label">${label}</div></div>`;
}

function renderOverviewCards(s, tiers) {
  document.getElementById("ov-cards").innerHTML = [
    card(fmt(s.total_views), "Total views"),
    card(hrs(s.total_watch_time_minutes), "Watch time"),
    card(pct(s.avg_view_pct), "Avg retention", "Average % of each video watched"),
    card(fmt(s.subscribers_gained), "Subs gained", "Subscribers attributed to your videos"),
    card(fmt(s.tracked_videos), "Videos tracked"),
    card(`${tiers.strong}<span class="mini weak"> / ${tiers.weak}</span>`, "Strong / weak"),
  ].join("");
}

function renderPlatformCards(elId, agg, isYouTube) {
  if (!agg) {
    document.getElementById(elId).innerHTML = `<p class="muted">No data on this platform yet.</p>`;
    return;
  }
  const cards = [
    card(fmt(agg.total_views), "Views"),
    card(hrs(agg.total_watch_time_minutes), "Watch time"),
    card(pct(agg.avg_view_pct), "Avg retention"),
    card(fmt(agg.total_likes), "Likes"),
    card(fmt(agg.total_comments), "Comments"),
    card(fmt(agg.tracked_videos), "Videos"),
  ];
  if (isYouTube) cards.push(card(fmt(agg.subscribers_gained), "Subs gained"));
  document.getElementById(elId).innerHTML = cards.join("");
}

// ---------- growth chart (inline SVG, no deps) ----------
function renderGrowth(timeline) {
  const el = document.getElementById("growth-chart");
  const legend = document.getElementById("growth-legend");
  if (!timeline || timeline.length === 0) {
    el.innerHTML = `<p class="muted">No history yet — the growth line appears after a couple of polls.</p>`;
    legend.innerHTML = "";
    return;
  }
  const series = [
    { key: "youtube_views", color: "#ff4e45", name: "YouTube" },
    { key: "facebook_views", color: "#4e8cff", name: "Facebook" },
    { key: "total_views", color: "#6fdc8c", name: "Total" },
  ].filter((s) => timeline.some((p) => p[s.key] !== undefined));

  const W = 900, H = 220, PAD = 40;
  const xs = timeline.map((_, i) => (timeline.length === 1 ? 0.5 : i / (timeline.length - 1)));
  const maxV = Math.max(1, ...timeline.flatMap((p) => series.map((s) => p[s.key] || 0)));
  const px = (t) => PAD + t * (W - 2 * PAD);
  const py = (v) => H - PAD - (v / maxV) * (H - 2 * PAD);

  const gridVals = [0, 0.25, 0.5, 0.75, 1].map((f) => Math.round(maxV * f));
  const grid = gridVals
    .map((v) => `<line x1="${PAD}" y1="${py(v)}" x2="${W - PAD}" y2="${py(v)}" class="grid"/>
      <text x="${PAD - 6}" y="${py(v) + 4}" class="axis" text-anchor="end">${fmt(v)}</text>`)
    .join("");

  const paths = series
    .map((s) => {
      const pts = timeline.map((p, i) => `${px(xs[i])},${py(p[s.key] || 0)}`);
      const dots = timeline.length <= 12
        ? pts.map((pt) => `<circle cx="${pt.split(",")[0]}" cy="${pt.split(",")[1]}" r="2.5" fill="${s.color}"/>`).join("")
        : "";
      return `<polyline points="${pts.join(" ")}" fill="none" stroke="${s.color}" stroke-width="2"/>${dots}`;
    })
    .join("");

  const labelEvery = Math.ceil(timeline.length / 6);
  const xlabels = timeline
    .map((p, i) => (i % labelEvery === 0 || i === timeline.length - 1
      ? `<text x="${px(xs[i])}" y="${H - PAD + 16}" class="axis" text-anchor="middle">${p.date.slice(5)}</text>` : ""))
    .join("");

  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" class="growth" preserveAspectRatio="xMidYMid meet">
    ${grid}${paths}${xlabels}</svg>`;
  legend.innerHTML = series
    .map((s) => `<span class="leg"><span class="swatch" style="background:${s.color}"></span>${s.name}</span>`)
    .join("");
}

// ---------- horizontal bar lists ----------
function bars(el, items, labelKey, valKey, subFn) {
  if (!items.length) { el.innerHTML = `<p class="muted">Not enough data yet.</p>`; return; }
  const max = Math.max(...items.map((i) => i[valKey] || 0), 1);
  el.innerHTML = items
    .map((i) => {
      const w = Math.round(((i[valKey] || 0) / max) * 100);
      return `<div class="bar-row"><span class="bar-name">${esc(String(i[labelKey]))}</span>
        <span class="bar-track"><span class="bar-fill" style="width:${w}%"></span></span>
        <span class="bar-val">${subFn(i)}</span></div>`;
    })
    .join("");
}

function renderTierBar(tiers) {
  const total = tiers.strong + tiers.average + tiers.weak || 1;
  const seg = (n, cls, label) =>
    n ? `<span class="seg ${cls}" style="width:${(n / total) * 100}%" title="${n} ${label}">${n}</span>` : "";
  document.getElementById("tier-bar").innerHTML =
    `<div class="stacked">${seg(tiers.strong, "s-strong", "strong")}${seg(tiers.average, "s-avg", "average")}${seg(tiers.weak, "s-weak", "weak")}</div>
     <div class="stacked-key"><span>🔥 ${tiers.strong} strong</span><span>➖ ${tiers.average} avg</span><span>⚠️ ${tiers.weak} weak</span></div>`;
}

function leadList(el, items, valFn) {
  if (!items.length) { el.innerHTML = `<p class="muted">No data yet.</p>`; return; }
  el.innerHTML = items
    .map((v) => {
      const t = v.url ? `<a href="${v.url}" target="_blank" rel="noopener">${esc(v.title) || "(untitled)"}</a>` : esc(v.title) || "(untitled)";
      return `<div class="lead"><span class="lead-title">${t}</span><span class="lead-val">${valFn(v)}</span></div>`;
    })
    .join("");
}

function renderBestTime(hours, recommended) {
  const el = document.getElementById("besttime");
  if (!hours.length) {
    el.innerHTML = `<p class="muted">Not enough data yet — stats build up here as you post.</p>`;
    document.getElementById("apply-hours").hidden = true;
    return;
  }
  const max = Math.max(...hours.map((h) => h.avg_views), 1);
  el.innerHTML = hours
    .map((h) => {
      const w = Math.round((h.avg_views / max) * 100);
      const rec = recommended.includes(h.hour) ? " rec" : "";
      return `<div class="bar-row"><span class="bar-hour">${String(h.hour).padStart(2, "0")}:00</span>
        <span class="bar-track"><span class="bar-fill${rec}" style="width:${w}%"></span></span>
        <span class="bar-val">${fmt(h.avg_views)} avg · ${h.samples} post${h.samples === 1 ? "" : "s"}</span></div>`;
    })
    .join("");
  const btn = document.getElementById("apply-hours");
  btn.hidden = false;
  btn.title = `Set auto-post to ${recommended.map((h) => `${h}:00`).join(", ")}`;
}

// ---------- sortable video tables ----------
function tierCell(tier, ratio) {
  const m = TIER_META[tier] || TIER_META.average;
  const hint = ratio ? `${ratio}× the channel median` : "";
  return `<span class="tier ${tier}" title="${hint}">${m.icon} ${m.label}</span>`;
}

function renderTable(platform) {
  const table = document.querySelector(`.vtable[data-platform="${platform}"]`);
  const tbody = table.querySelector("tbody");
  let rows = STATE.videos.filter((v) => v.platform === platform);
  const empty = table.closest(".panel").querySelector(".empty");
  empty.hidden = rows.length > 0;

  const key = STATE.sort[platform] || "views";
  const dir = STATE.dir[platform] || "desc";
  rows = rows.slice().sort((a, b) => {
    let av = a[key], bv = b[key];
    if (key === "tier") { const o = { strong: 3, average: 2, weak: 1 }; av = o[a.tier]; bv = o[b.tier]; }
    if (typeof av === "string" || typeof bv === "string") {
      av = (av || "").toString().toLowerCase(); bv = (bv || "").toString().toLowerCase();
      return dir === "asc" ? av.localeCompare(bv) : bv.localeCompare(av);
    }
    av = av ?? -1; bv = bv ?? -1;
    return dir === "asc" ? av - bv : bv - av;
  });

  tbody.innerHTML = rows
    .map((v) => {
      const title = v.url ? `<a href="${v.url}" target="_blank" rel="noopener">${esc(v.title) || "(untitled)"}</a>` : esc(v.title) || "(untitled)";
      const common = `<td class="title-cell">${title}</td><td>${tierCell(v.tier, v.views_vs_median)}</td>
        <td>${v.kind === "full_game" ? "full game" : "clip"}</td><td class="num">${fmt(v.views)}</td>`;
      if (platform === "youtube") {
        return `<tr>${common}<td class="num">${pct(v.avg_view_pct)}</td><td class="num">${fmt(Math.round(v.watch_time_minutes || 0))}</td>
          <td class="num">${fmt(v.likes)}</td><td class="num">${fmt(v.comments)}</td>
          <td class="num">${v.subscribers_gained ? "+" + v.subscribers_gained : "—"}</td><td>${when(v.published_at)}</td></tr>`;
      }
      return `<tr>${common}<td class="num">${fmt(v.reel_plays)}</td><td class="num">${pct(v.avg_view_pct)}</td>
        <td class="num">${fmt(Math.round(v.watch_time_minutes || 0))}</td><td class="num">${fmt(v.likes)}</td>
        <td class="num">${fmt(v.comments)}</td><td>${when(v.published_at)}</td></tr>`;
    })
    .join("");

  table.querySelectorAll("th").forEach((th) => {
    th.classList.toggle("sorted", th.dataset.sort === key);
    th.classList.toggle("asc", th.dataset.sort === key && dir === "asc");
  });
}

function wireSorting() {
  document.querySelectorAll(".vtable").forEach((table) => {
    const platform = table.dataset.platform;
    table.querySelectorAll("th").forEach((th) => {
      th.addEventListener("click", () => {
        const k = th.dataset.sort;
        if (STATE.sort[platform] === k) {
          STATE.dir[platform] = STATE.dir[platform] === "asc" ? "desc" : "asc";
        } else {
          STATE.sort[platform] = k;
          STATE.dir[platform] = k === "title" || k === "published_at" ? "asc" : "desc";
        }
        renderTable(platform);
      });
    });
  });
}

// ---------- load + render ----------
async function loadMetrics() {
  try {
    const data = await (await fetch("/api/metrics")).json();
    STATE.videos = data.videos || [];
    const s = data.summary || {};
    const ins = data.insights || {};
    const byP = s.by_platform || {};

    renderOverviewCards(s, ins.tier_counts || { strong: 0, average: 0, weak: 0 });
    renderGrowth(data.timeline || []);
    bars(document.getElementById("champ-bars"), (ins.best_champions || []).slice(0, 6), "champion", "avg_views",
      (i) => `${fmt(i.avg_views)} avg · ${i.count} clip${i.count === 1 ? "" : "s"}${i.avg_view_pct != null ? " · " + pct(i.avg_view_pct) : ""}`);
    bars(document.getElementById("kind-bars"), ins.kind_performance || [], "kind", "avg_views",
      (i) => `${fmt(i.avg_views)} avg · ${i.count}`);
    renderTierBar(ins.tier_counts || { strong: 0, average: 0, weak: 0 });
    leadList(document.getElementById("retention-leaders"), ins.retention_leaders || [], (v) => `${pct(v.avg_view_pct)} · ${fmt(v.views)} views`);
    leadList(document.getElementById("conversion-leaders"), ins.conversion_leaders || [], (v) => `+${v.subscribers_gained} subs`);
    renderBestTime(data.hour_performance || [], data.recommended_hours || []);

    renderPlatformCards("yt-cards", byP.youtube, true);
    renderPlatformCards("fb-cards", byP.facebook, false);
    renderTable("youtube");
    renderTable("facebook");
  } catch (e) {
    /* non-critical; keep last render */
  }
}

// ---------- tabs ----------
function wireTabs() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === tab));
      const name = tab.dataset.tab;
      document.querySelectorAll(".tab-panel").forEach((p) => p.classList.toggle("active", p.dataset.panel === name));
    });
  });
}

document.getElementById("refresh-btn").addEventListener("click", async () => {
  const note = document.getElementById("refresh-note");
  note.textContent = "Fetching…";
  await fetch("/api/metrics/refresh", { method: "POST" });
  setTimeout(async () => {
    await loadMetrics();
    note.textContent = "Updated ✓";
    setTimeout(() => (note.textContent = ""), 2500);
  }, 4000);
});

document.getElementById("apply-hours").addEventListener("click", async () => {
  const note = document.getElementById("apply-note");
  const res = await fetch("/api/autopost/apply-recommended", { method: "POST" });
  if (res.ok) {
    const data = await res.json();
    note.textContent = `Auto-post hours set to ${data.post_hours.map((h) => `${h}:00`).join(", ")} ✓`;
  } else {
    note.textContent = (await res.json().catch(() => ({}))).detail || "Could not apply.";
  }
  setTimeout(() => (note.textContent = ""), 4000);
});

wireTabs();
wireSorting();
loadMetrics();
setInterval(loadMetrics, 30000);

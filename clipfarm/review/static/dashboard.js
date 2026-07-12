async function loadQuota() {
  const quotaDiv = document.getElementById("quota");
  try {
    const res = await fetch("/api/quota");
    const data = await res.json();
    quotaDiv.textContent = `YouTube uploads today: ${data.youtube.used}/${data.youtube.limit}`;
  } catch (e) {
    // non-critical
  }
}

async function loadAccounts() {
  const select = document.getElementById("account-select");
  try {
    const res = await fetch("/api/accounts");
    const data = await res.json();
    select.innerHTML = "";
    data.accounts.forEach((a) => {
      const opt = document.createElement("option");
      opt.value = a.riot_id;
      opt.textContent = `${a.riot_id} (${a.platform})`;
      opt.selected = a.riot_id === data.current;
      select.appendChild(opt);
    });
  } catch (e) {
    // non-critical
  }
}

async function switchAccount(riotId, platform, errorEl) {
  const res = await fetch("/api/accounts/switch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ riot_id: riotId, platform: platform || null }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const msg = `Could not switch: ${err.detail || res.statusText}`;
    if (errorEl) errorEl.textContent = msg;
    else alert(msg);
    loadAccounts(); // reset the dropdown to the real current account
    return false;
  }
  // New account is active -- refresh everything that depends on it.
  await Promise.all([loadAccounts(), loadRiotHealth(), loadRematchButton()]);
  loadQueue();
  return true;
}

function toggleAccountForm(show) {
  const form = document.getElementById("account-form");
  form.classList.toggle("hidden", !show);
  document.getElementById("account-error").textContent = "";
  if (show) document.getElementById("account-riot-id").focus();
}

document.getElementById("account-select").addEventListener("change", (e) => {
  switchAccount(e.target.value, null);
});

document.getElementById("account-add").addEventListener("click", () => toggleAccountForm(true));
document.getElementById("account-cancel").addEventListener("click", () => toggleAccountForm(false));

document.getElementById("account-save").addEventListener("click", async () => {
  const errorEl = document.getElementById("account-error");
  const riotId = document.getElementById("account-riot-id").value.trim();
  const platform = document.getElementById("account-server").value;
  if (!riotId.includes("#")) {
    errorEl.textContent = "Enter the Riot ID as Name#TAG (e.g. Faker#KR1).";
    return;
  }
  const btn = document.getElementById("account-save");
  btn.disabled = true;
  btn.textContent = "Checking...";
  const ok = await switchAccount(riotId, platform, errorEl);
  btn.disabled = false;
  btn.textContent = "Add & Switch";
  if (ok) toggleAccountForm(false);
});

document.getElementById("account-riot-id").addEventListener("keydown", (e) => {
  if (e.key === "Enter") document.getElementById("account-save").click();
});

async function loadRematchButton() {
  const btn = document.getElementById("rematch");
  try {
    const res = await fetch("/api/rematch/pending");
    const data = await res.json();
    if (data.pending > 0) {
      btn.textContent = `Re-match ${data.pending} awaiting`;
      btn.classList.remove("hidden");
    } else {
      btn.classList.add("hidden");
    }
  } catch (e) {
    // non-critical
  }
}

document.getElementById("rematch").addEventListener("click", async () => {
  const btn = document.getElementById("rematch");
  btn.disabled = true;
  btn.textContent = "Re-matching...";
  await fetch("/api/rematch", { method: "POST" });
  // Poll until the awaiting pool drains, or stalls for a while (items whose game truly
  // isn't in Riot's API yet stay awaiting -- that's expected, not an error).
  let last = Infinity;
  let stalledPolls = 0;
  const poll = async () => {
    const res = await fetch("/api/rematch/pending");
    const data = await res.json();
    btn.textContent = `Re-matching... ${data.pending} left`;
    stalledPolls = data.pending >= last ? stalledPolls + 1 : 0;
    if (data.pending === 0 || stalledPolls >= 4) {
      btn.disabled = false;
      loadRematchButton();
      loadQueue();
      return;
    }
    last = data.pending;
    setTimeout(poll, 5000);
  };
  setTimeout(poll, 5000);
});

async function loadRiotHealth() {
  const banner = document.getElementById("riot-banner");
  try {
    const res = await fetch("/api/riot-health");
    const data = await res.json();
    if (!data.ok) {
      banner.textContent = "Riot API key expired -- click to paste a new one";
      banner.classList.remove("hidden");
    } else {
      banner.classList.add("hidden");
    }
  } catch (e) {
    // dashboard should still work even if the health check itself fails
  }
}

function toggleKeyForm(show) {
  const form = document.getElementById("key-form");
  form.classList.toggle("hidden", !show);
  const err = document.getElementById("key-error");
  err.textContent = "";
  err.classList.remove("ok");
  if (show) document.getElementById("key-input").focus();
}

document.getElementById("key-btn").addEventListener("click", () => toggleKeyForm(true));
document.getElementById("key-cancel").addEventListener("click", () => toggleKeyForm(false));
document.getElementById("riot-banner").addEventListener("click", () => toggleKeyForm(true));

document.getElementById("key-save").addEventListener("click", async () => {
  const errorEl = document.getElementById("key-error");
  errorEl.classList.remove("ok");
  const key = document.getElementById("key-input").value.trim();
  if (!key) {
    errorEl.textContent = "Paste your Riot API key first.";
    return;
  }
  const btn = document.getElementById("key-save");
  btn.disabled = true;
  btn.textContent = "Checking...";
  try {
    const res = await fetch("/api/riot-key", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      errorEl.textContent = err.detail || res.statusText;
    } else {
      errorEl.textContent = "Key updated ✓";
      errorEl.classList.add("ok");
      document.getElementById("key-input").value = "";
      await Promise.all([loadRiotHealth(), loadRematchButton()]);
      loadQueue();
      setTimeout(() => toggleKeyForm(false), 900);
    }
  } finally {
    btn.disabled = false;
    btn.textContent = "Save";
  }
});

document.getElementById("key-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") document.getElementById("key-save").click();
});

async function loadFacebook() {
  try {
    const res = await fetch("/api/facebook");
    const data = await res.json();
    document.getElementById("fb-btn").textContent = data.enabled ? "Facebook ✓" : "Facebook";
    // Don't overwrite the inputs while the user has the form open and is editing them.
    if (document.getElementById("fb-form").classList.contains("hidden")) {
      document.getElementById("fb-page-id").value = data.page_id || "";
      document.getElementById("fb-enabled").checked = data.enabled;
    }
  } catch (e) {
    // non-critical
  }
}

function toggleFbForm(show) {
  const form = document.getElementById("fb-form");
  form.classList.toggle("hidden", !show);
  const err = document.getElementById("fb-error");
  err.textContent = "";
  err.classList.remove("ok");
}

document.getElementById("fb-btn").addEventListener("click", () => toggleFbForm(true));
document.getElementById("fb-cancel").addEventListener("click", () => toggleFbForm(false));

document.getElementById("fb-save").addEventListener("click", async () => {
  const errorEl = document.getElementById("fb-error");
  errorEl.classList.remove("ok");
  const pageId = document.getElementById("fb-page-id").value.trim();
  const token = document.getElementById("fb-token").value.trim();
  const enabled = document.getElementById("fb-enabled").checked;
  const btn = document.getElementById("fb-save");
  btn.disabled = true;
  btn.textContent = "Checking...";
  try {
    const res = await fetch("/api/facebook", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ page_id: pageId, access_token: token, enabled }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      errorEl.textContent = err.detail || res.statusText;
    } else {
      const data = await res.json();
      errorEl.textContent = data.page_name ? `Connected to ${data.page_name} ✓` : "Saved ✓";
      errorEl.classList.add("ok");
      document.getElementById("fb-token").value = "";
      await loadFacebook();
      loadQueue();
      setTimeout(() => toggleFbForm(false), 1100);
    }
  } finally {
    btn.disabled = false;
    btn.textContent = "Save";
  }
});

// ---- Auto-post ------------------------------------------------------------------------------
function renderAutopostStatus(data) {
  const st = data.status || {};
  const nextHour = st.next_slot_hour;
  const nextLabel = nextHour === null || nextHour === undefined ? "—" : `${nextHour}:00`;
  const bits = [
    `${st.posted_today ?? 0}/${st.daily_cap ?? 0} posted today`,
    `next slot ${nextLabel}`,
  ];
  document.getElementById("autopost-status").textContent = data.enabled
    ? `ON · ${bits.join(" · ")}`
    : "Off — clips wait for manual Approve.";
}

async function loadAutopost() {
  try {
    const res = await fetch("/api/autopost");
    const data = await res.json();
    const btn = document.getElementById("autopost-btn");
    btn.textContent = data.enabled ? "Auto-post: ON" : "Auto-post: OFF";
    btn.classList.toggle("on", data.enabled);
    // Don't stomp the inputs while the user is editing the open form.
    if (document.getElementById("autopost-form").classList.contains("hidden")) {
      document.getElementById("autopost-enabled").checked = data.enabled;
      document.getElementById("autopost-hours").value = (data.post_hours || []).join(", ");
      document.getElementById("autopost-min-streak").value = data.min_kill_streak;
      document.getElementById("autopost-cap").value = data.per_game_cap;
      document.getElementById("autopost-fg-enabled").checked = data.full_game_enabled;
      document.getElementById("autopost-fg-hours").value = (data.full_game_hours || []).join(", ");
    }
    renderAutopostStatus(data);
  } catch (e) {
    // non-critical
  }
}

function toggleAutopostForm(show) {
  document.getElementById("autopost-form").classList.toggle("hidden", !show);
}

document.getElementById("autopost-btn").addEventListener("click", () => toggleAutopostForm(true));
document.getElementById("autopost-cancel").addEventListener("click", () => toggleAutopostForm(false));

document.getElementById("autopost-save").addEventListener("click", async () => {
  const hours = document
    .getElementById("autopost-hours")
    .value.split(",")
    .map((h) => parseInt(h.trim(), 10))
    .filter((h) => Number.isInteger(h) && h >= 0 && h <= 23);
  const fgHours = document
    .getElementById("autopost-fg-hours")
    .value.split(",")
    .map((h) => parseInt(h.trim(), 10))
    .filter((h) => Number.isInteger(h) && h >= 0 && h <= 23);
  const body = {
    enabled: document.getElementById("autopost-enabled").checked,
    post_hours: hours,
    min_kill_streak: parseInt(document.getElementById("autopost-min-streak").value, 10) || 1,
    per_game_cap: parseInt(document.getElementById("autopost-cap").value, 10) || 3,
    full_game_enabled: document.getElementById("autopost-fg-enabled").checked,
    full_game_hours: fgHours,
  };
  const btn = document.getElementById("autopost-save");
  btn.disabled = true;
  btn.textContent = "Saving...";
  try {
    const res = await fetch("/api/autopost", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    renderAutopostStatus(data);
    await loadAutopost();
    setTimeout(() => toggleAutopostForm(false), 900);
  } finally {
    btn.disabled = false;
    btn.textContent = "Save";
  }
});

// Set an editable field from the server only if the user hasn't touched it (its current value
// still equals the last value we wrote) and it isn't focused -- so live polling fills in an
// awaiting-match item's generated title/tags but never wipes an edit in progress.
function setField(field, serverVal) {
  serverVal = serverVal || "";
  if (field.dataset.server === undefined) {
    field.value = serverVal;
    field.dataset.server = serverVal;
    return;
  }
  if (document.activeElement !== field && field.value === field.dataset.server) {
    field.value = serverVal;
    field.dataset.server = serverVal;
  }
}

function reconcilePlatforms(platformsDiv, targets) {
  const existing = {};
  platformsDiv.querySelectorAll("label").forEach((l) => {
    existing[l.querySelector("input").value] = l;
  });
  const want = new Set((targets || []).map((t) => t.platform));
  Object.keys(existing).forEach((p) => {
    if (!want.has(p)) existing[p].remove();
  });
  (targets || []).forEach((t) => {
    if (existing[t.platform]) return; // leave the user's checkbox state alone
    const label = document.createElement("label");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = t.platform;
    checkbox.checked = t.selected;
    label.appendChild(checkbox);
    label.append(t.platform);
    platformsDiv.appendChild(label);
  });
}

// Update the non-structural parts of an existing card in place (no full re-render, so edits,
// focus, checkbox state, and the <video> position are all preserved).
function applyCardState(card, item) {
  const thumb = card.querySelector("img.thumb");
  const sig = `${item.champion}|${item.kill_streak}|${item.thumbnail_url || ""}`;
  if (item.thumbnail_url) {
    thumb.style.display = "";
    if (card.dataset.thumbSig === undefined) {
      thumb.src = item.thumbnail_url;
    } else if (card.dataset.thumbSig !== sig) {
      thumb.src = `${item.thumbnail_url}?t=${Date.now()}`; // file changed -> bust cache
    }
  }
  card.dataset.thumbSig = sig;

  setField(card.querySelector(".title"), item.draft_title);
  setField(card.querySelector(".description"), item.draft_description);
  setField(card.querySelector(".hashtags"), (item.hashtags || []).join(" "));
  setField(card.querySelector(".tags"), (item.tags || []).join(", "));

  const metaBits = [
    item.kind, item.queue_type, item.champion,
    item.opponent_champion ? `vs ${item.opponent_champion}` : null,
    item.role, item.rank, item.kda,
    item.kill_streak ? `${item.kill_streak}-kill` : null,
    item.patch ? `patch ${item.patch}` : null,
  ].filter(Boolean);
  card.querySelector(".meta").textContent = metaBits.join(" | ") || "No Riot match found";

  // Duplicate guard: this exact content already went to these platforms (as some earlier
  // file/upload). Approving again is blocked server-side; warn here so it's not a surprise.
  const dupeEl = card.querySelector(".dupe-warning");
  const dupes = item.already_published_on || [];
  if (dupes.length) {
    dupeEl.textContent = `⚠ Already published to ${dupes.join(", ")} — re-posting is blocked to protect the channel.`;
    dupeEl.hidden = false;
  } else {
    dupeEl.hidden = true;
  }

  reconcilePlatforms(card.querySelector(".platforms"), item.publish_targets);
  renderTargetStatus(card, item);

  const statusDiv = card.querySelector(".status");
  statusDiv.textContent = item.error_message ? `${item.status}: ${item.error_message}` : item.status;
  statusDiv.className = `status ${item.status}`;

  // Lock Approve while any selected platform is mid-upload (the backend also guards
  // against double-publish; this is just honest UI).
  const uploading = (item.publish_targets || []).some((t) => t.selected && t.status === "uploading");
  const approveBtn = card.querySelector(".approve");
  approveBtn.disabled = uploading;
  approveBtn.textContent = uploading ? "Publishing..." : "Approve";
}

// Per-platform chips: youtube/facebook state at a glance, tooltip with the full error,
// and a Retry button on failures that re-attempts just that platform.
function renderTargetStatus(card, item) {
  const wrap = card.querySelector(".target-status");
  wrap.innerHTML = "";
  (item.publish_targets || []).forEach((t) => {
    if (t.status === "pending") return; // nothing has happened yet; don't clutter the card
    const chip = document.createElement("span");
    chip.className = `chip ${t.status}`;
    chip.textContent = `${t.platform}: ${t.status}`;
    if (t.error) chip.title = t.error;
    wrap.appendChild(chip);
    if (t.status === "failed") {
      const retry = document.createElement("button");
      retry.className = "retry";
      retry.textContent = "Retry";
      retry.addEventListener("click", async () => {
        retry.disabled = true;
        retry.textContent = "Retrying...";
        await fetch(`/api/queue/${item.id}/retry/${t.platform}`, { method: "POST" });
        loadQueue();
      });
      wrap.appendChild(retry);
    }
  });
}

function renderCard(item, template) {
  const card = template.content.cloneNode(true).querySelector(".card");
  card.dataset.id = item.id;
  card.querySelector("video").src = item.video_url;

  applyCardState(card, item);

  card.querySelector(".regen").addEventListener("click", async () => {
    const res = await fetch(`/api/queue/${item.id}/regenerate-thumbnail`, { method: "POST" });
    const data = await res.json();
    card.querySelector("img.thumb").src = data.thumbnail_url; // response already cache-busted
    card.dataset.thumbSig = "regen-" + Date.now();
  });

  card.querySelector(".approve").addEventListener("click", async () => {
    const title = card.querySelector(".title");
    const description = card.querySelector(".description");
    const hashtags = card.querySelector(".hashtags");
    const tags = card.querySelector(".tags");
    const platformsDiv = card.querySelector(".platforms");
    const statusDiv = card.querySelector(".status");
    await fetch(`/api/queue/${item.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        draft_title: title.value,
        draft_description: description.value,
        hashtags: hashtags.value.split(/\s+/).filter(Boolean),
        tags: tags.value.split(",").map((t) => t.trim()).filter(Boolean),
      }),
    });
    // Reflect the saved edits as the new "server" baseline so live polling doesn't revert them.
    [title, description, hashtags, tags].forEach((f) => (f.dataset.server = f.value));
    const selected = Array.from(platformsDiv.querySelectorAll("input:checked")).map((c) => c.value);
    const approveBtn = card.querySelector(".approve");
    approveBtn.disabled = true;
    approveBtn.textContent = "Publishing...";
    await fetch(`/api/queue/${item.id}/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ platforms: selected }),
    });
    statusDiv.textContent = selected.length ? "publishing..." : "approved (nothing selected)";
    statusDiv.className = "status";
    // The live queue poll takes over from here: chips flip pending -> uploading -> published,
    // the Approve button unlocks when done, and quota refreshes on its own cadence.
    setTimeout(loadQueue, 800);
  });

  return card;
}

let _reconciling = false;
async function loadQueue() {
  if (_reconciling) return;
  _reconciling = true;
  try {
    const queue = document.getElementById("queue");
    const template = document.getElementById("card-template");
    const res = await fetch("/api/queue");
    const items = await res.json();

    const existing = {};
    queue.querySelectorAll(".card").forEach((c) => (existing[c.dataset.id] = c));
    const incoming = new Set(items.map((i) => String(i.id)));
    // Remove cards for items no longer in the queue.
    Object.keys(existing).forEach((id) => {
      if (!incoming.has(id)) existing[id].remove();
    });
    // Update existing cards in place; insert new ones in their sorted slot without moving others.
    let prev = null;
    items.forEach((item) => {
      let card = existing[String(item.id)];
      if (card) {
        applyCardState(card, item);
      } else {
        card = renderCard(item, template);
        if (prev) prev.after(card);
        else queue.prepend(card);
      }
      prev = card;
    });
  } catch (e) {
    // transient fetch failure -- keep the current view, try again next tick
  } finally {
    _reconciling = false;
  }
}

// Live updates: no manual refresh needed. Queue reconciles frequently; the header bits (which
// hit lighter endpoints) refresh a bit slower.
function startLivePolling() {
  setInterval(loadQueue, 4000);
  setInterval(() => {
    loadQuota();
    loadRematchButton();
    loadRiotHealth();
    loadFacebook();
    loadAutopost();
  }, 12000);
}

loadAccounts();
loadRiotHealth();
loadQuota();
loadRematchButton();
loadFacebook();
loadAutopost();
loadQueue();
startLivePolling();

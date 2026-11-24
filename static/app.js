"use strict";

const $ = (sel) => document.querySelector(sel);
const euro = (n) => (Number(n) < 0 ? "-€" : "€") + Math.abs(Number(n || 0)).toFixed(2);
const BADGES = {
  alias: "known",
  fuzzy_alias: "known",
  catalogue: "matched",
  new: "new",
  discount: "discount",
};

const state = { config: null, draft: null, tab: "scan" };

// ---------- API ----------

async function api(path, options = {}) {
  const res = await fetch(path, { credentials: "same-origin", ...options });
  if (res.status === 401) {
    showLogin();
    throw new Error("Please log in");
  }
  const body = res.headers.get("content-type")?.includes("json") ? await res.json() : null;
  if (!res.ok) {
    const detail = body?.detail;
    throw new Error(typeof detail === "string" ? detail : "Something went wrong");
  }
  return body;
}

const postJson = (path, data) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });

function toast(message) {
  const el = $("#toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (el.hidden = true), 3500);
}

// ---------- login ----------

function showLogin() {
  $("#app-view").hidden = true;
  $("#login-view").hidden = false;
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await postJson("/api/login", { password: $("#password").value });
    $("#login-error").hidden = true;
    await start();
  } catch (err) {
    $("#login-error").textContent = err.message;
    $("#login-error").hidden = false;
  }
});

// ---------- start ----------

async function loadConfig() {
  state.config = await api("/api/config");
  $("#store-list").innerHTML = state.config.stores.map((s) => `<option value="${esc(s)}">`).join("");
  $("#product-list").innerHTML = state.config.products.map((p) => `<option value="${esc(p.name)}">`).join("");
  const last = localStorageGet("paidBy");
  $("#r-paid-by").innerHTML = state.config.members
    .map((m) => `<option ${m === last ? "selected" : ""}>${esc(m)}</option>`)
    .join("");
}

async function start() {
  await loadConfig();
  $("#login-view").hidden = true;
  $("#app-view").hidden = false;
  if (!$("#month").value) $("#month").value = new Date().toISOString().slice(0, 7);
  showTab(state.tab);
}

// ---------- tabs ----------

document.querySelectorAll(".tabbar button").forEach((btn) =>
  btn.addEventListener("click", () => showTab(btn.dataset.tab))
);
$("#month").addEventListener("change", () => showTab(state.tab));

function showTab(tab) {
  state.tab = tab;
  document.querySelectorAll(".tabbar button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  ["scan", "receipts", "summary"].forEach((t) => ($(`#tab-${t}`).hidden = t !== tab));
  $("#month").style.visibility = tab === "scan" ? "hidden" : "visible";
  if (tab === "receipts") loadReceipts().catch((e) => toast(e.message));
  if (tab === "summary") loadSummary().catch((e) => toast(e.message));
}

// ---------- scan ----------

$("#photo").addEventListener("change", onPhoto);
$("#photo-gallery").addEventListener("change", onPhoto);

async function onPhoto(e) {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  setScanStep("loading");
  try {
    const blob = await shrinkImage(file);
    const form = new FormData();
    form.append("image", blob, "receipt.jpg");
    const draft = await api("/api/receipts/scan", { method: "POST", body: form });
    renderReview(draft);
    setScanStep("review");
  } catch (err) {
    toast(err.message);
    setScanStep("start");
  }
}

function setScanStep(step) {
  $("#scan-start").hidden = step !== "start";
  $("#scan-loading").hidden = step !== "loading";
  $("#review").hidden = step !== "review";
}

// Resize big phone photos before upload (faster, cheaper, enough for OCR).
async function shrinkImage(file, maxSide = 2000) {
  try {
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, maxSide / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    return await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.85));
  } catch {
    return file; // browser can't decode it; let the server try
  }
}

function renderReview(draft) {
  state.draft = draft;
  $("#r-store").value = draft.store;
  $("#r-date").value = draft.purchased_on || new Date().toISOString().slice(0, 10);
  $("#r-total").value = draft.total.toFixed(2);
  const dup = $("#duplicate-warning");
  dup.hidden = !draft.possible_duplicate_of;
  dup.textContent = "This looks like a receipt that was already saved (same store, date and total).";
  const missing = [
    draft.total_missing && "the total (using the sum of the lines)",
    draft.date_missing && "the date (using today)",
  ].filter(Boolean);
  const miss = $("#missing-warning");
  miss.hidden = missing.length === 0;
  miss.textContent = `Couldn't see ${missing.join(" or ")} on the photo. Check it, or retake the photo with the whole receipt.`;
  $("#r-items").innerHTML = "";
  draft.items.forEach(addItemRow);
  updateSumCheck();
}

function addItemRow(item) {
  const li = $("#item-template").content.firstElementChild.cloneNode(true);
  const source = item.match_source || "new";
  li.classList.toggle("discount", item.is_discount);
  li.dataset.raw = item.raw_name;
  li.dataset.discount = item.is_discount ? "1" : "";
  li.dataset.quantity = item.quantity ?? 1;
  li.dataset.generic = item.generic_name || "";
  li.querySelector(".raw").textContent = item.raw_name;
  const badge = li.querySelector(".badge");
  badge.textContent = BADGES[source];
  badge.className = `badge ${source}`;

  const category = li.querySelector(".category");
  category.innerHTML = state.config.categories.map((c) => `<option>${esc(c)}</option>`).join("");
  category.value = item.category || "Other";

  const product = li.querySelector(".product");
  product.value = item.product_name || "";
  product.addEventListener("change", () => {
    const known = state.config.products.find((p) => p.name.toLowerCase() === product.value.trim().toLowerCase());
    if (known) {
      category.value = known.category;
      li.dataset.generic = known.generic_name;
    } else {
      li.dataset.generic = product.value.trim();
    }
  });

  const amount = li.querySelector(".amount");
  amount.value = Number(item.line_total).toFixed(2);
  amount.addEventListener("input", updateSumCheck);
  li.querySelector(".remove").addEventListener("click", () => {
    li.remove();
    updateSumCheck();
  });
  $("#r-items").appendChild(li);
}

$("#add-item").addEventListener("click", () => {
  const name = prompt("Line text (as on the receipt):");
  if (!name) return;
  addItemRow({ raw_name: name, product_name: name, line_total: 0, is_discount: false, match_source: "new" });
});

$("#r-total").addEventListener("input", updateSumCheck);

function updateSumCheck() {
  const sum = [...document.querySelectorAll("#r-items .amount")].reduce((acc, el) => acc + Number(el.value || 0), 0);
  const total = Number($("#r-total").value || 0);
  const diff = Math.abs(sum - total);
  const el = $("#sum-check");
  el.textContent =
    diff < 0.05 ? `✓ Lines add up to ${euro(sum)}` : `⚠ Lines add up to ${euro(sum)}, total is ${euro(total)}`;
  el.className = diff < 0.05 ? "muted" : "warning";
}

$("#cancel-review").addEventListener("click", () => setScanStep("start"));

$("#review").addEventListener("submit", async (e) => {
  e.preventDefault();
  const items = [...document.querySelectorAll("#r-items .item")].map((li) => ({
    raw_name: li.dataset.raw,
    quantity: Number(li.dataset.quantity || 1),
    line_total: Number(li.querySelector(".amount").value || 0),
    is_discount: li.dataset.discount === "1",
    product_name: li.querySelector(".product").value.trim() || null,
    generic_name: li.dataset.generic || null,
    category: li.querySelector(".category").value,
  }));
  if (!items.length) return toast("Add at least one line");
  const sum = items.reduce((acc, i) => acc + i.line_total, 0);
  const total = Number($("#r-total").value || 0);
  if (Math.abs(sum - total) >= 0.05 &&
      !confirm(`Lines add up to ${euro(sum)} but the total is ${euro(total)}. Save anyway?`)) return;
  const paidBy = $("#r-paid-by").value;
  try {
    await postJson("/api/receipts", {
      store: $("#r-store").value.trim(),
      purchased_on: $("#r-date").value,
      total: Number($("#r-total").value),
      paid_by: paidBy,
      items,
    });
    localStorageSet("paidBy", paidBy);
    toast("Receipt saved ✓");
    setScanStep("start");
    await loadConfig(); // new products/stores for autocomplete
  } catch (err) {
    toast(err.message);
  }
});

// ---------- receipts ----------

async function loadReceipts() {
  const receipts = await api(`/api/receipts?month=${$("#month").value}`);
  $("#no-receipts").hidden = receipts.length > 0;
  $("#receipt-list").innerHTML = receipts
    .map(
      (r) => `
      <li><details>
        <summary>
          <div><strong>${esc(r.store)}</strong><div class="meta">${fmtDate(r.purchased_on)} · ${esc(r.paid_by)}</div></div>
          <strong>${euro(r.total)}</strong>
        </summary>
        <table>${r.items
          .map((i) => `<tr><td>${esc(i.product)}<div class="meta">${esc(i.category)}</div></td><td class="num">${euro(i.line_total)}</td></tr>`)
          .join("")}</table>
        <button class="delete" data-id="${r.id}">Delete receipt</button>
      </details></li>`
    )
    .join("");
  document.querySelectorAll("#receipt-list .delete").forEach((btn) =>
    btn.addEventListener("click", async () => {
      if (!confirm("Delete this receipt?")) return;
      try {
        await api(`/api/receipts/${btn.dataset.id}`, { method: "DELETE" });
        toast("Receipt deleted");
        loadReceipts();
      } catch (err) {
        toast(err.message);
      }
    })
  );
}

// ---------- summary ----------

async function loadSummary() {
  const s = await api(`/api/summary?month=${$("#month").value}`);
  $("#s-total").textContent = euro(s.total);
  $("#s-count").textContent = s.receipts;
  $("#s-avg").textContent = euro(s.monthly_average);
  renderBars("#s-person", s.by_person);
  renderBars("#s-store", s.by_store);
  renderBars("#s-category", s.by_category);
  renderBars("#s-products", s.top_products);
  renderBars("#s-monthly", s.monthly.map((m) => ({ label: m.month, amount: m.amount })));
}

function renderBars(sel, rows) {
  const max = Math.max(...rows.map((r) => Math.abs(Number(r.amount))), 0.01);
  $(sel).innerHTML = rows.length
    ? rows
        .map(
          (r) => `<li><div class="fill" style="width:${(Math.abs(Number(r.amount)) / max) * 100}%"></div>
            <span>${esc(r.label)}</span><span>${euro(r.amount)}</span></li>`
        )
        .join("")
    : `<li class="muted">No data</li>`;
}

// ---------- helpers ----------

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function fmtDate(iso) {
  return new Date(iso + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function localStorageGet(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function localStorageSet(key, value) {
  try { localStorage.setItem(key, value); } catch { /* private mode */ }
}

start().catch(() => showLogin());

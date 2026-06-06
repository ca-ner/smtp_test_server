"use strict";

const $ = (sel) => document.querySelector(sel);
const emailList = $("#emailList");
const viewer = $("#viewer");
const addressInput = $("#addressInput");

let activeId = null;
let pollTimer = null;

// ---- helpers -----------------------------------------------------------
function esc(s) {
  return (s || "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function timeAgo(epochSeconds) {
  const diff = Date.now() / 1000 - epochSeconds;
  if (diff < 60) return "just now";
  if (diff < 3600) return Math.floor(diff / 60) + "m ago";
  if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
  return Math.floor(diff / 86400) + "d ago";
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error((await res.text()) || res.statusText);
  return res.status === 204 ? null : res.json();
}

// ---- connection info ---------------------------------------------------
async function loadInfo() {
  try {
    const info = await api("/api/info");
    $("#smtpHost").textContent = info.smtp_host;
    $("#smtpPort").textContent = info.smtp_port;
    $("#smtpAuth").textContent = info.auth;
    $("#retentionNote").textContent =
      `Showing the latest ${info.max_per_address} emails per address. ` +
      `Captured emails are deleted after ${info.retention_hours} hours.`;
  } catch (e) {
    console.error(e);
  }
}

// ---- list --------------------------------------------------------------
function renderList(emails) {
  if (!emails.length) {
    emailList.innerHTML = '<li class="empty">No emails found. Send one below, or configure your app to use the settings above.</li>';
    return;
  }
  emailList.innerHTML = emails.map((e) => `
    <li class="email-item ${e.id === activeId ? "active" : ""}" data-id="${e.id}">
      <div class="row1">
        <span class="subject">${esc(e.subject)}</span>
        <span class="time">${timeAgo(e.received_at)}</span>
      </div>
      <div class="addrs">${esc(e.from)} → ${esc((e.to || []).join(", "))}</div>
      <div class="preview">${esc(e.preview)}</div>
    </li>`).join("");
  emailList.querySelectorAll(".email-item").forEach((li) => {
    li.addEventListener("click", () => openEmail(li.dataset.id));
  });
}

async function loadList() {
  try {
    const data = await api("/api/emails?address=" + encodeURIComponent(addressInput.value.trim()));
    renderList(data.emails);
  } catch (e) {
    console.error(e);
  }
}

// ---- viewer ------------------------------------------------------------
async function openEmail(id) {
  activeId = id;
  emailList.querySelectorAll(".email-item").forEach((li) =>
    li.classList.toggle("active", li.dataset.id === id));
  try {
    const e = await api("/api/emails/" + id);
    renderViewer(e);
  } catch (err) {
    viewer.innerHTML = `<p class="muted">Could not load email (it may have expired).</p>`;
  }
}

function renderViewer(e) {
  viewer.classList.remove("empty-viewer");
  const tabs = [["text", "Text"]];
  if (e.html) tabs.splice(0, 0, ["html", "HTML"]);
  tabs.push(["raw", "Raw"]);
  const def = e.html ? "html" : "text";

  viewer.innerHTML = `
    <h3>${esc(e.subject)}</h3>
    <div class="meta"><strong>From:</strong> ${esc(e.from)}</div>
    <div class="meta"><strong>To:</strong> ${esc((e.to || []).join(", "))}</div>
    <div class="meta"><strong>Received:</strong> ${new Date(e.received_at * 1000).toLocaleString()}</div>
    <div class="tabs">${tabs.map(([k, lbl]) =>
      `<button class="tab ${k === def ? "active" : ""}" data-tab="${k}">${lbl}</button>`).join("")}</div>
    <div id="bodyArea"></div>`;

  const bodyArea = $("#bodyArea");
  const show = (kind) => {
    viewer.querySelectorAll(".tab").forEach((t) =>
      t.classList.toggle("active", t.dataset.tab === kind));
    if (kind === "html") {
      const f = document.createElement("iframe");
      f.className = "body-html";
      f.setAttribute("sandbox", "");
      f.srcdoc = e.html;
      bodyArea.innerHTML = "";
      bodyArea.appendChild(f);
    } else if (kind === "raw") {
      bodyArea.innerHTML = `<pre class="body-text body-raw">${esc(e.raw)}</pre>`;
    } else {
      bodyArea.innerHTML = `<div class="body-text">${esc(e.text) || '<span class="muted">(no plain-text body)</span>'}</div>`;
    }
  };
  viewer.querySelectorAll(".tab").forEach((t) =>
    t.addEventListener("click", () => show(t.dataset.tab)));
  show(def);
}

// ---- actions -----------------------------------------------------------
$("#lookupForm").addEventListener("submit", (ev) => { ev.preventDefault(); loadList(); });
$("#refreshBtn").addEventListener("click", loadList);

$("#clearBtn").addEventListener("click", async () => {
  if (!confirm("Delete all captured emails?")) return;
  await api("/api/emails", { method: "DELETE" });
  activeId = null;
  viewer.classList.add("empty-viewer");
  viewer.innerHTML = '<p class="muted">Select an email to read it here.</p>';
  loadList();
});

$("#sendForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const status = $("#sendStatus");
  const fd = new FormData(ev.target);
  const payload = {
    from: fd.get("from"), to: fd.get("to"),
    subject: fd.get("subject"), body: fd.get("body"),
  };
  status.textContent = "Sending…";
  status.className = "send-status";
  try {
    await api("/api/test-send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    status.textContent = "✓ Sent — captured below.";
    status.className = "send-status ok";
    setTimeout(loadList, 400);
  } catch (e) {
    status.textContent = "✗ " + e.message;
    status.className = "send-status err";
  }
});

// copy buttons
document.querySelectorAll(".copy").forEach((btn) => {
  btn.addEventListener("click", () => {
    const text = $("#" + btn.dataset.copy).textContent;
    navigator.clipboard.writeText(text).then(() => {
      const old = btn.textContent;
      btn.textContent = "Copied!";
      setTimeout(() => (btn.textContent = old), 1200);
    });
  });
});

// theme toggle (overrides prefers-color-scheme)
$("#themeToggle").addEventListener("click", () => {
  const cur = document.documentElement.getAttribute("data-theme");
  const next = cur === "dark" ? "light" : cur === "light" ? "" : "dark";
  if (next) document.documentElement.setAttribute("data-theme", next);
  else document.documentElement.removeAttribute("data-theme");
});

// ---- init --------------------------------------------------------------
loadInfo();
loadList();
pollTimer = setInterval(loadList, 5000);

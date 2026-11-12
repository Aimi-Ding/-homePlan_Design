const $ = (id) => document.getElementById(id);

const STAGE_LABEL = {
  queued: "排队中",
  probe: "检查图纸",
  geometry: "识别墙体",
  spec: "补全门窗与房间",
  build: "生成设计器",
  check: "自查",
  finalize: "打包交付文件",
  done: "已完成",
  failed: "生成失败",
  rejected: "未通过检查",
};

const STATUS_LABEL = {
  queued: "排队中",
  running: "生成中",
  succeeded: "已完成",
  partial: "完成（有未解决项）",
  failed: "失败",
  rejected: "未通过检查",
};

let cfg = null;
let me = null;
let currentId = null;
let stream = null;
let pollTimer = null;
let lastEventId = 0;
let codeTimer = null;
let pendingDownload = null;

const VID = (() => {
  let v = localStorage.getItem("hpd_vid");
  if (!v) {
    v = crypto.randomUUID ? crypto.randomUUID().replace(/-/g, "")
      : `${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;
    localStorage.setItem("hpd_vid", v);
  }
  return v;
})();

function toast(msg, isError = false) {
  const el = $("toast");
  const icon = isError ? "!" : "✓";
  el.innerHTML = `<span class="toast-icon">${icon}</span><span>${esc(msg)}</span>`;
  el.className = "toast" + (isError ? " error" : "");
  el.hidden = false;
  // 重新触发入场动画（连续提示时也能看到变化）
  el.style.animation = "none";
  void el.offsetWidth;
  el.style.animation = "";
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (el.hidden = true), isError ? 5000 : 3800);
}

async function api(path, opts) {
  const headers = new Headers((opts && opts.headers) || {});
  headers.set("X-Visitor-Id", VID);
  const res = await fetch(path, { ...(opts || {}), headers });
  if (res.status === 401) {
    const err = new Error("请先登录");
    err.unauthorized = true;
    throw err;
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = data.detail || detail;
    } catch (_) {}
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

/* ---------- 启动 ---------- */
async function init() {
  try {
    cfg = await api("/api/config");
  } catch (e) {
    cfg = { mode: "rule", llmEnabled: false };
  }
  renderModeBadge();
  bindUpload();
  bindAuth();
  bindLead();
  $("newBtn").onclick = () => { location.hash = "#/"; };
  $("refreshBtn").onclick = () => loadProjects();
  $("logoutBtn").onclick = doLogout;
  $("loginBtn").onclick = () => openLogin();
  window.addEventListener("hashchange", route);

  let user = null;
  try {
    user = (await api("/api/auth/me")).user;
  } catch (_) {}
  setUser(user);
  route();
}

function renderModeBadge() {
  const badge = $("modeBadge");
  if (cfg.llmEnabled) {
    badge.textContent = `模型已启用 · ${cfg.model || ""}`;
    badge.className = "badge";
  } else {
    badge.textContent = "未配置模型 · 仅几何初稿";
    badge.className = "badge rule";
  }
  $("vendorInfo").textContent = cfg.vendorCommit ? `上游技能版本 ${cfg.vendorCommit}` : "";
}

/* 首页始终可见；未登录只影响「方案库」和生成动作 */
function setUser(user) {
  me = user || null;
  const logged = !!me;
  $("userChip").hidden = !logged;
  $("logoutBtn").hidden = !logged;
  $("loginBtn").hidden = logged;
  if (logged) $("userChip").textContent = me.phone;
  if (logged) {
    loadProjects();
  } else {
    resetSendCode();
    $("projectList").innerHTML = '<div class="empty">暂未登录<br>方案只保存在当前浏览器</div>';
    loadProjects();
  }
}

let pendingAction = null;

function openLogin(action) {
  pendingAction = typeof action === "function" ? action : null;
  $("authModal").hidden = false;
  authHint("");
  setTimeout(() => $("phoneInput")?.focus(), 60);
}

function resetSendCode() {
  if (codeTimer) { clearInterval(codeTimer); codeTimer = null; }
  const send = $("sendCodeBtn");
  if (send) { send.disabled = false; send.textContent = "获取验证码"; }
}

function closeLogin() {
  $("authModal").hidden = true;
  pendingAction = null;
  resetSendCode();
}

function authHint(text, kind = "") {
  const el = $("authHint");
  el.textContent = text;
  el.className = "auth-hint" + (kind ? ` ${kind}` : "");
}

function bindAuth() {
  $("sendCodeBtn").onclick = async () => {
    const phone = $("phoneInput").value.trim();
    if (!/^1[3-9]\d{9}$/.test(phone)) {
      authHint("请输入 11 位手机号", "err");
      return;
    }
    const btn = $("sendCodeBtn");
    btn.disabled = true;
    btn.textContent = "发送中…";
    try {
      const data = await api("/api/auth/send-code", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ phone }),
      });
      if (data.debugCode) {
        $("codeInput").value = data.debugCode;
        authHint(`本地调试验证码：${data.debugCode}（已自动填入）`, "ok");
      } else {
        authHint(`验证码已发送至 ${data.phone}，5 分钟内有效`, "ok");
      }
      let left = 60;
      btn.textContent = `${left}s`;
      clearInterval(codeTimer);
      codeTimer = setInterval(() => {
        left -= 1;
        btn.textContent = left > 0 ? `${left}s` : "获取验证码";
        if (left <= 0) {
          clearInterval(codeTimer);
          codeTimer = null;
          btn.disabled = false;
        }
      }, 1000);
    } catch (e) {
      authHint(e.message, "err");
      resetSendCode();
    }
  };

  $("loginSubmitBtn").onclick = doLogin;
  $("authClose").onclick = closeLogin;
  $("authMask").onclick = closeLogin;
  $("codeInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") doLogin();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (!$("authModal").hidden) closeLogin();
    if (!$("leadModal").hidden) closeLead();
  });
}

async function doLogin() {
  const phone = $("phoneInput").value.trim();
  const code = $("codeInput").value.trim();
  if (!/^1[3-9]\d{9}$/.test(phone)) { authHint("请输入 11 位手机号", "err"); return; }
  if (!/^\d{6}$/.test(code)) { authHint("请输入 6 位验证码", "err"); return; }

  const btn = $("loginSubmitBtn");
  btn.disabled = true;
  try {
    const data = await api("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ phone, code }),
    });
    const next = pendingAction;
    pendingAction = null;
    authHint("");
    $("codeInput").value = "";
    $("authModal").hidden = true;
    setUser(data.user);
    toast(`已登录：${data.user.phone}`);
    if (next) setTimeout(next, 30);
  } catch (e) {
    authHint(e.message, "err");
  } finally {
    btn.disabled = false;
  }
}

async function doLogout() {
  try {
    await api("/api/auth/logout", { method: "POST" });
  } catch (_) {}
  closeStream();
  currentId = null;
  location.hash = "#/";
  setUser(null);
  toast("已退出登录");
}

async function loadProjects() {
  try {
    const list = await api("/api/projects");
    renderProjects(list);
  } catch (e) {
    if (e.unauthorized && me) setUser(null);
  }
}

function renderProjects(list) {
  const box = $("projectList");
  if (!list.length) {
    box.innerHTML = '<div class="empty">还没有方案<br>上传一张户型图开始</div>';
    return;
  }
  box.innerHTML = list.map((p) => `
    <div class="project-item ${p.id === currentId ? "active" : ""}" data-id="${p.id}">
      <div class="pi-name">${esc(p.name)}</div>
      <div class="pi-meta"><span>${esc(STATUS_LABEL[p.status] || p.status)}</span><span>${p.progress || 0}%</span></div>
    </div>`).join("");
  box.querySelectorAll(".project-item").forEach((el) => {
    el.onclick = () => { location.hash = `#/p/${el.dataset.id}`; };
  });
}

/* ---------- 路由 ---------- */
function route() {
  closeStream();
  const m = location.hash.match(/^#\/p\/([0-9a-f]+)/);
  if (m) {
    showDetail(m[1]);
  } else {
    currentId = null;
    $("viewUpload").hidden = false;
    $("viewDetail").hidden = true;
    loadProjects();
  }
}

function showDetail(id) {
  currentId = id;
  lastEventId = 0;
  $("viewUpload").hidden = true;
  $("viewDetail").hidden = false;
  $("resultCard").hidden = true;
  $("resultList").innerHTML = "";
  $("issuesCard").hidden = true;
  $("eventList").innerHTML = "";
  $("detailName").textContent = "加载中…";
  $("detailMeta").textContent = "";
  $("detailActions").innerHTML = "";
  loadDetail(id);
  loadProjects();
}

async function loadDetail(id) {
  let p;
  try {
    p = await api(`/api/projects/${id}`);
  } catch (e) {
    if (e.unauthorized) { openLogin(() => loadDetail(id)); return; }
    toast("方案不存在或已删除", true);
    location.hash = "#/";
    return;
  }
  if (currentId !== id) return;
  renderDetail(p);
  if (!["succeeded", "partial", "failed", "rejected"].includes(p.status)) {
    subscribe(id);
  }
}

function renderDetail(p) {
  $("detailName").textContent = p.name;
  const kind = p.probe?.kind ? ` · ${p.probe.kind}` : "";
  const stats = p.stats && p.stats.walls
    ? ` · ${p.stats.walls} 段墙 / ${p.stats.rooms} 个房间 / ${p.stats.furniture} 件家具`
    : "";
  $("detailMeta").textContent = `${p.input.name || ""}${kind}${stats}`;

  const pill = $("statusPill");
  pill.textContent = STATUS_LABEL[p.status] || p.status;
  pill.className = "pill " + (p.status === "succeeded" ? "ok"
    : p.status === "failed" ? "err"
    : p.status === "partial" || p.status === "rejected" ? "warn" : "");
  $("stageText").textContent = STAGE_LABEL[p.stage] || p.stage || "";
  $("progressText").textContent = `${p.progress || 0}%`;
  $("progressBar").style.width = `${p.progress || 0}%`;
  $("progressBar").parentElement.classList.toggle("err", p.status === "failed");
  $("statusMessage").textContent = p.message || "";

  updateEta(p.eta, p.status);

  const needScale = p.status === "failed" && p.errorCode === "NEEDS_SCALE";
  $("scaleFix").hidden = !needScale;
  if (needScale) {
    $("scaleHint").textContent = "";
    $("scaleHint").className = "hint";
    $("scaleSubmit").onclick = () => submitScale(p.id);
  }

  renderIssues(p.issues || []);
  renderActions(p);
}

function updateEta(eta, status = "") {
  const el = $("etaText");
  if (!el) return;
  if (["succeeded", "partial", "failed", "rejected"].includes(status) || !eta || !eta.remaining) {
    el.textContent = "";
    return;
  }
  const remain = eta.remaining;
  el.textContent = remain < 60 ? `预计还需约 ${remain} 秒` : `预计还需约 ${Math.round(remain / 60)} 分钟`;
}

async function submitScale(id) {
  const width = $("scaleWidth").value.trim();
  const height = $("scaleHeight").value.trim();
  const hint = $("scaleHint");
  if (!width && !height) {
    hint.textContent = "填一个总宽或总深，单位毫米";
    hint.className = "hint err";
    return;
  }
  const body = {};
  if (width) body.totalWidth = Number(width);
  if (height) body.totalHeight = Number(height);
  const btn = $("scaleSubmit");
  btn.disabled = true;
  hint.textContent = "";
  try {
    await api(`/api/projects/${id}/retry`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    toast("已按新尺寸重新生成，预计 1–2 分钟");
    showDetail(id);
  } catch (e) {
    hint.textContent = e.message;
    hint.className = "hint err";
  } finally {
    btn.disabled = false;
  }
}

function renderIssues(issues) {
  const card = $("issuesCard");
  if (!issues.length) { card.hidden = true; return; }
  card.hidden = false;
  $("issuesList").innerHTML = issues.map((i) => `
    <li class="${esc(i.level || "info")}">
      <span class="dot"></span>
      <span><span class="i-title">${esc(i.title || "")}</span>
      ${i.detail ? `<span class="i-detail"> — ${esc(i.detail)}</span>` : ""}</span>
    </li>`).join("");
}

function renderActions(p) {
  const box = $("detailActions");
  const done = ["succeeded", "partial"].includes(p.status);
  const running = !["succeeded", "partial", "failed", "rejected"].includes(p.status);
  const parts = [];
  if (done) {
    parts.push(`<a class="btn primary small" href="${p.urls.designer}" target="_blank" rel="noopener">打开设计器</a>`);
    if (p.urls.html) parts.push(`<button class="btn ghost small" data-dl="${p.urls.html}">下载 HTML</button>`);
    if (p.urls.zip) parts.push(`<button class="btn ghost small" data-dl="${p.urls.zip}">下载 zip</button>`);
    if (p.urls.overlay) parts.push(`<a class="btn ghost small" href="${p.urls.overlay}" target="_blank" rel="noopener">查看叠图</a>`);
  }
  if (!running) {
    parts.push(`<button class="btn ghost small" id="retryBtn">重新生成</button>`);
  }
  parts.push(`<button class="btn danger small" id="deleteBtn">删除</button>`);
  box.innerHTML = parts.join("");

  renderResults(p, done);

  const retry = $("retryBtn");
  if (retry) {
    retry.onclick = async () => {
      retry.disabled = true;
      try {
        await api(`/api/projects/${p.id}/retry`, { method: "POST" });
        toast("已重新排队");
        showDetail(p.id);
      } catch (e) {
        toast(e.message, true);
        retry.disabled = false;
      }
    };
  }
  $("deleteBtn").onclick = async () => {
    if (!confirm(`删除「${p.name}」？生成的方案会一起删掉。`)) return;
    try {
      await api(`/api/projects/${p.id}`, { method: "DELETE" });
      toast("已删除");
      location.hash = "#/";
    } catch (e) {
      toast(e.message, true);
    }
  };
  box.querySelectorAll("[data-dl]").forEach((btn) => {
    btn.onclick = () => startDownload(btn.dataset.dl);
  });
}

/* ---------- 已生成成果列表：统一引导到新窗口打开 ---------- */
function renderResults(p, done) {
  const card = $("resultCard");
  const list = $("resultList");
  if (!done) {
    card.hidden = true;
    list.innerHTML = "";
    return;
  }
  const items = [{
    title: "打开设计器",
    desc: "2D 布置、3D 漫游、水电点位与报价算量，都在设计器里完成",
    action: `<a class="btn primary small" href="${p.urls.designer}" target="_blank" rel="noopener">新窗口打开</a>`,
  }];
  if (p.urls.html) {
    items.push({
      title: "单文件 HTML",
      desc: "一个文件，双击就能离线打开同一个设计器，方便发给客户",
      action: `<button class="btn ghost small" data-dl="${p.urls.html}">下载</button>`,
    });
  }
  if (p.urls.zip) {
    items.push({
      title: "完整交付包 zip",
      desc: "含设计器、叠图、报价与交付说明，适合存档或交付",
      action: `<button class="btn ghost small" data-dl="${p.urls.zip}">下载</button>`,
    });
  }
  if (p.urls.overlay) {
    items.push({
      title: "识别叠图",
      desc: "原图与识别出的墙体、门窗叠在一起，用来核对还原是否准确",
      action: `<a class="btn ghost small" href="${p.urls.overlay}" target="_blank" rel="noopener">查看</a>`,
    });
  }
  list.innerHTML = items.map((i) => `
    <li class="result-item">
      <div class="ri-text">
        <div class="ri-title">${esc(i.title)}</div>
        <div class="ri-desc">${esc(i.desc)}</div>
      </div>
      ${i.action}
    </li>`).join("");
  card.hidden = false;
  list.querySelectorAll("[data-dl]").forEach((btn) => {
    btn.onclick = () => startDownload(btn.dataset.dl);
  });
}

/* ---------- 下载留资 ---------- */
function startDownload(url) {
  if (!cfg.leadRequired || me || localStorage.getItem("hpd_lead")) {
    window.location.href = url;
    return;
  }
  pendingDownload = url;
  $("leadModal").hidden = false;
  leadHint("");
  setTimeout(() => $("leadPhone")?.focus(), 60);
}

function closeLead() {
  $("leadModal").hidden = true;
  pendingDownload = null;
}

function leadHint(text, kind = "") {
  const el = $("leadHint");
  el.textContent = text;
  el.className = "auth-hint" + (kind ? ` ${kind}` : "");
}

function bindLead() {
  $("leadClose").onclick = closeLead;
  $("leadMask").onclick = closeLead;
  $("leadSubmit").onclick = submitLead;
}

async function submitLead() {
  const phone = $("leadPhone").value.trim();
  if (!/^1[3-9]\d{9}$/.test(phone)) { leadHint("请输入 11 位手机号", "err"); return; }
  if (!$("leadConsent").checked) { leadHint("请先勾选同意隐私说明", "err"); return; }
  const btn = $("leadSubmit");
  btn.disabled = true;
  try {
    await api("/api/leads", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ phone, projectId: currentId || "", consent: true, source: "download" }),
    });
    localStorage.setItem("hpd_lead", phone);
    const url = pendingDownload;
    closeLead();
    toast("验证通过，开始下载");
    if (url) window.location.href = url;
  } catch (e) {
    leadHint(e.message, "err");
  } finally {
    btn.disabled = false;
  }
}

/* ---------- 进度 ---------- */
function closeStream() {
  if (stream) { stream.close(); stream = null; }
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

function applyProgress(data) {
  if (data.progress != null) {
    $("progressBar").style.width = `${data.progress}%`;
    $("progressText").textContent = `${data.progress}%`;
    updateEta(data.eta, data.status);
  }
  if (data.stage) $("stageText").textContent = STAGE_LABEL[data.stage] || data.stage;
  const pill = $("statusPill");
  if (data.status) {
    pill.textContent = STATUS_LABEL[data.status] || data.status;
    pill.className = "pill " + (data.status === "succeeded" ? "ok"
      : data.status === "failed" ? "err"
      : data.status === "partial" || data.status === "rejected" ? "warn" : "");
  }
}

function appendEvents(events) {
  const list = $("eventList");
  const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
  for (const e of events) {
    lastEventId = Math.max(lastEventId, e.id || 0);
    const li = document.createElement("li");
    li.className = e.level || "info";
    const time = e.at ? new Date(e.at).toLocaleTimeString("zh-CN", { hour12: false }) : "";
    li.innerHTML = `<span class="t">${time}</span><span>${esc(e.message)}</span>`;
    list.appendChild(li);
  }
  if (atBottom) list.scrollTop = list.scrollHeight;
}

function subscribe(id) {
  closeStream();
  try {
    stream = new EventSource(`/api/projects/${id}/stream?after=${lastEventId}`);
    stream.onmessage = async (ev) => {
      if (currentId !== id) { closeStream(); return; }
      let data;
      try { data = JSON.parse(ev.data); } catch (_) { return; }
      appendEvents(data.events || []);
      applyProgress(data);
      if (data.terminal) {
        closeStream();
        await loadDetail(id);
        loadProjects();
      }
    };
    stream.onerror = () => {
      closeStream();
      pollTimer = setInterval(async () => {
        if (currentId !== id) { closeStream(); return; }
        try {
          const data = await api(`/api/projects/${id}/events?after=${lastEventId}`);
          appendEvents(data.events || []);
          applyProgress(data);
          if (data.terminal) { closeStream(); await loadDetail(id); loadProjects(); }
        } catch (_) {}
      }, 1500);
    };
  } catch (_) {
    pollTimer = setInterval(() => loadDetail(id), 2000);
  }
}

/* ---------- 上传 ---------- */
function bindUpload() {
  const dz = $("dropzone");
  const input = $("fileInput");
  input.onchange = () => {
    const f = input.files?.[0];
    $("filePicked").hidden = !f;
    if (f) {
      $("filePicked").textContent = `已选择：${f.name}（${(f.size / 1024 / 1024).toFixed(1)} MB）`;
      if (!$("nameInput").value) $("nameInput").value = f.name.replace(/\.[^.]+$/, "");
    }
  };
  ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => {
    e.preventDefault(); dz.classList.add("drag");
  }));
  ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => {
    e.preventDefault(); dz.classList.remove("drag");
  }));
  dz.addEventListener("drop", (e) => {
    const f = e.dataTransfer?.files?.[0];
    if (f) {
      const dt = new DataTransfer();
      dt.items.add(f);
      input.files = dt.files;
      input.onchange();
    }
  });

  // 首页可以直接选图、填参数；是否登录不影响生成
  $("uploadForm").onsubmit = (e) => {
    e.preventDefault();
    const file = input.files?.[0];
    if (!file) { toast("先选一张户型图", true); return; }
    performUpload();
  };

  function performUpload() {
    const file = input.files?.[0];
    if (!file) return;

    const fd = new FormData();
    fd.append("file", file);
    fd.append("name", $("nameInput").value.trim());
    fd.append("total_width", $("widthInput").value.trim());
    fd.append("total_height", $("depthInput").value.trim());
    fd.append("furniture", $("furnitureInput").checked ? "true" : "false");

    const btn = $("submitBtn");
    btn.disabled = true;
    $("uploadProgress").hidden = false;
    $("uploadProgressText").textContent = "上传中…";
    $("uploadProgress").querySelector("i").style.width = "0%";

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/projects");
    xhr.setRequestHeader("X-Visitor-Id", VID);
    xhr.upload.onprogress = (ev) => {
      if (!ev.lengthComputable) return;
      const pct = Math.round((ev.loaded / ev.total) * 100);
      $("uploadProgress").querySelector("i").style.width = `${pct}%`;
      $("uploadProgressText").textContent = `上传中 ${pct}%`;
    };
    xhr.onload = () => {
      btn.disabled = false;
      $("uploadProgress").hidden = true;
      if (xhr.status === 401) { openLogin(performUpload); return; }
      if (xhr.status >= 200 && xhr.status < 300) {
        const data = JSON.parse(xhr.responseText);
        $("uploadForm").reset();
        $("filePicked").hidden = true;
        toast("已开始生成，通常 1–2 分钟，可留在本页看进度");
        location.hash = `#/p/${data.id}`;
      } else {
        let msg = "上传失败";
        try { msg = JSON.parse(xhr.responseText).detail || msg; } catch (_) {}
        toast(msg, true);
      }
    };
    xhr.onerror = () => {
      btn.disabled = false;
      $("uploadProgress").hidden = true;
      toast("网络错误，上传失败", true);
    };
    xhr.send(fd);
  }
}

init();

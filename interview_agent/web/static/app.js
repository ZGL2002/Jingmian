const $ = (id) => document.getElementById(id);
let currentSessionId = null;
let pendingBox = null;
let pendingBuf = "";
let es = null;
let thinkingSince = null;

async function api(path, options) {
  const resp = await fetch(path, options);
  if (resp.status === 401) { location.href = "/login"; throw new Error("未登录"); }
  if (!resp.ok) {
    const d = await resp.json().catch(() => ({}));
    throw new Error(d.error || d.detail || resp.statusText);
  }
  return resp.json();
}

function switchView(name) {
  ["interview", "history", "experiences"].forEach((v) =>
    $(`view-${v}`).classList.toggle("hidden", v !== name));
  if (name === "history") loadHistory();
  if (name === "experiences") loadExperiences();
}

function addChat(role, text) {
  const box = document.createElement("div");
  box.className = `msg ${role}`;
  box.textContent = text;
  $("chat").appendChild(box);
  $("chat").scrollTop = $("chat").scrollHeight;
  return box;
}

function setControls(on) {
  $("answer-input").disabled = !on;
  $("btn-send").disabled = !on;
  $("btn-end").disabled = !on;
}

function setThinking(on) {
  if (!on) $("thinking").textContent = "面试官思考中…";
  $("thinking").classList.toggle("hidden", !on);
  thinkingSince = on ? Date.now() : null;
}

function currentSessionFromUrl() {
  return new URLSearchParams(location.search).get("session");
}

function setSessionInUrl(sid) {
  history.replaceState(null, "", location.pathname + "?session=" + encodeURIComponent(sid));
}

function clearSessionInUrl() {
  history.replaceState(null, "", location.pathname);
}

function addReportLink() {
  if (!currentSessionId) return;
  if ($("chat").querySelector('a[href*="/report"]')) return;
  const link = document.createElement("a");
  link.href = `/api/sessions/${currentSessionId}/report`;
  link.target = "_blank";
  link.textContent = "查看评估报告";
  const box = document.createElement("div");
  box.className = "msg system";
  box.appendChild(link);
  $("chat").appendChild(box);
}

function renderTranscript(entries) {
  $("chat").innerHTML = "";
  for (const e of entries) {
    if (e.role === "interviewer") addChat("interviewer", e.content);
    if (e.role === "candidate") addChat("candidate", e.content);
  }
  $("config-panel").open = false;
}

async function resumeSession(sid) {
  const snap = await api(`/api/session?session_id=${encodeURIComponent(sid)}`);
  if (snap.state === "missing") {
    clearSessionInUrl();
    return;
  }
  currentSessionId = sid;
  const transcript = await api(`/api/sessions/${sid}/transcript`);
  renderTranscript(transcript);
  if (snap.state === "done") {
    setControls(false);
    addReportLink();
    return;
  }
  setControls(true);
  setThinking(!!snap.busy);
  openStream(snap.last_seq || 0);
}

function handleEvent(e) {
  switch (e.type) {
    case "snapshot":
      if (e.state === "done") {
        setControls(false);
        addReportLink();
        if (es) es.close();
      } else {
        setThinking(!!e.busy);
      }
      break;
    case "status":
      if (e.status === "thinking") setThinking(true);
      if (e.status === "evaluating") { setThinking(true); addChat("system", "评估报告生成中…"); }
      if (e.status === "done") {
        setThinking(false);
        setControls(false);
        addReportLink();
        if (es) es.close();
      }
      break;
    case "delta":
      if (!pendingBox) pendingBox = addChat("interviewer", "");
      pendingBuf += e.text;
      pendingBox.textContent = pendingBuf;
      break;
    case "turn_end":
      setThinking(false);
      pendingBox = null;
      pendingBuf = "";
      break;
    case "error":
      setThinking(false);
      addChat("system", "错误：" + e.message);
      break;
  }
}

function openStream(lastId) {
  if (es) es.close();
  const url = `/api/stream?session_id=${encodeURIComponent(currentSessionId)}`
    + (lastId ? `&last_id=${lastId}` : "");
  es = new EventSource(url);
  es.onmessage = (ev) => handleEvent(JSON.parse(ev.data));
  es.onopen = async () => {
    if (!currentSessionId) return;
    try {
      const snap = await api(`/api/session?session_id=${encodeURIComponent(currentSessionId)}`);
      if (snap.state === "done") {
        setControls(false);
        addReportLink();
        if (es) es.close();
      } else {
        setThinking(!!snap.busy);
      }
    } catch (err) { /* 网络暂时不可达，EventSource 会继续重连 */ }
  };
  es.onerror = () => {}; // 自动重连；服务器按 Last-Event-ID 只补发未收到的事件
}

function startInterview() {
  const fd = new FormData();
  fd.append("company", $("cfg-company").value.trim());
  fd.append("position", $("cfg-position").value.trim());
  fd.append("jd_text", $("cfg-jd").value);
  fd.append("resume_text", $("cfg-resume").value);
  document.querySelectorAll("#cfg-experiences input:checked")
    .forEach((cb) => fd.append("experience_ids", cb.value));
  const file = $("cfg-resume-file").files[0];
  if (file) fd.append("resume", file);
  api("/api/session/start", {method: "POST", body: fd})
    .then(({session_id}) => {
      currentSessionId = session_id;
      setSessionInUrl(session_id);
      $("chat").innerHTML = "";
      $("config-panel").open = false;
      $("start-error").textContent = "";
      setControls(true);
      setThinking(false);
      openStream(0);
    })
    .catch((e) => { $("start-error").textContent = e.message; });
}

function sendAnswer() {
  const text = $("answer-input").value;
  if (!text.trim() || !currentSessionId) return;
  addChat("candidate", text);
  $("answer-input").value = "";
  api("/api/answer", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({session_id: currentSessionId, text}),
  }).catch((e) => addChat("system", "发送失败：" + e.message));
}

function endInterview() {
  if (!currentSessionId) return;
  api("/api/end", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({session_id: currentSessionId}),
  }).then(() => setControls(false)).catch((e) => addChat("system", e.message));
}

async function loadHistory() {
  const list = await api("/api/history");
  const box = $("view-history");
  box.innerHTML = "";
  if (!list.length) { box.innerHTML = "<p>暂无面试记录</p>"; return; }
  for (const s of list) {
    const row = document.createElement("div");
    row.className = "history-row";
    row.innerHTML = `<span>${s.created_at}</span> <b>${s.company || "（未指定公司）"} ${s.position || ""}</b> <span>${s.question_count} 题</span> ${s.has_report ? "报告✓" : "无报告"}`;
    const btn = document.createElement("button");
    btn.textContent = "查看";
    btn.onclick = () => openSessionDetail(s.session_id);
    row.appendChild(btn);
    box.appendChild(row);
  }
}

async function openSessionDetail(sid) {
  const transcript = await api(`/api/sessions/${sid}/transcript`);
  const reportResp = await fetch(`/api/sessions/${sid}/report`).then((r) => r.ok ? r.text() : null);
  const box = $("view-history");
  box.innerHTML = "<h3>对话回放</h3><pre class='transcript'></pre>";
  box.querySelector("pre").textContent = transcript
    .filter((e) => ["interviewer", "candidate"].includes(e.role))
    .map((e) => `${e.role === "interviewer" ? "面试官" : "候选人"}: ${e.content}`)
    .join("\n\n");
  if (reportResp) {
    box.insertAdjacentHTML("beforeend", "<h3>评估报告</h3>");
    const art = document.createElement("div");
    art.className = "report";
    art.innerHTML = reportResp;
    box.appendChild(art);
  }
}

async function loadExperiences() {
  const list = await api("/api/experiences");
  const box = $("view-experiences");
  box.innerHTML = "";
  const form = document.createElement("div");
  form.className = "exp-form";
  form.innerHTML = `
    <input id="exp-title" placeholder="标题"><br>
    <input id="exp-source" placeholder="来源（可选）">
    <input id="exp-company" placeholder="公司（可选）">
    <input id="exp-position" placeholder="岗位（可选）"><br>
    <textarea id="exp-content" placeholder="面经内容"></textarea><br>
    <button id="exp-add">保存到面经库</button>`;
  box.appendChild(form);
  $("exp-add").onclick = async () => {
    await api("/api/experiences", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        title: $("exp-title").value, source: $("exp-source").value,
        company: $("exp-company").value, position: $("exp-position").value,
        content: $("exp-content").value,
      }),
    });
    loadExperiences();
    loadExperienceOptions();
  };
  for (const e of list) {
    const row = document.createElement("div");
    row.className = "history-row";
    row.innerHTML = `<b>${e.title}</b> ${e.company ? "· " + e.company : ""}${e.position ? " · " + e.position : ""} <small>${e.source || ""}</small>`;
    const del = document.createElement("button");
    del.textContent = "删除";
    del.onclick = async () => {
      await api(`/api/experiences/${e.entry_id}`, {method: "DELETE"});
      loadExperiences();
      loadExperienceOptions();
    };
    row.appendChild(del);
    box.appendChild(row);
  }
}

async function loadExperienceOptions() {
  const list = await api("/api/experiences");
  const box = $("cfg-experiences");
  box.innerHTML = "";
  if (!list.length) { box.textContent = "（面经库为空，可到“面经库”页添加）"; return; }
  for (const e of list) {
    const label = document.createElement("label");
    label.className = "exp-option";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.value = e.entry_id;
    label.append(cb, ` ${e.title}${e.company ? "（" + e.company + "）" : ""}`);
    box.appendChild(label);
  }
}

document.querySelectorAll("nav button[data-view]").forEach((b) => {
  b.onclick = () => switchView(b.dataset.view);
});
$("btn-start").onclick = startInterview;
$("btn-send").onclick = sendAnswer;
$("btn-end").onclick = endInterview;
$("answer-input").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); sendAnswer(); }
});
$("logout").onclick = async () => {
  await api("/api/logout", {method: "POST"}).catch(() => {});
  location.href = "/login";
};
setInterval(() => {
  if (thinkingSince && Date.now() - thinkingSince > 90_000) {
    $("thinking").textContent = "回复耗时较长，仍在等待…（可稍后刷新页面恢复本场面试）";
  }
}, 5000);
loadExperienceOptions();
const savedSid = currentSessionFromUrl();
if (savedSid) {
  switchView("interview");
  resumeSession(savedSid).catch(() => {
    clearSessionInUrl();
    switchView("interview");
  });
} else {
  switchView("interview");
}

const $ = (id) => document.getElementById(id);
let currentSessionId = null;
let pendingBox = null;
let pendingBuf = "";
let es = null;

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

function showThinking(on) { $("thinking").classList.toggle("hidden", !on); }

function setControls(on) {
  $("answer-input").disabled = !on;
  $("btn-send").disabled = !on;
  $("btn-end").disabled = !on;
}

function handleEvent(e) {
  switch (e.type) {
    case "snapshot":
      if (e.state === "done") setControls(false);
      break;
    case "status":
      if (e.status === "thinking") showThinking(true);
      if (e.status === "evaluating") { showThinking(true); addChat("system", "评估报告生成中…"); }
      if (e.status === "done") {
        showThinking(false);
        setControls(false);
        const link = document.createElement("a");
        link.href = `/api/sessions/${currentSessionId}/report`;
        link.target = "_blank";
        link.textContent = "查看评估报告";
        const box = document.createElement("div");
        box.className = "msg system";
        box.appendChild(link);
        $("chat").appendChild(box);
        if (es) es.close();
      }
      break;
    case "delta":
      if (!pendingBox) pendingBox = addChat("interviewer", "");
      pendingBuf += e.text;
      pendingBox.textContent = pendingBuf;
      break;
    case "turn_end":
      showThinking(false);
      pendingBox = null;
      pendingBuf = "";
      break;
    case "error":
      showThinking(false);
      addChat("system", "错误：" + e.message);
      break;
  }
}

function openStream() {
  if (es) es.close();
  es = new EventSource(`/api/stream?session_id=${currentSessionId}`);
  es.onmessage = (ev) => handleEvent(JSON.parse(ev.data));
  es.onerror = () => {}; // 自动重连；服务器推送 snapshot 事件同步状态
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
      $("chat").innerHTML = "";
      $("config-panel").open = false;
      $("start-error").textContent = "";
      setControls(true);
      openStream();
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
switchView("interview");
loadExperienceOptions();

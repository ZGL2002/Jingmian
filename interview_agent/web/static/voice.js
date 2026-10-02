// static/voice.js —— 实时语音引擎：流式识别（边说边出字）、攒句自动提交、
// 流式合成首包即播、回声闸门与 barge-in 打断、整场混音录制。
"use strict";

const PCM_RATE = 16000;       // 采集与识别采样率（AudioContext 固定 16k，浏览器自动重采样）
const TTS_RATE = 22050;       // 流式合成 PCM 采样率
const BATCH_BYTES = 3200;     // ~100ms 的 PCM16，攒批发送
const SUBMIT_WINDOW_MS = 4200;// 句尾事件（停止说话约 800ms 后到达）之后的攒句窗口，合计≈停止说话后 5s 自动发送；想更跟手可调小
const GATE_REOPEN_MS = 600;   // 播报结束后的闸门重开延迟（留出扬声器尾音衰减时间）
const BARGE_MS = 150;         // 连续超阈值多久判定为插话
const FILLER_CHARS = "嗯呃啊哦噢唉诶哈嘿呀吧呢哎"; // 纯语气词不作为回答发送

class LiveVoiceEngine {
  constructor() {
    this.ctx = null;
    this.mediaStream = null;   // 麦克风+摄像头共用流
    this.recDest = null;       // 整场混音目的地（麦克风 + TTS）
    this.micSource = null;
    this.worklet = null;
    this.ws = null;
    this.mixRecorder = null;
    this.mixChunks = [];
    this.style = "serious";
    this.sessionId = null;
    this.stopped = false;
    // 识别流
    this.gateOpen = true;
    this.batchBuf = [];
    this.batchLen = 0;
    this.transcriptBuf = "";
    this.submitTimer = null;
    // 播放队列
    this._pending = "";
    this.sentenceQueue = [];
    this.pumping = false;
    this.gen = 0;              // 打断代数：递增使在途的合成流作废
    this.scheduled = new Set();
    this.nextTime = 0;
    this.pcmTail = new Uint8Array(0);
    this.turnBuffers = [];
    this.lastTurnBuffers = null;
    this._turnEnded = false;
    // barge-in 阈值自适应
    this.ambient = Infinity;
    this.warmup = 0;
    this.streakMs = 0;
    // app.js 注入的回调
    this.onCaption = null;   // (partialText) => {}
    this.onSubmit = null;    // (finalText) => {}，自动发送
    this.onState = null;     // ("listening"|"speaking") => {}
  }

  async enable() {
    this.mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: true });
    this.ctx = new AudioContext({ sampleRate: PCM_RATE });
    this.recDest = this.ctx.createMediaStreamDestination();
    this.micSource = this.ctx.createMediaStreamSource(this.mediaStream);
    this.micSource.connect(this.recDest); // 麦克风进整场混音（不进扬声器，避免回声）
    await this.ctx.audioWorklet.addModule("/static/pcm-worklet.js");
    this.worklet = new AudioWorkletNode(this.ctx, "pcm-worklet");
    this.worklet.port.onmessage = (e) => this._onMicFrame(e.data);
    this.micSource.connect(this.worklet);
    const cam = document.getElementById("cam-preview");
    cam.srcObject = new MediaStream(this.mediaStream.getVideoTracks());
    cam.classList.remove("hidden");
    await cam.play().catch(() => {});
  }

  beginSession(sessionId, style) {
    this.sessionId = sessionId;
    this.style = style;
    this._connectWs();
    const mixed = new MediaStream(this.recDest.stream.getAudioTracks());
    this.mixRecorder = new MediaRecorder(mixed);
    this.mixChunks = [];
    this.mixRecorder.ondataavailable = (e) => { if (e.data && e.data.size) this.mixChunks.push(e.data); };
    this.mixRecorder.start(5000);
    this._setState("listening");
  }

  _connectWs() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    this.ws = new WebSocket(`${proto}://${location.host}/api/ws/voice/${this.sessionId}`);
    this.ws.binaryType = "arraybuffer";
    this.ws.onmessage = (ev) => {
      let m;
      try { m = JSON.parse(ev.data); } catch (e) { return; }
      if (m.type === "partial") this._onPartial(m.text || "");
      else if (m.type === "final") this._onFinal(m.text || "");
      else if (m.type === "error") console.warn("识别流错误：", m.message);
    };
    this.ws.onopen = () => {
      this.wsAttempts = 0;
      this._setState("listening");
    };
    this.ws.onclose = () => {
      if (this.stopped) return;
      this._scheduleReconnect();
    };
  }

  // 通道断开自动重连：服务重启/网络闪断/识别会话闲置超时都能恢复
  _scheduleReconnect() {
    const MAX = 10;
    if (this.stopped || !this.sessionId) return;
    this.wsAttempts = (this.wsAttempts || 0) + 1;
    if (this.wsAttempts > MAX) {
      this._setState("listening");
      if (this.onCaption) {
        this.onCaption("（语音通道已断开：请确认服务在运行；可刷新页面继续本场面试，打字不受影响）");
      }
      return;
    }
    this._setState("reconnecting");
    setTimeout(() => {
      if (!this.stopped && this.wsAttempts <= MAX) this._connectWs();
    }, 2000);
  }

  // —— 麦克风帧：PCM16 批量发送 + 闸门关闭期静音保活 + barge-in 检测 ——
  _onMicFrame(f32) {
    const bytes = new Uint8Array(f32.length * 2);
    const dv = new DataView(bytes.buffer);
    let sum = 0;
    for (let i = 0; i < f32.length; i++) {
      const s = Math.max(-1, Math.min(1, f32[i]));
      dv.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
      sum += f32[i] * f32[i];
    }
    this._detectBargeIn(Math.sqrt(sum / f32.length));
    if (!this.gateOpen) {
      // 播报期间不发真实音频（防回声），但每 5s 补一帧全零静音保活，
      // 否则识别会话长时间无数据会被服务端闲置断开
      const now = performance.now();
      if (now - (this._lastKeepalive || 0) < 5000) return;
      this._lastKeepalive = now;
      bytes.fill(0);
    }
    this.batchBuf.push(bytes);
    this.batchLen += bytes.length;
    if (this.batchLen >= BATCH_BYTES) this._flushPcm();
  }

  _flushPcm() {
    if (!this.batchLen) return;
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      const merged = new Uint8Array(this.batchLen);
      let o = 0;
      for (const b of this.batchBuf) { merged.set(b, o); o += b.length; }
      this.ws.send(merged);
    }
    this.batchBuf = [];
    this.batchLen = 0;
  }

  _detectBargeIn(rms) {
    if (this.warmup < 5) { this.ambient = Math.min(this.ambient, rms); this.warmup++; return; }
    const thr = Math.max(this.ambient * 3, 0.01);
    const speaking = this.scheduled.size > 0 || this.pumping;
    if (speaking && rms > thr) {
      this.streakMs += (1000 * 128) / PCM_RATE; // 每帧 128 samples
      if (this.streakMs >= BARGE_MS) this._interrupt();
    } else {
      this.streakMs = 0;
    }
  }

  _interrupt() {
    this.gen++;
    this.sentenceQueue = [];
    for (const src of this.scheduled) { try { src.stop(); } catch (e) { /* 已结束 */ } }
    this.scheduled.clear();
    this.nextTime = 0;
    this.gateOpen = true;
    this._setState("listening");
  }

  // —— 识别事件：字幕 + 攒句自动提交 ——
  // 播报期间（闸门关闭）到达的事件一律丢弃：不戴耳机时是回声，戴耳机时是
  // 发送断开前的在途残留，混入会导致重复/碎片发送
  _onPartial(text) {
    if (!this.gateOpen) return;
    if (this.onCaption) this.onCaption(this.transcriptBuf + text);
    this._armSubmit();
  }

  _onFinal(text) {
    if (!this.gateOpen) return;
    if (text) this.transcriptBuf += text;
    if (this.onCaption) this.onCaption(this.transcriptBuf);
    this._armSubmit();
  }

  _armSubmit() {
    if (this.submitTimer) clearTimeout(this.submitTimer);
    this.submitTimer = setTimeout(() => {
      const text = this.transcriptBuf.trim();
      this.transcriptBuf = "";
      if (this.onCaption) this.onCaption("");
      // 纯语气词/超短音（如听题时下意识的"嗯"）丢弃，等真正的回答，
      // 避免面试官刚问完就被"嗯。"顶掉这道题
      if (!text || isFillerOnly(text)) return;
      if (this.onSubmit) this.onSubmit(text);
    }, SUBMIT_WINDOW_MS);
  }

  // —— 面试官语音：SSE 流式合成 → PCM 块顺序调度 ——
  // sentenceQueue 的元素带轮次标签（{text, buf}）：上一轮仍在途的尾块
  // 不会错落进下一轮的音频缓冲，保证「重听」回放的归属正确
  feedDelta(text) {
    if (this._turnEnded) { // 新一轮第一段 delta：此刻才切换缓冲，上轮尾块仍落上轮
      this._turnEnded = false;
      this.turnBuffers = [];
    }
    this._pending += text;
    const [finished, rest] = splitSentences(this._pending);
    this._pending = rest;
    finished.forEach((s) => this.sentenceQueue.push({ text: s, buf: this.turnBuffers }));
    this._pump();
  }

  endTurn() {
    if (this._pending.trim()) {
      this.sentenceQueue.push({ text: this._pending.trim(), buf: this.turnBuffers });
      this._pending = "";
      this._pump();
    }
    this._turnEnded = true;
    this.lastTurnBuffers = this.turnBuffers;
    return this.lastTurnBuffers; // 供该轮的「重听」按钮捕获自己的音频引用
  }

  async _pump() {
    if (this.pumping) return;
    this.pumping = true;
    const gen = this.gen;
    try {
      while (this.sentenceQueue.length) {
        if (gen !== this.gen) return;
        const item = this.sentenceQueue.shift();
        await this._speakStream(item.text, item.buf, gen);
      }
    } finally {
      this.pumping = false;
      this._checkGateReopen();
    }
  }

  async _speakStream(sentence, buf, gen) {
    try {
      const resp = await fetch("/api/tts/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: sentence, style: this.style }),
      });
      if (!resp.ok || !resp.body) throw new Error("tts " + resp.status);
      const reader = resp.body.getReader();
      const dec = new TextDecoder();
      let sseBuf = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        sseBuf += dec.decode(value, { stream: true });
        let idx;
        while ((idx = sseBuf.indexOf("\n\n")) >= 0) {
          const chunk = sseBuf.slice(0, idx);
          sseBuf = sseBuf.slice(idx + 2);
          for (const line of chunk.split("\n")) {
            if (!line.startsWith("data: ")) continue;
            const data = line.slice(6);
            if (data === "[DONE]") return;
            if (data.startsWith("{")) { console.warn("TTS 流错误", data); return; }
            if (gen !== this.gen) return; // 已被打断，丢弃剩余音频
            this._schedulePcm(_b64ToBytes(data), buf);
          }
        }
      }
    } catch (e) {
      console.warn("流式合成失败，降级为纯文字：", e);
    }
  }

  _schedulePcm(bytes, buf) {
    const all = new Uint8Array(this.pcmTail.length + bytes.length);
    all.set(this.pcmTail);
    all.set(bytes, this.pcmTail.length);
    const usable = all.length - (all.length % 2); // PCM16 必须整样本
    if (usable <= 0) { this.pcmTail = all; return; }
    this.pcmTail = all.slice(usable);
    const n = usable / 2;
    const f32 = new Float32Array(n);
    const dv = new DataView(all.buffer, all.byteOffset, usable);
    for (let i = 0; i < n; i++) f32[i] = dv.getInt16(i * 2, true) / 32768;
    const buffer = this.ctx.createBuffer(1, n, TTS_RATE);
    buffer.copyToChannel(f32, 0);
    (buf || this.turnBuffers).push(buffer);
    this.gateOpen = false; // 回声闸门：播报期间不向识别流送麦克风
    this._setState("speaking");
    const src = this.ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(this.ctx.destination);
    src.connect(this.recDest); // 面试官语音混入整场录音
    this.scheduled.add(src);
    const startAt = Math.max(this.ctx.currentTime + 0.02, this.nextTime);
    this.nextTime = startAt + buffer.duration;
    src.onended = () => { this.scheduled.delete(src); this._checkGateReopen(); };
    src.start(startAt);
  }

  _checkGateReopen() {
    if (this.scheduled.size > 0 || this.pumping || this.sentenceQueue.length) return;
    setTimeout(() => {
      if (this.scheduled.size === 0 && !this.pumping) {
        this.gateOpen = true;
        this._setState("listening");
      }
    }, GATE_REOPEN_MS);
  }

  async playTurn(buffers) {
    // 重听：按序播放该提问自己的音频（重听不进整场录音）
    for (const b of buffers || []) {
      await new Promise((resolve) => {
        const src = this.ctx.createBufferSource();
        src.buffer = b;
        src.connect(this.ctx.destination);
        src.onended = resolve;
        src.start();
      });
    }
  }

  async finish() {
    if (this.stopped) return;
    this.stopped = true;
    if (this.submitTimer) clearTimeout(this.submitTimer);
    this._interrupt();
    if (this.ws) {
      try { if (this.ws.readyState === WebSocket.OPEN) this.ws.send('{"type":"stop"}'); } catch (e) { /* 忽略 */ }
      try { this.ws.close(); } catch (e) { /* 忽略 */ }
    }
    await new Promise((resolve) => {
      if (!this.mixRecorder || this.mixRecorder.state === "inactive") return resolve();
      this.mixRecorder.onstop = () => resolve();
      this.mixRecorder.stop();
    });
    if (this.mediaStream) this.mediaStream.getTracks().forEach((t) => t.stop());
    const cam = document.getElementById("cam-preview");
    if (cam) cam.srcObject = null;
    if (this.ctx) { try { await this.ctx.close(); } catch (e) { /* 忽略 */ } }
    if (!this.sessionId || !this.mixChunks.length) return;
    const blob = new Blob(this.mixChunks, { type: "audio/webm" });
    for (let i = 0; i < 3; i++) {
      try {
        const fd = new FormData();
        fd.append("file", blob);
        const r = await fetch(`/api/sessions/${this.sessionId}/audio`, { method: "POST", body: fd });
        if (r.ok) return;
      } catch (e) { /* 重试 */ }
    }
    alert("整场音频上传失败，本场录音未能保存");
  }

  _setState(s) {
    if (this.onState) this.onState(s);
  }
}

// 句末切分：硬标点直接切；半角句点/问叹号要求后随空白/换行/中文标点才算句末（避免 3.5 误切）
function splitSentences(buf) {
  const out = [];
  let start = 0;
  for (let i = 0; i < buf.length; i++) {
    const c = buf[i];
    const hard = "。？！；\n".includes(c);
    const soft = /[.?!]/.test(c) && (
      i + 1 >= buf.length || /[\s\u3000]/.test(buf[i + 1]) || "，。？！；".includes(buf[i + 1])
    );
    if (hard || soft) {
      out.push(buf.slice(start, i + 1).trim());
      start = i + 1;
    }
  }
  return [out.filter(Boolean), buf.slice(start)];
}

function _b64ToBytes(b64) {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

// 判定是否纯语气词：去掉标点后为空、过短（单字"对/好"多半也是应答声）、或全部由语气词组成
function isFillerOnly(text) {
  const stripped = text.replace(/[\s，。？！,.?!；;、~～]/g, "");
  if (!stripped) return true;
  if (stripped.length < 2) return true;
  return [...stripped].every((c) => FILLER_CHARS.includes(c));
}
window.isFillerOnly = isFillerOnly;

window.LiveVoiceEngine = LiveVoiceEngine;
window.splitSentences = splitSentences; // 便于控制台自测

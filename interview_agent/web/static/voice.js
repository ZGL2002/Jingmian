// static/voice.js —— 语音模式引擎：ASR 录音、TTS 播放队列、整场混音录制
"use strict";

class VoiceEngine {
  constructor() {
    this.ctx = new AudioContext();
    this.recDest = this.ctx.createMediaStreamDestination();
    this.mediaStream = null;      // 麦克风+摄像头共用流
    this.micSource = null;
    this.mixRecorder = null;
    this.mixChunks = [];
    this.answerRecorder = null;
    this.answerChunks = [];
    this.sentenceQueue = [];
    this.pending = "";
    this.pumping = false;
    this.turnBuffers = [];
    this.lastTurnBuffers = null;
    this.style = "serious";
    this.sessionId = null;
    this.stopped = false;
    // VAD 状态（app.js 挂 onSpeak/onSilence 回调做倒计时 UI）
    this.onSpeak = null;
    this.onSilence = null;
    this._vadTimer = null;
    this._vadAnalyser = null;
  }

  async enable() {
    this.mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: true });
    this.micSource = this.ctx.createMediaStreamSource(this.mediaStream);
    // 麦克风只进录制、不进扬声器，避免回声
    this.micSource.connect(this.recDest);
    const cam = document.getElementById("cam-preview");
    cam.srcObject = new MediaStream(this.mediaStream.getVideoTracks());
    cam.classList.remove("hidden");
    await cam.play().catch(() => {});
  }

  beginSession(sessionId, style) {
    this.sessionId = sessionId;
    this.style = style;
    const mixed = new MediaStream(this.recDest.stream.getAudioTracks());
    this.mixRecorder = new MediaRecorder(mixed);
    this.mixChunks = [];
    this.mixRecorder.ondataavailable = (e) => { if (e.data && e.data.size) this.mixChunks.push(e.data); };
    this.mixRecorder.start(5000);
  }

  feedDelta(text) {
    this.pending += text;
    const [finished, rest] = splitSentences(this.pending);
    this.pending = rest;
    finished.forEach((s) => this.sentenceQueue.push(s));
    this._pump();
  }

  endTurn() {
    if (this.pending.trim()) {
      this.sentenceQueue.push(this.pending.trim());
      this.pending = "";
      this._pump();
    }
    this.lastTurnBuffers = this.turnBuffers;
    this.turnBuffers = [];
  }

  async _pump() {
    if (this.pumping) return;
    this.pumping = true;
    try {
      while (this.sentenceQueue.length) {
        await this._speak(this.sentenceQueue.shift());
      }
    } finally {
      this.pumping = false;
    }
  }

  async _speak(sentence) {
    try {
      const resp = await fetch("/api/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: sentence, style: this.style }),
      });
      if (!resp.ok) throw new Error("tts " + resp.status);
      const mp3 = await resp.arrayBuffer();
      const buffer = await this.ctx.decodeAudioData(mp3);
      this.turnBuffers.push(buffer);
      await this._play(buffer, true);
    } catch (e) {
      console.warn("TTS 失败，降级为纯文字：", e);
    }
  }

  _play(buffer, intoRecording) {
    return new Promise((resolve) => {
      const src = this.ctx.createBufferSource();
      src.buffer = buffer;
      src.connect(this.ctx.destination);
      if (intoRecording) src.connect(this.recDest); // 面试官语音混入整场录音
      src.onended = resolve;
      src.start();
    });
  }

  async replayLastTurn() {
    for (const b of this.lastTurnBuffers || []) {
      await this._play(b, false);
    }
  }

  async startAnswer(opts = {}) {
    this.answerChunks = [];
    this.answerRecorder = new MediaRecorder(this.mediaStream);
    this.answerRecorder.ondataavailable = (e) => { if (e.data && e.data.size) this.answerChunks.push(e.data); };
    this.answerRecorder.start();
    if (opts.autoStopMs) this._startVad(opts.autoStopMs, opts.onAutoStop);
  }

  // VAD：100ms 采样 RMS；前 5 个采样（0.5s）取最小值当环境噪声基线；
  // 开口后连续静音达 silenceMs 判定说完。阈值 = max(基线×3, 0.01)。
  _startVad(silenceMs, onAutoStop) {
    const analyser = this.ctx.createAnalyser();
    analyser.fftSize = 512;
    this.micSource.connect(analyser); // analyser 不接 destination，无回声
    this._vadAnalyser = analyser;
    const samples = new Float32Array(analyser.fftSize);
    const step = 100;
    const warmupSteps = 5;
    let tick = 0;
    let spoke = false;
    let quietMs = 0;
    let ambient = Infinity;
    this._vadTimer = setInterval(() => {
      analyser.getFloatTimeDomainData(samples);
      let sum = 0;
      for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
      const rms = Math.sqrt(sum / samples.length);
      tick++;
      if (tick <= warmupSteps) { ambient = Math.min(ambient, rms); return; }
      const threshold = Math.max(ambient * 3, 0.01);
      if (rms > threshold) {
        if (quietMs > 0 && this.onSpeak) this.onSpeak();
        spoke = true;
        quietMs = 0;
      } else if (spoke) {
        quietMs += step;
        if (this.onSilence) this.onSilence(quietMs, silenceMs);
        if (quietMs >= silenceMs) {
          this._stopVad();
          onAutoStop();
        }
      }
    }, step);
  }

  _stopVad() {
    if (this._vadTimer) { clearInterval(this._vadTimer); this._vadTimer = null; }
    if (this._vadAnalyser) {
      try { this.micSource.disconnect(this._vadAnalyser); } catch (e) { /* 已断开 */ }
      this._vadAnalyser = null;
    }
  }

  stopAnswer() {
    this._stopVad();
    return new Promise((resolve) => {
      this.answerRecorder.onstop = () => resolve(new Blob(this.answerChunks, { type: "audio/webm" }));
      this.answerRecorder.stop();
    });
  }

  async recognize(blob) {
    const wav = await this._toWav16k(blob);
    const fd = new FormData();
    fd.append("file", new Blob([wav], { type: "audio/wav" }));
    fd.append("fmt", "wav");
    const r = await fetch("/api/asr", { method: "POST", body: fd });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.error || d.detail || "识别失败");
    }
    return (await r.json()).text;
  }

  async _toWav16k(blob) {
    const arr = await blob.arrayBuffer();
    const decoded = await this.ctx.decodeAudioData(arr);
    const rate = 16000;
    const len = Math.max(1, Math.ceil(decoded.duration * rate));
    const off = new OfflineAudioContext(1, len, rate);
    const src = off.createBufferSource();
    src.buffer = decoded;
    src.connect(off.destination);
    src.start();
    const rendered = await off.startRendering();
    return encodeWav(rendered.getChannelData(0), rate);
  }

  async finish() {
    if (this.stopped) return;
    this.stopped = true;
    this.endTurn();
    this._stopVad();
    await new Promise((resolve) => {
      if (!this.mixRecorder || this.mixRecorder.state === "inactive") return resolve();
      this.mixRecorder.onstop = () => resolve();
      this.mixRecorder.stop();
    });
    if (this.mediaStream) {
      this.mediaStream.getTracks().forEach((t) => t.stop());
    }
    const cam = document.getElementById("cam-preview");
    if (cam) cam.srcObject = null;
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

// 单声道 16kHz PCM16 WAV 编码（浏览器不能直接产出 wav，ASR 不认 webm）
function encodeWav(samples, rate) {
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const v = new DataView(buf);
  const ws = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  ws(0, "RIFF"); v.setUint32(4, 36 + samples.length * 2, true); ws(8, "WAVE"); ws(12, "fmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  ws(36, "data"); v.setUint32(40, samples.length * 2, true);
  let o = 44;
  for (let i = 0; i < samples.length; i++, o += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(o, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buf;
}

window.VoiceEngine = VoiceEngine;
window.splitSentences = splitSentences; // 便于控制台自测

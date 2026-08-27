// static/pcm-worklet.js —— 麦克风帧采集：把每渲染量子（128 帧 Float32）发给主线程
class PcmWorklet extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) this.port.postMessage(ch);
    return true;
  }
}
registerProcessor("pcm-worklet", PcmWorklet);

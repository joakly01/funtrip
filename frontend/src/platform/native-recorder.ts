// @ts-nocheck
// 录音先保留在本机，完成后才交给页面上传；取消或离开页面不提交。
export class NativeRecorder {
  constructor({
    manager,
    onState,
    onComplete,
    onError,
    now = Date.now,
    setTimer = setInterval,
    clearTimer = clearInterval,
  }) {
    Object.assign(this, {
      manager,
      onState,
      onComplete,
      onError,
      now,
      setTimer,
      clearTimer,
    });
    this.phase = "idle";
    this.discard = false;
    this.disposed = false;
    this.handlers = {
      start: () => this.started(),
      stop: (r) => this.stopped(r),
      error: () => this.failed(),
      interruption: () => this.cancel(),
    };
    manager.onStart(this.handlers.start);
    manager.onStop(this.handlers.stop);
    manager.onError(this.handlers.error);
    if (manager.onInterruptionBegin)
      manager.onInterruptionBegin(this.handlers.interruption);
  }
  update(phase, seconds) {
    if (seconds === undefined)
      seconds = phase === "idle" ? 0 : this.seconds || 0;
    this.seconds = seconds;
    this.phase = phase;
    if (!this.disposed) this.onState({ phase, seconds });
  }
  clear() {
    if (this.timer) {
      this.clearTimer(this.timer);
      this.timer = null;
    }
  }
  start() {
    if (this.disposed || this.phase !== "idle") return;
    this.discard = false;
    this.update("starting");
    try {
      this.manager.start({
        duration: 119000,
        format: "mp3",
        sampleRate: 16000,
        numberOfChannels: 1,
        encodeBitRate: 48000,
      });
    } catch (e) {
      this.failed();
    }
  }
  started() {
    if (this.disposed || this.discard) {
      this.manager.stop();
      return;
    }
    this.startedAt = this.now();
    this.update("recording");
    this.timer = this.setTimer(() => {
      const seconds = Math.floor((this.now() - this.startedAt) / 1000);
      this.update("recording", seconds);
      if (seconds >= 119) this.finish();
    }, 250);
  }
  finish() {
    if (this.phase !== "recording") return;
    this.clear();
    this.update("stopping");
    try {
      this.manager.stop();
    } catch (e) {
      this.failed();
    }
  }
  cancel() {
    if (this.phase === "idle") return;
    this.discard = true;
    this.clear();
    this.update("stopping");
    try {
      this.manager.stop();
    } catch (e) {
      this.update("idle");
    }
  }
  stopped(result) {
    this.clear();
    const discarded = this.discard || this.disposed;
    this.update("idle");
    if (discarded) return;
    if (!result.tempFilePath || !result.fileSize || result.duration < 800) {
      this.onError("录音太短，请说完一句话后再完成");
      return;
    }
    this.onComplete({
      tempFilePath: result.tempFilePath,
      size: result.fileSize,
      duration: result.duration,
    });
  }
  failed() {
    this.clear();
    const cancelled = this.discard || this.disposed;
    this.update("idle");
    if (!cancelled) this.onError("录音未能完成，请检查麦克风权限后重试");
  }
  dispose() {
    this.disposed = true;
    this.cancel();
    this.clear();
    for (const [method, handler] of [
      ["offStart", "start"],
      ["offStop", "stop"],
      ["offError", "error"],
      ["offInterruptionBegin", "interruption"],
    ])
      if (this.manager[method]) this.manager[method](this.handlers[handler]);
  }
}

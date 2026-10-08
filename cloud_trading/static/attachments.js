"use strict";

/* Files remain ordinary chat attachments; speech is reviewed as editable text. */
class ChatAttachments {
  constructor({ $, el, icon, toast, data, onText, onChange }) {
    Object.assign(this, { $, el, icon, toast, data, onText, onChange });
    this.items = [];
    this.busy = false;
    this.voiceReady = false;
    this.recording = false;
    this.recordingPending = false;
    this.processingRecording = false;
    this.recordVersion = 0;
    this.controllers = new Set();
    $("attach-image").onclick = () => $("image-picker").click();
    $("attach-file").onclick = () => $("file-picker").click();
    $("voice-upload").onclick = () => $("audio-picker").click();
    for (const id of ["image-picker", "file-picker", "audio-picker"]) {
      $(id).onchange = (event) => {
        this.addFiles([...event.target.files]);
        event.target.value = "";
      };
    }
    $("record-voice").onclick = () => this.record();
    $("voice-cancel").onclick = () => this.stop(true);
    $("voice-finish").onclick = () => this.stop(false);
    $("attachment-close").onclick = () => $("attachment-dialog").close();
    $("attachment-dialog").onclick = (event) => {
      if (event.target === $("attachment-dialog"))
        $("attachment-dialog").close();
    };
    const composer = $("chat-form");
    composer.addEventListener("dragover", (event) => {
      if (!event.dataTransfer.types.includes("Files") || this.busy) return;
      event.preventDefault();
      composer.classList.add("drag-over");
    });
    composer.addEventListener("dragleave", (event) => {
      if (!composer.contains(event.relatedTarget))
        composer.classList.remove("drag-over");
    });
    composer.addEventListener("drop", (event) => {
      composer.classList.remove("drag-over");
      if (!event.dataTransfer.files.length) return;
      event.preventDefault();
      this.addFiles([...event.dataTransfer.files]);
    });
    $("input").addEventListener("paste", (event) => {
      const files = [...(event.clipboardData?.files || [])];
      if (files.length) {
        event.preventDefault();
        this.addFiles(files);
      }
    });
    window.addEventListener("pagehide", () => this.stop(true));
    document.addEventListener("visibilitychange", () => {
      if (document.hidden && this.recording) this.stop(false);
    });
  }

  async enter() {
    const status = await this.data("/api/attachments/status");
    this.voiceReady = status.voice_ready;
    if (this.items.length) return;
    let identifiers = [];
    try {
      identifiers = JSON.parse(
        sessionStorage.getItem("trading-attachments") || "[]",
      );
    } catch {}
    const results = await Promise.allSettled(
      identifiers.slice(0, 6).map((id) => this.data("/api/attachments/" + id)),
    );
    for (const result of results) {
      if (result.status === "fulfilled")
        this.items.push({
          ...result.value,
          state: "ready",
          key: result.value.id,
        });
    }
    this.render();
  }

  size(bytes = 0) {
    return bytes > 1024 * 1024
      ? (bytes / 1024 / 1024).toFixed(1) + " MB"
      : Math.max(1, Math.round(bytes / 1024)) + " KB";
  }

  saveDraft() {
    try {
      sessionStorage.setItem(
        "trading-attachments",
        JSON.stringify(
          this.items
            .filter((item) => item.state === "ready")
            .map((item) => item.id),
        ),
      );
    } catch {}
  }

  async addFiles(files) {
    if (
      this.busy ||
      this.recording ||
      this.recordingPending ||
      this.processingRecording
    ) {
      this.toast("请先完成当前录音或回复。");
      return;
    }
    for (const file of files) {
      if (this.items.length >= 6) {
        this.toast("每次最多添加 6 个附件。");
        break;
      }
      if (file.size > 20 * 1024 * 1024) {
        this.toast(file.name + " 超过 20 MB，请缩小后上传。");
        continue;
      }
      const audio =
        file.type.startsWith("audio/") ||
        /\.(mp3|wav|m4a|webm|ogg|flac|mp4|aac)$/i.test(file.name);
      if (audio && !this.voiceReady) {
        this.toast("语音识别模型尚未安装，请先使用文字输入。");
        continue;
      }
      const item = {
        key: crypto.getRandomValues(new Uint32Array(2)).join("-"),
        name: file.name,
        size: file.size,
        kind: audio
          ? "audio"
          : file.type.startsWith("image/")
            ? "image"
            : "file",
        file,
        state: "uploading",
      };
      if (item.kind === "image") item.localPreview = URL.createObjectURL(file);
      this.items.push(item);
      this.render();
      this.upload(item);
    }
  }

  async upload(item) {
    item.state = "uploading";
    this.render();
    const controller = new AbortController();
    item.controller = controller;
    this.controllers.add(controller);
    const body = new FormData();
    body.append("file", item.file);
    try {
      const result = await this.data("/api/attachments", {
        method: "POST",
        body,
        signal: controller.signal,
      });
      if (!this.items.includes(item)) return;
      Object.assign(item, result, { state: "ready" });
      if (item.localPreview) {
        URL.revokeObjectURL(item.localPreview);
        delete item.localPreview;
      }
      if (result.kind === "audio") {
        this.onText(result.transcript);
        this.toast("录音已转成文字，请核对后发送。");
      } else if (result.warning) this.toast(result.warning);
    } catch (error) {
      if (error.name === "AbortError" || !this.items.includes(item)) return;
      item.state = "error";
      item.error = error.message;
      this.toast(error.message);
    } finally {
      this.controllers.delete(controller);
      this.render();
    }
  }

  remove(item) {
    item.controller?.abort();
    if (item.localPreview) URL.revokeObjectURL(item.localPreview);
    this.items = this.items.filter((value) => value !== item);
    this.render();
  }

  render() {
    const target = this.$("draft-attachments");
    target.replaceChildren();
    target.hidden = !this.items.length;
    for (const item of this.items) {
      const card = this.el("div", "attachment-card " + item.state),
        open = this.el("button", "attachment-open"),
        visual = this.el("span", "attachment-visual");
      open.type = "button";
      open.setAttribute(
        "aria-label",
        item.state === "error" ? "重试上传 " + item.name : "预览 " + item.name,
      );
      if (item.kind === "image" && (item.localPreview || item.id)) {
        const image = this.el("img");
        image.src =
          item.localPreview || "/api/attachments/" + item.id + "/preview";
        image.alt = "";
        visual.append(image);
      } else visual.append(this.icon(item.kind === "audio" ? "mic" : "note"));
      const info = this.el("span", "attachment-copy"),
        label =
          item.state === "uploading"
            ? item.kind === "audio"
              ? "上传并转写中…"
              : "上传并读取中…"
            : item.state === "error"
              ? "上传失败 · 点击重试"
              : item.kind === "image"
                ? "图片已就绪"
                : item.kind === "audio"
                  ? item.duration + " 秒 · 已转写"
                  : "已读取 " + item.text_length.toLocaleString() + " 字";
      info.append(this.el("strong", "", item.name), this.el("span", "", label));
      open.append(visual, info);
      open.disabled = item.state === "uploading";
      open.title = item.error || item.warning || item.name;
      open.onclick = () =>
        item.state === "error" ? this.upload(item) : this.preview(item);
      const remove = this.el("button", "attachment-remove");
      remove.type = "button";
      remove.setAttribute("aria-label", "移除 " + item.name);
      remove.append(this.icon("close"));
      remove.disabled = this.busy;
      remove.onclick = () => this.remove(item);
      card.append(open, remove);
      target.append(card);
    }
    this.saveDraft();
    this.onChange();
  }

  renderMessage(target, items) {
    if (!items?.length) return;
    const list = this.el("div", "message-attachments");
    for (const item of items) {
      const button = this.el("button", "sent-attachment " + item.kind);
      button.type = "button";
      button.disabled = !!item.missing;
      button.setAttribute("aria-label", "预览 " + item.name);
      if (item.kind === "image") {
        const image = this.el("img");
        image.src = "/api/attachments/" + item.id + "/preview";
        image.alt = item.name;
        image.loading = "lazy";
        button.append(image, this.el("span", "", item.name));
      } else {
        button.append(this.icon(item.kind === "audio" ? "mic" : "note"));
        const info = this.el("span", "sent-attachment-copy");
        info.append(
          this.el("strong", "", item.name),
          this.el(
            "span",
            "",
            item.kind === "audio"
              ? item.duration + " 秒 · 录音"
              : this.size(item.size),
          ),
        );
        button.append(info);
      }
      button.onclick = () => this.preview(item);
      list.append(button);
    }
    target.append(list);
  }

  async preview(item) {
    const dialog = this.$("attachment-dialog"),
      target = this.$("attachment-preview");
    this.$("attachment-title").textContent = item.name;
    this.$("attachment-info").textContent =
      this.size(item.size) + (item.warning ? " · " + item.warning : "");
    const download = this.$("attachment-download");
    download.href = "/api/attachments/" + item.id + "/content";
    download.download = item.name;
    target.replaceChildren();
    if (item.kind === "image") {
      const image = this.el("img", "large-attachment-image");
      image.src = "/api/attachments/" + item.id + "/preview";
      image.alt = item.name;
      target.append(image);
    } else if (item.kind === "audio") {
      const audio = this.el("audio");
      audio.controls = true;
      audio.src = "/api/attachments/" + item.id + "/content";
      target.append(
        audio,
        this.el(
          "p",
          "attachment-preview-label",
          "自动转写 · 以你发送时核对后的文字为准",
        ),
        this.el("p", "attachment-excerpt", item.transcript),
      );
    } else {
      target.append(
        this.el(
          "p",
          "attachment-preview-label",
          "已读取 " +
            item.text_length.toLocaleString() +
            " 字 · 以下为内容预览",
        ),
        this.el(
          "pre",
          "attachment-excerpt",
          item.preview_text ||
            "读取内容已随附件发送，完整内容可下载原文件查看。",
        ),
      );
    }
    dialog.showModal();
  }

  pending() {
    return (
      this.items.some((item) => item.state !== "ready") ||
      this.recording ||
      this.recordingPending ||
      this.processingRecording
    );
  }
  ready() {
    return this.items.filter((item) => item.state === "ready");
  }
  hasImages() {
    return this.ready().some((item) => item.kind === "image");
  }
  setBusy(value) {
    this.busy = value;
    for (const id of [
      "attach-image",
      "attach-file",
      "record-voice",
      "voice-upload",
    ])
      this.$(id).disabled = value;
    this.render();
  }
  clear() {
    for (const controller of this.controllers) controller.abort();
    for (const item of this.items)
      if (item.localPreview) URL.revokeObjectURL(item.localPreview);
    this.items = [];
    this.render();
  }

  async record() {
    if (
      this.busy ||
      this.recording ||
      this.recordingPending ||
      this.processingRecording
    )
      return;
    if (this.items.length >= 6) {
      this.toast("每次最多添加 6 个附件，请先移除一个。");
      return;
    }
    const $ = this.$;
    $("voice-panel").hidden = false;
    $("voice-panel").classList.remove("is-recording");
    $("voice-finish").hidden = true;
    $("voice-upload").hidden = false;
    $("voice-title").textContent = "语音输入";
    if (!this.voiceReady) {
      $("voice-description").textContent =
        "语音识别模型尚未安装，请先使用文字输入。";
      return;
    }
    if (
      !window.isSecureContext ||
      !navigator.mediaDevices?.getUserMedia ||
      !window.MediaRecorder
    ) {
      $("voice-description").textContent = window.isSecureContext
        ? "当前浏览器无法直接录音，可以上传已有录音。"
        : "直接录音需要 HTTPS。现在可以上传手机或电脑已有的录音。";
      return;
    }
    this.recordingPending = true;
    const version = ++this.recordVersion;
    $("voice-title").textContent = "正在打开麦克风…";
    this.onChange();
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!this.recordingPending || version !== this.recordVersion) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      this.media = stream;
      const mimeType = [
        "audio/webm;codecs=opus",
        "audio/mp4",
        "audio/ogg;codecs=opus",
      ].find((value) => MediaRecorder.isTypeSupported(value));
      this.recorder = new MediaRecorder(stream, mimeType ? { mimeType } : {});
      const recorder = this.recorder;
      const capture = { cancelled: false };
      this.capture = capture;
      const chunks = [];
      this.recorder.ondataavailable = (event) => {
        if (event.data.size) chunks.push(event.data);
      };
      this.recorder.onstop = () => {
        if (this.capture === capture) this.processingRecording = false;
        if (capture.cancelled || !chunks.length) {
          this.onChange();
          return;
        }
        const type = recorder.mimeType || chunks[0].type || "audio/webm";
        const extension = type.includes("mp4")
          ? "m4a"
          : type.includes("ogg")
            ? "ogg"
            : "webm";
        this.addFiles([
          new File(
            chunks,
            "语音 " +
              new Date()
                .toLocaleTimeString("zh-CN", { hour12: false })
                .replaceAll(":", "-") +
              "." +
              extension,
            { type },
          ),
        ]);
      };
      this.recorder.onerror = () => {
        this.stop(true);
        this.toast("录音未能保存，请重新录制或上传已有录音。");
      };
      this.recorder.start(250);
      this.recording = true;
      this.startedAt = Date.now();
      $("voice-panel").classList.add("is-recording");
      $("voice-upload").hidden = true;
      $("voice-finish").hidden = false;
      $("voice-description").textContent =
        "最长 2 分钟 · 完成后转成文字，核对再发送";
      const tick = () => {
        const seconds = Math.floor((Date.now() - this.startedAt) / 1000);
        $("voice-title").textContent =
          "正在录音 " +
          String(Math.floor(seconds / 60)).padStart(2, "0") +
          ":" +
          String(seconds % 60).padStart(2, "0");
        if (Date.now() - this.startedAt >= 119500) this.stop(false);
      };
      tick();
      this.timer = setInterval(tick, 250);
    } catch (error) {
      if (version !== this.recordVersion) return;
      this.media?.getTracks().forEach((track) => track.stop());
      $("voice-title").textContent = "暂时无法录音";
      $("voice-description").textContent =
        error.name === "NotAllowedError"
          ? "麦克风没有获准使用，可在浏览器设置中开启，或上传已有录音。"
          : "没有找到可用的麦克风，可以上传已有录音。";
    } finally {
      if (version === this.recordVersion) this.recordingPending = false;
      this.onChange();
    }
  }

  stop(cancel) {
    this.recordVersion++;
    this.recordingPending = false;
    if (this.capture) this.capture.cancelled = cancel;
    this.processingRecording = !cancel && this.recorder?.state === "recording";
    clearInterval(this.timer);
    this.recording = false;
    if (this.recorder?.state === "recording") this.recorder.stop();
    this.media?.getTracks().forEach((track) => track.stop());
    this.$("voice-panel").hidden = true;
    this.$("voice-panel").classList.remove("is-recording");
    this.onChange();
  }
}

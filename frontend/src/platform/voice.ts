import { NativeRecorder } from "./native-recorder";
import { authorizePrivacy } from "./privacy";
import type { SelectedFile } from "../domain/types";
export interface VoiceHooks {
  state: (phase: string, seconds: number) => void;
  complete: (file: SelectedFile) => void;
  error: (message: string) => void;
}
export function createVoice(hooks: VoiceHooks) {
  // #ifdef MP-WEIXIN
  {
    let cancelled = false;
    const native = new NativeRecorder({
      manager: wx.getRecorderManager(),
      onState: (s: any) => hooks.state(s.phase, s.seconds),
      onComplete: (f: any) =>
        hooks.complete({
          path: f.tempFilePath,
          name: "录音.mp3",
          size: f.size,
          duration: f.duration,
        }),
      onError: hooks.error,
    });
    return {
      async start() {
        cancelled = false;
        await authorizePrivacy();
        if (cancelled) return;
        const setting: any = await new Promise((resolve, reject) =>
          wx.getSetting({
            success: resolve,
            fail: () => reject(new Error("无法读取麦克风权限")),
          }),
        );
        if (setting.authSetting["scope.record"] === false)
          throw new Error("麦克风权限已关闭，请在设置中允许录音");
        await new Promise((resolve, reject) =>
          wx.authorize({
            scope: "scope.record",
            success: resolve,
            fail: () => reject(new Error("请允许麦克风权限，也可继续输入文字")),
          }),
        );
        if (!cancelled) native.start();
      },
      finish() {
        native.finish();
      },
      cancel() {
        cancelled = true;
        native.cancel();
      },
      dispose() {
        cancelled = true;
        native.dispose();
      },
    };
  }
  // #endif
  // #ifdef H5
  {
    let recorder: MediaRecorder | null = null,
      media: MediaStream | null = null,
      timer: any = null,
      seconds = 0,
      started = 0,
      cancelled = false,
      generation = 0;
    function clean() {
      clearInterval(timer);
      timer = null;
      media?.getTracks().forEach((t) => t.stop());
      media = null;
    }
    function stop(discard: boolean) {
      cancelled = discard;
      generation++;
      const stopping = recorder?.state === "recording";
      if (stopping) {
        recorder!.stop();
        hooks.state("stopping", seconds);
      } else hooks.state("idle", 0);
      clean();
    }
    return {
      async start() {
        if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia)
          throw new Error("浏览器录音需要 HTTPS 和麦克风支持");
        cancelled = false;
        const gen = ++generation;
        hooks.state("starting", 0);
        try {
          const acquired = await navigator.mediaDevices.getUserMedia({
            audio: true,
          });
          if (gen !== generation) {
            acquired.getTracks().forEach((t) => t.stop());
            return;
          }
          media = acquired;
          const mime = [
            "audio/webm;codecs=opus",
            "audio/mp4",
            "audio/ogg;codecs=opus",
          ].find((m) => MediaRecorder.isTypeSupported(m));
          recorder = new MediaRecorder(
            media,
            mime ? { mimeType: mime } : undefined,
          );
          const localRecorder = recorder;
          const parts: BlobPart[] = [];
          recorder.ondataavailable = (e) => {
            if (e.data.size) parts.push(e.data);
          };
          recorder.onstop = () => {
            const duration = Date.now() - started;
            const actual = localRecorder.mimeType;
            clean();
            hooks.state("idle", 0);
            if (cancelled) return;
            if (duration < 800) {
              hooks.error("录音太短，请说完一句话后再完成");
              return;
            }
            const suffix = actual.includes("mp4")
              ? "m4a"
              : actual.includes("ogg")
                ? "ogg"
                : "webm";
            const file = new File(parts, "录音." + suffix, { type: actual });
            hooks.complete({
              file,
              name: file.name,
              size: file.size,
              duration,
            });
          };
          recorder.onerror = () => {
            cancelled = true;
            clean();
            hooks.state("idle", 0);
            hooks.error("录音未能完成，请重试");
          };
          started = Date.now();
          seconds = 0;
          recorder.start();
          hooks.state("recording", 0);
          timer = setInterval(() => {
            seconds = Math.floor((Date.now() - started) / 1000);
            hooks.state("recording", seconds);
            if (seconds >= 119) stop(false);
          }, 250);
        } catch (e) {
          if (gen !== generation) return;
          clean();
          hooks.state("idle", 0);
          throw new Error("麦克风无法使用，请检查浏览器权限");
        }
      },
      finish() {
        stop(false);
      },
      cancel() {
        stop(true);
      },
      dispose() {
        stop(true);
      },
    };
  }
  // #endif
}

<script setup lang="ts">
import { onHide } from "@dcloudio/uni-app";
import { computed, ref, reactive, onMounted, onUnmounted, watch } from "vue";
import {
  request,
  stream,
  upload,
  attachmentLocation,
  releaseLocation,
} from "../platform/network";
import { chooseFiles } from "../platform/files";
import { createVoice } from "../platform/voice";
import { newId } from "../domain/types";
import MarkdownView from "./MarkdownView.vue";
const props = defineProps<{
  draft?: string;
  active?: boolean;
  context?: { id: string; title: string };
}>();
const emit = defineEmits(["changed", "busy"]);
const storageKey = (name: string) =>
  props.context
    ? "growth-chat-" + props.context.id + "-" + name
    : "trading-" + name + "-v2";
const sessions = ref<any[]>([]),
  session = ref(props.context ? "growth_" + props.context.id : newId()),
  messages = ref<any[]>([]),
  text = ref(""),
  files = ref<any[]>([]),
  busy = ref(false),
  uploading = ref(false),
  error = ref(""),
  remember = ref(true),
  phase = ref("idle"),
  seconds = ref(0),
  showHistory = ref(false),
  models = ref<any[]>([]),
  active = ref(""),
  scroll = ref("");
const previews = reactive<Record<string, string>>({}),
  failedVoice = ref<any>(null);
const currentModelIndex = computed(() =>
  Math.max(
    0,
    models.value.findIndex((m) => m.id === active.value),
  ),
);
const blocked = computed(
  () => busy.value || uploading.value || phase.value !== "idle",
);
watch(blocked, (v) => emit("busy", v));
const recoverable = ref<any>(null);
const runs = ref<any[]>([]),
  showRuns = ref(false),
  cooperation = ref(false),
  feedbackOpen = ref(false),
  feedbackNote = ref(""),
  feedbackNotice = ref(""),
  feedbackBusy = ref(false);
const filePreview = ref<any>(null);
const playing = ref(false);
let audio: any = null;
let alive = true;
function closeFile() {
  audio?.destroy();
  audio = null;
  playing.value = false;
  if (filePreview.value?.url) releaseLocation(filePreview.value.url);
  filePreview.value = null;
}
function toggleAudio() {
  if (!audio) return;
  if (playing.value) audio.pause();
  else audio.play();
}
function restoreDraft() {
  if (!recoverable.value) return;
  text.value = [text.value, recoverable.value.text].filter(Boolean).join("\n");
  files.value = [
    ...files.value,
    ...recoverable.value.files.filter(
      (f: any) => !files.value.some((v) => v.id === f.id),
    ),
  ].slice(0, 6);
  recoverable.value = null;
}
async function thumbnail(f: any) {
  if (f.kind !== "image" || previews[f.id]) return;
  try {
    const url = await attachmentLocation(f);
    if (alive) previews[f.id] = url;
    else releaseLocation(url);
  } catch {}
}
watch(
  files,
  (v) => {
    uni.setStorageSync(storageKey("attachments"), v);
    v.forEach(thumbnail);
  },
  { deep: true },
);
watch(remember, (v) => uni.setStorageSync(storageKey("remember"), v));
watch(session, (v) => uni.setStorageSync(storageKey("current-session"), v));
watch(
  () => props.draft,
  (v) => {
    if (v && text.value !== v)
      text.value = [text.value, v].filter(Boolean).join("\n");
  },
);
watch(text, (v) => uni.setStorageSync(storageKey("draft"), v));
async function refresh() {
  sessions.value = await request("/api/sessions");
  const m = await request("/api/models");
  models.value = m.models;
  active.value = m.active;
}
onMounted(async () => {
  text.value = uni.getStorageSync(storageKey("draft")) || props.draft || "";
  files.value = uni.getStorageSync(storageKey("attachments")) || [];
  const savedRemember = uni.getStorageSync(storageKey("remember"));
  if (typeof savedRemember === "boolean") remember.value = savedRemember;
  const previous = props.context
    ? "growth_" + props.context.id
    : uni.getStorageSync(storageKey("current-session"));
  try {
    await refresh();
    if (previous) await selectSession(previous);
    else uni.setStorageSync(storageKey("current-session"), session.value);
  } catch (e: any) {
    error.value = e.message;
  }
});
let bottomTimer: ReturnType<typeof setTimeout> | null = null;
function bottom() {
  if (bottomTimer) return;
  bottomTimer = setTimeout(() => {
    bottomTimer = null;
    if (!alive) return;
    scroll.value = "";
    setTimeout(() => {
      if (alive) scroll.value = "message-end";
    }, 20);
  }, 100);
}
async function selectSession(id: string) {
  if (blocked.value) return;
  try {
    messages.value = await request("/api/sessions/" + id);
    messages.value.forEach((m) => m.attachments?.forEach(thumbnail));
    session.value = id;
    runs.value = (
      await request(
        "/api/companion/collaborations?session_id=" + encodeURIComponent(id),
      )
    ).runs.map((r: any) => r.body);
    showRuns.value = false;
    feedbackNotice.value = "";
    feedbackOpen.value = false;
    showHistory.value = false;
    bottom();
  } catch (e: any) {
    if (e.status === 404) {
      session.value = props.context ? "growth_" + props.context.id : newId();
      messages.value = [];
      runs.value = [];
      showHistory.value = false;
      error.value = "";
      return;
    }
    error.value = e.message;
  }
}
function newChat() {
  if (blocked.value) return;
  session.value = newId();
  messages.value = [];
  runs.value = [];
  feedbackNotice.value = "";
  feedbackOpen.value = false;
  showHistory.value = false;
}
async function add(selected: any[]) {
  uploading.value = true;
  error.value = "";
  cooperation.value = false;
  feedbackNotice.value = "";
  feedbackOpen.value = false;
  try {
    for (const f of selected) {
      if (files.value.length >= 6) break;
      files.value.push(await upload(f));
    }
  } catch (e: any) {
    error.value = e.message;
  } finally {
    uploading.value = false;
  }
}
async function choose(kind: "image" | "file") {
  try {
    await add(await chooseFiles(kind, 6 - files.value.length));
  } catch (e: any) {
    error.value = e.message;
  }
}
watch(
  () => props.active,
  (v) => {
    if (v === false) voice?.cancel();
  },
);
onHide(() => voice?.cancel());
async function transcribe(f: any) {
  if (!alive) return;
  if (files.value.length >= 6) {
    failedVoice.value = f;
    error.value = "请先移除一个附件，再重试转写";
    return;
  }
  uploading.value = true;
  error.value = "";
  try {
    const item = await upload(f);
    if (!alive) return;
    files.value.push(item);
    if (item.transcript)
      text.value = [text.value, item.transcript].filter(Boolean).join("\n");
    failedVoice.value = null;
  } catch (e: any) {
    error.value = e.message;
    failedVoice.value = f;
  } finally {
    uploading.value = false;
  }
}
const voice = createVoice({
  state: (p, s) => {
    phase.value = p;
    seconds.value = s;
  },
  complete: transcribe,
  error: (m) => (error.value = m),
});
async function record() {
  try {
    await voice!.start();
  } catch (e: any) {
    error.value = e.message;
  }
}
onUnmounted(() => {
  emit("busy", false);
  alive = false;
  voice?.dispose();
  closeFile();
  Object.values(previews).forEach(releaseLocation);
});
async function send() {
  if (
    busy.value ||
    uploading.value ||
    phase.value !== "idle" ||
    (!text.value.trim() && !files.value.length)
  )
    return;
  recoverable.value = null;
  let streamError = false;
  const content = text.value.trim(),
    attached = [...files.value];
  messages.value.push({ role: "user", text: content, attachments: attached });
  const reply = { role: "assistant", text: "" };
  messages.value.push(reply);
  text.value = "";
  files.value = [];
  busy.value = true;
  error.value = "";
  cooperation.value = false;
  feedbackNotice.value = "";
  feedbackOpen.value = false;
  bottom();
  try {
    await stream(
      {
        session_id: session.value,
        message_id: newId(),
        text: content,
        attachment_ids: attached.map((f) => f.id),
        remember: remember.value,
        context_id: props.context?.id,
      },
      (event) => {
        if (
          event.type === "tool_use" &&
          event.payload.tool_name === "collaborate"
        )
          cooperation.value = true;
        if (
          event.type === "tool_result" &&
          event.payload.tool_name === "collaborate" &&
          event.payload.result?.members
        ) {
          runs.value.unshift(event.payload.result);
          cooperation.value = false;
        }
        if (event.type === "assistant") {
          messages.value[messages.value.length - 1].text += event.payload.text;
          bottom();
        }
        if (event.type === "error") {
          streamError = true;
          error.value = event.payload.text;
        }
      },
    );
    if (streamError) recoverable.value = { text: content, files: attached };
    await refresh();
    emit("changed");
  } catch (e: any) {
    error.value = e.message;
    recoverable.value = { text: content, files: attached };
  } finally {
    const last = messages.value[messages.value.length - 1];
    if (last?.role === "assistant" && !last.text) messages.value.pop();
    busy.value = false;
    cooperation.value = false;
  }
}
async function feedback(rating: string) {
  if (feedbackBusy.value || !remember.value) return;
  feedbackBusy.value = true;
  try {
    await request(
      "/api/companion/feedback",
      {
        target_id: props.context?.id,
        session_id: session.value,
        rating,
        note: rating === "adjust" ? feedbackNote.value : "",
        key: newId(),
      },
      "POST",
    );
    feedbackNotice.value = "已记住，会用于后续同类任务。";
    feedbackOpen.value = false;
    feedbackNote.value = "";
    emit("changed");
  } catch (e: any) {
    error.value = e.message;
  } finally {
    feedbackBusy.value = false;
  }
}
async function modelChange(e: any) {
  try {
    const m = models.value[Number(e.detail.value)];
    if (!m.configured)
      throw new Error("这个模型尚未配置，请先补齐服务端模型配置");
    await request("/api/models/select", { model: m.id }, "POST");
    active.value = m.id;
  } catch (e: any) {
    error.value = e.message;
  }
}
async function downloadOriginal(f: any) {
  try {
    const location = await attachmentLocation(f);
    // #ifdef H5
    const link = document.createElement("a");
    link.href = location;
    link.download = f.name;
    link.click();
    setTimeout(() => releaseLocation(location), 60000);
    // #endif
    // #ifdef MP-WEIXIN
    const fileType = f.name.split(".").pop()?.toLowerCase();
    if (
      !["doc", "docx", "xls", "xlsx", "ppt", "pptx", "pdf"].includes(fileType)
    )
      throw new Error("这类文件可在内容预览中阅读，微信暂不支持打开原文件");
    await new Promise((resolve, reject) =>
      wx.openDocument({
        filePath: location,
        fileType,
        showMenu: true,
        success: resolve,
        fail: () => reject(new Error("文件无法打开，可先阅读内容预览")),
      }),
    );
    // #endif
  } catch (e: any) {
    error.value = e.message;
  }
}
async function preview(f: any) {
  try {
    if (f.missing) throw new Error("这个附件已不可用，请重新添加");
    if (f.kind === "image") {
      const location = previews[f.id] || (await attachmentLocation(f));
      previews[f.id] = location;
      uni.previewImage({ urls: [location] });
      return;
    }
    closeFile();
    filePreview.value = f;
    if (f.kind === "audio") {
      const location = await attachmentLocation(f);
      if (filePreview.value?.id !== f.id || !alive) {
        releaseLocation(location);
        return;
      }
      filePreview.value = { ...f, url: location };
      audio = uni.createInnerAudioContext();
      audio.src = location;
      audio.onPlay(() => (playing.value = true));
      audio.onPause(() => (playing.value = false));
      audio.onEnded(() => (playing.value = false));
      audio.onError(() => {
        playing.value = false;
        error.value = "录音无法播放，请关闭后重试";
      });
    }
  } catch (e: any) {
    error.value = e.message;
  }
}
</script>
<template>
  <view class="chat-layout"
    ><view class="chat-toolbar"
      ><text v-if="context" class="muted">围绕「{{ context.title }}」继续</text
      ><button v-else class="quiet" @tap="showHistory = !showHistory">
        历史对话</button
      ><button v-if="!context" class="quiet" :disabled="blocked" @tap="newChat">
        ＋ 新对话</button
      ><picker
        :value="currentModelIndex"
        :range="
          models.map(
            (m) =>
              m.label +
              (m.configured
                ? m.vision
                  ? ' · 可读图'
                  : ' · 文字'
                : ' · 未配置'),
          )
        "
        :disabled="blocked"
        @change="modelChange"
        ><view class="model-select"
          >{{ models.find((m) => m.id === active)?.label || "选择模型"
          }}{{
            models.find((m) => m.id === active)?.vision ? " · 可读图" : ""
          }}
          ▾</view
        ></picker
      ></view
    ><view v-if="showHistory" class="history-panel"
      ><button
        v-for="s in sessions"
        :key="s.id"
        class="history-item"
        :disabled="blocked"
        @tap="selectSession(s.id)"
      >
        {{ s.title || "未命名对话" }}</button
      ><view v-if="!sessions.length" class="empty">还没有历史对话</view></view
    ><scroll-view
      scroll-y
      :scroll-into-view="scroll"
      :scroll-with-animation="!busy"
      class="conversation"
      ><view v-if="!messages.length && context" class="growth-chat-welcome"
        ><view class="section-title">背景已经带入，接着聊就好。</view
        ><view class="muted"
          >结合当前正文、来源和相关案例；整理建议需要你审阅后才更新正文。</view
        ></view
      ><view v-else-if="!messages.length" class="welcome"
        ><text class="eyebrow">与你一起，持续复盘</text
        ><view class="welcome-title">把想法说出来，<br />一起理清下一步。</view
        ><view class="muted">记录交易、回看经验，或慢慢打磨你的交易体系。</view
        ><view class="suggestions"
          ><button @tap="text = '帮我梳理最近的交易经验'">回看交易经验 ↗</button
          ><button @tap="text = '一起检查我的交易计划'">检查交易计划 ↗</button
          ><button @tap="text = '回顾我的交易体系有哪些待验证的认识'">
            梳理交易体系 ↗
          </button></view
        ></view
      ><view v-for="(m, i) in messages" :key="i" :class="['message', m.role]"
        ><view class="message-label">{{
          m.role === "user" ? "我" : "交易伙伴"
        }}</view
        ><MarkdownView
          :text="
            m.text ||
            (busy && i === messages.length - 1
              ? cooperation
                ? '伙伴正在分别检查…'
                : '正在思考…'
              : '')
          "
        /><view
          v-for="f in m.attachments"
          :key="f.id"
          class="attachment"
          @tap="preview(f)"
          ><image
            v-if="f.kind === 'image' && previews[f.id]"
            :src="previews[f.id]"
            mode="widthFix"
            class="attachment-image"
          /><text
            >{{
              f.kind === "image" ? "图片" : f.kind === "audio" ? "录音" : "文件"
            }}
            · {{ f.name }}</text
          ><view v-if="f.transcript" class="muted">{{
            f.transcript
          }}</view></view
        ></view
      ><view v-if="runs.length" class="companion-run"
        ><button class="quiet" @tap="showRuns = !showRuns">
          {{ showRuns ? "收起" : "查看" }}协作记录 ·
          {{ runs[0].members.map((m: any) => m.name).join(" / ") }}
          {{ showRuns ? "↑" : "↓" }}</button
        ><template v-if="showRuns"
          ><view v-for="(r, ri) in runs" :key="r.id || ri"
            ><view class="muted">{{ r.task }}</view
            ><view v-for="m in r.members" :key="m.id" class="companion-agent"
              ><view class="subheading"
                >{{ m.name }} ·
                {{
                  m.status === "complete"
                    ? "已完成"
                    : m.status === "partial"
                      ? "仍有缺口"
                      : "未完成"
                }}</view
              ><MarkdownView :text="m.text" /></view></view
          ><view class="muted"
            >独立检查意见由主伙伴综合，不能直接成为正式纪律。</view
          ></template
        ></view
      >
      <view
        v-if="
          !busy &&
          remember &&
          messages.some((m: any) => m.role === 'assistant' && m.text)
        "
        class="companion-feedback"
        ><text v-if="feedbackNotice" class="muted">{{ feedbackNotice }}</text
        ><template v-else
          ><text class="muted">这次协助</text
          ><button
            class="quiet"
            :disabled="feedbackBusy"
            @tap="feedback('helpful')"
          >
            有帮助</button
          ><button
            class="quiet"
            :disabled="feedbackBusy"
            @tap="feedbackOpen = !feedbackOpen"
          >
            希望调整</button
          ><view v-if="feedbackOpen" class="companion-feedback-input">
            <textarea
              v-model="feedbackNote"
              maxlength="2000"
              auto-height
              placeholder="哪里没帮到你，希望下次怎样做？"
            /><button
              class="primary"
              :disabled="feedbackBusy || !feedbackNote.trim()"
              @tap="feedback('adjust')"
            >
              记住这个反馈
            </button></view
          ></template
        ></view
      >
      <view id="message-end" class="scroll-end" /></scroll-view
    ><view class="composer"
      ><view v-if="error" class="notice error">{{ error }}</view
      ><view v-if="recoverable" class="notice"
        ><text>发送未完成。先查看历史确认结果，避免重复记录。</text
        ><button class="reference-link" @tap="restoreDraft">
          找回原稿与附件 ↗
        </button></view
      ><view v-if="failedVoice" class="recording"
        ><text>录音仍保留在本机</text
        ><button
          class="quiet"
          :disabled="uploading"
          @tap="transcribe(failedVoice)"
        >
          重试转写</button
        ><button class="quiet" :disabled="uploading" @tap="failedVoice = null">
          放弃
        </button></view
      ><view v-if="files.some((f) => f.kind === 'audio')" class="muted"
        >录音转写已填入输入框，可修改后发送。</view
      ><view class="attachment-list"
        ><view v-for="(f, i) in files" :key="f.id" class="attachment"
          ><image
            v-if="f.kind === 'image' && previews[f.id]"
            :src="previews[f.id]"
            mode="widthFix"
            class="attachment-image"
            @tap="preview(f)"
          /><view @tap="preview(f)">{{ f.name }}</view
          ><view v-if="f.transcript" class="muted">{{ f.transcript }}</view
          ><button class="quiet" @tap="files.splice(i, 1)">移除</button></view
        ></view
      ><view v-if="phase !== 'idle'" class="recording"
        ><text
          >●
          {{
            phase === "recording"
              ? `正在录音 · ${seconds} 秒`
              : phase === "stopping"
                ? "正在结束录音…"
                : "正在准备录音…"
          }}</text
        ><button class="quiet" @tap="voice?.cancel()">取消</button
        ><button
          class="primary"
          :disabled="phase !== 'recording'"
          @tap="voice?.finish()"
        >
          完成录音
        </button></view
      ><textarea
        v-else
        v-model="text"
        class="input-message"
        placeholder="说说你的想法，也可以添加图片、文件或录音…"
        maxlength="16000"
        auto-height
      /><view class="composer-actions"
        ><view class="input-tools"
          ><button
            class="quiet"
            :disabled="
              busy || uploading || phase !== 'idle' || files.length >= 6
            "
            @tap="choose('image')"
          >
            图片</button
          ><button
            class="quiet"
            :disabled="
              busy || uploading || phase !== 'idle' || files.length >= 6
            "
            @tap="choose('file')"
          >
            文件</button
          ><button
            class="quiet"
            :disabled="
              busy || uploading || phase !== 'idle' || files.length >= 6
            "
            @tap="record"
          >
            录音
          </button></view
        ><button
          class="primary"
          :disabled="
            busy ||
            uploading ||
            phase !== 'idle' ||
            (!text.trim() && !files.length)
          "
          @tap="send"
        >
          {{ busy ? "回复中…" : uploading ? "上传中…" : "发送 ↗" }}
        </button></view
      ><view class="remember" @tap="remember = !remember"
        ><text>{{ remember ? "☑" : "☐" }} 允许归集到个人资料</text
        ><text class="muted">候选认识会保留待验证状态</text></view
      ></view
    ><view v-if="filePreview" class="overlay" @tap.self="closeFile"
      ><view class="detail-sheet"
        ><view class="detail-head"
          ><view class="section-title">{{ filePreview.name }}</view
          ><button @tap="closeFile">关闭</button></view
        ><scroll-view scroll-y class="detail-scroll">
          <view v-if="filePreview.kind === 'audio'" class="notice">
            <button class="primary" :disabled="!audio" @tap="toggleAudio">
              {{ playing ? "暂停播放" : "播放录音" }}
            </button>
            <view class="muted"
              >原始录音 · {{ filePreview.duration || "—" }} 秒</view
            >
          </view>
          <view class="subheading">{{
            filePreview.kind === "audio"
              ? "原始转写（发送前可修改）"
              : "内容预览"
          }}</view>
          <MarkdownView
            :text="
              filePreview.transcript ||
              filePreview.preview_text ||
              '此附件暂无可读预览。'
            "
          />
          <view v-if="filePreview.kind === 'file'" class="muted"
            >预览展示前 3,000 字，模型最多读取 60,000 字。{{
              filePreview.warning || ""
            }}</view
          >
          <view v-if="error" class="notice error">{{ error }}</view>
        </scroll-view>
        <view v-if="filePreview.kind === 'file'" class="detail-bottom"
          ><button class="primary" @tap="downloadOriginal(filePreview)">
            打开或下载原文件 ↗
          </button></view
        >
      </view></view
    ></view
  >
</template>

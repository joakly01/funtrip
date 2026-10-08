<script setup lang="ts">
import { ref, onMounted, nextTick, watch } from "vue";
import { onHide, onPageScroll } from "@dcloudio/uni-app";
import { request, login, onUnauthorized } from "../../platform/network";
import {
  privacy,
  acceptPrivacy,
  rejectPrivacy,
  openPrivacy,
} from "../../platform/privacy";
import { VERSION } from "../../domain/types";
import ChatView from "../../components/ChatView.vue";
import GrowthWorkspace from "../../components/GrowthWorkspace.vue";
import RecordDetail from "../../components/RecordDetail.vue";
import ToolPanel from "../../components/ToolPanel.vue";
const authorized = ref(false),
  checking = ref(true),
  password = ref(""),
  error = ref(""),
  pending = ref(false),
  tab = ref("chat"),
  detail = ref(""),
  tool = ref(""),
  classic = ref<any>(null),
  draft = ref(""),
  revision = ref(0);
const freeChat = ref(false),
  conversationBusy = ref(false),
  navigationRevision = ref(0),
  scrollTop = ref(0);
onPageScroll((e) => {
  scrollTop.value = e.scrollTop;
});
watch([tab, freeChat], async () => {
  await nextTick();
  uni.pageScrollTo({ scrollTop: 0, duration: 0 });
});
const tabs = [
  ["chat", "工作台"],
  ["library", "知识库"],
  ["system", "交易体系"],
];
onUnauthorized(() => {
  authorized.value = false;
  password.value = "";
});
onMounted(async () => {
  try {
    await request("/api/models");
    authorized.value = true;
  } catch (e: any) {
    if (e.status !== 401) error.value = "暂时无法连接服务，请检查网络后重试";
  } finally {
    checking.value = false;
  }
});
async function unlock() {
  pending.value = true;
  error.value = "";
  try {
    await login(password.value);
    password.value = "";
    authorized.value = true;
  } catch (e: any) {
    error.value = e.message;
  } finally {
    pending.value = false;
  }
}
async function logout() {
  try {
    await request("/api/logout", {}, "POST");
  } finally {
    authorized.value = false;
  }
}
function discuss(t: string) {
  draft.value = "";
  nextTick(() => (draft.value = t));
  detail.value = "";
  classic.value = null;
  tool.value = "";
  tab.value = "chat";
  freeChat.value = true;
}
</script>
<template>
  <view class="app-shell"
    ><view v-if="checking" class="login-shell"
      ><view class="brand">有趣的旅途</view
      ><text class="muted">正在打开你的工作台…</text></view
    ><view v-else-if="!authorized" class="login-shell"
      ><view class="login-card"
        ><text class="eyebrow">学习 · 判断 · 复盘</text
        ><view class="welcome-title">有趣的旅途</view
        ><view class="muted"
          >记录、理解、验证。<br />让每一次经历，成为下一步的依据。</view
        ><input
          v-model="password"
          password
          placeholder="输入访问密码"
          @confirm="unlock"
        /><view v-if="error" class="notice error">{{ error }}</view
        ><button class="primary" :disabled="pending || !password" @tap="unlock">
          {{ pending ? "正在打开…" : "进入我的工作台 ↗" }}</button
        ><text class="version">统一版本 {{ VERSION }}</text></view
      ></view
    ><template v-else
      ><view class="app-header"
        ><view class="brand">有趣的旅途<text class="brand-dot">●</text></view
        ><view class="nav"
          ><button
            v-for="t in tabs"
            :key="t[0]"
            :class="['nav-item', { active: tab === t[0] }]"
            :disabled="conversationBusy"
            @tap="
              tab = t[0];
              freeChat = false;
              navigationRevision++;
            "
          >
            {{ t[1] }}
          </button></view
        ><button class="quiet exit" :disabled="conversationBusy" @tap="logout">
          退出
        </button></view
      ><view class="main"
        ><view v-if="freeChat" class="growth-chat-back"
          ><button
            class="quiet"
            :disabled="conversationBusy"
            @tap="freeChat = false"
          >
            ← 回到工作台
          </button></view
        ><ChatView
          v-show="freeChat"
          :active="freeChat"
          :draft="draft"
          @busy="conversationBusy = $event"
          @changed="revision++" /><GrowthWorkspace
          v-show="!freeChat"
          :mode="tab"
          :revision="revision"
          :navigation-revision="navigationRevision"
          :scroll-top="scrollTop"
          @record="
            detail = $event;
            classic = null;
          "
          @classic="
            classic = $event;
            detail = '';
          "
          @chat="discuss"
          @busy="conversationBusy = $event"
          @changed="revision++"
          @tool="tool = $event" /></view
      ><view class="app-foot">私人工作台 · 统一版本 {{ VERSION }}</view
      ><RecordDetail
        v-if="detail || classic"
        :id="detail"
        :classic="classic"
        @close="
          detail = '';
          classic = null;
        "
        @open="
          detail = $event;
          classic = null;
        "
        @discuss="discuss"
        @changed="revision++" />
      <ToolPanel
        v-if="tool && !detail"
        :key="tool"
        :id="tool"
        @close="tool = ''"
        @open="
          detail = $event;
          classic = null;
        "
        @discuss="discuss" /></template
    ><view v-if="privacy.visible" class="overlay"
      ><view class="privacy-card"
        ><view class="section-title">录音与文件授权</view
        ><view class="muted"
          >选择录音、图片或文件时，需要先阅读并同意隐私保护指引。</view
        ><button class="reference-link" @tap="openPrivacy">
          阅读 {{ privacy.title }} ↗</button
        ><button
          class="primary"
          id="agree-privacy"
          open-type="agreePrivacyAuthorization"
          @agreeprivacyauthorization="acceptPrivacy"
        >
          同意并继续</button
        ><button class="quiet" @tap="rejectPrivacy">暂不使用</button></view
      ></view
    ></view
  >
</template>

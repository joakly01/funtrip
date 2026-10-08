<script setup lang="ts">
import { ref, onMounted, computed } from "vue";
import { request } from "../platform/network";
import { fields, labels } from "../domain/growth";
import { sections } from "../domain/records";
import MarkdownView from "./MarkdownView.vue";
const props = defineProps<{
  source: any;
  busy?: boolean;
  hideLatest?: boolean;
  allowRefresh?: boolean;
  errorMessage?: string;
}>();
defineEmits(["close", "latest", "refresh"]);
const row = ref<any>(null),
  error = ref(""),
  current = ref<any>(null),
  loading = ref(false),
  showCurrent = ref(false);
const displayed = computed(() =>
  showCurrent.value ? current.value : row.value,
);
const parts = computed(() =>
  !displayed.value
    ? []
    : fields[displayed.value.kind]
      ? fields[displayed.value.kind]
          .filter((k) => displayed.value.body[k])
          .map((k) => ({
            key: k,
            label: labels[k],
            text: displayed.value.body[k],
          }))
      : sections(displayed.value),
);
async function compare() {
  loading.value = true;
  error.value = "";
  try {
    current.value = await request(
      "/api/records/" + encodeURIComponent(props.source.id),
    );
    showCurrent.value = true;
  } catch (e: any) {
    error.value = e.message;
  } finally {
    loading.value = false;
  }
}
onMounted(async () => {
  try {
    row.value = await request(
      "/api/records/" +
        encodeURIComponent(props.source.id) +
        "?version=" +
        props.source.version,
    );
  } catch (e: any) {
    error.value = e.message;
  }
});
</script>
<template>
  <view class="overlay" @tap.self="$emit('close')"
    ><view class="detail-sheet">
      <view class="detail-head"
        ><view
          ><text class="eyebrow"
            >{{ showCurrent ? "当前来源" : "引用时的来源" }} · 第
            {{ displayed?.version || source.version }} 版</text
          ><view class="section-title">{{
            displayed?.body.title || source.title
          }}</view></view
        ><button class="quiet" @tap="$emit('close')">关闭</button></view
      >
      <scroll-view scroll-y class="detail-scroll"
        ><view v-if="error || errorMessage" class="notice error">{{
          error || errorMessage
        }}</view>
        <view v-if="!row && !error" class="empty">正在读取当时的内容…</view>
        <view v-if="source.changed" class="growth-context-note">{{
          showCurrent
            ? "正在查阅最新内容。原引用版本仍然保留，可随时切回对照。"
            : "来源之后有过修改。这里保留的是当时引用的内容。"
        }}</view>
        <view v-if="source.changed && !hideLatest" class="growth-actions"
          ><button class="quiet" :disabled="loading || busy" @tap="compare">
            {{ loading ? "正在读取…" : "对照最新内容" }}</button
          ><button
            v-if="current"
            class="quiet"
            @tap="showCurrent = !showCurrent"
          >
            {{ showCurrent ? "切回引用时内容" : "切回最新内容" }}
          </button></view
        >
        <view v-for="part in parts" :key="part.key" class="detail-section"
          ><view class="subheading">{{ part.label }}</view
          ><MarkdownView :text="part.text"
        /></view>
        <view
          v-if="
            allowRefresh &&
            source.managed &&
            current &&
            current.version !== source.version
          "
          class="growth-context-note"
          >更新引用会保留旧版本记录。正文中的理解仍需你检查和修改。<button
            class="primary"
            :disabled="busy"
            @tap="$emit('refresh', source.id)"
          >
            已对照，更新此引用
          </button></view
        >
        <button
          v-if="!hideLatest"
          class="quiet"
          :disabled="busy"
          @tap="$emit('latest')"
        >
          打开当前版本 →
        </button>
      </scroll-view>
    </view></view
  >
</template>

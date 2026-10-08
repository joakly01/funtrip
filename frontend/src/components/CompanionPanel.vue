<script setup lang="ts">
import { ref, onMounted } from "vue";
import { request } from "../platform/network";
import { newId } from "../domain/types";
const emit = defineEmits(["close", "open", "changed"]);
const data = ref<any>(null),
  style = ref("balanced"),
  focus = ref(""),
  busy = ref(false),
  error = ref(""),
  notice = ref("");
async function load() {
  try {
    data.value = await request("/api/companion");
    style.value = data.value.preference.style;
    focus.value = data.value.preference.focus;
  } catch (e: any) {
    error.value = e.message;
  }
}
onMounted(load);
async function save() {
  busy.value = true;
  error.value = "";
  notice.value = "";
  try {
    await request(
      "/api/companion/preferences",
      { style: style.value, focus: focus.value, key: newId() },
      "POST",
    );
    notice.value = "已记住，下次协助会带入这些偏好。";
    await load();
    emit("changed");
  } catch (e: any) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
async function curate() {
  busy.value = true;
  error.value = "";
  notice.value = "";
  try {
    const r = await request("/api/companion/curate", {}, "POST");
    notice.value = r.message;
    await load();
    emit("changed");
  } catch (e: any) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <view class="companion-panel">
    <view class="growth-title-row"
      ><view
        ><text class="eyebrow">一起积累，也一起校正</text
        ><view class="page-title">伙伴与成长</view></view
      ><button class="quiet" :disabled="busy" @tap="$emit('close')">
        ← 返回
      </button></view
    >
    <view v-if="error" class="notice error"
      >{{ error }}<button class="quiet" @tap="load">重试</button></view
    >
    <view v-if="notice" class="growth-saved">{{ notice }}</view>
    <template v-if="data">
      <view class="companion-section"
        ><view class="section-title">越来越懂你的做事方式</view
        ><view class="muted"
          >反馈会用于后续同类任务，相关复盘与已采用的整理会成为下一次的背景。</view
        >
        <view class="growth-tabs"
          ><button
            v-for="s in [
              ['concise', '简洁直接'],
              ['balanced', '重点清楚'],
              ['detailed', '展开解释'],
            ]"
            :key="s[0]"
            :class="{ selected: style === s[0] }"
            :disabled="busy"
            @tap="style = s[0]"
          >
            {{ s[1] }}
          </button></view
        >
        <textarea
          v-model="focus"
          maxlength="2000"
          auto-height
          placeholder="希望伙伴记住什么？例如：先讲判断依据，再说缺口；一次只推进一个问题。"
        />
        <button class="primary" :disabled="busy" @tap="save">
          {{ busy ? "正在保存…" : "记住我的偏好" }}
        </button>
      </view>
      <view v-if="data.revisit.length" class="companion-section"
        ><view class="section-title">值得再看一眼</view
        ><button
          v-for="r in data.revisit"
          :key="r.id"
          class="growth-list-row"
          @tap="$emit('open', r.id)"
        >
          <view
            ><text class="growth-row-title">{{ r.title }}</text
            ><text class="growth-row-summary">{{ r.reason }}</text></view
          ><text>→</text>
        </button></view
      >
      <view class="companion-section"
        ><view class="section-title">留下的成长</view
        ><view v-if="!data.history.length" class="muted"
          >在对话中留下反馈，或完成一次复盘，这里就会记下改变。</view
        ><view v-for="r in data.history" :key="r.id" class="companion-timeline"
          ><text class="eyebrow">{{ r.date.slice(0, 10) }}</text
          ><view class="subheading">{{ r.title }}</view
          ><view class="growth-prose">{{ r.text }}</view
          ><button
            v-if="r.target_id"
            class="quiet"
            @tap="$emit('open', r.target_id)"
          >
            回到相关内容 →
          </button></view
        ></view
      >
      <view class="companion-section"
        ><view class="section-title">同一个伙伴，几种专长</view
        ><view class="muted"
          >任务需要时，主伙伴会邀请专职伙伴分别检查，再汇总意见与分歧。</view
        ><view class="companion-agent-grid"
          ><view
            v-for="a in data.capabilities.agents"
            :key="a.id"
            class="companion-agent"
            ><view class="subheading">{{ a.name }}</view
            ><view class="muted">{{ a.description }}</view></view
          ></view
        ><view class="growth-hint"
          >技能提供流程，工具共享复用。正式准则和个人采用状态仍由你决定。</view
        ></view
      >
      <view class="companion-section"
        ><view class="section-title">把已有积累接进来</view
        ><view class="muted"
          >将历史记录与已有经典整理为专题和笔记，保留原文与来源，等你校对。</view
        ><button class="quiet" :disabled="busy" @tap="curate">
          {{ busy ? "正在整理…" : "整理新增资料 →" }}
        </button></view
      >
    </template>
  </view>
</template>

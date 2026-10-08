<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { request } from "../platform/network";
import {
  title,
  sections,
  states,
  stateNames,
  date,
  classicSections,
  readable,
} from "../domain/records";
import MarkdownView from "./MarkdownView.vue";
import { shelves, topics, shelfName } from "../domain/knowledge";
const props = defineProps<{ id?: string; classic?: any }>();
const emit = defineEmits(["close", "open", "discuss", "changed"]);
const row = ref<any>(null),
  error = ref(""),
  loading = ref(false),
  showHistory = ref(false);
const organizing = ref(false),
  saving = ref(false),
  saved = ref(false),
  shelf = ref(""),
  topic = ref("general"),
  starred = ref(false);
const shelfOptions = shelves.filter((s) => s.key !== "classics");
const topicKeys = Object.keys(topics);
let version = 0;
watch(
  () => props.id,
  async (id) => {
    const n = ++version;
    row.value = null;
    showHistory.value = false;
    organizing.value = false;
    saved.value = false;
    error.value = "";
    if (!id) {
      loading.value = false;
      return;
    }
    loading.value = true;
    try {
      const value = await request("/api/records/" + encodeURIComponent(id));
      if (n === version) {
        row.value = value;
        shelf.value = value.collection.shelf;
        topic.value = value.collection.topic;
        starred.value = value.collection.starred;
      }
    } catch (e: any) {
      if (n === version) error.value = e.message;
    } finally {
      if (n === version) loading.value = false;
    }
  },
  { immediate: true },
);
const parts = computed(() => (row.value ? sections(row.value) : []));
const classicParts = computed(() =>
  props.classic ? classicSections(props.classic) : [],
);
async function save() {
  if (!row.value || saving.value) return;
  const n = version;
  saving.value = true;
  error.value = "";
  saved.value = false;
  try {
    const value = await request(
      "/api/knowledge/" + encodeURIComponent(row.value.id),
      { shelf: shelf.value, topic: topic.value, starred: starred.value },
      "PUT",
    );
    emit("changed");
    if (n === version) {
      row.value.collection = value;
      saved.value = true;
    }
  } catch (e: any) {
    if (n === version) error.value = e.message;
  } finally {
    saving.value = false;
  }
}
</script>
<template>
  <view class="overlay" @tap.self="$emit('close')"
    ><view class="detail-sheet"
      ><view class="detail-head"
        ><view
          ><text class="eyebrow">{{ classic ? "经典资料" : "资料详情" }}</text
          ><view class="section-title">{{
            classic?.title ||
            (row ? title(row) : error ? "暂时无法读取" : "正在读取…")
          }}</view></view
        ><button class="icon-button" @tap="$emit('close')">关闭</button></view
      ><scroll-view scroll-y class="detail-scroll"
        ><view v-if="loading" class="empty">正在找回资料…</view
        ><view v-if="error" class="notice error">{{ error }}</view
        ><template v-if="row"
          ><view class="muted"
            >{{ shelfName(row.collection?.shelf) }} ·
            {{ topics[row.collection?.topic] || "未分类" }}<br />{{
              date(row.created_at)
            }}
            · 第 {{ row.version }} 版</view
          ><view v-for="part in parts" :key="part.key" class="detail-section"
            ><view class="subheading">{{ part.label }}</view
            ><MarkdownView :text="part.text" /></view
          ><view class="detail-section"
            ><button class="reference-link" @tap="organizing = !organizing">
              {{ organizing ? "收起归档管理 ↑" : "调整归档、主题与收藏 ↓" }}
            </button>
            <view v-if="organizing" class="organize-panel">
              <view class="filter-inner"
                ><picker
                  :disabled="saving || row.collection.archive"
                  :value="shelfOptions.findIndex((s) => s.key === shelf)"
                  :range="shelfOptions.map((s) => s.title)"
                  @change="
                    shelf = shelfOptions[$event.detail.value].key;
                    saved = false;
                  "
                  ><view class="chip"
                    >{{ shelfName(shelf) }}
                    {{ row.collection.archive ? "" : "▾" }}</view
                  ></picker
                >
                <picker
                  :disabled="saving"
                  :value="topicKeys.indexOf(topic)"
                  :range="topicKeys.map((k) => topics[k])"
                  @change="
                    topic = topicKeys[$event.detail.value];
                    saved = false;
                  "
                  ><view class="chip">{{ topics[topic] }} ▾</view></picker
                >
                <button
                  :disabled="saving"
                  :class="['chip', { selected: starred }]"
                  @tap="
                    starred = !starred;
                    saved = false;
                  "
                >
                  {{ starred ? "★ 已收藏" : "☆ 收藏" }}
                </button></view
              >
              <text class="muted">{{
                row.collection.archive
                  ? "原始依据保留在资料档案，可在对话中另行整理成果。"
                  : "只调整阅读归档，不改变来源、认同、实践证据与纪律。"
              }}</text>
              <button class="quiet" :disabled="saving" @tap="save">
                {{ saving ? "正在保存…" : saved ? "已保存 ✓" : "保存归档" }}
              </button>
            </view></view
          ><view class="detail-section"
            ><view class="subheading">判断与验证</view
            ><view class="state-grid"
              ><view v-for="(value, key) in row.states" :key="key"
                ><text class="muted">{{ stateNames[key] }}</text
                ><view>{{ states[key]?.[value] || value }}</view></view
              ></view
            ></view
          ><view v-if="row.reference_ids?.length" class="detail-section"
            ><view class="subheading">来源与关联资料</view
            ><button
              v-for="id in row.reference_ids"
              :key="id"
              class="reference-link"
              @tap="$emit('open', id)"
            >
              {{
                row.links?.[id]?.title ||
                (id === row.body.source_id
                  ? "关联的个人原话"
                  : "关联资料 · " + id.slice(-8))
              }}
              →
            </button></view
          ><view v-if="row.dependent_ids?.length" class="detail-section"
            ><view class="subheading">引用这份资料的记录</view
            ><button
              v-for="id in row.dependent_ids"
              :key="id"
              class="reference-link"
              @tap="$emit('open', id)"
            >
              {{ row.links?.[id]?.title || "引用记录 · " + id.slice(-8) }} →
            </button></view
          ><view v-if="row.history?.length" class="detail-section"
            ><button class="reference-link" @tap="showHistory = !showHistory">
              {{ showHistory ? "收起" : "查看" }}原文与版本 ·
              {{ row.history.length }} 版 {{ showHistory ? "↑" : "↓" }}</button
            ><view
              v-for="revision in showHistory ? [...row.history].reverse() : []"
              :key="revision.version"
              class="revision"
              ><view
                >第 {{ revision.version }} 版 ·
                {{ date(revision.recorded_at) }}</view
              ><text class="muted">{{ revision.reason || "首次记录" }}</text
              ><MarkdownView
                :text="
                  revision.body.text ||
                  revision.body.summary ||
                  readable(revision.body)
                " /></view></view
          ><view class="notice">{{
            row.states?.rule === "active"
              ? "这是已确认的当前生效纪律。"
              : "来源核验、个人认同与实践效果分别保留，候选认识不会自动成为纪律。"
          }}</view></template
        ><template v-if="classic"
          ><view class="notice">经典观点与个人认同、实践效果分别记录。</view
          ><view
            v-for="group in classicParts"
            :key="group.label"
            class="detail-section"
            ><view class="subheading">{{ group.label }}</view
            ><view
              v-for="part in group.items"
              :key="part.label"
              class="classic-part"
              ><text class="muted">{{ part.label }}</text
              ><MarkdownView
                :text="part.text" /></view></view></template></scroll-view
      ><view class="detail-bottom"
        ><button
          class="primary"
          :disabled="!row && !classic"
          @tap="
            $emit(
              'discuss',
              classic
                ? '一起理解这份经典资料：' + classic.title
                : '一起回看这份资料：' + row.id,
            )
          "
        >
          带到对话中讨论
        </button></view
      ></view
    ></view
  >
</template>

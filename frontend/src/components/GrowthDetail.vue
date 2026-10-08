<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, watch, nextTick } from "vue";
import { request } from "../platform/network";
import { newId } from "../domain/types";
import {
  fields,
  labels,
  kindNames,
  stageName,
  summary,
  evidenceNames,
  isGrowth,
  contextPrompt,
  displayTitle,
} from "../domain/growth";
import MarkdownView from "./MarkdownView.vue";
import ChatView from "./ChatView.vue";
import GrowthEditor from "./GrowthEditor.vue";
import SourceSnapshot from "./SourceSnapshot.vue";
import SourcePicker from "./SourcePicker.vue";
const props = defineProps<{ id: string; intent?: string }>();
const emit = defineEmits([
  "back",
  "open",
  "record",
  "create",
  "proposal",
  "changed",
  "busy",
  "tool",
]);
const row = ref<any>(null),
  error = ref(""),
  pending = ref(false),
  editing = ref(false),
  adding = ref(false),
  mode = ref("read"),
  history = ref(false),
  talked = ref(false),
  busy = ref(false),
  notice = ref(""),
  pinnedSource = ref<any>(null);
let alive = true;
watch([mode, editing], async () => {
  await nextTick();
  uni.pageScrollTo({ scrollTop: 0, duration: 0 });
});
const sections = computed(() =>
  row.value
    ? fields[row.value.kind].filter((k) => k !== "title" && row.value.body[k])
    : [],
);
const grouped = computed(() =>
  ["insight", "method", "practice"]
    .map((kind) => ({
      kind,
      title: kindNames[kind],
      items: (row.value?.related || []).filter((r: any) => r.kind === kind),
    }))
    .filter((g) => g.items.length),
);
async function load() {
  try {
    const value = await request("/api/growth/items/" + props.id);
    if (alive) {
      row.value = value;
      error.value = "";
    }
  } catch (e: any) {
    error.value = e.message;
  }
}
onMounted(async () => {
  await load();
  if (props.intent === "suggestions" && row.value?.suggestions.length)
    mode.value = "suggestions";
  if (props.intent === "sources")
    pinnedSource.value = row.value?.sources.find((s: any) => s.changed) || null;
});
onUnmounted(() => {
  alive = false;
  emit("busy", false);
});
async function saved() {
  editing.value = false;
  notice.value = "已保存，原有版本可回查";
  await load();
  emit("changed");
}
function openSource(id: string, kind?: string) {
  if (kind && isGrowth(kind)) emit("open", id);
  else emit("record", id);
}
async function selectSources(ids: string[]) {
  pending.value = true;
  error.value = "";
  try {
    await request(
      "/api/growth/items/" + props.id,
      {
        version: row.value.version,
        patch: {},
        refs: [
          ...new Set([...row.value.body.sources.map((s: any) => s.id), ...ids]),
        ],
        key: newId(),
      },
      "PUT",
    );
    adding.value = false;
    await saved();
  } catch (e: any) {
    error.value = e.message;
  } finally {
    pending.value = false;
  }
}
async function changeSource(id: string, refresh = false) {
  pending.value = true;
  error.value = "";
  try {
    const refs = row.value.body.sources
      .map((s: any) => s.id)
      .filter((s: string) => refresh || s !== id);
    await request(
      "/api/growth/items/" + props.id,
      {
        version: row.value.version,
        patch: {},
        refs,
        refresh_refs: refresh ? [id] : [],
        key: newId(),
      },
      "PUT",
    );
    pinnedSource.value = null;
    await saved();
    notice.value = refresh
      ? "引用已更新，旧引用保留在版本记录；请检查正文理解是否也需要修改。"
      : "已解除此引用，原资料仍保留在知识库，旧引用可从版本记录回查。";
  } catch (e: any) {
    error.value = e.message;
  } finally {
    pending.value = false;
  }
}
function continueStep() {
  const actionName = row.value.progress.action;
  if (actionName === "edit") editing.value = true;
  else if (["freeze", "review"].includes(actionName)) action(actionName);
  else create(actionName);
}
function create(kind: string) {
  if (busy.value || pending.value) return;
  const b = row.value.body;
  let seed: any = {
    title: "",
    topic_id: row.value.kind === "topic" ? row.value.id : b.topic_id || "",
  };
  if (kind === "method")
    seed = {
      ...seed,
      title: row.value.kind === "insight" ? b.title : "",
      unknowns: b.open_questions || b.boundary || "",
    };
  if (kind === "insight" && row.value.kind === "practice")
    seed = { ...seed, title: "这次实践的认识", understanding: b.lesson || "" };
  if (kind === "practice")
    seed = {
      ...seed,
      title: "一次观察 · " + b.title,
      method_id: row.value.kind === "method" ? row.value.id : "",
      conditions: b.conditions || "",
      invalidation: b.invalidation || "",
      plan: b.steps || "",
    };
  emit("create", { kind, seed, refs: [row.value.id] });
}
async function action(name: string) {
  pending.value = true;
  error.value = "";
  try {
    await request(
      "/api/growth/items/" + props.id + "/action",
      { version: row.value.version, action: name, key: newId() },
      "POST",
    );
    await saved();
    notice.value =
      name === "freeze"
        ? "事前记录已锁定。之后可补充实际情况与复盘。"
        : "复盘已保存，并关联回当前专题和方法。";
  } catch (e: any) {
    error.value = e.message;
  } finally {
    pending.value = false;
  }
}
async function decide(id: string, decision: string) {
  pending.value = true;
  error.value = "";
  try {
    await request(
      "/api/growth/suggestions/" + id + "/decision",
      { decision },
      "POST",
    );
    await saved();
    notice.value =
      decision === "accept"
        ? "已采用建议，旧正文保留在历史版本。"
        : "建议已收起，正文保留。";
  } catch (e: any) {
    error.value = e.message;
  } finally {
    pending.value = false;
  }
}
async function chatChanged() {
  const count = row.value?.suggestions.length || 0;
  await load();
  if ((row.value?.suggestions.length || 0) > count)
    notice.value =
      "伙伴已留下整理建议。打开“整理建议”对照正文，确认后才会保存。";
  emit("changed");
}
function chatBusy(v: boolean) {
  busy.value = v;
  emit("busy", v);
}
</script>
<template>
  <view class="growth-detail">
    <SourceSnapshot
      v-if="pinnedSource"
      :source="pinnedSource"
      :allow-refresh="true"
      :error-message="error"
      @refresh="changeSource($event, true)"
      @close="pinnedSource = null"
      @latest="
        openSource(pinnedSource.id, pinnedSource.kind);
        pinnedSource = null;
      "
      :busy="busy || pending"
    />
    <view class="growth-breadcrumb"
      ><button class="quiet" :disabled="busy || pending" @tap="$emit('back')">
        ← 返回</button
      ><text v-if="row">{{ kindNames[row.kind] }}</text
      ><text v-if="row" class="muted">{{ stageName(row) }}</text></view
    >
    <view v-if="error" class="notice error"
      >{{ error
      }}<button
        class="quiet"
        @tap="
          error = '';
          load();
        "
      >
        重新载入
      </button></view
    >
    <view v-if="!row" class="empty">正在打开…</view>
    <template v-else>
      <GrowthEditor
        v-if="editing"
        :key="row.id"
        :kind="row.kind"
        :row="row"
        :initial-field="row.progress?.missing[0]?.field"
        @saved="saved"
        @cancel="editing = false"
      />
      <template v-else>
        <view class="growth-title-row"
          ><view
            ><text class="eyebrow">{{
              row.kind === "practice"
                ? "把经历变成下一次的依据"
                : "围绕同一个问题，持续积累"
            }}</text
            ><view class="page-title">{{ displayTitle(row) }}</view></view
          ><button
            class="quiet"
            :disabled="busy || pending"
            @tap="
              editing = true;
              notice = '';
            "
          >
            编辑内容
          </button></view
        >
        <view v-if="notice" class="growth-saved">{{ notice }}</view>
        <view v-if="row.progress && mode === 'read'" class="growth-progress"
          ><view
            ><text class="eyebrow">接下来可以做</text
            ><view class="section-title">{{ row.progress.label }}</view
            ><view class="muted">{{ row.progress.hint }}</view
            ><view v-if="row.progress.missing.length" class="growth-missing"
              >还可补充：{{
                row.progress.missing.map((m: any) => m.label).join(" · ")
              }}</view
            ></view
          ><button
            class="quiet"
            :disabled="busy || pending"
            @tap="continueStep"
          >
            {{
              row.progress.action === "edit" ? "继续填写" : row.progress.label
            }}
            →
          </button></view
        >
        <view v-if="row.body.origin" class="growth-provenance"
          ><text class="eyebrow"
            >{{ row.body.origin.label }} ·
            {{
              row.body.origin.status === "pending" ? "待校对" : "已编辑"
            }}</text
          ><view class="muted"
            >{{
              row.body.origin.kind === "classic"
                ? "先读原意与导读，再写下自己的理解。"
                : "根据已有资料整理，保留当时的语境与来源。"
            }}编辑内容后可留下自己的版本，个人认同和采用状态另行选择。</view
          ></view
        >
        <view class="growth-tabs"
          ><button :class="{ selected: mode === 'read' }" @tap="mode = 'read'">
            阅读</button
          ><button
            :class="{ selected: mode === 'talk' }"
            @tap="
              mode = 'talk';
              talked = true;
            "
          >
            一起讨论</button
          ><button
            v-if="row.suggestions.length"
            :class="{ selected: mode === 'suggestions' }"
            @tap="mode = 'suggestions'"
          >
            整理建议 · {{ row.suggestions.length }}
          </button></view
        >
        <view v-show="mode === 'read'" class="growth-reading-layout"
          ><view class="growth-reading">
            <view v-if="!sections.length" class="growth-empty-note"
              >先留下一个问题，逐步把自己的理解写进来。可以直接编辑，也可以与伙伴一起讨论。</view
            >
            <view
              v-for="field in sections"
              :key="field"
              class="growth-reading-section"
              ><text class="growth-section-label"
                >{{
                  row.body.origin?.status === "pending" &&
                  ["understanding", "summary", "example"].includes(field)
                    ? (
                        {
                          understanding:
                            row.body.origin.kind === "classic"
                              ? "导读理解"
                              : "整理摘要",
                          summary: "整理概览",
                          example: "可以继续想的问题",
                        } as any
                      )[field]
                    : labels[field]
                }}<text
                  v-if="
                    row.kind === 'practice' &&
                    row.body.phase !== 'draft' &&
                    ['judgment', 'conditions', 'invalidation', 'plan'].includes(
                      field,
                    )
                  "
                  class="muted"
                >
                  · 事前记录</text
                ></text
              ><MarkdownView :text="row.body[field]"
            /></view>
            <view
              v-if="row.kind === 'practice' && row.body.phase !== 'draft'"
              class="growth-context-note"
              ><view
                >本次保留的方法：{{
                  row.body.method_snapshot?.body?.title || "独立观察"
                }}<text v-if="row.body.method_snapshot">
                  · 第 {{ row.body.method_snapshot.version }} 版</text
                ></view
              ><view
                >当时生效准则：{{
                  row.body.strategy_snapshot?.id
                    ? "已保留当时版本"
                    : "尚无生效版本"
                }}</view
              ><view v-if="row.body.phase === 'reviewed'"
                >案例意义：{{ evidenceNames[row.body.evidence] }} ·
                不代表方法已验证</view
              ></view
            >
            <view v-if="row.kind === 'method'" class="growth-context-note"
              >采用状态代表你的选择。方法的支持、反例和未解问题在下方实践中持续积累。</view
            >
            <view
              v-for="group in grouped"
              :key="group.kind"
              class="growth-related"
              ><view class="section-bar"
                ><view class="section-title">{{ group.title }}</view
                ><text class="muted">{{ group.items.length }}</text></view
              ><button
                v-for="r in group.items"
                :key="r.id"
                class="growth-list-row"
                :disabled="busy || pending"
                @tap="$emit('open', r.id)"
              >
                <view
                  ><text class="growth-row-title">{{ r.body.title }}</text
                  ><text class="growth-row-summary">{{
                    summary(r)
                  }}</text></view
                ><text class="growth-stage"
                  >{{
                    r.kind === "practice" && r.body.phase === "reviewed"
                      ? evidenceNames[r.body.evidence]
                      : stageName(r)
                  }}
                  ↗</text
                >
              </button></view
            >
            <view class="growth-related"
              ><view class="section-bar"
                ><view class="section-title">来源与来处</view
                ><button
                  class="quiet"
                  :disabled="busy || pending || row.body.sources.length >= 40"
                  @tap="adding = !adding"
                >
                  关联已有资料
                </button></view
              ><SourcePicker
                v-if="adding"
                :excluded="[row.id, ...row.sources.map((s: any) => s.id)]"
                :limit="40 - row.body.sources.length"
                :pending="pending"
                :error-message="error"
                @select="selectSources"
                @close="adding = false"
                @open="$emit('record', $event)"
              /><view v-if="!row.sources.length && !adding" class="muted"
                >可以找回以前的原话、学习资料或案例，不必重新录入。</view
              ><view
                v-for="s in row.sources"
                :key="s.id"
                class="growth-reference-row"
              >
                <button class="growth-reference" @tap="pinnedSource = s">
                  {{ s.title }}
                  <text class="muted"
                    >· 第 {{ s.version }} 版{{
                      s.changed ? " · 来源已有更新" : ""
                    }}
                    ↗</text
                  ></button
                ><button
                  v-if="s.managed"
                  class="quiet"
                  :disabled="busy || pending"
                  @tap="changeSource(s.id)"
                >
                  解除引用</button
                ><text v-else class="muted">对话原始记录</text>
              </view></view
            >
            <view class="growth-history"
              ><button class="quiet" @tap="history = !history">
                {{ history ? "收起" : "查看" }}版本记录 ·
                {{ row.history.length }}</button
              ><template v-if="history"
                ><view
                  v-for="rev in [...row.history].reverse()"
                  :key="rev.version"
                  class="growth-history-entry"
                  ><text class="growth-label"
                    >第 {{ rev.version }} 版 ·
                    {{ rev.recorded_at.slice(0, 16).replace("T", " ") }} ·
                    {{ rev.reason }}</text
                  ><view
                    v-for="field in fields[row.kind].filter((k) => rev.body[k])"
                    :key="field"
                    ><text class="growth-label">{{ labels[field] }}</text
                    ><view class="growth-prose">{{
                      rev.body[field]
                    }}</view></view
                  ><view
                    v-if="rev.body.sources?.length"
                    class="growth-history-sources"
                    ><text class="growth-label">这一版引用的资料</text
                    ><button
                      v-for="s in rev.body.sources"
                      :key="s.id"
                      class="growth-reference"
                      @tap="pinnedSource = { ...s, managed: false }"
                    >
                      {{ s.title }} · 第 {{ s.version }} 版 ↗
                    </button></view
                  ></view
                ></template
              ></view
            > </view
          ><view class="growth-sidebar"
            ><view class="growth-side-heading">一起想清楚</view
            ><view v-if="row.body.next_step" class="growth-next-question">{{
              row.body.next_step
            }}</view
            ><button
              class="primary"
              @tap="
                mode = 'talk';
                talked = true;
              "
            >
              与伙伴一起梳理</button
            ><template v-if="row.kind === 'topic'"
              ><button
                class="quiet"
                :disabled="busy || pending"
                @tap="create('insight')"
              >
                ＋ 写下我的理解</button
              ><button
                class="quiet"
                :disabled="busy || pending"
                @tap="create('method')"
              >
                形成一个方法
              </button></template
            ><template v-if="row.kind === 'insight'"
              ><button
                class="quiet"
                :disabled="busy || pending"
                @tap="create('method')"
              >
                看看下次怎样用 →</button
              ><button
                v-if="row.body.topic_id"
                class="quiet"
                :disabled="busy || pending"
                @tap="$emit('open', row.body.topic_id)"
              >
                回到所属专题
              </button></template
            ><template v-if="row.kind === 'method'"
              ><button
                class="quiet"
                :disabled="busy || pending || row.body.stage === 'paused'"
                @tap="create('practice')"
              >
                准备一次观察 →</button
              ><button class="quiet" @tap="$emit('tool', 'risk')">
                打开仓位计算
              </button></template
            ><template
              v-if="row.kind === 'practice' && row.body.phase === 'reviewed'"
              ><button
                class="quiet"
                :disabled="busy || pending"
                @tap="create('insight')"
              >
                留下一条知识</button
              ><button
                v-if="row.body.method_id"
                class="quiet"
                :disabled="busy || pending"
                @tap="$emit('open', row.body.method_id)"
              >
                回到方法继续完善
              </button></template
            ><button
              v-if="
                row.kind === 'method' ||
                (row.kind === 'practice' && row.body.phase === 'reviewed')
              "
              class="quiet"
              @tap="
                $emit('proposal', {
                  refs: [row.id],
                  rationale: row.body.lesson || '',
                })
              "
            >
              提出准则修订</button
            ><view class="growth-hint"
              >可以停在这里。下次仍从这个问题继续。</view
            ></view
          ></view
        >
        <view v-show="mode === 'talk'" class="growth-context-chat"
          ><ChatView
            v-if="talked"
            :key="row.id"
            :context="{ id: row.id, title: displayTitle(row) }"
            :draft="contextPrompt(row)"
            :active="mode === 'talk'"
            @changed="chatChanged"
            @busy="chatBusy"
        /></view>
        <view v-if="mode === 'suggestions'" class="growth-suggestions"
          ><view
            v-for="s in row.suggestions"
            :key="s.id"
            class="growth-suggestion"
            ><view class="section-title">伙伴的整理建议</view
            ><view class="muted">{{ s.body.reason }}</view
            ><view v-if="s.body.expected_version !== row.version" class="notice"
              >正文已有更新，这份建议不能直接覆盖当前版本。可以参考后手动编辑。</view
            ><view
              v-for="(value, field) in s.body.patch"
              :key="field"
              class="growth-diff"
              ><text class="growth-label">{{ labels[field] || field }}</text
              ><view class="growth-old">{{
                row.body[field] || "当前未填写"
              }}</view
              ><view class="growth-prose">{{ value }}</view></view
            ><view class="growth-actions"
              ><button
                class="primary"
                :disabled="
                  pending || busy || s.body.expected_version !== row.version
                "
                @tap="decide(s.id, 'accept')"
              >
                采用这份建议</button
              ><button
                class="quiet"
                :disabled="pending || busy"
                @tap="decide(s.id, 'dismiss')"
              >
                保留当前正文
              </button></view
            ></view
          ><view v-if="!row.suggestions.length" class="empty"
            >本次建议已处理，可以回到正文继续。</view
          ></view
        >
      </template></template
    >
  </view>
</template>

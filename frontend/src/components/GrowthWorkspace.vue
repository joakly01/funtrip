<script setup lang="ts">
import { ref, onMounted, watch, computed, onUnmounted, nextTick } from "vue";
import { request } from "../platform/network";
import {
  kindNames,
  stageName,
  summary,
  textMatches,
  editorDraftKey,
  stages,
  displayTitle,
} from "../domain/growth";
import CompanionPanel from "./CompanionPanel.vue";
import { newId } from "../domain/types";
import GrowthDetail from "./GrowthDetail.vue";
import GrowthEditor from "./GrowthEditor.vue";
import ProposalPanel from "./ProposalPanel.vue";
import LibraryView from "./LibraryView.vue";
const props = defineProps<{
  mode: string;
  revision: number;
  navigationRevision?: number;
  scrollTop?: number;
}>();
const emit = defineEmits([
  "chat",
  "record",
  "classic",
  "tool",
  "changed",
  "busy",
]);
const data = ref<any>(null),
  system = ref<any>(null),
  error = ref(""),
  query = ref(""),
  focused = ref(""),
  creating = ref<any>(null),
  proposal = ref<any>(null),
  sources = ref(false),
  shelf = ref("topics"),
  systemTab = ref("handbook"),
  question = ref(""),
  pending = ref(false),
  busy = ref(false),
  showPaused = ref(false),
  lastOpen = ref(uni.getStorageSync("growth-last-open") || "");
const focusedIntent = ref("read"),
  showAttention = ref(false),
  stageFilter = ref("all"),
  topicFilter = ref(""),
  page = ref(1);
const trail = ref<string[]>([]);
let listScroll = 0,
  returningToList = false;
const companion = ref<any>(null),
  showCompanion = ref(false),
  originFilter = ref("all");
watch(
  [focused, creating, sources, shelf, systemTab, showCompanion],
  async () => {
    await nextTick();
    uni.pageScrollTo({
      scrollTop: returningToList ? listScroll : 0,
      duration: 0,
    });
    returningToList = false;
  },
);
let alive = true,
  seq = 0;
async function load() {
  const n = ++seq;
  try {
    const results = await Promise.allSettled([
      request("/api/growth"),
      request("/api/system"),
      request("/api/companion"),
    ]);
    if (alive && n === seq) {
      if (results[0].status === "fulfilled") data.value = results[0].value;
      if (results[1].status === "fulfilled") system.value = results[1].value;
      if (results[2].status === "fulfilled") companion.value = results[2].value;
      error.value = results.some((r) => r.status === "rejected")
        ? "部分内容暂时未能读取，已读出的内容仍可使用。请重试加载。"
        : "";
    }
  } catch (e: any) {
    if (alive && n === seq) error.value = e.message;
  }
}
onMounted(() => {
  load();
  question.value = uni.getStorageSync("growth-new-question") || "";
});
watch(question, (v) => uni.setStorageSync("growth-new-question", v));
watch(() => props.revision, load);
watch([() => props.mode, () => props.navigationRevision], () => {
  focused.value = "";
  creating.value = null;
  sources.value = false;
  trail.value = [];
  query.value = "";
  showCompanion.value = false;
  originFilter.value = "all";
  stageFilter.value = "all";
  topicFilter.value = "";
  page.value = 1;
});
onUnmounted(() => {
  alive = false;
  seq++;
});
const items = computed(() => data.value?.items || []);
const resume = computed(() =>
  items.value.find(
    (r: any) =>
      r.id === lastOpen.value && !["paused", "archived"].includes(r.body.stage),
  ),
);
const topics = computed(() =>
  items.value
    .filter(
      (r: any) =>
        r.kind === "topic" &&
        (showPaused.value || r.body.stage === "active") &&
        matches(r),
    )
    .sort((a: any, b: any) => {
      const rank = (r: any) =>
        r.body.origin?.kind === "classic" ? 2 : r.body.origin ? 1 : 0;
      return rank(a) - rank(b);
    }),
);
const insights = computed(() =>
  items.value.filter(
    (r: any) =>
      r.kind === "insight" &&
      matches(r) &&
      (stageFilter.value === "all" ||
        (stageFilter.value === "pending" &&
          r.body.origin?.status === "pending") ||
        r.body.stage === stageFilter.value) &&
      (!topicFilter.value || r.body.topic_id === topicFilter.value) &&
      (originFilter.value === "all" ||
        (r.body.origin?.kind || "personal") === originFilter.value),
  ),
);
const methods = computed(() =>
  items.value.filter(
    (r: any) =>
      r.kind === "method" &&
      (showPaused.value || r.body.stage !== "paused") &&
      matches(r),
  ),
);
const handbook = computed(() =>
  items.value.find((r: any) => r.kind === "handbook"),
);
const reviews = computed(() =>
  items.value.filter(
    (r: any) =>
      r.kind === "practice" &&
      matches(r) &&
      (!topicFilter.value || r.body.topic_id === topicFilter.value) &&
      (stageFilter.value === "all" || r.body.phase === stageFilter.value),
  ),
);
const listed = computed(() =>
  shelf.value === "insights" ? insights.value : reviews.value,
);
const listedPage = computed(() =>
  listed.value.slice((page.value - 1) * 12, page.value * 12),
);
const topicOptions = computed(() => [
  { id: "", body: { title: "所有专题" } },
  ...items.value.filter((r: any) => r.kind === "topic"),
]);
const statusOptions = computed(() =>
  shelf.value === "insights"
    ? [["all", "全部状态"], ["pending", "待校对"], ...stages.insight]
    : [
        ["all", "全部阶段"],
        ["draft", "事前草稿"],
        ["observing", "待回顾"],
        ["reviewed", "已复盘"],
      ],
);
watch([query, shelf, originFilter, stageFilter, topicFilter], () => {
  page.value = 1;
});
watch(shelf, () => {
  stageFilter.value = "all";
});
watch(listed, () => {
  page.value = Math.min(
    page.value,
    Math.max(1, Math.ceil(listed.value.length / 12)),
  );
});
const waiting = computed(
  () =>
    data.value?.proposals.filter(
      (r: any) => r.decision === "pending" || r.decision === "later",
    ) || [],
);
function matches(r: any) {
  return !query.value.trim() || textMatches(r, query.value);
}
function open(id: string, intent = "read") {
  if (!focused.value) listScroll = props.scrollTop || 0;
  if (focused.value) trail.value.push(focused.value);
  focused.value = id;
  focusedIntent.value = intent;
  showCompanion.value = false;
  creating.value = null;
  uni.setStorageSync("growth-last-open", id);
  lastOpen.value = id;
}
function back() {
  creating.value = null;
  focused.value = trail.value.pop() || "";
  returningToList = !focused.value;
  focusedIntent.value = "read";
}
async function changed() {
  await load();
  emit("changed");
}
async function saved(row: any) {
  creating.value = null;
  open(row.id);
  await changed();
}
async function startQuestion() {
  if (!question.value.trim()) return;
  pending.value = true;
  error.value = "";
  try {
    const row = await request(
      "/api/growth/items",
      {
        kind: "topic",
        fields: {
          title: question.value.trim().slice(0, 60),
          question: question.value.trim(),
        },
        refs: [],
        key: newId(),
      },
      "POST",
    );
    question.value = "";
    open(row.id);
    await changed();
  } catch (e: any) {
    error.value = e.message;
  } finally {
    pending.value = false;
  }
}
async function openFeatured() {
  try {
    const r = await request(
      "/api/classics?query=" + encodeURIComponent(companion.value.featured.id),
    );
    const item = r.entries.find(
      (e: any) => e.id === companion.value.featured.id,
    );
    if (item) emit("classic", item);
  } catch (e: any) {
    error.value = e.message;
  }
}
function setBusy(v: boolean) {
  busy.value = v;
  emit("busy", v);
}
</script>
<template>
  <view class="growth-workspace page-content">
    <CompanionPanel
      v-if="showCompanion"
      @close="showCompanion = false"
      @open="open"
      @changed="changed"
    />
    <GrowthEditor
      v-else-if="creating"
      :key="
        editorDraftKey(creating.kind, undefined, creating.seed, creating.refs)
      "
      :kind="creating.kind"
      :seed="creating.seed"
      :refs="creating.refs"
      @cancel="creating = null"
      @saved="saved"
    />
    <GrowthDetail
      v-else-if="focused"
      :key="focused"
      :id="focused"
      :intent="focusedIntent"
      @back="back"
      @open="open"
      @record="$emit('record', $event)"
      @create="creating = $event"
      @proposal="proposal = $event"
      @changed="changed"
      @busy="setBusy"
      @tool="$emit('tool', $event)"
    />
    <template v-else-if="sources"
      ><button class="quiet" @tap="sources = false">← 返回我的知识</button
      ><LibraryView
        :revision="revision"
        @open="$emit('record', $event)"
        @classic="$emit('classic', $event)"
        @discuss="$emit('chat', $event)"
    /></template>
    <template v-else>
      <view v-if="error" class="notice error"
        >{{ error }}<button class="quiet" @tap="load">重试</button></view
      >
      <template v-if="mode === 'chat'">
        <view class="growth-title-row growth-home-heading"
          ><view
            ><text class="eyebrow">学习有积累，判断有依据</text
            ><view class="page-title">今天，继续一点。</view
            ><view class="muted"
              >收下一段资料，想清一个问题，回看一次经历。</view
            ></view
          >
          <button class="quiet" @tap="showCompanion = true">
            伙伴与成长 ↗
          </button></view
        >
        <view class="growth-capture">
          <textarea
            v-model="question"
            auto-height
            maxlength="2000"
            placeholder="最近在学什么，或有什么一直没想清楚？"
          /><view class="growth-actions"
            ><button
              class="primary"
              :disabled="pending || !question.trim()"
              @tap="startQuestion"
            >
              {{ pending ? "正在保存…" : "开始一个学习专题 →" }}</button
            ><button class="quiet" @tap="$emit('chat', '')">
              随手记录 · 文字 / 语音 / 附件
            </button></view
          ></view
        >
        <view v-if="data" class="growth-path"
          ><view
            v-for="step in [
              ['topic', '学习专题'],
              ['insight', '知识笔记'],
              ['method', '交易方法'],
              ['practice', '观察复盘'],
            ]"
            :key="step[0]"
            ><text class="eyebrow">{{ step[1] }}</text
            ><view
              >{{ items.filter((r: any) => r.kind === step[0]).length
              }}<text class="muted"> 项积累</text></view
            ></view
          ></view
        >
        <button v-if="resume" class="growth-resume" @tap="open(resume.id)">
          <view
            ><text class="eyebrow">接着上次</text
            ><view>{{ displayTitle(resume) }}</view></view
          ><text class="muted">{{ kindNames[resume.kind] }} · 继续 →</text>
        </button>
        <view v-if="data?.attention?.length" class="growth-attention"
          ><view class="section-bar"
            ><view
              ><text class="eyebrow">把积累接起来</text
              ><view class="section-title"
                >有 {{ data.attention.length }} 项可以接着整理</view
              ></view
            ><button
              v-if="data.attention.length > 3"
              class="quiet"
              @tap="showAttention = !showAttention"
            >
              {{ showAttention ? "收起" : "查看全部" }}
            </button></view
          ><button
            v-for="a in showAttention
              ? data.attention
              : data.attention.slice(0, 3)"
            :key="a.id + a.type"
            class="growth-list-row"
            @tap="
              open(
                a.id,
                a.type === 'suggestions'
                  ? 'suggestions'
                  : a.type === 'sources'
                    ? 'sources'
                    : 'read',
              )
            "
          >
            <view
              ><text class="growth-row-title">{{ a.title }}</text
              ><text class="growth-row-summary">{{ a.detail }}</text></view
            ><text class="growth-stage">{{ a.label }} →</text>
          </button></view
        >
        <view v-if="data?.focus.length" class="growth-focus"
          ><text class="eyebrow">值得接着做</text
          ><button class="growth-focus-main" @tap="open(data.focus[0].id)">
            <view
              ><view class="section-title">{{
                data.focus[0].body.next_step
              }}</view
              ><view class="muted"
                >来自「{{ data.focus[0].body.title }}」· 你留下的下一步</view
              ></view
            ><text>继续 →</text></button
          ><button
            v-for="r in data.focus.slice(1)"
            :key="r.id"
            class="growth-list-row"
            @tap="open(r.id)"
          >
            <text>{{ r.body.next_step }}</text
            ><text class="growth-stage">{{ r.body.title }} ↗</text>
          </button></view
        >
        <view class="section-bar"
          ><view class="section-title">正在推进的专题</view
          ><button class="quiet" @tap="sources = true">
            找回已有资料 ↗
          </button></view
        ><view class="growth-topic-grid"
          ><button
            v-for="r in topics.slice(0, 6)"
            :key="r.id"
            class="growth-topic-card"
            @tap="open(r.id)"
          >
            <text class="eyebrow">{{
              r.body.category ||
              (r.body.area === "general" ? "其他学习" : "交易学习")
            }}</text
            ><view class="section-title">{{ r.body.title }}</view
            ><view class="growth-card-summary">{{ summary(r) }}</view
            ><text class="growth-card-link">打开专题 →</text>
          </button></view
        ><view v-if="data && !topics.length" class="growth-empty-note"
          >从上面一个真实问题开始。已有资料可以随时关联进来。</view
        >
        <button
          v-if="companion?.featured"
          class="growth-classic"
          @tap="openFeatured"
        >
          <view
            ><text class="eyebrow"
              >读一句，也想一步 ·
              {{ companion.featured.is_quote ? "原文摘录" : "观点导读" }}</text
            ><view class="section-title">{{ companion.featured.title }}</view
            ><view class="growth-classic-text">{{
              companion.featured.excerpt
            }}</view
            ><view class="muted">{{ companion.featured.boundary }}</view></view
          ><text class="growth-card-link">查阅经典与出处 →</text>
        </button>
      </template>
      <template v-else-if="mode === 'library'">
        <view class="growth-title-row"
          ><view
            ><text class="eyebrow">把资料读懂，把理解留下</text
            ><view class="page-title">知识库</view
            ><view class="muted"
              >按专题阅读，区分原文、导读与自己的理解。</view
            ></view
          ><button class="quiet" @tap="sources = true">
            资料档案与经典 ↗
          </button></view
        >
        <view class="growth-library-tools"
          ><view class="growth-tabs"
            ><button
              :class="{ selected: shelf === 'topics' }"
              @tap="shelf = 'topics'"
            >
              学习专题</button
            ><button
              :class="{ selected: shelf === 'insights' }"
              @tap="shelf = 'insights'"
            >
              知识笔记</button
            ><button
              :class="{ selected: shelf === 'reviews' }"
              @tap="shelf = 'reviews'"
            >
              观察与复盘
            </button></view
          ><input
            v-model="query"
            placeholder="搜索标题与正文，可用空格组合关键词"
            aria-label="搜索知识库"
        /></view>
        <view v-if="!data && !error" class="growth-empty-note"
          >正在读取你的积累…</view
        >
        <view v-if="shelf !== 'topics'" class="growth-library-filters"
          ><picker
            :range="topicOptions.map((r) => r.body.title)"
            :value="
              Math.max(
                0,
                topicOptions.findIndex((r) => r.id === topicFilter),
              )
            "
            @change="topicFilter = topicOptions[Number($event.detail.value)].id"
            ><view class="growth-select"
              >{{
                topicOptions.find((r) => r.id === topicFilter)?.body.title ||
                "所有专题"
              }}
              ▾</view
            ></picker
          ><picker
            :range="statusOptions.map((s) => s[1])"
            :value="
              Math.max(
                0,
                statusOptions.findIndex((s) => s[0] === stageFilter),
              )
            "
            @change="
              stageFilter = statusOptions[Number($event.detail.value)][0]
            "
            ><view class="growth-select"
              >{{
                statusOptions.find((s) => s[0] === stageFilter)?.[1]
              }}
              ▾</view
            ></picker
          ><text v-if="data" class="muted">找到 {{ listed.length }} 项</text
          ><button
            v-if="
              query ||
              topicFilter ||
              stageFilter !== 'all' ||
              originFilter !== 'all'
            "
            class="quiet"
            @tap="
              query = '';
              topicFilter = '';
              stageFilter = 'all';
              originFilter = 'all';
            "
          >
            清除筛选
          </button></view
        >
        <view v-if="shelf === 'insights'" class="growth-origin-filters"
          ><button
            v-for="o in [
              ['all', '全部'],
              ['personal', '自己写的'],
              ['history', '历史整理'],
              ['classic', '经典导读'],
            ]"
            :key="o[0]"
            :class="['chip', { selected: originFilter === o[0] }]"
            @tap="originFilter = o[0]"
          >
            {{ o[1] }}
          </button></view
        >
        <view class="section-bar"
          ><button
            v-if="shelf === 'topics'"
            class="quiet"
            @tap="showPaused = !showPaused"
          >
            {{ showPaused ? "☑" : "☐" }} 包含暂缓与归档</button
          ><button
            class="quiet"
            @tap="
              creating = {
                kind:
                  shelf === 'topics'
                    ? 'topic'
                    : shelf === 'reviews'
                      ? 'practice'
                      : 'insight',
              }
            "
          >
            ＋
            {{
              shelf === "topics"
                ? "新建专题"
                : shelf === "reviews"
                  ? "开始一次观察"
                  : "写一张笔记"
            }}
          </button></view
        >
        <view v-if="shelf === 'topics'" class="growth-topic-grid"
          ><button
            v-for="r in topics"
            :key="r.id"
            class="growth-topic-card"
            @tap="open(r.id)"
          >
            <text class="eyebrow">{{ r.body.category || stageName(r) }}</text
            ><view class="section-title">{{ r.body.title }}</view
            ><view class="growth-card-summary">{{ summary(r) }}</view
            ><text class="growth-card-link">打开专题 →</text></button
          ><view v-if="!topics.length" class="growth-empty-note"
            >还没有匹配的专题。可以从一个问题开始，逐步关联已有材料。</view
          ></view
        >
        <template v-else
          ><button
            v-for="r in listedPage"
            :key="r.id"
            class="growth-list-row"
            @tap="open(r.id)"
          >
            <view
              ><text class="growth-row-title">{{ r.body.title }}</text
              ><text class="growth-row-summary">{{ summary(r) }}</text></view
            ><text class="growth-stage"
              >{{
                r.body.origin?.status === "pending"
                  ? r.body.origin.label + " · 待校对"
                  : stageName(r)
              }}
              ↗</text
            ></button
          ><view v-if="data && !listed.length" class="growth-empty-note"
            >这里还没有匹配的内容。已有分析和学习笔记仍可从资料档案查阅并关联。</view
          ><view v-if="listed.length > 12" class="growth-pagination"
            ><button class="quiet" :disabled="page === 1" @tap="page--">
              上一页</button
            ><text class="muted"
              >{{ page }} / {{ Math.ceil(listed.length / 12) }}</text
            ><button
              class="quiet"
              :disabled="page * 12 >= listed.length"
              @tap="page++"
            >
              下一页
            </button></view
          ></template
        >
      </template>
      <template v-else-if="mode === 'system'">
        <view class="growth-title-row"
          ><view
            ><text class="eyebrow">从理念到行动，逐层建立自己的体系</text
            ><view class="page-title">交易体系</view
            ><view class="muted"
              >认识有来处，方法有条件，修改有理由。</view
            ></view
          ></view
        >
        <view class="growth-tabs growth-system-tabs"
          ><button
            v-for="t in [
              ['handbook', '交易理念', '道'],
              ['rules', '交易准则', '法'],
              ['methods', '交易方法', '术'],
              ['tools', '交易工具', '器'],
            ]"
            :key="t[0]"
            :aria-label="t[2] + ' · ' + t[1]"
            :class="{ selected: systemTab === t[0] }"
            @tap="systemTab = t[0]"
          >
            <text class="growth-system-symbol">{{ t[2] }}</text
            ><text>{{ t[1] }}</text>
          </button></view
        >
        <template v-if="systemTab === 'handbook'"
          ><view class="growth-handbook-intro"
            ><text class="eyebrow">道 · 从生活与目标出发</text
            ><view class="section-title">我为什么交易，哪些边界需要守住？</view
            ><view class="muted"
              >目标、可投入的时间与资金、参与范围、选择的原则和回顾节奏。</view
            ><button
              class="primary"
              @tap="
                handbook ? open(handbook.id) : (creating = { kind: 'handbook' })
              "
            >
              {{ handbook ? "查看与完善交易理念 →" : "梳理我的交易理念 →" }}
            </button></view
          ><view v-if="system?.background.length" class="growth-related"
            ><view class="section-title">之前留下的目标与背景</view
            ><button
              v-for="r in system.background"
              :key="r.id"
              class="growth-list-row"
              @tap="$emit('record', r.id)"
            >
              <text>{{ r.body.text }}</text
              ><text>回看 ↗</text>
            </button></view
          ><view v-if="system?.principles.length" class="growth-related"
            ><view class="section-title">已认同的原则</view
            ><button
              v-for="r in system.principles"
              :key="r.id"
              class="growth-list-row"
              @tap="$emit('record', r.id)"
            >
              <text>{{ r.body.title || r.body.text }}</text
              ><text>↗</text>
            </button></view
          ><view class="growth-hint"
            >从自己的目标、经历与生活出发，逐步明确理念，再用准则和方法落实到行动。</view
          ></template
        >
        <template v-else-if="systemTab === 'methods'"
          ><view class="section-bar"
            ><view
              ><text class="eyebrow">术 · 把判断落到行动</text
              ><view class="section-title">从构思，到观察，再到采用</view></view
            ><button class="quiet" @tap="creating = { kind: 'method' }">
              ＋ 写一个方法
            </button></view
          ><button class="quiet" @tap="showPaused = !showPaused">
            {{ showPaused ? "☑" : "☐" }} 包含已暂停的方法</button
          ><button
            v-for="r in methods"
            :key="r.id"
            class="growth-list-row"
            @tap="open(r.id)"
          >
            <view
              ><text class="growth-row-title">{{ r.body.title }}</text
              ><text class="growth-row-summary">{{ summary(r) }}</text></view
            ><text class="growth-stage">{{ stageName(r) }} ↗</text></button
          ><view v-if="!methods.length" class="growth-empty-note"
            >从一个具体问题形成方法，逐步写清条件与风险，再关联观察和复盘。</view
          ><view v-if="system?.methods.length" class="growth-related"
            ><view class="section-title">过去的候选认识与方法依据</view
            ><button
              v-for="r in system.methods.slice(0, 8)"
              :key="r.id"
              class="growth-list-row"
              @tap="$emit('record', r.id)"
            >
              <text>{{
                (r.body.title || r.body.text || "历史认识").slice(0, 120)
              }}</text
              ><text>回看 ↗</text>
            </button></view
          ></template
        >
        <template v-else-if="systemTab === 'rules'"
          ><view class="section-bar"
            ><view
              ><text class="eyebrow">法 · 约束每一次行动</text
              ><view class="section-title">当前生效的交易准则</view></view
            ><button class="quiet" @tap="proposal = {}">修订准则</button></view
          ><view
            v-for="r in data?.strategy.body.rules || []"
            :key="r.id"
            class="growth-rule"
            ><view class="growth-prose">{{ r.text }}</view
            ><text class="muted">{{
              r.condition ? "含可检查条件" : "文字纪律，需人工判断"
            }}</text></view
          ><view v-if="!data?.strategy.id" class="growth-empty-note"
            >还没有正式生效的准则。可以从具体认识提出修订，审阅后由你确认。</view
          ><button
            v-if="data?.strategy.id"
            class="quiet"
            @tap="$emit('tool', 'rules')"
          >
            检查准则条件 ↗</button
          ><view class="growth-related"
            ><view class="section-title">待审阅的修订</view
            ><button
              v-for="r in waiting"
              :key="r.id"
              class="growth-list-row"
              @tap="proposal = { id: r.id }"
            >
              <text>{{ r.body.rationale || "历史修改提案" }}</text
              ><text class="growth-stage"
                >{{ r.decision === "later" ? "已暂缓" : "待审阅" }} ↗</text
              ></button
            ><view v-if="!waiting.length" class="muted"
              >暂无待决定的修改。</view
            ></view
          ><view
            v-if="
              data?.proposals.some((p: any) =>
                ['adopted', 'reject'].includes(p.decision),
              )
            "
            class="growth-related"
            ><view class="section-title">修订记录</view
            ><button
              v-for="r in data.proposals.filter((p: any) =>
                ['adopted', 'reject'].includes(p.decision),
              )"
              :key="r.id"
              class="growth-list-row"
              @tap="proposal = { id: r.id }"
            >
              <text>{{ r.body.rationale }}</text
              ><text
                >{{ r.decision === "adopted" ? "已采用" : "不采用" }} ↗</text
              >
            </button></view
          ></template
        >
        <template v-else
          ><text class="eyebrow">器 · 让判断与执行更从容</text
          ><view class="section-title">按需使用的交易工具</view
          ><view class="muted"
            >先看趋势与阶段，再观察量价、画出关键位置，计算风险与核对行动条件。</view
          >
          <view class="growth-topic-grid"
            ><button
              v-for="t in system?.tools || []"
              :key="t.id"
              class="growth-topic-card"
              :disabled="!t.available"
              @tap="$emit('tool', t.id)"
            >
              <view class="section-title">{{ t.title }}</view
              ><view class="growth-card-summary">{{ t.description }}</view
              ><text class="growth-card-link">{{ t.action }} →</text
              ><text class="muted">{{ t.note }}</text>
            </button></view
          ></template
        >
      </template>
    </template>
    <ProposalPanel
      v-if="proposal"
      :key="proposal.id || 'new'"
      :id="proposal.id"
      :refs="proposal.refs"
      :rationale="proposal.rationale"
      @close="proposal = null"
      @changed="changed"
    />
  </view>
</template>

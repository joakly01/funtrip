<script setup lang="ts">
import { ref, watch, computed, onMounted } from "vue";
import { request } from "../platform/network";
import { newId } from "../domain/types";
import {
  fields,
  labels,
  stages,
  kindNames,
  editableFields,
  editorDraftKey,
  editorGroups,
  fieldHints,
  mergeDraft,
  displayTitle,
} from "../domain/growth";
const props = defineProps<{
  kind: string;
  row?: any;
  seed?: any;
  refs?: string[];
  initialField?: string;
}>();
const emit = defineEmits(["saved", "cancel"]);
const cacheKey = editorDraftKey(props.kind, props.row, props.seed, props.refs);
const cached = uni.getStorageSync(cacheKey);
const body = ref<any>({
  ...props.seed,
  ...(props.row ? { ...props.row.body, title: displayTitle(props.row) } : {}),
  ...cached?.body,
});
const base = ref<any>(cached?.base || props.row?.body || {});
const sourceRow = ref<any>(props.row);
const version = ref(cached?.version || props.row?.version || 1),
  pending = ref(false),
  error = ref(""),
  latest = ref<any>(null),
  restored = ref(!!cached);
const groupIndex = ref(
    cached?.groupIndex ??
      Math.max(
        0,
        editorGroups(props.kind, props.row?.body.phase).findIndex((g) =>
          g.fields.includes(props.initialField || ""),
        ),
      ),
  ),
  choices = ref<Record<string, string>>({}),
  directory = ref<any[] | null>(null),
  directoryError = ref("");
let operationKey = cached?.key || newId();
const phase = computed(() => sourceRow.value?.body.phase || "draft");
const editingFields = computed(() =>
  editableFields({
    kind: props.kind,
    body: { ...body.value, phase: phase.value },
  }),
);
const groups = computed(() => editorGroups(props.kind, phase.value));
const activeGroup = computed(
  () => groups.value[Math.min(groupIndex.value, groups.value.length - 1)],
);
const merge = computed(() =>
  latest.value ? mergeDraft(base.value, body.value, latest.value.body) : null,
);
const conflicts = computed(() => merge.value?.conflicts || []);
const associationLocked = computed(
  () => props.kind === "practice" && phase.value !== "draft",
);
const methodTopicLocked = computed(
  () =>
    props.kind === "method" &&
    sourceRow.value?.related?.some(
      (r: any) =>
        r.kind === "practice" && r.body.method_id === sourceRow.value.id,
    ),
);
const topics = computed(() =>
  (directory.value || []).filter(
    (r) =>
      r.kind === "topic" &&
      (r.body.stage === "active" || r.id === body.value.topic_id),
  ),
);
const methods = computed(() =>
  (directory.value || []).filter(
    (r) =>
      r.kind === "method" &&
      (r.body.stage !== "paused" || r.id === body.value.method_id),
  ),
);
const selectedMethod = computed(() =>
  methods.value.find((r) => r.id === body.value.method_id),
);
const topicOptions = computed(() => [
  { id: "", body: { title: "暂不归入专题" } },
  ...topics.value,
]);
const methodOptions = computed(() => [
  { id: "", body: { title: "独立观察，不关联方法" } },
  ...methods.value,
]);
function optionIndex(options: any[], id: string) {
  return Math.max(
    0,
    options.findIndex((r) => r.id === id),
  );
}
if (!body.value.title)
  body.value.title = props.kind === "handbook" ? "我的交易理念" : "";
if (stages[props.kind] && !body.value.stage)
  body.value.stage = stages[props.kind][0][0];
if (props.kind === "topic" && !body.value.area) body.value.area = "trading";
if (props.kind === "practice" && !body.value.evidence)
  body.value.evidence = "uncertain";
if (cached && props.row && cached.version !== props.row.version)
  latest.value = props.row;
function cache() {
  uni.setStorageSync(cacheKey, {
    body: body.value,
    base: base.value,
    version: version.value,
    key: operationKey,
    groupIndex: groupIndex.value,
  });
}
watch(
  body,
  () => {
    operationKey = newId();
    choices.value = {};
    cache();
  },
  { deep: true },
);
watch(groupIndex, cache);
const stageIndex = computed(() =>
  Math.max(
    0,
    stages[props.kind]?.findIndex((s) => s[0] === body.value.stage) || 0,
  ),
);
async function loadDirectory() {
  directoryError.value = "";
  try {
    const d = await request("/api/growth");
    directory.value = d.items;
  } catch (e: any) {
    directoryError.value = e.message;
  }
}
onMounted(() => {
  if (["insight", "method", "practice"].includes(props.kind)) loadDirectory();
});
function chooseTopic(index: number) {
  body.value.topic_id = topicOptions.value[index].id;
  if (
    selectedMethod.value &&
    (selectedMethod.value.body.topic_id || "") !== body.value.topic_id
  )
    body.value.method_id = "";
}
function chooseMethod(index: number) {
  const method = methodOptions.value[index];
  body.value.method_id = method.id;
  if (method.id) body.value.topic_id = method.body.topic_id || "";
  if (method.id && !body.value.title?.trim())
    body.value.title = "一次观察 · " + method.body.title;
}
function fillMethod() {
  const method = selectedMethod.value;
  if (!method) return;
  for (const [dest, src] of [
    ["conditions", "conditions"],
    ["invalidation", "invalidation"],
    ["plan", "steps"],
  ])
    if (!body.value[dest]?.trim()) body.value[dest] = method.body[src] || "";
  error.value = "已填入尚未填写的条件与计划，请结合这次情境检查。";
}
async function save() {
  if (pending.value || latest.value) return;
  if (
    props.kind === "method" &&
    ["trial", "adopted"].includes(body.value.stage)
  ) {
    const missing = [
      "scope",
      "conditions",
      "steps",
      "invalidation",
      "risk",
    ].filter((k) => !body.value[k]?.trim());
    if (missing.length) {
      error.value =
        "试用前还需写清：" +
        missing.map((k) => labels[k]).join("、") +
        "。也可以把状态改为构思中，先保存。";
      groupIndex.value = Math.max(
        0,
        groups.value.findIndex((g) => g.fields.includes(missing[0])),
      );
      return;
    }
  }
  pending.value = true;
  error.value = "";
  try {
    const allowed = [
      ...editingFields.value,
      "topic_id",
      "method_id",
      "stage",
      "area",
      "evidence",
    ];
    const data: any = {};
    for (const k of allowed) {
      if (typeof body.value[k] !== "string") continue;
      if (
        ["topic_id", "method_id"].includes(k) &&
        (associationLocked.value ||
          !["insight", "method", "practice"].includes(props.kind) ||
          (k === "method_id" && props.kind !== "practice"))
      )
        continue;
      if (
        (k === "stage" && !stages[props.kind]) ||
        (k === "area" && props.kind !== "topic") ||
        (k === "evidence" && props.kind !== "practice")
      )
        continue;
      // Only fields actually edited are sent; unrelated server changes remain intact.
      if (!props.row || body.value[k] !== base.value[k])
        data[k] = body.value[k];
    }
    const result = props.row
      ? await request(
          "/api/growth/items/" + props.row.id,
          { version: version.value, patch: data, key: operationKey },
          "PUT",
        )
      : await request(
          "/api/growth/items",
          {
            kind: props.kind,
            fields: data,
            refs: props.refs || [],
            key: operationKey,
          },
          "POST",
        );
    uni.removeStorageSync(cacheKey);
    emit("saved", result);
  } catch (e: any) {
    error.value = e.message;
    if (e.status === 409 && props.row) {
      try {
        latest.value = await request("/api/growth/items/" + props.row.id);
      } catch {
        error.value += "。暂时无法读取新版本，草稿仍已保留，请稍后再试。";
      }
    }
  } finally {
    pending.value = false;
  }
}
function useLatest() {
  if (!latest.value || conflicts.value.some((k) => !choices.value[k])) return;
  const merged = merge.value!.body;
  for (const k of conflicts.value)
    if (choices.value[k] === "latest") merged[k] = latest.value.body[k] || "";
  const next = latest.value;
  if (props.kind === "practice" && next.body.phase !== "draft")
    for (const k of [
      "judgment",
      "conditions",
      "invalidation",
      "plan",
      "topic_id",
      "method_id",
    ])
      merged[k] = next.body[k] || "";
  sourceRow.value = next;
  base.value = next.body;
  body.value = merged;
  version.value = next.version;
  latest.value = null;
  operationKey = newId();
  choices.value = {};
  cache();
  error.value = "已合并未冲突的内容，请检查后保存。";
}
function discard() {
  uni.removeStorageSync(cacheKey);
  emit("cancel");
}
</script>
<template>
  <view class="growth-editor">
    <view class="section-bar"
      ><view class="section-title"
        >{{ row ? "编辑" : "新建" }}{{ kindNames[kind] }}</view
      ><button class="quiet" :disabled="pending" @tap="$emit('cancel')">
        保存草稿，稍后继续
      </button></view
    >
    <view v-if="restored" class="growth-hint"
      >已找回这台设备上的未完成草稿。</view
    >
    <view class="growth-field"
      ><text class="growth-label">标题</text
      ><input
        v-model="body.title"
        aria-label="标题"
        :disabled="pending"
        maxlength="160"
        placeholder="用一句话说清这是什么"
    /></view>
    <view class="growth-editor-steps"
      ><button
        v-for="(g, i) in groups"
        :key="g.title"
        :class="{ selected: groupIndex === i }"
        :disabled="pending"
        @tap="groupIndex = i"
      >
        <text class="growth-step-number">{{ i + 1 }}</text
        ><text>{{ g.title }}</text
        ><text class="muted"
          >{{ g.fields.filter((k) => body[k]?.trim()).length }}/{{
            g.fields.length
          }}</text
        >
      </button></view
    >
    <view class="growth-editor-section"
      ><view class="muted">{{ activeGroup.hint }}</view>
      <view
        v-for="field in activeGroup.fields"
        :key="field"
        class="growth-field"
        ><text class="growth-label">{{ labels[field] }}</text
        ><textarea
          v-model="body[field]"
          :aria-label="labels[field]"
          :disabled="pending"
          auto-height
          maxlength="12000"
          :placeholder="fieldHints[field]"
        />
      </view>
    </view>
    <view class="growth-editor-options">
      <view
        v-if="['insight', 'method', 'practice'].includes(kind)"
        class="growth-association"
      >
        <view v-if="directoryError" class="notice error"
          >{{ directoryError
          }}<button class="quiet" @tap="loadDirectory">
            重试加载专题
          </button></view
        >
        <view v-if="kind === 'practice'" class="growth-field"
          ><text class="growth-label">关联方法</text
          ><picker
            :disabled="pending || associationLocked || !directory"
            :range="methodOptions.map((r) => r.body.title)"
            :value="optionIndex(methodOptions, body.method_id || '')"
            @change="chooseMethod(Number($event.detail.value))"
            ><view class="growth-select"
              >{{
                methodOptions[optionIndex(methodOptions, body.method_id || "")]
                  ?.body.title
              }}{{ associationLocked ? " · 已锁定" : " ▾" }}</view
            ></picker
          ><button
            v-if="selectedMethod && !associationLocked"
            class="quiet"
            :disabled="pending"
            @tap="fillMethod"
          >
            用此方法补入空白的条件与计划
          </button></view
        >
        <view class="growth-field"
          ><text class="growth-label">所属专题</text
          ><picker
            :disabled="
              pending || associationLocked || methodTopicLocked || !directory
            "
            :range="topicOptions.map((r) => r.body.title)"
            :value="optionIndex(topicOptions, body.topic_id || '')"
            @change="chooseTopic(Number($event.detail.value))"
            ><view class="growth-select"
              >{{
                !directory && body.topic_id
                  ? "正在读取所属专题…"
                  : topicOptions[optionIndex(topicOptions, body.topic_id || "")]
                      ?.body.title
              }}{{
                associationLocked || methodTopicLocked ? " · 保留原归属" : " ▾"
              }}</view
            ></picker
          ><view v-if="methodTopicLocked" class="growth-hint"
            >已有实践的方法保留原专题，保证案例仍能对应原方法。</view
          ></view
        >
      </view>
      <view v-if="stages[kind]" class="growth-field"
        ><text class="growth-label">当前状态</text
        ><picker
          :disabled="pending"
          :range="stages[kind].map((s) => s[1])"
          :value="stageIndex"
          @change="body.stage = stages[kind][Number($event.detail.value)][0]"
          ><view class="growth-select"
            >{{ stages[kind][stageIndex][1] }} ▾</view
          ></picker
        ></view
      >
      <view v-if="kind === 'topic'" class="growth-field"
        ><text class="growth-label">学习范围</text
        ><picker
          :disabled="pending"
          :range="['交易学习', '其他学习']"
          :value="body.area === 'general' ? 1 : 0"
          @change="
            body.area =
              Number($event.detail.value) === 1 ? 'general' : 'trading'
          "
          ><view class="growth-select"
            >{{ body.area === "general" ? "其他学习" : "交易学习" }} ▾</view
          ></picker
        ></view
      >
      <view v-if="kind === 'practice' && phase !== 'draft'" class="growth-field"
        ><text class="growth-label">这个案例对方法的意义</text
        ><picker
          :disabled="pending"
          :range="['暂难判断', '提供支持', '出现反例']"
          :value="['uncertain', 'support', 'counter'].indexOf(body.evidence)"
          @change="
            body.evidence = ['uncertain', 'support', 'counter'][
              Number($event.detail.value)
            ]
          "
          ><view class="growth-select"
            >{{
              (
                {
                  uncertain: "暂难判断",
                  support: "提供支持",
                  counter: "出现反例",
                } as any
              )[body.evidence]
            }}
            ▾</view
          ></picker
        ></view
      >
    </view>
    <view v-if="latest" class="growth-comparison"
      ><view class="section-title">另一端已有更新，先合并再保存</view
      ><view class="muted"
        >没有冲突的修改会自动保留。以下同一字段的两种写法由你选择。</view
      >
      <view v-for="field in conflicts" :key="field" class="growth-diff"
        ><text class="growth-label">{{
          labels[field] ||
          (
            {
              topic_id: "所属专题",
              method_id: "关联方法",
              stage: "当前状态",
            } as any
          )[field] ||
          field
        }}</text
        ><view class="growth-prose">你的草稿：{{ body[field] || "留空" }}</view
        ><view class="growth-prose"
          >最新内容：{{ latest.body[field] || "留空" }}</view
        ><view class="growth-actions"
          ><button
            :class="['chip', { selected: choices[field] === 'mine' }]"
            @tap="choices[field] = 'mine'"
          >
            保留草稿</button
          ><button
            :class="['chip', { selected: choices[field] === 'latest' }]"
            @tap="choices[field] = 'latest'"
          >
            使用最新内容
          </button></view
        ></view
      >
      <button
        class="quiet"
        :disabled="conflicts.some((k) => !choices[k])"
        @tap="useLatest"
      >
        合并并继续编辑
      </button>
    </view>
    <view v-if="error" class="notice" role="status">{{ error }}</view>
    <view class="growth-editor-footer"
      ><view class="growth-actions"
        ><button
          v-if="groupIndex < groups.length - 1"
          class="quiet"
          :disabled="pending"
          @tap="groupIndex++"
        >
          下一步 · {{ groups[groupIndex + 1].title }} →</button
        ><button
          class="primary"
          :disabled="pending || !body.title?.trim() || !!latest"
          @tap="save"
        >
          {{ pending ? "正在保存…" : "保存内容 →" }}
        </button></view
      ><view class="growth-hint"
        >不必一次填完。草稿留在这台设备，保存内容后可在其他设备继续。</view
      ><button class="quiet growth-discard" :disabled="pending" @tap="discard">
        放弃这份未保存草稿
      </button></view
    >
  </view>
</template>

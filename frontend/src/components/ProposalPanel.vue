<script setup lang="ts">
import { ref, onMounted, watch } from "vue";
import SourceSnapshot from "./SourceSnapshot.vue";
import { request } from "../platform/network";
import { newId } from "../domain/types";
const props = defineProps<{
  id?: string;
  refs?: string[];
  rationale?: string;
}>();
const emit = defineEmits(["close", "changed"]);
const data = ref<any>(null),
  rules = ref<any[]>([]),
  rationale = ref(props.rationale || ""),
  error = ref(""),
  pending = ref(false),
  notice = ref(""),
  newText = ref(""),
  horizon = ref("unknown");
const source = ref<any>(null),
  restored = ref(false);
const cacheKey = "growth-proposal-draft-" + (props.refs || []).join("-");
let ready = false;
let proposalId = props.id || "",
  baseId: string | null = null,
  key = newId();
const period = (value: string) =>
  (({ short: "短线", medium_long: "中长线", unknown: "通用" }) as any)[
    value || "unknown"
  ];
async function load() {
  try {
    if (proposalId)
      data.value = await request("/api/growth/proposals/" + proposalId);
    else {
      const all = await request("/api/growth");
      baseId = all.strategy.id;
      rules.value = JSON.parse(JSON.stringify(all.strategy.body.rules));
      const draft = uni.getStorageSync(cacheKey);
      if (draft) {
        rules.value = draft.rules;
        rationale.value = draft.rationale;
        newText.value = draft.newText;
        horizon.value = draft.horizon;
        key = draft.key;
        baseId = draft.baseId;
        restored.value = true;
        if (baseId !== all.strategy.id)
          error.value = "草稿基于旧准则，请先放弃草稿，按当前版本重新整理。";
      }
      ready = true;
    }
  } catch (e: any) {
    error.value = e.message;
  }
}
onMounted(load);
watch(
  [rules, rationale, newText, horizon],
  () => {
    if (!ready || proposalId) return;
    key = newId();
    uni.setStorageSync(cacheKey, {
      rules: rules.value,
      rationale: rationale.value,
      newText: newText.value,
      horizon: horizon.value,
      baseId,
      key,
    });
  },
  { deep: true },
);
async function discardDraft() {
  uni.removeStorageSync(cacheKey);
  ready = false;
  restored.value = false;
  error.value = "";
  rationale.value = props.rationale || "";
  newText.value = "";
  key = newId();
  await load();
}
function add() {
  if (!newText.value.trim()) return;
  rules.value.push({
    id: newId(),
    text: newText.value.trim(),
    horizon: horizon.value,
  });
  newText.value = "";
  key = newId();
}
async function create() {
  pending.value = true;
  error.value = "";
  try {
    const row = await request(
      "/api/growth/proposals",
      {
        rules: rules.value,
        rationale: rationale.value,
        refs: props.refs || [],
        base_id: baseId,
        key,
      },
      "POST",
    );
    proposalId = row.id;
    uni.removeStorageSync(cacheKey);
    await load();
    emit("changed");
  } catch (e: any) {
    error.value = e.message;
  } finally {
    pending.value = false;
  }
}
async function decide(decision: string) {
  pending.value = true;
  error.value = "";
  try {
    await request(
      "/api/growth/proposals/" + proposalId + "/decision",
      { decision, fingerprint: data.value.body.digest },
      "POST",
    );
    notice.value =
      decision === "adopt"
        ? "已生效。下一次检查使用新版本，旧案例继续保留当时的准则。"
        : decision === "later"
          ? "已暂缓，之后可从体系继续审阅。"
          : "已记录不采用，当前准则保留。";
    await load();
    emit("changed");
  } catch (e: any) {
    error.value = e.message;
  } finally {
    pending.value = false;
  }
}
function conditionText(rule: any) {
  const c = rule.condition;
  if (!c) return "文字准则 · 需要人工判断";
  return `${c.label || c.field} ${({ lte: "≤", gte: "≥", eq: "=", in: "属于" } as any)[c.operator]} ${JSON.stringify(c.value)}`;
}
</script>
<template>
  <view class="overlay"
    ><view class="detail-sheet growth-proposal"
      ><view class="detail-head"
        ><view class="section-title">{{
          data ? "审阅准则修订" : "准备准则修订"
        }}</view
        ><button :disabled="pending" @tap="$emit('close')">关闭</button></view
      ><scroll-view scroll-y class="detail-scroll"
        ><view v-if="error" class="notice error">{{ error }}</view
        ><view v-if="notice" class="growth-saved">{{ notice }}</view>
        <template v-if="!data"
          ><view v-if="restored" class="growth-context-note"
            >已找回未完成的修订草稿。<button class="quiet" @tap="discardDraft">
              放弃草稿，重新整理
            </button></view
          ><view class="growth-hint"
            >先整理完整的新准则，再审阅前后变化。此处保存的是提案。</view
          ><view class="growth-field"
            ><text class="growth-label">为什么修改</text
            ><textarea
              v-model="rationale"
              auto-height
              maxlength="8000"
              placeholder="来自哪次经历、什么新认识；还有哪些未知"
            /></view
          ><view class="section-title">准备保留的准则</view
          ><view v-for="(rule, i) in rules" :key="rule.id" class="growth-rule"
            ><view class="growth-label"
              >{{ period(rule.horizon) }} · {{ i + 1 }}</view
            ><view class="growth-prose">{{ rule.text }}</view
            ><view class="muted">{{ conditionText(rule) }}</view
            ><button
              class="quiet"
              @tap="
                rules.splice(i, 1);
                key = newId();
              "
            >
              从新版本移除
            </button></view
          ><view v-if="!rules.length" class="growth-empty-note"
            >新版本目前没有条款；若现有准则非空，这表示全部移除，请仔细审阅。</view
          ><view class="growth-field"
            ><text class="growth-label">新增或替换一条文字准则</text
            ><textarea
              v-model="newText"
              auto-height
              placeholder="说清适用情形和具体要求；不确定时先保留为方法草稿"
            /><picker
              :range="['通用', '短线', '中长线']"
              :value="['unknown', 'short', 'medium_long'].indexOf(horizon)"
              @change="
                horizon = ['unknown', 'short', 'medium_long'][
                  Number($event.detail.value)
                ]
              "
              ><view class="growth-select"
                >{{ period(horizon) }} ▾</view
              ></picker
            ><button class="quiet" :disabled="!newText.trim()" @tap="add">
              加入新版本
            </button></view
          ><view class="growth-hint"
            >原有计算条件随保留条款保留。这里新增的文字条款需人工判断；替换旧条款时会在审阅中显示其条件变化。</view
          ><button
            class="primary"
            :disabled="pending || !rationale.trim() || !!newText.trim()"
            @tap="create"
          >
            {{ pending ? "正在整理…" : "查看具体修改 →" }}</button
          ><view v-if="newText.trim()" class="muted"
            >请先把已输入的条款加入新版本。</view
          ></template
        >
        <template v-else
          ><view class="growth-reading-section"
            ><text class="growth-label">修改理由</text
            ><view class="growth-prose">{{ data.body.rationale }}</view></view
          ><view v-if="data.sources?.length" class="growth-related"
            ><view class="growth-label">修改依据 · 保留提出时的版本</view
            ><button
              v-for="s in data.sources"
              :key="s.id"
              class="growth-reference"
              @tap="source = s"
            >
              {{ s.title }} · 第 {{ s.version }} 版 ↗
            </button></view
          ><view class="growth-proposal-diff"
            ><view
              ><view class="section-title">原来</view
              ><view
                v-for="r in data.base.body.rules"
                :key="r.id"
                class="growth-rule"
                ><text class="growth-label">{{ period(r.horizon) }}</text
                ><view class="growth-prose">{{ r.text }}</view
                ><view class="muted">{{ conditionText(r) }}</view></view
              ><view v-if="!data.base.body.rules.length" class="muted"
                >没有生效准则</view
              ></view
            ><view
              ><view class="section-title">拟改为</view
              ><view
                v-for="r in data.body.rules"
                :key="r.id"
                class="growth-rule"
                ><text class="growth-label">{{ period(r.horizon) }}</text
                ><view class="growth-prose">{{ r.text }}</view
                ><view class="muted">{{ conditionText(r) }}</view></view
              ><view v-if="!data.body.rules.length" class="muted"
                >移除全部准则</view
              ></view
            ></view
          ><view
            v-if="!data.adopted && data.current.id !== data.body.base_id"
            class="notice"
            >生效准则已变化，这份提案不能直接采用。请关闭后基于当前准则重新提出。</view
          ><view class="growth-context-note"
            >采用后只影响后续的准则检查。方法是否有效仍需实践证据。</view
          ></template
        > </scroll-view
      ><view v-if="data && !data.adopted" class="detail-bottom growth-actions"
        ><button
          class="primary"
          :disabled="pending || data.current.id !== data.body.base_id"
          @tap="decide('adopt')"
        >
          确认采用以上变更</button
        ><button class="quiet" :disabled="pending" @tap="decide('later')">
          暂缓</button
        ><button class="quiet" :disabled="pending" @tap="decide('reject')">
          不采用
        </button></view
      ><view v-else-if="data?.adopted" class="detail-bottom growth-saved"
        >该提案已确认生效</view
      ></view
    ><SourceSnapshot
      v-if="source"
      :source="source"
      :hide-latest="true"
      @close="source = null"
  /></view>
</template>

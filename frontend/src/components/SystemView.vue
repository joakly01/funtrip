<script setup lang="ts">
import { computed, ref, onMounted, watch, onUnmounted } from "vue";
import { request } from "../platform/network";
import { date } from "../domain/records";
import RecordCard from "./RecordCard.vue";
defineEmits(["open", "discuss", "tool"]);
const props = defineProps<{ revision?: number }>();
const data = ref<any>(null),
  error = ref(""),
  current = ref("shu"),
  more = ref(false);
const layers = [
  {
    key: "dao",
    mark: "道",
    title: "方向与原则",
    description: "你的目标、取舍与已认同的原则。",
  },
  {
    key: "fa",
    mark: "法",
    title: "纪律与规则",
    description: "行动的约束。正式纪律与修改提案分开。",
  },
  {
    key: "shu",
    mark: "术",
    title: "方法与技能",
    description: "把问题交给合适的工作方法，得到可回查的成果。",
  },
  {
    key: "qi",
    mark: "器",
    title: "具体工具",
    description: "打开就能用的计算、查询与记录工具。",
  },
];
const currentLayer = computed(() =>
  layers.find((l) => l.key === current.value),
);
const skillCount = computed(
  () => data.value?.skills.filter((s: any) => s.available).length || 0,
);
const toolCount = computed(
  () => data.value?.tools.filter((t: any) => t.available).length || 0,
);
let seq = 0;
async function load() {
  const n = ++seq;
  error.value = "";
  try {
    const value = await request("/api/system");
    if (n === seq) data.value = value;
  } catch (e: any) {
    if (n === seq) error.value = e.message;
  }
}
onMounted(load);
watch(() => props.revision, load);
onUnmounted(() => {
  ++seq;
});
</script>
<template>
  <view class="page-content system-page">
    <view class="page-heading editorial-heading"
      ><view
        ><text class="eyebrow">从认识，到行动</text
        ><view class="page-title">交易体系</view>
        <view class="muted"
          >道定方向，法立边界，术成方法，器助行动。</view
        ></view
      >
      <button
        class="quiet"
        @tap="
          $emit(
            'discuss',
            '一起回顾我的交易目标、已确认纪律与方法，指出尚未明确的部分。不要把候选认识当作正式纪律。',
          )
        "
      >
        回顾我的体系 ↗
      </button></view
    >
    <view v-if="error" class="notice error"
      >{{ error }}<button class="quiet" @tap="load">重新加载</button></view
    >
    <template v-if="data">
      <view class="system-status"
        ><text>{{ data.strategy?.body?.rules?.length || 0 }} 条生效纪律</text
        ><text>{{ skillCount }} 个工作技能</text
        ><text>{{ toolCount }} 件具体工具</text></view
      >
      <view class="layer-tabs"
        ><button
          v-for="l in layers"
          :key="l.key"
          :class="['layer-tab', { selected: current === l.key }]"
          @tap="current = l.key"
        >
          <text class="layer-mark">{{ l.mark }}</text
          ><text>{{ l.title }}</text>
        </button></view
      >
      <view class="layer-intro"
        ><view class="section-title">{{ currentLayer?.title }}</view
        ><view class="muted">{{ currentLayer?.description }}</view></view
      >
      <template v-if="current === 'dao'">
        <view class="section-bar"
          ><view class="subheading">个人目标与背景</view
          ><button
            class="quiet"
            @tap="
              $emit(
                'discuss',
                '请帮我梳理交易目标、时间投入、资金用途和风险承受边界。先询问我尚未明确的部分，保留个人原话。',
              )
            "
          >
            梳理方向 ↗
          </button></view
        >
        <view class="card-grid"
          ><RecordCard
            v-for="r in data.background"
            :key="r.id"
            :row="r"
            @open="$emit('open', $event)"
        /></view>
        <view v-if="!data.background.length" class="empty"
          >还没有明确记录个人目标，可以从“梳理方向”开始。</view
        >
        <view class="section-bar"
          ><view class="subheading">已认同的原则</view
          ><text class="muted">个人认同与实践效果分别保留</text></view
        >
        <view class="card-grid"
          ><RecordCard
            v-for="r in data.principles"
            :key="r.id"
            :row="r"
            @open="$emit('open', $event)"
        /></view>
        <view v-if="!data.principles.length" class="empty"
          >还没有明确标为“个人已认同”的原则，不替你补造信念。</view
        >
        <view v-if="data.pending_principles.length" class="detail-section"
          ><view class="subheading"
            >待吸收的认识 · {{ data.pending_principles.length }}</view
          >
          <view class="card-grid"
            ><RecordCard
              v-for="r in data.pending_principles"
              :key="r.id"
              :row="r"
              @open="$emit('open', $event)" /></view
        ></view>
      </template>
      <template v-else-if="current === 'fa'">
        <view class="discipline-panel"
          ><text class="eyebrow">当前生效纪律</text>
          <template v-if="data.strategy?.id"
            ><view class="muted"
              >第 {{ data.strategy.version }} 版 ·
              {{ date(data.strategy.body.effective_at) }} 生效</view
            >
            <view
              v-for="(r, i) in data.strategy.body.rules"
              :key="r.id"
              class="rule-row"
              ><text class="rule-number">{{ i + 1 }}</text
              ><text>{{ r.text }}</text></view
            >
            <view class="action-row"
              ><button class="quiet" @tap="$emit('open', data.strategy.id)">
                查看确认依据与版本 ↗</button
              ><button class="primary" @tap="$emit('tool', 'rules')">
                检查纪律条件
              </button></view
            ></template
          >
          <template v-else
            ><view class="section-title">尚未形成正式纪律</view
            ><view class="muted"
              >从具体经验提出规则，经过你的明确确认后再生效。</view
            >
            <button
              class="quiet"
              @tap="
                $emit(
                  'discuss',
                  '请基于我的真实经验和候选认识，帮我拟定一份交易纪律提案，说明规则、适用条件和依据，先供我审阅，不自动激活。',
                )
              "
            >
              准备一份纪律提案 ↗
            </button></template
          >
        </view>
        <view class="section-bar"
          ><view class="subheading">修改提案 · {{ data.proposals.length }}</view
          ><text class="muted">提案不等于生效规则</text></view
        >
        <view class="card-grid"
          ><RecordCard
            v-for="r in data.proposals"
            :key="r.id"
            :row="r"
            @open="$emit('open', $event)"
        /></view>
        <view v-if="!data.proposals.length" class="empty"
          >没有待阅读的规则提案。</view
        >
      </template>
      <template v-else-if="current === 'shu'">
        <view class="capability-grid"
          ><view
            v-for="(s, i) in data.skills"
            :key="s.id"
            class="capability-card"
          >
            <view class="capability-heading"
              ><text class="capability-number">0{{ i + 1 }}</text
              ><text
                :class="[
                  'badge',
                  s.available && s.model_ready ? 'confirmed' : 'pending',
                ]"
                >{{
                  !s.available
                    ? "尚未接入"
                    : s.model_ready
                      ? "可使用"
                      : "需配置模型"
                }}</text
              ></view
            >
            <view class="card-title">{{ s.title }}</view
            ><view class="muted">{{ s.description }}</view>
            <view class="capability-io"
              ><view
                ><text class="eyebrow">准备</text
                ><view>{{ s.input }}</view></view
              ><view
                ><text class="eyebrow">得到</text
                ><view>{{ s.output }}</view></view
              ></view
            >
            <text class="capability-note">{{ s.boundary }}</text
            ><button
              class="capability-action"
              :disabled="!s.available || !s.model_ready"
              @tap="$emit('discuss', s.prompt)"
            >
              带着任务开始 ↗
            </button>
          </view></view
        >
        <view class="section-bar"
          ><view class="subheading"
            >交易方法积累 · {{ data.methods.length }}</view
          ><button v-if="data.methods.length" class="quiet" @tap="more = !more">
            {{ more ? "收起" : "查看依据" }} {{ more ? "↑" : "↓" }}
          </button></view
        >
        <view class="muted"
          >从真实案例中形成的方法保留在这里，候选与实践证据各自标明。</view
        >
        <view v-if="more" class="card-grid"
          ><RecordCard
            v-for="r in data.methods"
            :key="r.id"
            :row="r"
            @open="$emit('open', $event)"
        /></view>
      </template>
      <template v-else-if="current === 'qi'">
        <view class="capability-grid tool-grid"
          ><view
            v-for="(t, i) in data.tools"
            :key="t.id"
            class="capability-card tool-card"
          >
            <view class="capability-heading"
              ><text class="tool-symbol">{{ ["％", "↗", "≡", "✓"][i] }}</text
              ><text class="badge">{{
                t.available ? "已接入" : "尚未接入"
              }}</text></view
            >
            <view class="card-title">{{ t.title }}</view
            ><view class="muted">{{ t.description }}</view
            ><text class="capability-note">{{ t.note }}</text>
            <button
              class="capability-action"
              :disabled="!t.available"
              @tap="$emit('tool', t.id)"
            >
              {{ t.action }} ↗
            </button>
          </view></view
        >
      </template>
    </template>
    <view v-else-if="!error" class="empty">正在打开你的体系…</view>
  </view>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick } from "vue";
import { request } from "../platform/network";
import { money, percent } from "../domain/knowledge";
import RecordCard from "./RecordCard.vue";
import ChartTool from "./ChartTool.vue";
import TrendTool from "./TrendTool.vue";
import SelectionTool from "./SelectionTool.vue";
import VolumePatternTool from "./VolumePatternTool.vue";
import ToolHelp from "./ToolHelp.vue";
import { toolHelp } from "../domain/toolHelp";
const props = defineProps<{ id: string }>();
const emit = defineEmits(["close", "open", "discuss"]);
const chartTool = ref<any>(null);
const showHelp = ref(false);
function toggleHelp() {
  showHelp.value = !showHelp.value;
  // #ifdef H5
  nextTick(() => document.querySelector<HTMLElement>(".tool-help-trigger")?.focus());
  // #endif
}
function helpKey(event: KeyboardEvent) {
  if (showHelp.value && event.key === "Escape") { event.preventDefault(); event.stopPropagation(); showHelp.value = false; }
}
async function close() {
  if (showHelp.value) { showHelp.value = false; return; }
  if (!chartTool.value || (await chartTool.value.closeAllowed())) emit("close");
}
const titles = Object.fromEntries(Object.entries(toolHelp).map(([id, help]) => [id, help.title]));
const error = ref(""),
  loading = ref(false),
  pending = ref(false),
  result = ref<any>(null),
  initial = ref<any>(null),
  query = ref(""),
  journalKind = ref("position"),
  equity = ref(""),
  inputs = ref<Record<string, any>>({}),
  positions = ref([{ name: "", quantity: "", price: "", scenario: "" }]);
let alive = true;
const journalRows = computed(() =>
  (initial.value?.records || []).filter(
    (r: any) => r.kind === journalKind.value,
  ),
);
const labels: Record<string, string> = {
  pass: "条件已满足",
  conflict: "存在条件冲突",
  unknown: "仍有待判断项",
  not_defined: "尚无生效准则",
};
function number(value: string, label: string) {
  const n = Number(value);
  if (!value.trim() || !Number.isFinite(n) || n <= 0)
    throw new Error(label + "需填写大于零的数值");
  return n;
}
function options(field: any): any[] {
  return field.type === "boolean" ? [true, false] : field.options || [];
}
function optionLabel(v: any) {
  return v === true ? "是" : v === false ? "否" : String(v);
}
function optionIndex(field: any) {
  return options(field).indexOf(inputs.value[field.key]) + 1;
}
function choose(field: any, index: number) {
  inputs.value[field.key] = index > 0 ? options(field)[index - 1] : undefined;
  result.value = null;
}
async function load() {
  error.value = "";
  loading.value = true;
  try {
    initial.value = await request("/api/tools/" + props.id);
  } catch (e: any) {
    if (alive) error.value = e.message;
  } finally {
    if (alive) loading.value = false;
  }
}
async function run(securityId = "") {
  pending.value = true;
  error.value = "";
  result.value = null;
  try {
    let value;
    if (props.id === "risk") {
      const rows = positions.value.map((p, i) => ({
        security_id: p.name.trim() || "持仓 " + (i + 1),
        quantity: number(p.quantity, "数量"),
        price: number(p.price, "当前价格"),
        ...(p.scenario.trim()
          ? { scenario_price: number(p.scenario, "情景价格") }
          : {}),
      }));
      value = await request(
        "/api/tools/risk",
        {
          positions: rows,
          account_equity: equity.value.trim()
            ? number(equity.value, "账户总资产")
            : null,
        },
        "POST",
      );
    } else if (props.id === "quote") {
      if (!securityId && !query.value.trim())
        throw new Error("请输入股票名称或代码");
      value = await request(
        "/api/tools/quote",
        securityId
          ? { security_id: securityId }
          : { query: query.value.trim() },
      );
    } else {
      const prepared: Record<string, any> = {};
      for (const f of initial.value?.fields || []) {
        const raw = inputs.value[f.key];
        if (raw == null || (typeof raw === "string" && !raw.trim())) continue;
        if (f.type === "number") {
          const n = Number(raw);
          if (!Number.isFinite(n)) throw new Error(f.label + "需填写数值");
          prepared[f.key] = n;
        } else prepared[f.key] = raw;
      }
      value = await request("/api/tools/rules", { inputs: prepared }, "POST");
    }
    if (alive) result.value = value;
  } catch (e: any) {
    if (alive) error.value = e.message;
  } finally {
    if (alive) pending.value = false;
  }
}
function timestamp(value: string) {
  return value ? new Date(value).toLocaleString("zh-CN") : "来源未提供时间";
}
onMounted(() => {
  // #ifdef H5
  document.addEventListener("keydown", helpKey, true);
  // #endif
  if (["journal", "rules"].includes(props.id)) load();
});
onUnmounted(() => {
  // #ifdef H5
  document.removeEventListener("keydown", helpKey, true);
  // #endif
  alive = false;
});
</script>
<template>
  <view class="overlay" @tap.self="close"
    ><view
      class="detail-sheet tool-sheet"
      :class="{ 'chart-sheet': ['chart', 'activity', 'trend'].includes(id) }"
    >
      <view class="detail-head"
        ><view
          ><text class="eyebrow">{{ showHelp ? "工具帮助" : "交易工具" }}</text
          ><view class="section-title">{{ titles[id] }}</view></view
        ><view class="tool-head-actions"><button v-if="toolHelp[id]" class="tool-help-trigger" role="button" tabindex="0" @keydown.enter.prevent="toggleHelp" @keydown.space.prevent="toggleHelp" :aria-label="showHelp ? '返回' + titles[id] : titles[id] + '帮助'" :aria-expanded="showHelp" aria-controls="tool-help-content" @tap="toggleHelp"><text v-if="!showHelp" class="tool-help-symbol">?</text><text>{{ showHelp ? "返回工具" : "帮助" }}</text></button><button class="icon-button" role="button" tabindex="0" @keydown.enter.prevent="close" @keydown.space.prevent="close" :aria-label="showHelp ? '关闭帮助' : '关闭工具'" @tap="close">关闭</button></view></view
      >
      <scroll-view v-if="showHelp" scroll-y class="detail-scroll help-scroll"><ToolHelp :id="id" /></scroll-view>
      <scroll-view v-show="!showHelp" scroll-y class="detail-scroll">
        <TrendTool v-if="id === 'trend'" @discuss="$emit('discuss', $event)" />
        <SelectionTool v-if="id === 'selection'" @discuss="$emit('discuss', $event)" />
        <VolumePatternTool v-if="id === 'pattern'" @discuss="$emit('discuss', $event)" />
        <ChartTool
          v-if="['chart', 'activity'].includes(id)"
          ref="chartTool"
          :id="id"
          @discuss="$emit('discuss', $event)"
        />
        <view v-if="loading" class="empty">正在读取…</view>
        <template v-if="id === 'risk'">
          <view class="muted"
            >按你输入的持仓计算。本次输入只用于计算，不保存为成交或持仓记录。</view
          >
          <view v-for="(p, i) in positions" :key="i" class="position-form">
            <view class="section-bar"
              ><view class="subheading">持仓 {{ i + 1 }}</view
              ><button
                v-if="positions.length > 1"
                class="quiet"
                :disabled="pending"
                @tap="
                  positions.splice(i, 1);
                  result = null;
                "
              >
                移除
              </button></view
            >
            <view class="form-grid"
              ><view class="form-field"
                ><text>名称（可选）</text
                ><input
                  v-model="p.name"
                  maxlength="60"
                  placeholder="便于区分持仓"
                  :disabled="pending"
                  @input="result = null"
              /></view>
              <view class="form-field"
                ><text>数量（股）</text
                ><input
                  v-model="p.quantity"
                  type="digit"
                  placeholder="如 1000"
                  :disabled="pending"
                  @input="result = null"
              /></view>
              <view class="form-field"
                ><text>当前价格（元）</text
                ><input
                  v-model="p.price"
                  type="digit"
                  placeholder="如 10.5"
                  :disabled="pending"
                  @input="result = null"
              /></view>
              <view class="form-field"
                ><text>退出情景价格（可选）</text
                ><input
                  v-model="p.scenario"
                  type="digit"
                  placeholder="如 9.8"
                  :disabled="pending"
                  @input="result = null" /></view
            ></view>
          </view>
          <button
            class="reference-link"
            :disabled="pending || positions.length >= 20"
            @tap="
              positions.push({
                name: '',
                quantity: '',
                price: '',
                scenario: '',
              });
              result = null;
            "
          >
            ＋ 添加一笔持仓
          </button>
          <view class="form-field"
            ><text>账户总资产（元，可选）</text
            ><input
              v-model="equity"
              type="digit"
              placeholder="用于计算账户仓位，包含现金"
              :disabled="pending"
              @input="result = null"
          /></view>
          <view v-if="result" class="tool-result"
            ><text class="eyebrow">本次计算</text
            ><view class="metric-pair"
              ><view
                ><text class="muted">已知持仓市值</text
                ><view class="metric-value"
                  >¥ {{ money(result.known_market_value) }}</view
                ></view
              ><view
                ><text class="muted">占账户总资产</text
                ><view class="metric-value small">{{
                  percent(result.account_weight)
                }}</view></view
              ></view
            >
            <view
              v-for="(p, i) in result.positions"
              :key="i"
              class="calculation-row"
              ><text>{{ p.security_id }}</text
              ><view
                >市值 ¥ {{ money(p.market_value)
                }}<view v-if="p.scenario_pnl != null" class="muted"
                  >情景变动 {{ p.scenario_pnl > 0 ? "+" : ""
                  }}{{ money(p.scenario_pnl) }} 元</view
                ></view
              ></view
            >
            <view class="muted">{{ result.warning }}</view
            ><view v-if="result.account_weight > 1" class="notice"
              >持仓市值超过账户总资产，请核对输入或杠杆情况。</view
            ></view
          >
        </template>
        <template v-else-if="id === 'quote'">
          <view class="muted"
            >查询公开 A 股报价，保留来源时间；本次查询不保存行情快照。</view
          >
          <view class="search-row tool-search"
            ><input
              v-model="query"
              placeholder="股票名称或代码，如 600519"
              :disabled="pending"
              @input="result = null"
              @confirm="run()"
            /><button class="primary" :disabled="pending" @tap="run()">
              {{ pending ? "查询中…" : "查询" }}
            </button></view
          >
          <template v-if="result && !result.quote"
            ><view class="subheading">{{
              result.candidates?.length
                ? "请选择准确的证券"
                : "没有找到对应的 A 股证券"
            }}</view
            ><button
              v-for="c in result.candidates || []"
              :key="c.security_id"
              class="reference-link"
              :disabled="pending"
              @tap="run(c.security_id)"
            >
              {{ c.name }} · {{ c.code }} →
            </button></template
          >
          <view v-if="result?.quote" class="tool-result"
            ><view class="subheading"
              >{{ result.quote.name }} ·
              {{
                result.quote.security_id?.split(":")[1] ||
                result.quote.security_id
              }}</view
            ><text class="eyebrow">来源报价</text
            ><view class="quote-price">{{ money(result.quote.last) }}</view>
            <view class="metric-pair"
              ><view
                ><text class="muted">今开</text
                ><view>{{ money(result.quote.open) }}</view></view
              ><view
                ><text class="muted">昨收</text
                ><view>{{ money(result.quote.previous_close) }}</view></view
              ><view
                ><text class="muted">最高 / 最低</text
                ><view
                  >{{ money(result.quote.high) }} /
                  {{ money(result.quote.low) }}</view
                ></view
              ></view
            >
            <view class="muted"
              >数据时间 {{ timestamp(result.quote.data_as_of) }}<br />来源：{{
                result.quote.source === "tencent_public"
                  ? "腾讯公开行情"
                  : result.quote.source
              }}<br />{{ result.quote.freshness?.label }}</view
            ></view
          >
        </template>
        <template v-else-if="id === 'journal' && initial">
          <view class="muted">{{ initial.scope }}</view
          ><view class="filter-inner journal-tabs"
            ><button
              :class="['chip', { selected: journalKind === 'position' }]"
              @tap="journalKind = 'position'"
            >
              持仓记录</button
            ><button
              :class="['chip', { selected: journalKind === 'trade' }]"
              @tap="journalKind = 'trade'"
            >
              成交记录
            </button></view
          >
          <view class="journal-list"
            ><RecordCard
              v-for="r in journalRows"
              :key="r.id"
              :row="r"
              @open="$emit('open', $event)" /></view
          ><view v-if="!journalRows.length" class="empty"
            >没有对应的历史记录。<button
              class="quiet"
              @tap="
                $emit(
                  'discuss',
                  '我想记录一笔真实持仓或成交，请先确认时间、股票、数量、价格，以及这是持仓快照还是实际成交。',
                )
              "
            >
              去记录 ↗
            </button></view
          >
        </template>
        <template v-else-if="id === 'rules' && initial">
          <view v-if="!initial.strategy?.id" class="empty empty-guide"
            ><view class="section-title">先有准则，再检查条件</view
            ><view class="muted"
              >目前没有生效准则。已有心得或修改提案不会被当作检查标准。</view
            ><button
              class="quiet"
              @tap="
                $emit(
                  'discuss',
                  '请帮我审阅已有规则提案或准备一份交易准则提案，解释规则条件和依据，先供我确认。',
                )
              "
            >
              准备准则提案 ↗
            </button></view
          >
          <template v-else
            ><view class="muted"
              >以当前生效版本检查。空项保留未知，描述性规则仍需结合证据判断。</view
            >
            <view
              v-for="f in initial.fields"
              :key="f.key"
              class="form-field rule-field"
              ><text>{{ f.label }}</text>
              <picker
                v-if="options(f).length"
                :value="optionIndex(f)"
                :range="['暂不提供', ...options(f).map(optionLabel)]"
                :disabled="pending"
                @change="choose(f, Number($event.detail.value))"
                ><view class="form-choice"
                  >{{
                    inputs[f.key] == null
                      ? "暂不提供"
                      : optionLabel(inputs[f.key])
                  }}
                  ▾</view
                ></picker
              >
              <input
                v-else
                v-model="inputs[f.key]"
                :type="f.type === 'number' ? 'digit' : 'text'"
                placeholder="暂不提供也可以"
                :disabled="pending"
                @input="result = null"
            /></view>
            <view v-if="!initial.fields.length" class="notice"
              >现有准则以文字原则为主，工具会保留待判断项。</view
            >
            <view v-if="result" class="tool-result"
              ><view class="section-title">{{
                labels[result.overall] || "待判断"
              }}</view
              ><view
                v-for="r in result.rules"
                :key="r.rule_id"
                class="rule-result"
                ><text
                  :class="[
                    'badge',
                    r.result === 'pass' ? 'confirmed' : 'pending',
                  ]"
                  >{{ labels[r.result] }}</text
                ><view>{{ r.text }}</view
                ><text class="muted">{{ r.reason }}</text></view
              ></view
            >
          </template>
        </template>
        <view v-if="error" class="notice error"
          >{{ error
          }}<button
            v-if="id === 'journal' || id === 'rules'"
            class="quiet"
            @tap="load"
          >
            重新加载
          </button></view
        >
      </scroll-view>
      <view
        v-if="!showHelp && (id === 'risk' || (id === 'rules' && initial?.strategy?.id))"
        class="detail-bottom"
        ><button class="primary" :disabled="pending || loading" @tap="run()">
          {{
            pending
              ? "正在计算…"
              : id === "risk"
                ? "计算仓位与情景"
                : "按生效准则检查"
          }}
        </button></view
      >
    </view></view
  >
</template>

<style scoped>
.tool-head-actions{display:flex;align-items:center;gap:12px;flex-shrink:0}.tool-help-trigger{display:flex;align-items:center;gap:6px;padding:0 6px;margin:0;background:transparent;color:#55725e;font-size:12px;line-height:32px;white-space:nowrap}.tool-help-trigger::after{border:0}.tool-help-symbol{display:flex;align-items:center;justify-content:center;border:1px solid #839b82;border-radius:50%;width:18px;height:18px;font-size:12px;line-height:18px}.tool-sheet>.detail-head>view:first-child{min-width:0}@media(max-width:600px){.tool-sheet>.detail-head{gap:8px}.tool-head-actions{gap:6px}.tool-sheet>.detail-head .section-title{font-size:19px}.tool-help-trigger{padding:0 3px}}
</style>

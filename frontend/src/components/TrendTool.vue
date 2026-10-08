<script setup lang="ts">
import { computed, onUnmounted, ref } from "vue";
import { request } from "../platform/network";
const emit = defineEmits(["discuss"]);
const query = ref(""),
  pending = ref(false),
  error = ref("");
const response = ref<any>(null),
  selectedName = ref("");
const horizon = ref("weekly"),
  guide = ref("");
let controller: AbortController | undefined,
  generation = 0,
  alive = true;
const analysis = computed(() => response.value?.analysis);
const timeframe = computed(() => analysis.value?.[horizon.value]);
const bookKey = computed(
  () => guide.value || analysis.value?.state || "unclear",
);
const book = computed(() => analysis.value?.playbooks[bookKey.value]);
const labels: Record<string, string> = {
  up: "上涨趋势",
  range: "盘整阶段",
  down: "下跌趋势",
  unclear: "方向待确认",
};
function cancel() {
  generation++;
  controller?.abort();
  pending.value = false;
}
async function run(securityId = "", name = "") {
  if (!securityId && !query.value.trim()) {
    error.value = "请输入股票名称或代码";
    return;
  }
  cancel();
  const current = generation;
  controller =
    typeof AbortController !== "undefined" ? new AbortController() : undefined;
  pending.value = true;
  error.value = "";
  response.value = null;
  guide.value = "";
  horizon.value = "weekly";
  selectedName.value = name || query.value.trim();
  try {
    const result = await request(
      "/api/tools/trend",
      securityId ? { security_id: securityId } : { query: query.value.trim() },
      "GET",
      { signal: controller?.signal },
    );
    if (alive && current === generation) response.value = result;
  } catch (e: any) {
    if (alive && current === generation) error.value = e.message;
  } finally {
    if (alive && current === generation) pending.value = false;
  }
}
onUnmounted(() => {
  alive = false;
  cancel();
});
const chart = computed(() => {
  const rows = timeframe.value?.chart || [];
  if (!rows.length) return null;
  const prices = rows.flatMap((r: any) =>
    [r.close, r.fast, r.slow].filter((x: any) => Number.isFinite(x)),
  );
  const lo = Math.min(...prices),
    hi = Math.max(...prices),
    pad = Math.max((hi - lo) * 0.1, hi * 0.015);
  const low = lo - pad,
    high = hi + pad;
  const x = (i: number) => 22 + (i / Math.max(rows.length - 1, 1)) * 676;
  const y = (v: number) => 20 + ((high - v) / (high - low)) * 180;
  const line = (key: string) =>
    rows
      .map((r: any, i: number) =>
        Number.isFinite(r[key]) ? `${x(i)},${y(r[key])}` : null,
      )
      .filter(Boolean)
      .join(" ");
  return {
    close: line("close"),
    fast: line("fast"),
    slow: line("slow"),
    from: rows[0].time.slice(0, 10),
    to: rows[rows.length - 1].time.slice(0, 10),
    ticks: [high, (high + low) / 2, low].map((v) => ({ v, y: y(v) })),
  };
});
function date(value: string) {
  return value ? value.slice(0, 10) : "尚无数据";
}
function discuss() {
  const a = analysis.value;
  if (!a) return;
  emit(
    "discuss",
    `请结合我的交易周期、已有持仓和生效准则，检查这份大趋势观察。信息不足请先追问，不把阶段提示自动当成买卖计划。\n\n股票：${response.value.identity?.name || selectedName.value}（${a.basis.security_id}）\n日线截至：${a.basis.as_of}；周线截至：${a.weekly.as_of || "历史不足"}\n来源：${a.basis.source}；前复权基准：${a.basis.adjustment_anchor || "未提供"}；算法：${a.algorithm_version}\n大趋势：${a.label}；日线：${a.daily.label}；${a.context}\n周线依据：${a.weekly.evidence.join("；")}\n相反信号：${a.weekly.counter_evidence.join("；") || "仍需结合环境验证"}\n当前阶段参考：${a.playbooks[a.state].prepare}\n重评条件：${a.playbooks[a.state].reassess}\n我的交易周期、持仓与想法：`,
  );
}
</script>
<template>
  <view class="trend-tool">
    <view class="trend-intro"
      ><text class="eyebrow">先定方向，再看位置</text
      ><view class="trend-lead">看清大趋势，选择合适的节奏</view
      ><view class="muted"
        >周线看大方向，日线看延续与转折。阶段不同，观察重点和行动条件也不同。</view
      ></view
    >
    <view class="trend-search"
      ><input
        v-model="query"
        maxlength="40"
        placeholder="股票名称或代码，如 中芯国际"
        :disabled="pending"
        @confirm="run()"
      /><button class="primary" :disabled="pending" @tap="run()">
        {{ pending ? "正在分析…" : "分析趋势" }}
      </button></view
    >
    <view v-if="pending" class="trend-status"
      ><text>正在读取历史日线并对照周线，通常数秒内完成。</text
      ><button class="quiet" @tap="cancel">取消</button></view
    >
    <view v-if="error" class="trend-error"
      ><text>{{ error }}</text
      ><button class="quiet" :disabled="pending" @tap="run()">
        重试
      </button></view
    >
    <view v-if="response && !analysis" class="trend-candidates"
      ><view class="muted">请选择你要分析的股票</view
      ><button
        v-for="item in response.candidates || []"
        :key="item.security_id"
        class="quiet"
        @tap="run(item.security_id, item.name)"
      >
        {{ item.name }} · {{ item.security_id }}</button
      ><view v-if="!response.candidates?.length" class="muted"
        >未找到匹配股票，请尝试六位代码。</view
      ></view
    >
    <template v-if="analysis">
      <view class="trend-summary" :class="'state-' + analysis.state"
        ><view class="trend-summary-top"
          ><text
            >{{ response.identity?.name || selectedName }} ·
            {{ analysis.basis.security_id }}</text
          ><text class="trend-badge">周线大方向</text></view
        ><view class="trend-stage">{{ analysis.label }}</view
        ><view class="trend-context">{{ analysis.context }}</view
        ><view class="trend-time"
          >日线截至 {{ date(analysis.basis.as_of) }} · 周线截至
          {{ date(analysis.weekly.as_of) }}</view
        ></view
      >
      <view class="trend-horizons"
        ><button
          v-for="key in ['weekly', 'daily']"
          :key="key"
          :class="['trend-horizon', { active: horizon === key }]"
          @tap="horizon = key"
        >
          <text>{{
            key === "weekly" ? "周线 · 大方向" : "日线 · 当前位置"
          }}</text
          ><text class="trend-horizon-label">{{ analysis[key].label }}</text
          ><text class="muted"
            >{{ analysis[key].count }} 根完成{{
              key === "weekly" ? "周线" : "日线"
            }}</text
          >
        </button></view
      >
      <view v-if="chart" class="trend-plot"
        ><view class="trend-legend"
          ><text class="legend-close">收盘走势</text
          ><text class="legend-fast"
            >{{ timeframe.fast_period
            }}{{ horizon === "weekly" ? "周" : "日" }}均线</text
          ><text class="legend-slow"
            >{{ timeframe.slow_period
            }}{{ horizon === "weekly" ? "周" : "日" }}均线</text
          ></view
        >
        <!-- #ifdef H5 -->
        <view class="trend-chart-body">
          <svg
            viewBox="0 0 780 232"
            role="img"
            :aria-label="
              horizon === 'weekly' ? '周线收盘走势与均线' : '日线收盘走势与均线'
            "
            class="trend-svg"
            preserveAspectRatio="none"
          >
            <g v-for="tick in chart.ticks" :key="tick.v">
              <line
                x1="22"
                x2="698"
                :y1="tick.y"
                :y2="tick.y"
                stroke="#e5eae8"
              />
            </g>
            <polyline
              :points="chart.close"
              stroke="#245d4d"
              stroke-width="2.5"
              fill="none"
              vector-effect="non-scaling-stroke"
            />
            <polyline
              :points="chart.fast"
              stroke="#bd8d3e"
              stroke-width="1.5"
              fill="none"
              vector-effect="non-scaling-stroke"
            />
            <polyline
              :points="chart.slow"
              stroke="#7b85a3"
              stroke-width="1.5"
              fill="none"
              vector-effect="non-scaling-stroke"
            />
          </svg>
          <text
            v-for="tick in chart.ticks"
            :key="tick.v"
            class="trend-axis-label"
            :style="{ top: (tick.y / 232) * 100 + '%' }"
            >{{ tick.v.toFixed(2) }}</text
          >
        </view>
        <!-- #endif -->
        <view class="trend-dates"
          ><text>{{ chart.from }}</text
          ><text>{{ chart.to }}</text></view
        ><view class="trend-footnote"
          >前复权历史价格，用于观察走势；参考价位需核对当前交易口径。</view
        >
      </view>
      <view class="trend-evidence"
        ><view
          ><view class="subheading">为什么这样判断</view
          ><view
            v-for="(reason, i) in timeframe.evidence"
            :key="i"
            class="trend-reason"
            ><text class="trend-dot">{{ i + 1 }}</text
            ><text>{{ reason }}</text></view
          ></view
        ><view
          ><view class="subheading">还要留意什么</view
          ><view
            v-for="(reason, i) in timeframe.counter_evidence"
            :key="i"
            class="trend-counter"
            >{{ reason }}</view
          ><view v-if="!timeframe.counter_evidence.length" class="trend-counter"
            >当前价格证据较一致，仍需核对大盘环境、量价和后续结构。</view
          ><view
            v-for="level in timeframe.levels"
            :key="level.kind"
            class="trend-level"
            ><text>{{ level.label }} · {{ date(level.time) }}</text
            ><text>{{ level.price.toFixed(2) }}</text></view
          ></view
        ></view
      >
      <view class="trend-playbook"
        ><view class="section-bar"
          ><view class="subheading">不同阶段，怎么应对</view
          ><text class="muted">观察与计划参考</text></view
        ><view class="trend-tabs"
          ><button
            v-for="key in ['up', 'range', 'down', 'unclear']"
            :key="key"
            :class="['quiet', { chosen: bookKey === key }]"
            @tap="guide = key"
          >
            {{ labels[key] }}{{ analysis.state === key ? " · 当前" : "" }}
          </button></view
        ><view class="trend-book-title">{{ book.title }}</view
        ><view v-if="bookKey !== analysis.state" class="trend-footnote"
          >正在查看其他阶段的参考方法，当前判断仍为“{{
            analysis.label
          }}”。</view
        ><view class="trend-guide-row"
          ><text class="trend-guide-label">看什么</text
          ><text>{{ book.observe }}</text></view
        ><view class="trend-guide-row"
          ><text class="trend-guide-label">怎么准备</text
          ><text>{{ book.prepare }}</text></view
        ><view class="trend-guide-row"
          ><text class="trend-guide-label">避免什么</text
          ><text>{{ book.avoid }}</text></view
        ><view class="trend-guide-row"
          ><text class="trend-guide-label">何时重评</text
          ><text>{{ book.reassess }}</text></view
        ></view
      >
      <view class="trend-bottom"
        ><button class="primary" @tap="discuss">结合我的情况，检查计划</button
        ><text class="muted"
          >把依据带入对话，你的持仓、周期与纪律决定具体行动。</text
        ></view
      >
      <view class="trend-source"
        ><text
          >历史范围 {{ date(analysis.basis.from) }} —
          {{ date(analysis.basis.as_of) }} ·
          {{ analysis.basis.daily_count }} 根日线</text
        ><text
          >来源 {{ analysis.basis.source }} · 前复权基准
          {{ analysis.basis.adjustment_anchor || "未提供" }}</text
        ><text>{{ analysis.basis.weekly_note }}</text
        ><text>{{ analysis.boundary }}</text>
        <details>
          <summary>查看数据边界与方法</summary>
          <p v-for="(warning, i) in analysis.warnings" :key="i">
            {{ warning }}
          </p>
          <p>
            周线观察10/40周均线，日线观察20/120日均线；结合均线斜率、回归方向和左右各两根K线确认的高低点。上涨/下跌需多项证据一致；盘整需要独立的区间证据。均线与高低点都有滞后，未通过收益回测。
          </p>
          <p>
            周线至少44根、日线至少130根；来源可能返回较少历史，历史不足保留待确认。完整日历、停牌与独立价格一致性尚未核验。
          </p>
        </details></view
      >
    </template>
    <view v-else-if="!pending && !error && !response" class="trend-empty"
      ><view class="trend-empty-item"
        ><text>01 · 大方向</text><view>先分清上涨、盘整与下跌</view></view
      ><view class="trend-empty-item"
        ><text>02 · 当前位置</text><view>再看回调、反弹与周期分歧</view></view
      ><view class="trend-empty-item"
        ><text>03 · 行动条件</text><view>最后明确等待什么、何时重评</view></view
      ></view
    >
  </view>
</template>
<style scoped>
.trend-tool {
  color: #263d34;
  padding: 4px 0 24px;
}
.trend-intro {
  padding-bottom: 20px;
}
.trend-lead {
  font-size: 23px;
  font-weight: 600;
  line-height: 1.5;
  margin: 8px 0;
}
.trend-search {
  display: flex;
  gap: 10px;
  align-items: center;
}
.trend-search input {
  flex: 1;
  min-width: 0;
  background: #f3f6f4;
  border: 1px solid #e3e9e5;
  border-radius: 12px;
  padding: 13px 15px;
  height: 48px;
  font-size: 14px;
}
.trend-search button {
  margin: 0;
  flex-shrink: 0;
}
.trend-status,
.trend-error {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin: 16px 0;
  padding: 12px;
  background: #f6f3eb;
  border-radius: 12px;
  font-size: 13px;
}
.trend-error {
  color: #974b42;
}
.trend-summary {
  margin: 22px 0 16px;
  padding: 24px 26px;
  border: 1px solid #dce7e1;
  background: linear-gradient(125deg, #edf5f0, #f7f9f6);
  border-radius: 18px;
}
.state-down {
  background: linear-gradient(125deg, #f5eeeb, #faf8f5);
  border-color: #e9ddd7;
}
.state-unclear,
.state-range {
  background: linear-gradient(125deg, #f3f1eb, #faf9f5);
  border-color: #e6e2d7;
}
.trend-summary-top {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 13px;
  color: #617568;
}
.trend-badge {
  border: 1px solid #cad8ce;
  border-radius: 20px;
  padding: 3px 9px;
  white-space: nowrap;
}
.trend-stage {
  font-size: 32px;
  font-weight: 600;
  letter-spacing: 1px;
  margin: 8px 0;
}
.trend-context {
  font-size: 15px;
  line-height: 1.7;
}
.trend-time {
  font-size: 12px;
  color: #7a8278;
  margin-top: 14px;
}
.trend-horizons {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.trend-horizon {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  padding: 14px 18px;
  background: #fafbf9;
  border: 1px solid #e4e9e4;
  border-radius: 12px;
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: #66776d;
}
.trend-horizon.active {
  border-color: #668c76;
  background: #f0f5f1;
}
.trend-horizon-label {
  font-size: 18px;
  color: #31483b;
  font-weight: 600;
  margin: 5px 0;
}
.trend-plot {
  margin-top: 16px;
  padding: 16px;
  background: #fbfcfa;
  border: 1px solid #e5eae4;
  border-radius: 14px;
}
.trend-legend {
  display: flex;
  gap: 20px;
  font-size: 12px;
  color: #728079;
  margin-bottom: 8px;
}
.trend-legend text:before {
  content: "";
  display: inline-block;
  width: 12px;
  height: 2px;
  margin: 0 6px 3px 0;
  background: #245d4d;
}
.trend-legend .legend-fast:before {
  background: #bd8d3e;
}
.trend-legend .legend-slow:before {
  background: #7b85a3;
}
.trend-chart-body {
  position: relative;
}
.trend-axis-label {
  position: absolute;
  right: 0;
  transform: translateY(-50%);
  font-size: 12px;
  color: #728079;
}
.trend-svg {
  width: 100%;
  height: 232px;
  display: block;
}
.trend-dates {
  display: flex;
  justify-content: space-between;
  padding-right: 9%;
  font-size: 11px;
  color: #87918a;
}
.trend-footnote {
  font-size: 12px;
  color: #81877c;
  line-height: 1.7;
  margin-top: 10px;
}
.trend-evidence {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
  padding: 24px 0;
}
.trend-reason {
  display: flex;
  gap: 10px;
  font-size: 13px;
  line-height: 1.9;
  margin-top: 12px;
}
.trend-dot {
  color: #809d89;
  font-size: 12px;
  flex-shrink: 0;
}
.trend-counter {
  font-size: 13px;
  color: #7c7260;
  line-height: 1.9;
  margin-top: 12px;
}
.trend-level {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
  padding-top: 12px;
  color: #6e7b70;
}
.trend-playbook {
  border-top: 1px solid #e6e9e2;
  padding-top: 20px;
}
.trend-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 16px 0;
}
.trend-tabs button {
  margin: 0;
  font-size: 12px;
  line-height: 20px;
  padding: 7px 12px;
  border: 1px solid #e0e6dc;
  border-radius: 20px;
}
.trend-tabs .chosen {
  background: #2d5946;
  color: #fff;
  border-color: #2d5946;
}
.trend-book-title {
  font-size: 20px;
  font-weight: 600;
  margin: 18px 0;
}
.trend-guide-row {
  display: grid;
  grid-template-columns: 76px 1fr;
  gap: 14px;
  font-size: 14px;
  line-height: 1.9;
  padding: 13px 0;
  border-bottom: 1px solid #eff0eb;
}
.trend-guide-label {
  font-size: 12px;
  color: #889079;
}
.trend-bottom {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 10px;
  margin: 24px 0;
}
.trend-bottom button {
  margin: 0;
}
.trend-source {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 11px;
  color: #939789;
  line-height: 1.8;
  border-top: 1px solid #e8ebe3;
  padding-top: 16px;
}
.trend-source summary {
  cursor: pointer;
  color: #6d7d6e;
}
.trend-source p {
  margin: 8px 0;
}
.trend-empty {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  margin: 32px 0;
}
.trend-empty-item {
  padding: 18px;
  background: #f7f8f4;
  border-radius: 14px;
  font-size: 13px;
  line-height: 1.9;
}
.trend-empty-item text {
  font-size: 12px;
  color: #889581;
}
.trend-candidates {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 20px;
}
.trend-candidates button {
  margin: 0;
  text-align: left;
}
@media (max-width: 600px) {
  .trend-lead {
    font-size: 20px;
  }
  .trend-search {
    gap: 8px;
  }
  .trend-search input {
    padding: 11px;
    font-size: 12px;
  }
  .trend-search button {
    min-width: 86px;
    height: 48px;
    line-height: 20px;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 10px 12px;
    font-size: 12px;
  }
  .trend-summary {
    padding: 18px;
  }
  .trend-stage {
    font-size: 28px;
  }
  .trend-summary-top {
    font-size: 11px;
  }
  .trend-context {
    font-size: 14px;
  }
  .trend-time {
    font-size: 11px;
  }
  .trend-horizons {
    gap: 8px;
  }
  .trend-horizon {
    padding: 12px;
    font-size: 12px;
  }
  .trend-horizon-label {
    font-size: 17px;
  }
  .trend-legend {
    gap: 10px;
    font-size: 10px;
  }
  .trend-svg {
    height: 210px;
  }
  .trend-axis-label {
    font-size: 11px;
  }
  .trend-plot {
    padding: 10px;
  }
  .trend-evidence {
    grid-template-columns: 1fr;
    gap: 20px;
  }
  .trend-guide-row {
    grid-template-columns: 60px 1fr;
    gap: 10px;
    font-size: 13px;
  }
  .trend-tabs {
    gap: 6px;
  }
  .trend-tabs button {
    font-size: 11px;
    padding: 6px 9px;
  }
  .trend-empty {
    grid-template-columns: 1fr;
    gap: 10px;
  }
  .trend-empty-item {
    padding: 12px 16px;
  }
  .trend-book-title {
    font-size: 18px;
  }
  .trend-bottom button {
    font-size: 13px;
  }
  .trend-status {
    font-size: 12px;
  }
}
</style>

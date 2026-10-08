<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue';
import { request } from '../platform/network';
import { eventsAt } from '../domain/volumePattern';
import { chartScale, plot, type Bar } from '../domain/chart';
const emit = defineEmits(['discuss']);
const query = ref(''), ratio = ref('2'), wait = ref('20');
const pending = ref(false), error = ref(''), payload = ref<any>(null);
const day = ref(0), selection = ref(''), details = ref(false);
let controller: AbortController | undefined, generation = 0, alive = true;
const rows = computed<Bar[]>(() => payload.value?.data?.bars || []);
const asOf = computed(() => rows.value[day.value]?.time || '');
const candidates = computed(() => asOf.value ? eventsAt(payload.value?.analysis?.events || [], asOf.value) : []);
const event = computed(() => candidates.value.find(e => e.id === selection.value) || candidates.value[0]);
const shown = computed(() => rows.value.slice(Math.max(0, day.value-79), day.value+1));
const scale = computed(() => shown.value.length ? chartScale(shown.value, event.value ? [event.value.levels.support_low, event.value.levels.support_high, event.value.levels.pressure] : []) : null);
const maxVolume = computed(() => Math.max(1, ...shown.value.map(b => b.volume ?? b.volume_raw ?? 0)));
const eventIndex = computed(() => shown.value.findIndex(b => b.time === event.value?.time));
const ticks = computed(() => scale.value ? [scale.value.max, (scale.value.max+scale.value.min)/2, scale.value.min].map(p => ({p,y:scale.value!.y(p)})) : []);
const date = (t: string) => t?.slice(0,10) || '—';
const price = (p: number) => Number.isFinite(p) ? p.toFixed(2) : '—';
function cancel() { generation++; controller?.abort(); pending.value = false; }
async function run(securityId = '') {
  if (!securityId && !query.value.trim()) { error.value = '请输入股票名称或代码'; return; }
  const volumeRatio = Number(ratio.value), maxWait = Number(wait.value);
  if (!Number.isFinite(volumeRatio) || volumeRatio < 1.2 || volumeRatio > 5 || !Number.isInteger(maxWait) || maxWait < 5 || maxWait > 40) {
    error.value = '放量门槛请输入1.2—5倍，观察期限请输入5—40根整数日线'; return;
  }
  cancel(); const token = generation;
  controller = typeof AbortController !== 'undefined' ? new AbortController() : undefined;
  pending.value = true; error.value = ''; payload.value = null; selection.value = '';
  try {
    const result = await request('/api/tools/pattern', { ...(securityId ? {security_id:securityId} : {query:query.value.trim()}), volume_ratio:volumeRatio, max_wait:maxWait }, 'GET', {timeoutMs:30000,signal:controller?.signal});
    if (alive && token === generation) {
      payload.value = result; day.value = (result.data?.bars.length || 1)-1;
      if (!result.analysis) error.value = result.candidates?.length ? '请选择要观察的股票' : '没有找到股票，请检查名称或代码';
    }
  } catch(e: any) { if (alive && token === generation) error.value = e.message; }
  finally { if (alive && token === generation) pending.value = false; }
}
function jumpToEvent() {
  if (!event.value) return;
  day.value = rows.value.findIndex(b => b.time === event.value.time);
}
function discuss() {
  if (!event.value) return;
  emit('discuss', `请审阅这份放量后结构观察，区分可观察现象、作者解释与工具参数。请核对大趋势、板块、反例与我的交易周期，不将确认条件满足当成买入指令。\n证券：${payload.value.data.security_id}；完成日线截至：${asOf.value}；来源：${payload.value.data.source_url}；不复权。\n算法：${payload.value.analysis.algorithm_version}；参数：${JSON.stringify(payload.value.analysis.parameters)}\n当时可见事件：${JSON.stringify(event.value)}\n我的计划或问题：`);
}
onUnmounted(() => { alive = false; cancel(); });
</script>
<template>
  <view class="pattern-tool">
    <view class="pattern-intro"><text class="eyebrow">从异常到确认，逐步观察</text><view class="subheading">放量之后，价格有没有守住？</view><view class="muted">定位放量上影，自动画出参考价区，再看整理回撤与再次突破。按所选日期截取历史行情，逐日查看依据。</view></view>
    <view class="pattern-search"><input v-model="query" placeholder="股票名称或代码，如 600000" maxlength="40" :disabled="pending" @confirm="run()"/><button :disabled="pending" @tap="run()">{{ pending ? '正在载入…' : '观察结构' }}</button></view>
    <view class="pattern-parameters"><label>放量门槛（倍）<input v-model="ratio" type="digit" :disabled="pending"/></label><label>观察期限（日线根数）<input v-model="wait" type="number" :disabled="pending"/></label></view>
    <view class="chart-meta">以上是可调整的工具参数，视频没有给出这些数值。修改后重新观察。</view>
    <view v-if="error" class="error" role="alert">{{ error }}</view>
    <view v-if="pending" role="status" class="pattern-search"><text>正在读取公开行情，最多等待30秒…</text><button class="quiet" @tap="cancel">取消</button></view>
    <button v-for="c in payload?.candidates || []" :key="c.security_id" class="reference-link" @tap="run(c.security_id)">{{ c.name }} · {{ c.code }} →</button>
    <template v-if="payload?.analysis">
      <view class="section-bar"><strong>{{ payload.identity?.name || payload.data.security_id }}</strong><text class="chart-meta">日线 · 不复权 · {{ payload.data.source === 'tencent_public' ? '腾讯公开行情' : payload.data.source }}</text></view>
      <view v-if="!payload.analysis.available" class="empty">{{ payload.analysis.reason }}</view>
      <template v-else>
        <view class="pattern-replay"><strong>{{ day === rows.length-1 ? '最新观察' : '历史逐日回看' }} · {{ date(asOf) }}</strong><view class="chart-controls"><button class="quiet" :disabled="day <= 0" @tap="day--">前一天</button><button class="quiet" :disabled="day >= rows.length-1" @tap="day++">后一天</button><button class="quiet" :disabled="!event" @tap="jumpToEvent">回到放量当天</button><button class="quiet" :disabled="day === rows.length-1" @tap="day=rows.length-1">回到最新</button></view></view>
        <view v-if="!candidates.length" class="empty">截至所选日期，没有符合门槛的放量上影候选；先继续观察。</view>
        <view v-else>
          <view class="pattern-candidates"><button v-for="e in candidates" :key="e.id" class="quiet" :class="{active:event?.id===e.id}" @tap="selection=e.id">{{ date(e.time) }} · {{ e.label }}</button></view>
          <view class="pattern-verdict"><text class="eyebrow">{{ date(event.time) }} · 放量{{ event.volume_ratio.toFixed(2) }}倍</text><view class="pattern-title">{{ event.label }}</view><view>{{ event.reason }}</view><view class="chart-meta">{{ event.observation }} · 连续阴线{{ event.bearish_streak }}根 · {{ event.support_test ? '本次触及参考区并收盘守住' : '本次没有触区守位证据' }}</view><view class="chart-meta">{{ event.context }} · 评估截至{{ date(event.assessed_at) }}</view></view>
        </view>
        <!-- #ifdef H5 -->
        <view v-if="scale" class="pattern-chart">
          <view class="chart-meta">{{ date(shown[0].time) }}—{{ date(asOf) }} · 红涨绿跌 · 下方为成交量</view>
          <view class="pattern-plot"><svg viewBox="0 0 780 420" role="img" aria-label="放量事件与冻结参考价区的日线图">
            <template v-if="event">
              <rect :x="eventIndex>=0 ? scale.x(eventIndex) : plot.left" :y="scale.y(event.levels.support_high)" :width="plot.right-(eventIndex>=0 ? scale.x(eventIndex) : plot.left)" :height="scale.y(event.levels.support_low)-scale.y(event.levels.support_high)" fill="#c4ac72" opacity=".16"/>
              <line :x1="eventIndex>=0 ? scale.x(eventIndex) : plot.left" :x2="plot.right" :y1="scale.y(event.levels.pressure)" :y2="scale.y(event.levels.pressure)" stroke="#9c7650" stroke-dasharray="5 5"/>
              <line v-if="eventIndex>=0" :x1="scale.x(eventIndex)" :x2="scale.x(eventIndex)" y1="24" y2="400" stroke="#7b7c97" stroke-dasharray="3 5"/>
            </template>
            <g v-for="(b,i) in shown" :key="b.time" :stroke="b.close>=b.open ? '#ac514d' : '#487f72'" :fill="b.close>=b.open ? '#ac514d' : '#487f72'">
              <line :x1="scale.x(i)" :x2="scale.x(i)" :y1="scale.y(b.high)" :y2="scale.y(b.low)"/>
              <rect :x="scale.x(i)-2.5" :y="Math.min(scale.y(b.open),scale.y(b.close))" width="5" :height="Math.max(1,Math.abs(scale.y(b.open)-scale.y(b.close)))"/>
              <rect :x="scale.x(i)-2.5" :y="400-((b.volume ?? b.volume_raw ?? 0)/maxVolume)*65" width="5" :height="((b.volume ?? b.volume_raw ?? 0)/maxVolume)*65" opacity=".45" stroke="none"/>
            </g>
          </svg><text v-for="tick in ticks" :key="tick.p" class="pattern-axis" :style="{top:(tick.y/420*100)+'%'}">{{ price(tick.p) }}</text></view>
          <view v-if="event" class="pattern-legend"><text>竖线标记：放量当天</text><text>浅色区域：{{ price(event.levels.support_low) }}—{{ price(event.levels.support_high) }}</text><text>虚线压力：{{ price(event.levels.pressure) }}</text></view>
        </view>
        <!-- #endif -->
        <template v-if="event">
          <view class="pattern-boundary"><strong>什么情况需要推翻？</strong><view>{{ event.invalidation }}</view><view class="chart-meta">{{ event.levels.basis }}</view></view>
          <button class="reference-link" @tap="details=!details">{{ details ? '收起' : '查看' }}逐日依据与参数 →</button>
          <view v-if="details" class="pattern-trace"><view v-for="t in event.trace" :key="t.time"><strong>{{ date(t.time) }} · {{ t.label }}</strong><view>{{ t.reason }} {{ t.observation }}</view></view><view>此前20根量的中位数作基准；上影占振幅≥20%；至少3根连续区内守位后核对突破；突破幅度≥事件前波幅的0.15倍、量比≥1.2倍，再等后续收盘守住。下沿容差为事件前波幅的0.25倍，最多价格的5%。这些是首版观察规则，效果待验证。</view><view v-for="u in payload.analysis.unknowns" :key="u">待核对：{{ u }}</view></view>
          <button class="reference-link" @tap="discuss">与伙伴核对这份观察 →</button>
        </template>
      </template>
      <view class="chart-meta pattern-footer">{{ payload.analysis.boundary }} 确认后的结构仅跟踪到本次观察期限；候选列表包含失效和到期案例，并非实时选股池。回看基于当前提供方历史行情，不是当时保存的事前快照。</view>
    </template>
  </view>
</template>
<style scoped>
.pattern-tool{display:grid;grid-template-columns:minmax(0,1fr);gap:18px}.pattern-tool>*{min-width:0;max-width:100%;box-sizing:border-box}.pattern-tool input{box-sizing:border-box;width:100%;border:1px solid #deddd3;border-radius:10px;padding:12px;background:#fff}.pattern-tool .pattern-parameters label{min-width:0}.pattern-intro{display:grid;gap:10px}.pattern-search{display:flex;align-items:center;gap:10px}.pattern-search input{flex:1;min-width:0}.pattern-parameters{display:flex;gap:18px}.pattern-parameters label{flex:1;font-size:12px;color:var(--muted,#74756e)}.pattern-parameters input{margin-top:6px}.pattern-replay,.pattern-boundary{padding:18px;border-radius:14px;background:#f3f2ec;display:grid;gap:12px}.pattern-candidates{display:flex;gap:8px;overflow-x:auto;max-width:100%;padding-bottom:12px}.pattern-candidates button{flex-shrink:0}.pattern-verdict{padding:22px;border:1px solid #deddd3;border-radius:14px;display:grid;gap:10px}.pattern-title{font-size:24px;font-weight:600}.pattern-plot{position:relative}.pattern-axis{position:absolute;right:0;transform:translateY(-50%);font-size:10px;color:#8b8a80;pointer-events:none}.pattern-chart svg{width:100%;height:auto;display:block}.pattern-legend{display:flex;gap:12px;flex-wrap:wrap;font-size:12px;color:#7c725f}.pattern-trace{display:grid;gap:15px;padding:18px;background:#faf9f5;border-radius:12px;font-size:13px;line-height:1.8}.pattern-footer{border-top:1px solid #e4e2d9;padding-top:16px}.pattern-tool .active{background:#e5e8df}.pattern-tool .chart-controls{display:flex;gap:8px;flex-wrap:wrap}.pattern-tool .chart-meta{line-height:1.8}@media(max-width:600px){.pattern-search{flex-wrap:wrap}.pattern-search input{min-width:180px}.pattern-parameters{gap:10px}.pattern-legend{display:grid;gap:5px}.pattern-title{font-size:21px}}
</style>

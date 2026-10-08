<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue';
import { request } from '../platform/network';
const emit = defineEmits(['discuss']);
const query = ref(''), ratio = ref('1.5'), lookback = ref(40), start = ref(''), cutoff = ref('');
const pending = ref(false), error = ref(''), payload = ref<any>(null), dates = ref<string[]>([]), detail = ref(false);
let controller: AbortController | undefined, generation = 0, alive = true;
const analysis = computed(() => payload.value?.analysis);
const labels: Record<string,string> = {pass:'观察条件满足',watch:'需要复查',unknown:'证据待补齐'};
const pct = (v: number | null) => v == null ? '未计算' : (v>=0 ? '+' : '')+(v*100).toFixed(2)+'%';
function dirty() { payload.value = null; error.value = ''; }
function changeStock() { dirty(); dates.value=[]; start.value=''; cutoff.value=''; }
function cancel() { generation++; controller?.abort(); pending.value=false; }
async function run(securityId='') {
  if (!securityId && !query.value.trim()) { error.value='请输入股票名称或代码'; return; }
  const threshold = Number(ratio.value);
  if (!Number.isFinite(threshold) || threshold<1.2 || threshold>5) {error.value='放量门槛请输入1.2—5倍';return;}
  cancel(); const token=generation;
  controller=typeof AbortController!=='undefined' ? new AbortController() : undefined;
  pending.value=true; error.value=''; payload.value=null;
  try {
    const value = await request('/api/tools/selection',{...(securityId?{security_id:securityId}:{query:query.value.trim()}), lookback:lookback.value,volume_ratio:threshold,...(start.value?{start_date:start.value}:{}),...(cutoff.value?{as_of:cutoff.value}:{})},'GET',{timeoutMs:30000,signal:controller?.signal});
    if (alive && token===generation) {payload.value=value; if (value.analysis?.dates) dates.value=value.analysis.dates; if (!value.analysis) error.value=value.candidates?.length?'请选择准确的股票':'没有找到对应股票';}
  } catch(e: any) {if(alive && token===generation) error.value=e.message;}
  finally {if(alive && token===generation) pending.value=false;}
}
function discuss() {
  if(!analysis.value?.available)return;
  emit('discuss',`请核对这份“趋势与量价检查”，不要把筛选达标当成买点。请区分视频原意、工具代理参数、大趋势背景与我的计划。\n证券：${payload.value.basis.security_id}；截至：${analysis.value.as_of}；复权：${payload.value.basis.adjustment}；行情来源：${payload.value.basis.source_url}\n工具版本：${analysis.value.algorithm_version}；参数：${JSON.stringify(analysis.value.parameters)}\n结果：${JSON.stringify(analysis.value.checks.map((c: any)=>({title:c.title,state:c.state,summary:c.summary,detail:c.detail})))}\n我的周期和计划：`);
}
onUnmounted(()=>{alive=false;cancel();});
</script>
<template>
  <view class="selection-tool">
    <view class="intro"><text class="eyebrow">先筛选，再观察，再准备计划</text><view class="section-title">值得继续观察吗？</view><view class="muted">分别核对方向、量价和近期涨幅；把依据摊开看，保留缺项与不符合条件的地方。</view></view>
    <view class="search"><input v-model="query" placeholder="股票名称或代码" maxlength="40" :disabled="pending" @input="changeStock" @confirm="run()"/><button class="primary" :disabled="pending" @tap="run()">{{pending?'正在载入…':'检查条件'}}</button></view>
    <view class="parameters"><view class="field"><text>观察范围</text><picker :value="[20,40,60].indexOf(lookback)" :range="['20根日线','40根日线','60根日线']" :disabled="pending" @change="lookback=[20,40,60][Number($event.detail.value)];dirty()"><view class="choice">{{lookback}}根日线 ▾</view></picker></view><view class="field"><text>放量门槛（倍）</text><input v-model="ratio" type="digit" :disabled="pending" @input="dirty"/></view><view class="field"><text>涨幅起点（可选）</text><picker mode="date" :value="start" :start="dates[0]" :end="dates[dates.length-2]" :disabled="pending || !dates.length" @change="start=$event.detail.value;dirty()"><view class="choice">{{start || (dates.length?'选择一段上涨的起点 ▾':'载入行情后选择')}}</view></picker><button v-if="start" class="quiet" :disabled="pending" @tap="start='';dirty()">清除起点</button></view><view class="field"><text>观察截止日（可选）</text><picker mode="date" :value="cutoff" :start="dates[0]" :disabled="pending || !dates.length" @change="cutoff=$event.detail.value;dirty()"><view class="choice">{{cutoff || '最新完成日线 ▾'}}</view></picker><button v-if="cutoff" class="quiet" :disabled="pending" @tap="cutoff='';dirty()">回到最新</button></view></view>
    <view class="meta">日期与参数修改后，点击“检查条件”重新计算。历史截取基于当前来源行情，不是当时保存的事前快照。</view>
    <view v-if="pending" class="pending" role="status">正在读取公开行情，最多等待30秒…<button class="quiet" @tap="cancel">取消</button></view>
    <view v-if="error" class="notice error" role="alert">{{error}}</view>
    <button v-for="c in payload?.candidates || []" :key="c.security_id" class="reference-link" @tap="run(c.security_id)">{{c.name}} · {{c.code}} →</button>
    <template v-if="analysis">
      <view class="meta">{{payload.identity?.name || payload.basis.security_id}} · 不复权日线 · {{payload.basis.source==='tencent_public'?'腾讯公开行情':payload.basis.source}}<br/>完成行情截至 {{analysis.as_of?.slice(0,10) || '无可用日线'}} · 读取于 {{payload.basis.fetched_at}}<br/>本次范围 {{analysis.from_date?.slice(0,10) || '—'}}—{{analysis.as_of?.slice(0,10) || '—'}} · 门槛 {{analysis.parameters.volume_ratio}}倍</view>
      <view v-if="!analysis.available" class="empty">{{analysis.reason}}</view>
      <template v-else><view class="verdict"><text class="eyebrow">三项依次核对</text><view class="subheading">{{analysis.label}}</view><view class="muted">{{analysis.reason}}</view></view>
        <view v-for="(c,i) in analysis.checks" :key="c.id" class="check"><view class="section-bar"><strong>{{i+1}}. {{c.title}}</strong><text :class="['state',c.state]">{{labels[c.state]}}</text></view><view class="subheading">{{c.summary}}</view><view>{{c.detail}}</view>
          <template v-if="c.id==='gain'"><view class="changes"><text v-for="(v,n) in c.changes" :key="n">近{{n}}根收盘变化 {{pct(v as number)}}</text></view><view class="meta">{{c.note}}</view></template>
          <template v-if="c.id==='trend'"><view v-for="p in c.highs" :key="'h'+p.time" class="meta">确认高点 {{p.price.toFixed(2)}} · {{p.time.slice(0,10)}} · {{p.known_at.slice(0,10)}}才可确认</view><view v-for="p in c.lows" :key="'l'+p.time" class="meta">确认低点 {{p.price.toFixed(2)}} · {{p.time.slice(0,10)}} · {{p.known_at.slice(0,10)}}才可确认</view></template>
        </view>
        <button class="reference-link" @tap="detail=!detail">{{detail?'收起':'查看'}}上涨放量日期与逐日量比 →</button><view v-if="detail" class="daily"><view v-if="!analysis.events.length" class="empty">没有达到门槛的上涨放量日。</view><view v-for="e in analysis.events" :key="e.time">{{e.time.slice(0,10)}} · 第{{e.group}}组 · {{e.ratio.toFixed(2)}}倍<text v-if="e.bearish"> · 高于昨收但为阴线，需同时检查回撤量能</text></view><strong>逐日对照</strong><view v-for="d in analysis.checks[1].observations" :key="d.time">{{d.time.slice(0,10)}} · {{d.rising?'收盘较前日上涨':'收盘较前日未上涨'}} · {{d.bearish?'阴线':'非阴线'}} · {{d.ratio.toFixed(2)}}倍</view></view>
        <view class="counter"><strong>同时保留其他解释</strong><view v-for="e in analysis.counter_evidence" :key="e">{{e}}</view></view><button class="reference-link" @tap="discuss">与伙伴核对条件和计划 →</button>
      </template><view class="meta">{{analysis.boundary}}</view>
    </template>
  </view>
</template>
<style scoped>
.selection-tool{display:grid;gap:18px;line-height:1.85;min-width:0}.intro,.check,.verdict,.counter{display:grid;gap:10px}.changes{display:flex;gap:8px;flex-wrap:wrap}.changes text{background:#ebe9de;padding:3px 9px;border-radius:8px;font-size:12px}.search{display:flex;gap:10px}.search input,.field input,.choice{border:1px solid #dddacf;padding:10px 12px;border-radius:10px;background:#fff;box-sizing:border-box;font-size:13px}.search input{flex:1;min-width:0;height:44px}.search button{flex-shrink:0}.parameters{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.field{min-width:0;display:grid;gap:6px;font-size:12px}.field input{height:42px;width:100%}.choice{min-height:42px}.meta{font-size:12px;color:#827e70;overflow-wrap:anywhere}.check,.counter{padding:18px;border:1px solid #e5e1d6;border-radius:16px;background:#fff;font-size:13px}.verdict{padding:18px;border-left:3px solid #8f8764;background:#f4f2e9}.state{font-size:11px;border-radius:20px;padding:3px 8px;white-space:nowrap;background:#eee}.state.pass{color:#446b5b;background:#eef4ef}.state.watch{color:#8a644c;background:#f6eee5}.state.unknown{color:#716a57;background:#f3f0e4}.daily{font-size:12px;display:grid;gap:8px;padding:16px;background:#f8f7f2;border-radius:12px;overflow-wrap:anywhere}.pending{display:flex;justify-content:space-between;align-items:center;font-size:12px}.section-bar{gap:10px;flex-wrap:wrap}@media(max-width:600px){.parameters{grid-template-columns:minmax(0,1fr)}.check,.counter{padding:16px}.search button{font-size:13px;padding:0 12px}}
</style>

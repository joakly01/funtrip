<script setup lang="ts">
import { ref, computed, watch, onUnmounted } from "vue";
import SvgLabel from "./SvgLabel";
import { request } from "../platform/network";
import {
  plot,
  chartScale,
  pointAt,
  drawingCoordinates,
  sameDrawing,
  type Bar,
  type Drawing,
  type ChartPoint,
} from "../domain/chart";
const props = defineProps<{ id: string }>();
const emit = defineEmits(["discuss"]);
const query = ref(""),
  interval = ref("1d"),
  payload = ref<any>(null),
  pending = ref(false),
  error = ref("");
const drawings = ref<Drawing[]>([]),
  version = ref(0),
  saved = ref(""),
  dirty = ref(false),
  saving = ref(false),
  storageReady = ref(false);
const mode = ref("inspect"),
  first = ref<ChartPoint | null>(null),
  size = ref(80),
  offset = ref(0),
  hovered = ref(""),
  exact = ref("");
const candidates = ref<any[]>([]);
let alive = true,
  requestId = 0;
let loadController: AbortController | null = null;
const loadStage = ref('');
function cancelLoad() {
  requestId++;
  loadController?.abort();
  loadController = null;
  pending.value = false;
  loadStage.value = '';
  error.value = '已取消载入，可以修改股票或重试。';
}
const bars = computed<Bar[]>(() => payload.value?.data?.bars || []);
const shown = computed(() =>
  bars.value.slice(
    Math.max(0, bars.value.length - offset.value - size.value),
    bars.value.length - offset.value,
  ),
);
const scale = computed(() =>
  shown.value.length ? chartScale(shown.value, framePrices.value) : null,
);
const chosen = computed(
  () =>
    shown.value.find((b) => b.time === hovered.value) ||
    shown.value[shown.value.length - 1],
);
const maxVolume = computed(() =>
  Math.max(1, ...shown.value.map((b) => b.volume ?? b.volume_raw ?? 0)),
);
const analysisWindow = ref(80), autoChoice = ref('recommended'), autoDetails = ref(false), manualEditing = ref(false);
const analysis = computed(() => payload.value?.chart_analysis?.[String(analysisWindow.value)]);
const autoMode = computed(() => analysis.value?.modes?.find((m: any) => m.id === (autoChoice.value === 'recommended' ? analysis.value.recommended : autoChoice.value)));
const autoDrawings = computed<Drawing[]>(() => props.id === 'chart' && autoMode.value?.available ? autoMode.value.drawings : []);
const framePrices = computed(() => [...autoDrawings.value, ...drawings.value].filter(d => d.kind === 'horizontal' || d.points.every(p => shown.value.some(b => b.time === p.time))).flatMap(d => d.points.map(p => p.price)));
const rendered = computed(() => [
  ...autoDrawings.value.filter(d => !drawings.value.some(saved => sameDrawing(d, saved))).map((d) => ({ drawing: d, automatic: true, points: drawingCoordinates(d, shown.value, framePrices.value) })),
  ...drawings.value.map((d) => ({ drawing: d, automatic: false, points: drawingCoordinates(d, shown.value, framePrices.value) })),
]);
const hidden = computed(() => rendered.value.filter((d) => !d.points).length);
function selectWindow(n: number) {
  analysisWindow.value = n;
  autoChoice.value = 'recommended';
  size.value = n;
  offset.value = 0;
  first.value = null;
}
function keepAutomatic() {
  if (!autoDrawings.value.length || pending.value || saving.value) return;
  if (drawings.value.length + autoDrawings.value.length > 50) { error.value = '标注数量已满，请先移除不需要的标注'; return; }
  for (const d of autoDrawings.value) add(d.kind, d.points.map(p => ({ ...p })));
  drawings.value.slice(-autoDrawings.value.length).forEach((d, i) => {
    d.label = `自动候选·${analysisWindow.value}根·${autoDrawings.value[i].label}`.slice(0,60);
    d.origin = { algorithm_version: analysis.value.algorithm_version, mode: autoMode.value.id,
      lookback: analysisWindow.value as 40 | 80 | 160, as_of: analysis.value.as_of };
  });
  autoChoice.value = 'none';
  save();
}
const activity = computed(() => payload.value?.activity);
const fundChoice = ref('recommended'), fundDetails = ref(false);
const participation = computed(() => activity.value?.participation);
const fundMode = computed(() => participation.value?.modes.find((m: any) => m.id === (fundChoice.value === 'recommended' ? participation.value.recommended : fundChoice.value)));
const hints: Record<string, string> = {
  inspect: "点击或移动到K线，查看当天价格。",
  horizontal: "点一次设定关键价位，也可填写精确价格。",
  trend: "依次点击两个位置，连接成趋势线。",
  zone: "依次点击区间的两个对角位置。",
};
const name = computed(
  () => payload.value?.identity?.name || payload.value?.data?.security_id || "",
);
const date = (t: string) =>
  t?.slice(0, 10) + (interval.value === "1d" ? "" : " " + t?.slice(11, 16));
const price = (n: number) => (Number.isFinite(n) ? n.toFixed(2) : "—");
const pct = (n: any) =>
  typeof n === "number" ? (n * 100).toFixed(1) + "%" : "暂无";
const metric = (n: any) => (typeof n === "number" ? n.toFixed(2) : "暂无");
const cmfPoints = computed(() => {
  const series = (activity.value?.series || [])
    .filter((b: any) => b.cmf_20 !== null)
    .slice(-80);
  return series
    .map(
      (b: any, i: number) =>
        `${16 + (i * 698) / Math.max(1, series.length - 1)},${70 - b.cmf_20 * 54}`,
    )
    .join(" ");
});
async function mayDiscard() {
  if (!dirty.value) return true;
  const r = await uni.showModal({
    title: "标注尚未保存",
    content: "是否放弃这次修改？保存后可以跨设备查看。",
    confirmText: "放弃修改",
    cancelText: "继续编辑",
  });
  return r.confirm;
}
async function closeAllowed() {
  if (saving.value) {
    error.value = "正在保存标注，请稍后关闭";
    return false;
  }
  return mayDiscard();
}
defineExpose({ closeAllowed });
async function load(securityId = "") {
  if (pending.value || saving.value || !(await mayDiscard())) return;
  if (!securityId && !query.value.trim()) {
    error.value = "请输入股票名称或代码";
    return;
  }
  const token = ++requestId;
  // #ifdef H5
  loadController = new AbortController();
  // #endif
  pending.value = true;
  loadStage.value = '正在查询公开行情，最多等待30秒…';
  error.value = "";
  candidates.value = [];
  try {
    const value = await request("/api/tools/study", {
      ...(securityId
        ? { security_id: securityId }
        : { query: query.value.trim() }),
      interval: interval.value,
    }, 'GET', { timeoutMs: 30000, signal: loadController?.signal });
    if (!alive || token !== requestId) return;
    if (!value.data) {
      candidates.value = value.candidates || [];
      error.value = candidates.value.length
        ? "请选择要分析的股票"
        : "没有找到股票，请检查名称或代码";
      return;
    }
    payload.value = value;
    offset.value = 0;
    hovered.value = "";
    first.value = null;
    mode.value = "inspect";
    autoChoice.value = "recommended";
    fundChoice.value = 'recommended';
    manualEditing.value = false;
    size.value = analysisWindow.value;
    drawings.value = [];
    dirty.value = false;
    version.value = 0;
    saved.value = "";
    storageReady.value = false;
    loadStage.value = '行情已返回，正在恢复标注…';
    try {
      const marks = await request("/api/tools/drawings", {
        security_id: value.data.security_id,
        interval: value.data.interval,
      }, 'GET', { timeoutMs: 8000, signal: loadController?.signal });
      if (!alive || token !== requestId) return;
      drawings.value = marks.drawings;
      version.value = marks.version;
      saved.value = marks.updated_at || "";
      storageReady.value = true;
    } catch (e: any) {
      error.value =
        "行情已载入，标注读取失败。请重新载入后再保存，避免覆盖旧标注。 " +
        e.message;
    }
  } catch (e: any) {
    if (alive && token === requestId) error.value = e.message;
  } finally {
    if (alive && token === requestId) { pending.value = false; loadStage.value = ''; loadController = null; }
  }
}
async function switchInterval(next: string) {
  if (next === interval.value || pending.value || saving.value) return;
  if (!(await mayDiscard())) return;
  dirty.value = false;
  interval.value = next;
  payload.value = null;
  first.value = null;
  drawings.value = [];
  if (query.value.trim()) await load();
}
function add(kind: Drawing["kind"], points: ChartPoint[]) {
  if (drawings.value.length >= 50) {
    error.value = "每张图最多保存50处标注，请先移除不需要的标注";
    return;
  }
  drawings.value.push({
    id: "d_" + Date.now() + "_" + Math.random().toString(36).slice(2, 8),
    kind,
    points,
    label: "",
  });
  dirty.value = true;
}
function tap(e: MouseEvent) {
  // #ifdef H5
  if (pending.value || saving.value || !scale.value) return;
  const box = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
  const point = pointAt(
    shown.value,
    ((e.clientX - box.left) / box.width) * plot.width,
    ((e.clientY - box.top) / box.height) * plot.height,
    framePrices.value,
  );
  if (!point) return;
  hovered.value = point.time;
  if (!manualEditing.value || mode.value === "inspect") return;
  if (mode.value === "horizontal") add("horizontal", [point]);
  else if (!first.value) first.value = point;
  else {
    add(mode.value as Drawing["kind"], [first.value, point]);
    first.value = null;
  }
  // #endif
}
function hover(e: PointerEvent) {
  // #ifdef H5
  if (e.pointerType === "touch" || !scale.value) return;
  const box = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
  const p = pointAt(
    shown.value,
    ((e.clientX - box.left) / box.width) * plot.width,
    ((e.clientY - box.top) / box.height) * plot.height,
    framePrices.value,
  );
  if (p) hovered.value = p.time;
  // #endif
}
function addExact() {
  const p = Number(exact.value);
  if (!Number.isFinite(p) || p <= 0) {
    error.value = "请填写大于零的价格";
    return;
  }
  add("horizontal", [
    { time: bars.value[bars.value.length - 1].time, price: p },
  ]);
  exact.value = "";
}
async function save() {
  if (saving.value || pending.value || !storageReady.value || !dirty.value)
    return;
  saving.value = true;
  error.value = "";
  first.value = null;
  try {
    const value = await request(
      "/api/tools/drawings",
      {
        security_id: payload.value.data.security_id,
        interval: payload.value.data.interval,
        expected_version: version.value,
        drawings: drawings.value,
      },
      "PUT",
    );
    if (alive) {
      version.value = value.version;
      saved.value = value.updated_at;
      dirty.value = false;
    }
  } catch (e: any) {
    if (alive) error.value = e.message;
  } finally {
    if (alive) saving.value = false;
  }
}
async function discuss() {
  if (dirty.value) {
    await save();
    if (dirty.value) return;
  }
  const d = payload.value.data,
    a = activity.value;
  emit(
    "discuss",
    `请帮我审阅以下量价观察，分别说明依据、其他解释及待验证条件。\n证券：${name.value}；周期：${d.interval}；不复权；行情截至：${d.data_as_of}；来源：${d.source_url}\n近20根价格变化：${pct(a.price_change_20)}；最新相对量：${metric(a.volume_ratio_20)}；CMF20：${metric(a.cmf_20)}；上涨K线量占比：${pct(a.up_volume_share_20)}。这些量价指标不等于实际资金净流入。\n资金参与观察：${JSON.stringify({ ...participation.value, selected_mode: fundMode.value?.id })}\n自动结构候选：${JSON.stringify({algorithm: analysis.value?.algorithm_version, window: analysisWindow.value, recommended: analysis.value?.recommended, selected: autoChoice.value === "none" ? "none" : autoMode.value?.id, reason: analysis.value?.reason, evidence: autoMode.value?.evidence || analysis.value?.modes?.find((m: any) => m.id === analysis.value.recommended)?.evidence, drawings: autoDrawings.value})}\n我的标注：${JSON.stringify(drawings.value)}\n需要结合更大周期及反例判断，不把手绘区间当成已确认的缠论中枢。`,
  );
}
watch([mode, size, offset], () => {
  first.value = null;
});
onUnmounted(() => {
  alive = false;
  requestId++;
  loadController?.abort();
});
</script>
<template>
  <view class="chart-tool">
    <view class="muted">{{
      id === "chart"
        ? "载入行情，自动选择观察模式并画线；再补充自己的判断。"
        : "自动观察参与变化和量价方向，区分依据与对主力行为的猜测。"
    }}</view>
    <view class="chart-search"
      ><input
        v-model="query"
        placeholder="股票名称或代码，如 600000"
        maxlength="40"
        :disabled="pending || saving"
        @confirm="load()"
      /><button :disabled="pending || saving" @tap="load()">
        {{ pending ? "正在载入…" : "载入行情" }}
      </button></view
    >
    <view class="chart-controls"
      ><button
        v-for="t in [
          ['1d', '日线'],
          ['60m', '60分钟'],
          ['30m', '30分钟'],
          ['15m', '15分钟'],
          ['5m', '5分钟'],
        ]"
        :key="t[0]"
        class="quiet"
        :class="{ active: interval === t[0] }"
        :disabled="pending || saving"
        @tap="switchInterval(t[0])"
      >
        {{ t[1] }}
      </button></view
    >
    <view v-if="error" class="error" role="alert">{{ error }}</view>
    <view v-if="pending" class="chart-loading" role="status">
      <text>{{ loadStage }}</text><button class="quiet" @tap="cancelLoad">取消载入</button>
    </view>
    <button v-if="error && !pending && query.trim()" class="reference-link" @tap="load()">重新载入行情 →</button>
    <button
      v-for="c in candidates"
      :key="c.security_id"
      class="reference-link"
      @tap="load(c.security_id)"
    >
      {{ c.name }} · {{ c.code }} →
    </button>
    <view v-if="!payload && !pending" class="chart-welcome"
      ><text class="eyebrow">从一张图开始</text
      ><view class="subheading">{{
        id === "chart"
          ? "自动推荐 · 趋势通道 · 区间与价位"
          : "相对成交量 · 量价配合 · CMF"
      }}</view
      ><view class="muted"
        >输入股票后载入真实行情。使用已完成K线，不复权；除权附近需单独核对价格变化。</view
      ></view
    >
    <template v-if="payload && scale">
      <view class="section-bar"
        ><view class="section-title">{{ name }}</view
        ><text class="muted">不复权 · {{ bars.length }} 根完整K线</text></view
      >
      <view class="chart-meta"
        >截至 {{ date(payload.data.data_as_of) }} ·
        {{
          payload.data.source === "tencent_public"
            ? "腾讯公开行情"
            : payload.data.source
        }}
        · 排除 {{ payload.data.excluded_unfinished }} 根未完成K线</view
      >
      <view v-if="id === 'activity' && participation" class="auto-chart-card">
        <view class="auto-chart-heading"><text class="eyebrow">自动观察 · 资金参与</text><text class="chart-meta">近5根对照此前20根</text></view>
        <view class="auto-chart-title">{{ participation.participation }}</view>
        <view class="fund-verdicts"><view><text>参与活跃程度</text><strong>{{ participation.degree }}</strong></view><view><text>量价方向</text><strong>{{ participation.bias }}</strong></view></view>
        <view class="auto-chart-reason">{{ participation.reason }}</view>
        <view class="chart-meta">主力／庄家身份：{{ participation.identity }}</view>
        <view class="chart-controls">
          <button class="quiet" :class="{active:fundChoice==='recommended'}" @tap="fundChoice='recommended'">跟随推荐</button>
          <button v-for="m in participation.modes" :key="m.id" class="quiet" :class="{active:fundChoice===m.id}" :disabled="!participation.available" @tap="fundChoice=m.id">{{ m.title }}</button>
        </view>
        <view v-if="fundMode && participation.available" class="fund-selected">
          <strong>{{ fundChoice === 'recommended' ? '推荐模式' : '查看模式' }} · {{ fundMode.title }}{{ !fundMode.matched ? ' · 本次未触发' : '' }}</strong>
          <view>{{ !fundMode.matched ? "该模式需要满足：" : "" }}{{ fundMode.reason }}</view><view v-for="c in fundMode.checks" :key="c">下一步：{{ c }}</view>
        </view>
        <view v-else class="chart-meta" v-for="c in participation.next_checks" :key="c">{{ c }}</view>
        <button class="reference-link" @tap="fundDetails=!fundDetails">{{ fundDetails ? '收起依据' : '查看依据与其他解释 →' }}</button>
        <view v-if="fundDetails" class="auto-chart-evidence">
          <view v-for="e in participation.evidence" :key="e">{{ e }}</view>
          <view v-for="e in participation.alternatives" :key="e">其他解释：{{ e }}</view>
          <view>程度按近5根均量相对此前20根划分：≥1.8倍为较高，≥1.2倍为有所增强，低于0.8倍为偏低，其余为常态；持续放量还要求近5根至少3根≥对照均量1.2倍。</view>
          <view>方向仅在CMF≥0.10、近5根上涨且上涨量占比≥55%时称偏买方；相反三项同向时称偏卖方，其余保留方向不明确。</view>
          <view>算法 {{ participation.algorithm_version }} · {{ participation.boundary }} · 阈值为观察规则，尚未验证预测胜率。</view>
        </view>
      </view>
      <view v-if="id === 'activity'" class="activity-metrics"
        ><view
          ><text>最新相对量</text
          ><strong
            >{{ metric(activity.volume_ratio_20)
            }}{{ activity.volume_ratio_20 != null ? " 倍" : "" }}</strong
          ><text>相对此前20根均量</text></view
        ><view
          ><text>量加权收盘位置</text
          ><strong>{{ metric(activity.cmf_20) }}</strong
          ><text>CMF · 近20根</text></view
        ><view
          ><text>上涨K线量占比</text
          ><strong>{{ pct(activity.up_volume_share_20) }}</strong
          ><text>近20根；非主动买入占比</text></view
        ><view
          ><text>近20根价格变化</text
          ><strong>{{ pct(activity.price_change_20) }}</strong
          ><text>与量价线索一起对照</text></view
        ></view
      >
      <view v-if="id === 'chart' && analysis" class="auto-chart-card">
        <view class="auto-chart-heading"><text class="eyebrow">自动识图 · 自动画线</text><text class="chart-meta">{{ analysis.count }}根 · 当前周期</text></view>
        <view class="chart-controls">
          <button v-for="n in [40,80,160]" :key="n" class="quiet" :class="{active:analysisWindow===n}" :disabled="pending || saving" @tap="selectWindow(n)">观察{{ n }}根</button>
        </view>
        <view class="auto-chart-title">{{ analysis.recommended ? '推荐 · ' + analysis.modes.find((m: any) => m.id === analysis.recommended)?.title : '暂不推荐画线模式' }}</view>
        <view class="auto-chart-reason">{{ analysis.reason }}</view>
        <view class="chart-controls">
          <button class="quiet" :class="{active:autoChoice==='recommended'}" @tap="autoChoice='recommended'; size=analysisWindow; offset=0">跟随推荐</button>
          <button v-for="m in analysis.modes" :key="m.id" class="quiet" :class="{active:autoChoice===m.id}" :disabled="!m.available || pending" @tap="autoChoice=m.id; size=analysisWindow; offset=0">{{ m.title }}</button>
          <button class="quiet" :class="{active:autoChoice==='none'}" @tap="autoChoice='none'">隐藏自动线</button>
        </view>
        <view v-if="autoMode?.available && autoChoice !== 'recommended'" class="chart-meta">已选择{{ autoMode.title }} · {{ autoMode.reason }}</view>
        <view class="auto-chart-actions">
          <button class="quiet" @tap="autoDetails=!autoDetails">{{ autoDetails ? '收起依据' : '查看依据与其他模式' }}</button>
          <button :disabled="!autoDrawings.length || !storageReady || pending || saving" @tap="keepAutomatic">保存为我的标注</button>
        </view>
        <view v-if="autoDetails" class="auto-chart-evidence">
          <view v-for="m in analysis.modes" :key="m.id"><strong>{{ m.title }} · {{ m.available ? '可供观察' : '依据不足' }}</strong><view>{{ m.reason }}</view><view v-for="e in m.evidence" :key="e">{{ e }}</view></view>
          <view>算法 {{ analysis.algorithm_version }} · {{ analysis.boundary }}</view>
          <view>推荐使用本应用的公开启发式阈值，尚未经过收益回测或胜率验证。改变周期或范围可能改变结果。</view>
        </view>
        <view class="chart-meta">蓝色为自动候选，金色为我的标注。自动线不外推未来；“保存”保留当前结果，下次载入仍会重新分析。</view>
      </view>
      <template v-if="id === 'chart'">
        <button class="reference-link" @tap="manualEditing=!manualEditing; mode='inspect'; first=null">{{ manualEditing ? '收起手动补充' : '手动补充标注 →' }}</button>
        <template v-if="manualEditing">
        <view class="chart-controls"
          ><button
            v-for="m in [
              ['inspect', '查看'],
              ['horizontal', '水平线'],
              ['trend', '趋势线'],
              ['zone', '价格区间'],
            ]"
            :key="m[0]"
            class="quiet"
            :class="{ active: mode === m[0] }"
            :disabled="pending || saving"
            @tap="mode = m[0]"
          >
            {{ m[1] }}
          </button></view
        >
        <view class="chart-instruction"
          >{{ first ? "已设起点，请点击第二个位置。" : hints[mode]
          }}<button v-if="first" class="quiet" @tap="first = null">
            取消起点
          </button></view
        >
        <view v-if="mode === 'horizontal'" class="chart-search"
          ><input
            v-model="exact"
            type="digit"
            placeholder="精确价位（元）"
            :disabled="saving || pending"
          /><button class="quiet" :disabled="saving || pending" @tap="addExact">
            添加水平线
          </button></view
        >
        </template>
      </template>
      <view v-if="chosen" class="chart-ohlc"
        ><text>{{ date(chosen.time) }}</text
        ><text>开 {{ price(chosen.open) }}</text
        ><text>高 {{ price(chosen.high) }}</text
        ><text>低 {{ price(chosen.low) }}</text
        ><text>收 {{ price(chosen.close) }}</text></view
      >
      <!-- #ifdef H5 -->
      <svg
        class="market-chart"
        viewBox="0 0 780 420"
        preserveAspectRatio="none"
        role="img"
        aria-label="价格K线、相对成交量与手绘标注；点击可查看或画线"
        @click="tap"
        @pointermove="hover"
      >
        <defs>
          <clipPath id="price-area">
            <rect x="16" y="24" width="698" height="286" />
          </clipPath>
        </defs>
        <g v-for="i in [0, 1, 2, 3, 4]" :key="'grid' + i">
          <line
            x1="16"
            x2="714"
            :y1="24 + i * 71.5"
            :y2="24 + i * 71.5"
            stroke="#e6e4dc"
            stroke-dasharray="3 4"
          />
          <SvgLabel x="724" :y="29 + i * 71.5" class="chart-axis">
            {{ price(scale.max - ((scale.max - scale.min) * i) / 4) }}
          </SvgLabel>
        </g>
        <g clip-path="url(#price-area)">
          <g
            v-for="(b, i) in shown"
            :key="b.time"
            :stroke="b.close >= b.open ? '#b75b4e' : '#438576'"
          >
            <line
              :x1="scale.x(i)"
              :x2="scale.x(i)"
              :y1="scale.y(b.high)"
              :y2="scale.y(b.low)"
            />
            <rect
              :x="scale.x(i) - Math.max(1, (698 / shown.length) * 0.3)"
              :y="Math.min(scale.y(b.open), scale.y(b.close))"
              :width="Math.max(2, (698 / shown.length) * 0.6)"
              :height="
                Math.max(1, Math.abs(scale.y(b.open) - scale.y(b.close)))
              "
              :fill="b.close >= b.open ? '#b75b4e' : '#438576'"
            />
          </g>
          <template v-for="r in rendered" :key="r.drawing.id">
            <g v-if="r.points" :stroke="r.automatic ? '#4f7f9c' : '#897345'" stroke-width="2">
              <line
                v-if="r.drawing.kind === 'horizontal'"
                x1="16"
                x2="714"
                :y1="r.points[0].y"
                :y2="r.points[0].y"
                stroke-dasharray="6 4"
              />
              <line
                v-else-if="r.drawing.kind === 'trend'"
                :x1="r.points[0].x"
                :y1="r.points[0].y"
                :x2="r.points[1].x"
                :y2="r.points[1].y"
              />
              <rect
                v-else
                :x="Math.min(r.points[0].x, r.points[1].x)"
                :y="Math.min(r.points[0].y, r.points[1].y)"
                :width="Math.max(1, Math.abs(r.points[0].x - r.points[1].x))"
                :height="Math.max(1, Math.abs(r.points[0].y - r.points[1].y))"
                :fill="r.automatic ? '#80a5b7' : '#ab9364'"
                fill-opacity=".14"
              />
              <SvgLabel
                :x="r.drawing.kind === 'horizontal' ? 22 : r.points[0].x + 4"
                :y="r.points[0].y - 5"
                class="chart-axis"
                stroke="none"
                :fill="r.automatic ? '#4f7f9c' : '#897345'"
              >
                {{
                  r.drawing.label ||
                  (r.drawing.kind === "horizontal"
                    ? price(r.drawing.points[0].price)
                    : "")
                }}
              </SvgLabel>
            </g>
          </template>
          <circle
            v-if="first"
            :cx="scale.x(shown.findIndex((b) => b.time === first?.time))"
            :cy="scale.y(first.price)"
            r="5"
            fill="#897345"
          />
        </g>
        <line
          v-if="hovered && shown.some((b) => b.time === hovered)"
          :x1="scale.x(shown.findIndex((b) => b.time === hovered))"
          :x2="scale.x(shown.findIndex((b) => b.time === hovered))"
          y1="24"
          y2="390"
          stroke="#9b9c91"
          stroke-dasharray="3 4"
        />
        <SvgLabel x="16" y="334" class="chart-axis">
          相对成交量 · 同来源原始量
        </SvgLabel>
        <template v-if="activity.volume_available">
          <rect
            v-for="(b, i) in shown"
            :key="'vol' + b.time"
            :x="scale.x(i) - Math.max(1, (698 / shown.length) * 0.3)"
            :y="390 - ((b.volume ?? b.volume_raw ?? 0) / maxVolume) * 46"
            :height="((b.volume ?? b.volume_raw ?? 0) / maxVolume) * 46"
            :width="Math.max(2, (698 / shown.length) * 0.6)"
            :fill="b.close >= b.open ? '#b75b4e' : '#438576'"
            opacity=".6"
          />
        </template>
        <SvgLabel x="16" y="412" class="chart-axis">
          {{ date(shown[0].time) }}
        </SvgLabel>
        <SvgLabel x="714" y="412" text-anchor="end" class="chart-axis">
          {{ date(shown[shown.length - 1].time) }}
        </SvgLabel>
      </svg>
      <!-- #endif -->
      <!-- #ifdef MP-WEIXIN --><view class="empty"
        >交互画线目前请在手机网页或电脑网页使用。</view
      ><!-- #endif -->
      <view class="chart-controls"
        ><button
          class="quiet"
          :disabled="pending || offset + size >= bars.length"
          @tap="offset = Math.min(bars.length - size, offset + 20)"
        >
          ← 更早</button
        ><button
          v-for="n in [40, 80, 160, 240]"
          :key="n"
          class="quiet"
          :class="{ active: size === n }"
          @tap="
            size = n;
            offset = 0;
          "
        >
          {{ n }}根</button
        ><button
          class="quiet"
          :disabled="pending || offset === 0"
          @tap="offset = Math.max(0, offset - 20)"
        >
          更新 →
        </button></view
      >
      <template v-if="id === 'chart'">
        <view v-if="hidden" class="muted"
          >{{ hidden }} 处标注端点在当前范围外，扩大显示范围可查看。</view
        >
        <view v-for="(d, i) in drawings" :key="d.id" class="drawing-row"
          ><text
            >{{
              d.kind === "horizontal"
                ? "水平线"
                : d.kind === "trend"
                  ? "趋势线"
                  : "区间"
            }}
            · {{ d.points.map((p) => price(p.price)).join(" → ") }}<text v-if="d.origin" class="chart-meta"> · 自动候选/{{ d.origin.lookback }}根 · {{ date(d.origin.as_of) }}</text></text
          ><input
            v-model="d.label"
            placeholder="备注，如等待回踩"
            maxlength="60"
            :disabled="saving || pending"
            @input="dirty = true"
          /><button
            class="quiet"
            :disabled="saving || pending"
            @tap="
              drawings.splice(i, 1);
              dirty = true;
            "
          >
            移除
          </button></view
        >
        <view class="section-bar"
          ><button
            :disabled="!dirty || saving || pending || !storageReady"
            @tap="save"
          >
            {{ saving ? "正在保存…" : "保存标注" }}</button
          ><button
            class="quiet"
            :disabled="!drawings.length || saving || pending"
            @tap="
              drawings.pop();
              dirty = true;
            "
          >
            撤销最后一笔
          </button></view
        >
        <view class="chart-meta"
          >{{
            dirty
              ? "有未保存的修改"
              : saved
                ? "已保存 · " + date(saved)
                : "标注仅在点击保存后上传"
          }}
          · 手绘区间不自动认定为缠论中枢。</view
        >
      </template>
      <template v-else>
        <view class="subheading">量价观察</view
        ><view
          v-for="s in activity.observations"
          :key="s"
          class="activity-observation"
          >{{ s }}</view
        >
        <!-- #ifdef H5 -->
        <svg
          v-if="activity.volume_available"
          class="cmf-chart"
          viewBox="0 0 780 140"
          preserveAspectRatio="none"
          role="img"
          aria-label="近80根K线的CMF20走势，零轴上方代表量加权收盘位置偏高"
        >
          <line
            x1="16"
            x2="714"
            y1="70"
            y2="70"
            stroke="#aaa"
            stroke-dasharray="4 4"
          />
          <polyline
            :points="cmfPoints"
            fill="none"
            stroke="#897345"
            stroke-width="2"
          />
          <SvgLabel x="16" y="16" class="chart-axis">
            CMF20 · 量加权收盘位置
          </SvgLabel>
          <SvgLabel x="724" y="75" class="chart-axis">0</SvgLabel>
        </svg>
        <!-- #endif -->
        <view class="activity-method"
          ><view class="subheading">怎么看这些结果</view
          ><view>相对量：最新完整K线成交量 ÷ 此前20根均量。</view
          ><view
            >CMF：近20根的“收盘位置 × 成交量”之和 ÷ 总成交量。收盘位置为
            (2×收盘−最高−最低) ÷ (最高−最低)；最高等于最低时记为0。</view
          ><view
            >上涨量占比：收盘高于前一根收盘的K线成交量，占近20根总量的比例。</view
          ><view
            >量单位未标准化，以上仅在同一来源、同一周期内比较相对量，不输出股数或净流入金额。跳空、除权和数据缺口可能影响解释。</view
          ><view>{{ activity.boundary }}</view
          ><text selectable class="chart-meta"
            >公式参考：StockCharts ChartSchool · Chaikin Money Flow</text
          ></view
        >
      </template>
      <view class="chart-provenance"
        ><text selectable>行情来源：{{ payload.data.source_url }}</text
        ><text>获取时间：{{ payload.data.fetched_at }}</text
        ><text
          >公开数据可能延迟，未独立核验交易日历及序列完整性。资金净流入数据尚未接入。</text
        ></view
      >
      <button
        class="reference-link"
        :disabled="saving || pending"
        @tap="discuss"
      >
        带着这张图与伙伴讨论 →
      </button>
    </template>
  </view>
</template>

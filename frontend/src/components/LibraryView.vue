<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted, watch } from "vue";
import { kinds } from "../domain/records";
import { shelves, topics } from "../domain/knowledge";
import { request } from "../platform/network";
import RecordCard from "./RecordCard.vue";
defineEmits(["open", "classic", "discuss"]);
const props = defineProps<{ revision?: number }>();
const query = ref(""),
  shelf = ref("outcomes"),
  topic = ref(""),
  kind = ref(""),
  state = ref(""),
  starred = ref(false),
  page = ref(1),
  result = ref<any>({ records: [], total: 0, shelves: {}, kinds: {} }),
  classics = ref<any[]>([]),
  loading = ref(false),
  error = ref("");
const kindKeys = ref<string[]>([]);
const kindIndex = computed(() =>
  kind.value ? kindKeys.value.indexOf(kind.value) + 1 : 0,
);
const topicKeys = Object.keys(topics);
const topicIndex = computed(() =>
  topic.value ? topicKeys.indexOf(topic.value) + 1 : 0,
);
const selectedShelf = computed(() =>
  shelves.find((s) => s.key === shelf.value),
);
let seq = 0,
  timer: ReturnType<typeof setTimeout> | undefined;
async function load(reset = true) {
  clearTimeout(timer);
  if (reset) page.value = 1;
  const n = ++seq;
  loading.value = true;
  error.value = "";
  try {
    if (shelf.value === "classics") {
      const x = await request(
        "/api/classics?query=" + encodeURIComponent(query.value),
      );
      if (n === seq) classics.value = x.entries;
    } else {
      const x = await request("/api/knowledge", {
        query: query.value,
        shelf: shelf.value,
        topic: topic.value,
        kind: kind.value,
        state: state.value,
        starred: starred.value,
        page: page.value,
        size: 12,
      });
      if (n === seq) {
        result.value = x;
        kindKeys.value = [
          ...new Set([
            ...Object.keys(x.kinds || {}),
            ...(kind.value ? [kind.value] : []),
          ]),
        ];
      }
    }
  } catch (e: any) {
    if (n === seq) error.value = e.message;
  } finally {
    if (n === seq) loading.value = false;
  }
}
function select(key: string) {
  shelf.value = key;
  kind.value = "";
  load();
}
function search() {
  ++seq;
  timer && clearTimeout(timer);
  timer = setTimeout(() => load(), 350);
}
function reset() {
  query.value = "";
  topic.value = "";
  kind.value = "";
  state.value = "";
  starred.value = false;
  load();
}
onMounted(() => load());
watch(
  () => props.revision,
  () => load(),
);
onUnmounted(() => {
  ++seq;
  clearTimeout(timer);
});
</script>
<template>
  <view class="page-content knowledge-page">
    <view class="page-heading editorial-heading">
      <view
        ><text class="eyebrow">让积累成为可用的知识</text
        ><view class="page-title">知识库</view>
        <view class="muted">先读成果，需要时再追溯它的来处。</view></view
      >
      <button
        class="quiet"
        @tap="
          $emit(
            'discuss',
            '请帮我把已有笔记整理成一份知识成果，保留核心结论、适用边界和真实来源。先列出适合整理的内容，让我选择。',
          )
        "
      >
        整理一份成果 ↗
      </button>
    </view>
    <view class="shelf-nav">
      <button
        v-for="s in shelves"
        :key="s.key"
        :class="['shelf-item', { selected: shelf === s.key }]"
        @tap="select(s.key)"
      >
        <text>{{ s.title }}</text
        ><text class="shelf-count" v-if="s.key !== 'classics'">{{
          result.shelves[s.key] || 0
        }}</text
        ><text v-else class="shelf-count">阅读</text>
      </button>
    </view>
    <view class="shelf-caption"
      ><view>{{ selectedShelf?.description }}</view
      ><text v-if="shelf === 'outcomes'" class="muted"
        >整理后的输出，验证状态另行保留。</text
      ></view
    >
    <view class="search-row"
      ><input
        v-model="query"
        placeholder="搜索观点、主题、股票或原话"
        confirm-type="search"
        @input="search"
        @confirm="load()"
      /><button class="primary" @tap="load()">搜索</button></view
    >
    <view v-if="shelf !== 'classics'" class="library-controls">
      <view class="filter-inner">
        <picker
          :value="topicIndex"
          :range="['全部主题', ...topicKeys.map((k) => topics[k])]"
          @change="
            topic =
              $event.detail.value == 0
                ? ''
                : topicKeys[$event.detail.value - 1];
            load();
          "
          ><view class="chip">{{ topics[topic] || "全部主题" }} ▾</view></picker
        >
        <picker
          :value="kindIndex"
          :range="['全部类型', ...kindKeys.map((k) => kinds[k] || k)]"
          @change="
            kind =
              $event.detail.value == 0 ? '' : kindKeys[$event.detail.value - 1];
            load();
          "
          ><view class="chip">{{ kinds[kind] || "全部类型" }} ▾</view></picker
        >
        <button
          :class="['chip', { selected: starred }]"
          @tap="
            starred = !starred;
            load();
          "
        >
          只看收藏
        </button>
        <button
          :class="['chip', { selected: state }]"
          @tap="
            state = state ? '' : 'needs_review';
            load();
          "
        >
          待回查
        </button> </view
      ><text class="muted">{{ result.total }} 份</text>
    </view>
    <view v-if="error" class="notice error"
      >{{ error
      }}<button class="quiet" @tap="load(false)">重新加载</button></view
    >
    <view v-else-if="loading" class="empty">正在找回资料…</view>
    <template v-else-if="shelf === 'classics'">
      <view class="card-grid"
        ><view
          v-for="c in classics"
          :key="c.id"
          class="record-card"
          @tap="$emit('classic', c)"
        >
          <text class="eyebrow">经典观点 · {{ c.id }}</text
          ><view class="card-title">{{ c.title }}</view>
          <view class="excerpt">{{
            c.summary ||
            (c.tags || []).join(" · ") ||
            "阅读原意、理解与适用边界"
          }}</view>
          <view class="card-footer">打开阅读 ↗</view></view
        ></view
      >
      <view v-if="!classics.length" class="empty"
        >没有找到匹配的经典，换个关键词试试。<button class="quiet" @tap="reset">
          清除筛选
        </button></view
      >
    </template>
    <template v-else>
      <view class="card-grid"
        ><RecordCard
          v-for="r in result.records"
          :key="r.id"
          :row="r"
          @open="$emit('open', $event)"
      /></view>
      <view v-if="!result.records.length" class="empty empty-guide">
        <view class="section-title">{{
          query || topic || kind || state || starred
            ? "没有匹配的资料"
            : shelf === "outcomes"
              ? "留一个位置，给下一份成果"
              : "这里还没有资料"
        }}</view>
        <view class="muted">{{
          shelf === "outcomes"
            ? "整理研究结论、学习总结或复盘经验；也可以打开待打磨内容，在详情里调整归档。"
            : "通过对话收集真实想法与资料，再逐步整理。"
        }}</view>
        <button
          v-if="query || topic || kind || state || starred"
          class="quiet"
          @tap="reset"
        >
          清除筛选
        </button>
        <button
          v-else-if="shelf === 'outcomes'"
          class="quiet"
          @tap="select('workbench')"
        >
          看看待打磨内容 ↗
        </button>
      </view>
      <view v-if="result.total > 12" class="pagination">
        <button
          class="quiet"
          :disabled="loading || page === 1"
          @tap="
            page--;
            load(false);
          "
        >
          上一页
        </button>
        <text
          >{{ page }} / {{ Math.max(1, Math.ceil(result.total / 12)) }}</text
        >
        <button
          class="quiet"
          :disabled="loading || page * 12 >= result.total"
          @tap="
            page++;
            load(false);
          "
        >
          下一页
        </button>
      </view>
    </template>
  </view>
</template>

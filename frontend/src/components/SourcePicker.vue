<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from "vue";
import { request } from "../platform/network";
import { brief, title } from "../domain/records";
const emit = defineEmits(["select", "close", "open"]);
const props = withDefaults(
  defineProps<{
    excluded?: string[];
    limit?: number;
    pending?: boolean;
    errorMessage?: string;
  }>(),
  { excluded: () => [], limit: 40, pending: false },
);
const query = ref(""),
  data = ref<any>(null),
  page = ref(1),
  error = ref(""),
  selected = ref<string[]>([]),
  busy = ref(false);
let seq = 0,
  timer: any;
async function load() {
  const n = ++seq;
  busy.value = true;
  error.value = "";
  try {
    const r = await request("/api/knowledge", {
      shelf: "all",
      query: query.value,
      page: page.value,
      size: 12,
    });
    if (n === seq) data.value = r;
  } catch (e: any) {
    if (n === seq) error.value = e.message;
  } finally {
    if (n === seq) busy.value = false;
  }
}
watch(query, () => {
  seq++;
  clearTimeout(timer);
  page.value = 1;
  timer = setTimeout(load, 300);
});
onMounted(load);
onUnmounted(() => {
  seq++;
  clearTimeout(timer);
});
function toggle(id: string) {
  if (props.pending || props.excluded.includes(id)) return;
  if (!selected.value.includes(id) && selected.value.length >= props.limit) {
    error.value = `最多还可关联 ${props.limit} 份资料，请先调整选择。`;
    return;
  }
  error.value = "";
  selected.value = selected.value.includes(id)
    ? selected.value.filter((x) => x !== id)
    : [...selected.value, id];
}
</script>
<template>
  <view class="growth-source-picker"
    ><view class="section-bar"
      ><view class="section-title">找回已有资料</view
      ><button class="quiet" :disabled="pending" @tap="$emit('close')">
        关闭
      </button></view
    ><input v-model="query" placeholder="搜索原话、知识或经历" /><view
      v-if="error || errorMessage"
      class="notice error"
      >{{ error || errorMessage
      }}<button class="quiet" @tap="load">重试</button></view
    ><view v-if="busy" class="muted">正在查找…</view
    ><view
      v-for="r in data?.records || []"
      :key="r.id"
      class="growth-source-row"
      ><button
        class="growth-source-title"
        :disabled="pending || excluded.includes(r.id)"
        @tap="toggle(r.id)"
      >
        <text>{{
          excluded.includes(r.id)
            ? "已关联"
            : selected.includes(r.id)
              ? "☑"
              : "☐"
        }}</text
        ><view
          ><view>{{ title(r) }}</view
          ><text class="muted">{{ brief(r) }}</text></view
        ></button
      ><button class="quiet" @tap="$emit('open', r.id)">阅读</button></view
    ><view v-if="data && !data.total && !busy" class="empty"
      >没有找到相关资料，可以换一个词。</view
    ><view class="growth-actions"
      ><button
        class="quiet"
        :disabled="page === 1 || busy"
        @tap="
          page--;
          load();
        "
      >
        上一页</button
      ><text>{{ page }}</text
      ><button
        class="quiet"
        :disabled="!data || page * 12 >= data.total || busy"
        @tap="
          page++;
          load();
        "
      >
        下一页</button
      ><button
        class="primary"
        :disabled="!selected.length || pending"
        @tap="$emit('select', selected)"
      >
        {{
          pending ? "正在关联…" : "关联 " + (selected.length || "") + " 份资料"
        }}
      </button></view
    ></view
  >
</template>

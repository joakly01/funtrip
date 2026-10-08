<script setup lang="ts">
import { computed } from "vue";
import { title, brief, badge, date, kindLabel } from "../domain/records";
import type { RecordRow } from "../domain/types";
import { topics } from "../domain/knowledge";
const props = defineProps<{ row: RecordRow }>();
const b = computed(() => badge(props.row));
defineEmits(["open"]);
</script>
<template>
  <view
    class="card record-card"
    hover-class="card-hover"
    @tap="$emit('open', row.id)"
    ><view class="card-meta"
      ><text>{{ kindLabel(row) }} · {{ date(row.updated_at) }}</text
      ><text v-if="b.tone !== 'neutral'" :class="['badge', b.tone]">{{
        b.text
      }}</text></view
    ><view class="record-title">{{ title(row) }}</view
    ><text class="excerpt">{{ brief(row) }}</text
    ><view class="card-foot"
      ><text
        >{{ row.collection?.starred ? "★ 收藏 · " : ""
        }}{{ topics[row.collection?.topic || "general"] }}</text
      ><text>阅读完整资料 ↗</text></view
    ></view
  >
</template>

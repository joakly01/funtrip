<script setup lang="ts">
import { computed } from "vue";
import { markdown, inline } from "../domain/markdown";
const props = defineProps<{ text: string }>();
const blocks = computed(() => markdown(props.text || ""));
</script>
<template>
  <view class="markdown"
    ><view
      v-for="(block, i) in blocks"
      :key="i"
      :class="[
        'md-block',
        block.kind,
        block.kind === 'heading' ? 'h' + block.level : '',
      ]"
      ><text v-if="block.kind === 'code'" selectable>{{ block.text }}</text
      ><scroll-view
        v-else-if="block.kind === 'table'"
        scroll-x
        class="table-scroll"
        ><view class="md-table"
          ><view v-for="(row, j) in block.rows" :key="j" class="md-row"
            ><view v-for="(cell, k) in row" :key="k" class="md-cell"
              ><rich-text
                :nodes="inline(cell)"
                selectable /></view></view></view></scroll-view
      ><rich-text v-else :nodes="block.nodes" selectable /></view
  ></view>
</template>

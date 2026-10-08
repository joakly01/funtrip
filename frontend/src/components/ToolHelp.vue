<script setup lang="ts">
import { ref, computed } from 'vue';
import { toolHelp } from '../domain/toolHelp';
const props = defineProps<{ id: string }>();
const help = computed(() => toolHelp[props.id]);
const detailed = ref(false);
const expandedSources = ref<string[]>([]);
function toggleSource(url: string) {
  expandedSources.value = expandedSources.value.includes(url) ? expandedSources.value.filter(value => value !== url) : [...expandedSources.value, url];
}
function copySource(url: string) { uni.setClipboardData({ data: url }); }
</script>
<template>
  <view v-if="help" class="tool-help" id="tool-help-content" role="region" :aria-label="help.title + '使用帮助'">
    <view class="help-intro"><text class="eyebrow">了解工具 · 用得明白</text><view class="help-heading">{{ help.purpose }}</view></view>
    <view class="help-section"><view class="help-label">怎么用</view><view v-for="(step, i) in help.steps" :key="step" class="help-step"><text class="help-number">{{ i + 1 }}</text><view>{{ step }}</view></view></view>
    <view v-if="help.references?.length" class="help-section help-sources"><view class="help-label">资料中的分析思路</view><view v-for="(source, i) in help.references" :key="source.url" class="help-source"><view class="help-source-meta">{{ source.kind }} · {{ source.publisher }}</view><strong>{{ source.title }}</strong><view>{{ source.summary }}</view><view class="help-source-actions">
      <!-- #ifdef H5 -->
      <a :href="source.url" target="_blank" rel="noopener noreferrer" class="help-source-link" :aria-label="'打开来源：' + source.title">查看原{{ source.kind === '视频' ? '视频' : '资料' }} ↗</a>
      <!-- #endif -->
      <!-- #ifndef H5 -->
      <button class="help-source-link" @tap="copySource(source.url)">复制来源链接</button>
      <!-- #endif -->
      <button class="help-source-toggle" role="button" tabindex="0" :aria-expanded="expandedSources.includes(source.url)" :aria-controls="'help-source-' + i" @keydown.enter.prevent="toggleSource(source.url)" @keydown.space.prevent="toggleSource(source.url)" @tap="toggleSource(source.url)">{{ expandedSources.includes(source.url) ? '收起分析思路 −' : '展开分析思路 ＋' }}</button></view>
      <view v-if="expandedSources.includes(source.url)" :id="'help-source-' + i" class="help-source-details"><view class="help-source-basis">{{ source.basis }}</view><view v-for="section in source.sections" :key="section.label" class="help-logic"><strong>{{ section.label }}</strong><view>{{ section.text }}</view></view><view class="help-source-application"><strong>本工具采用了什么</strong><view>{{ source.application }}</view></view></view>
    </view></view>
    <view v-else-if="help.basis" class="help-section"><view class="help-label">工具依据</view><view>{{ help.basis }}</view></view>
    <view class="help-section"><view class="help-label">本工具的判断逻辑</view><view v-for="item in help.logic" :key="item.title" class="help-logic"><strong>{{ item.title }}</strong><view>{{ item.text }}</view></view></view>
    <view class="help-section"><view class="help-label">怎么看结果</view><view>{{ help.results }}</view></view>
    <view class="help-advanced"><button class="help-expand" role="button" tabindex="0" @keydown.enter.prevent="detailed = !detailed" @keydown.space.prevent="detailed = !detailed" :aria-expanded="detailed" aria-controls="tool-help-details" @tap="detailed = !detailed"><text>{{ detailed ? '收起计算细节与参数' : '查看计算细节与参数' }}</text><text>{{ detailed ? '−' : '＋' }}</text></button><view v-if="detailed" id="tool-help-details" class="help-details"><view v-for="(item, i) in help.details" :key="item"><text class="help-detail-index">{{ String(i + 1).padStart(2, '0') }}</text><view>{{ item }}</view></view></view></view>
    <view class="help-boundary"><strong>使用边界</strong><view>{{ help.boundary }}</view></view>
    <view class="help-footnote">帮助说明不会触发计算或修改资料。返回工具后可接着操作。</view>
  </view>
</template>
<style scoped>
.tool-help{display:grid;gap:26px;color:var(--ink,#27392f);font-size:14px;line-height:1.95;min-width:0;padding-bottom:16px;overflow-wrap:anywhere}.help-intro{display:grid;gap:12px;padding:4px 0 20px;border-bottom:1px solid #e2e5db}.help-heading{font-size:23px;line-height:1.6;font-weight:600;letter-spacing:-.3px}.help-section{display:grid;gap:14px}.help-label{font-size:12px;letter-spacing:1px;color:#777d6c}.help-step{display:flex;gap:12px;align-items:start}.help-number{width:24px;height:24px;flex-shrink:0;border-radius:50%;background:#e9eee3;color:#4a6655;display:flex;align-items:center;justify-content:center;font-size:12px;margin-top:2px}.help-logic{display:grid;gap:5px}.help-logic strong{font-size:15px}.help-advanced{border:1px solid #dfe3d7;border-radius:14px;overflow:hidden}.help-expand{display:flex;align-items:center;justify-content:space-between;width:100%;margin:0;padding:15px 18px;color:#456252;background:#f0f3eb;font-size:13px;text-align:left;line-height:1.8;white-space:normal}.help-expand::after{border:0}.help-details{display:grid;gap:18px;padding:18px;font-size:13px}.help-details>view{display:flex;gap:12px}.help-detail-index{color:#8a927d;font-size:11px;padding-top:2px;flex-shrink:0}.help-boundary{display:grid;gap:8px;background:#f2f1e9;border-left:2px solid #9b977b;border-radius:0 12px 12px 0;padding:16px 18px;font-size:13px;color:#626452}.help-source{display:grid;gap:10px;border:1px solid #dfe4d8;border-radius:14px;background:#f5f7f0;padding:18px;min-width:0}.help-source-meta{font-size:11px;color:#78836f}.help-source>strong{font-size:15px}.help-source-actions{display:flex;align-items:center;gap:12px;justify-content:space-between;flex-wrap:wrap}.help-source-link,.help-source-toggle{font-size:12px;color:#496b55;text-decoration:none;background:transparent;margin:0;padding:3px 0;line-height:1.9;text-align:left}.help-source-toggle::after{border:0}.help-source-link:focus-visible{outline:2px solid #286b53;outline-offset:3px}.help-source-details{display:grid;gap:18px;border-top:1px solid #dfe4d8;padding-top:16px}.help-source-basis{font-size:12px;color:#7c806f}.help-source-application{display:grid;gap:6px;border-left:2px solid #98a38b;padding-left:12px}.help-footnote{font-size:12px;color:#8a8c7f}@media(max-width:600px){.help-heading{font-size:20px}.tool-help{font-size:13px;gap:23px}.help-details{padding:15px}}
</style>

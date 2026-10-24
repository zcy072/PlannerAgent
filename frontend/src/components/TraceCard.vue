<template>
  <el-card shadow="hover" data-html2canvas-ignore="true">
    <template #header>
      <div class="card-header">
        <el-icon><Clock /></el-icon>
        <span>规划过程</span>
        <span class="trace-total">共 {{ formatDuration(totalTraceMs) }}</span>
      </div>
    </template>
    <el-timeline>
      <el-timeline-item
        v-for="(trace, traceIndex) in traces"
        :key="`${trace.node}-${traceIndex}`"
        :type="trace.status === 'completed' ? 'success' : 'danger'"
        :timestamp="formatDuration(trace.duration_ms)"
        placement="top"
      >
        <strong>{{ traceNodeLabel(trace.node) }}</strong>
        <p class="trace-summary">{{ trace.summary }}</p>
      </el-timeline-item>
    </el-timeline>
  </el-card>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { ElCard, ElIcon, ElTimeline, ElTimelineItem } from 'element-plus'
import { Clock } from '@element-plus/icons-vue'
import { traceNodeLabel } from '@/utils/nodeLabels'
import type { NodeTrace } from '@/types'

const props = defineProps<{ traces: NodeTrace[] }>()

const formatDuration = (ms: number) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)} 秒` : `${ms} 毫秒`)
const totalTraceMs = computed(() =>
  props.traces.reduce((sum, trace) => sum + (trace.duration_ms || 0), 0),
)
</script>

<style scoped>
.trace-total {
  margin-left: auto;
  color: #728079;
  font-size: 12px;
  font-weight: normal;
}

.trace-summary {
  margin: 4px 0 0;
  color: #5f6f69;
  font-size: 13px;
  line-height: 1.5;
}
</style>

<template>
  <div class="metrics-strip">
    <div class="metric-item">
      <span class="metric-label">目的地</span>
      <strong>{{ plan.city }}</strong>
    </div>
    <div class="metric-item">
      <span class="metric-label">行程天数</span>
      <strong>{{ plan.days?.length || 0 }} 天</strong>
    </div>
    <div class="metric-item">
      <span class="metric-label">景点数量</span>
      <strong>{{ totalAttractions }} 个</strong>
    </div>
    <div class="metric-item">
      <span class="metric-label">路线距离</span>
      <strong>{{ totalRouteDistance }} km</strong>
    </div>
    <div class="metric-item">
      <span class="metric-label">规则检查</span>
      <strong>{{ evaluatorStatus }}</strong>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { TripPlan } from '@/types'

const props = defineProps<{ plan: TripPlan }>()

const totalAttractions = computed(() =>
  props.plan.days?.reduce((sum, day) => sum + (day.attractions?.length || 0), 0) || 0,
)

const totalRouteDistance = computed(() => {
  const total = props.plan.days?.reduce(
    (sum, day) => sum + (day.route_summary?.estimated_distance_km || 0),
    0,
  ) || 0
  return total.toFixed(1)
})

const evaluatorStatus = computed(() => {
  const report = props.plan.evaluator_report
  if (!report) return '未检查'
  return report.passed ? '已通过' : `${report.issues.length} 项待调整`
})
</script>

<style scoped>
.metrics-strip {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 12px;
}

.metric-item {
  padding: 16px;
  background: #ffffff;
  border: 1px solid var(--trip-border);
  border-radius: 8px;
  box-shadow: 0 10px 26px rgba(31, 41, 51, 0.06);
}

.metric-label {
  display: block;
  margin-bottom: 8px;
  color: #71817a;
  font-size: 12px;
}

.metric-item strong {
  color: var(--trip-heading);
  font-size: 22px;
  line-height: 1.1;
}

@media (max-width: 760px) {
  .metrics-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>

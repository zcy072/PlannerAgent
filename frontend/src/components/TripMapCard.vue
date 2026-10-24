<template>
  <el-card shadow="hover">
    <template #header>
      <div class="card-header">
        <el-icon><MapLocation /></el-icon>
        <span>景点地图</span>
      </div>
    </template>
    <div class="map-shell">
      <div v-if="mapStatus !== 'ready'" class="map-state" :class="`map-state-${mapStatus}`">
        <el-icon v-if="mapStatus === 'loading'" class="is-loading" aria-hidden="true"><Loading /></el-icon>
        <el-icon v-else aria-hidden="true"><Warning /></el-icon>
        <span role="status" aria-live="polite">{{ mapStatus === 'loading' ? '地图加载中…' : mapError }}</span>
        <el-button
          v-if="mapStatus === 'error' && mapErrorRetryable"
          type="primary"
          size="small"
          @click="retryMap"
        >
          重试
        </el-button>
      </div>
      <div
        ref="containerRef"
        class="map-container"
        role="region"
        aria-label="行程地图"
        :class="{ 'map-container-hidden': mapStatus !== 'ready' }"
      ></div>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElButton, ElCard, ElIcon } from 'element-plus'
import { Loading, MapLocation, Warning } from '@element-plus/icons-vue'
import { useTripMap } from '@/composables/useTripMap'
import type { TripPlan } from '@/types'

// The parent mounts this card only while a plan exists, so mount/unmount is the map's lifecycle:
// a restored or replaced plan remounts the card and gets a fresh map on a fresh container.
const props = defineProps<{ plan: TripPlan }>()

const containerRef = ref<HTMLElement | null>(null)
const plan = computed<TripPlan | null>(() => props.plan)
const { initMap, retryMap, mapStatus, mapError, mapErrorRetryable } = useTripMap(plan, containerRef)

onMounted(initMap)
</script>

<style scoped>
.map-shell {
  position: relative;
  min-height: 420px;
}

.map-container {
  width: 100%;
  height: 420px;
  border-radius: 8px;
  background-color: #e9eee9;
  overflow: hidden;
}

.map-container-hidden {
  visibility: hidden;
}

.map-state {
  position: absolute;
  inset: 0;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  flex-wrap: wrap;
  align-content: center;
  padding: 24px;
  border: 1px solid #dfe9e2;
  border-radius: 8px;
  background: #f7faf8;
  color: #52645b;
  text-align: center;
}

.map-state-error {
  color: #856404;
  background: #fffaf0;
  border-color: #f1dfac;
}

.map-state .is-loading {
  animation: map-spin 1s linear infinite;
}

@keyframes map-spin {
  to { transform: rotate(360deg); }
}

@media (max-width: 760px) {
  .map-container {
    height: 320px;
  }

  .map-shell {
    min-height: 320px;
  }
}
</style>

<template>
  <div class="result-container">
    <!-- 头部 -->
    <div class="header">
      <el-button @click="$router.push('/')" text>
        <el-icon><ArrowLeft /></el-icon>
        返回首页
      </el-button>
      <div class="title-block">
        <h1>行程规划结果</h1>
        <span v-if="tripPlan">{{ tripPlan.city }} · {{ tripPlan.days?.length || 0 }} 天</span>
      </div>
      <div class="header-actions">
        <el-button type="primary" :loading="isExporting" :disabled="!tripPlan || isRestoring || isExporting || tripStore.modifying || tripStore.chatting" @click="handleExportPDF">
          <el-icon><Download /></el-icon>
          导出PDF
        </el-button>
        <el-button @click="handleReset">
          <el-icon><Refresh /></el-icon>
          重新规划
        </el-button>
      </div>
    </div>

    <!-- 加载状态 -->
    <div v-if="isRestoring" class="empty-state">
      <el-skeleton :rows="6" animated />
    </div>

    <div v-else-if="!tripPlan" class="empty-state">
      <el-empty description="暂无行程数据">
        <el-button type="primary" @click="$router.push('/')">开始规划</el-button>
      </el-empty>
    </div>

    <!-- 行程内容 -->
    <div v-else ref="contentRef" class="content">
      <MetricsStrip :plan="tripPlan" />

      <el-alert
        v-if="evaluatorWarnings.length > 0"
        class="evaluator-alert"
        type="warning"
        :closable="false"
        show-icon
      >
        <template #title>
          自动评估提示：{{ evaluatorWarnings.join('；') }}
        </template>
      </el-alert>

      <!-- 概览卡片 -->
      <el-card class="overview-card" shadow="hover">
        <template #header>
          <div class="card-header">
            <el-icon><Location /></el-icon>
            <span>行程概览</span>
          </div>
        </template>
        <el-descriptions :column="4" border>
          <el-descriptions-item label="目的地">{{ tripPlan.city }}</el-descriptions-item>
          <el-descriptions-item label="出发日期">{{ tripPlan.start_date }}</el-descriptions-item>
          <el-descriptions-item label="返回日期">{{ tripPlan.end_date }}</el-descriptions-item>
          <el-descriptions-item label="行程天数">{{ tripPlan.days?.length || 0 }}天</el-descriptions-item>
        </el-descriptions>
      </el-card>

      <WeatherCard v-if="tripPlan.weather_info?.length" :weather-info="tripPlan.weather_info" />

      <!-- 地图展示 -->
      <TripMapCard :key="tripStore.sessionId" :plan="tripPlan" />

      <!-- 每日行程 -->
      <el-card class="itinerary-card" shadow="hover">
        <template #header>
          <div class="card-header">
            <el-icon><Calendar /></el-icon>
            <span>每日行程</span>
          </div>
        </template>
        <el-collapse v-model="activeDays" @change="onActiveDaysChange">
          <el-collapse-item
            v-for="(day, index) in tripPlan.days"
            :key="index"
            :name="index"
          >
            <template #title>
              <div class="day-title">
                <el-tag type="primary">第{{ day.day_index + 1 }}天</el-tag>
                <span class="day-date">{{ day.date }}</span>
                <span class="day-theme">{{ day.description }}</span>
              </div>
            </template>

            <DayPanel
              :day="day"
              :restaurants="nearbyRestaurants[index]"
              :restaurants-loading="!!nearbyRestaurantLoading[index]"
              :restaurants-error="nearbyRestaurantErrors[index]"
              :actions-disabled="actionsDisabled"
              @attraction-command="(cmd, attrIndex) => handleAttractionCommand(cmd, index, attrIndex)"
              @replace-hotel="openHotelReplaceDialog(index)"
            />
          </el-collapse-item>
        </el-collapse>
      </el-card>

      <!-- 总预算 -->
      <BudgetCard v-if="tripPlan.budget" :budget="tripPlan.budget" />

      <!-- 规划过程：展示每个工作流节点的耗时与摘要 -->
      <TraceCard v-if="tripPlan.node_traces?.length" :traces="tripPlan.node_traces" />

      <!-- 总体建议 -->
      <el-card v-if="tripPlan.overall_suggestions" class="tips-card" shadow="hover">
        <template #header>
          <div class="card-header">
            <el-icon><Warning /></el-icon>
            <span>旅行建议</span>
          </div>
        </template>
        <p style="line-height: 1.8; color: #606266;">{{ tripPlan.overall_suggestions }}</p>
      </el-card>
    </div>

    <ReplaceDialog
      v-model="showReplaceDialog"
      :item-type="replaceItemType"
      :target="replaceTarget"
      :candidates="replaceCandidates"
      :city="tripPlan?.city || ''"
      :loading="tripStore.modifying"
      @confirm="confirmReplace"
      @closed="resetReplaceState"
    />

    <!-- 对话式修改：悬浮按钮 + 抽屉 -->
    <button v-if="tripPlan" type="button" class="chat-fab" @click="showChatDrawer = true">
      <el-icon :size="20"><ChatDotRound /></el-icon>
      <span>对话修改</span>
    </button>

    <ChatDrawer v-model:open="showChatDrawer" />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed, watch, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import {
  ElAlert, ElButton, ElCard, ElCollapse, ElCollapseItem,
  ElDescriptions, ElDescriptionsItem, ElEmpty, ElIcon, ElSkeleton,
  ElTag, ElMessage, ElMessageBox,
} from 'element-plus'
import {
  ArrowLeft, Download, Refresh, Location, Calendar,
  Warning, ChatDotRound
} from '@element-plus/icons-vue'
import { useTripStore } from '@/stores/trip'
import { useTripRestore } from '@/composables/useTripRestore'
import { useNearbyRestaurants } from '@/composables/useNearbyRestaurants'
import { useTripPdf } from '@/composables/useTripPdf'
import { pickPoiCandidates, pickHotelCandidates } from '@/composables/replaceCandidates'
import { getErrorMessage } from '@/utils/errors'
import { persistenceWarning } from '@/utils/persistence'
import ChatDrawer from '@/components/ChatDrawer.vue'
import ReplaceDialog from '@/components/ReplaceDialog.vue'
import DayPanel from '@/components/DayPanel.vue'
import TripMapCard from '@/components/TripMapCard.vue'
import MetricsStrip from '@/components/MetricsStrip.vue'
import WeatherCard from '@/components/WeatherCard.vue'
import BudgetCard from '@/components/BudgetCard.vue'
import TraceCard from '@/components/TraceCard.vue'
import type { POI, Hotel, Attraction } from '@/types'

const router = useRouter()
const tripStore = useTripStore()

const tripPlan = computed(() => tripStore.tripPlan)
const activeDays = ref<number[]>([0])

const { isRestoring, restore, cancel: cancelRestore } = useTripRestore()
const {
  restaurants: nearbyRestaurants,
  loading: nearbyRestaurantLoading,
  errors: nearbyRestaurantErrors,
  load: loadNearbyRestaurant,
  loadMany: loadNearbyRestaurantsFor,
  reset: resetNearbyRestaurants,
} = useNearbyRestaurants(() => tripPlan.value)

const isExporting = ref(false)
const exportActiveDays = ref<number[]>([])
// Element Plus collapse animates for ~0.3s; capturing earlier yields half-open panels.
const COLLAPSE_TRANSITION_MS = 400
// A stuck restaurant search must not hold the export hostage.
const EXPORT_RESTAURANT_WAIT_MS = 15000
const delay = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

const contentRef = ref<HTMLElement | null>(null)
const { exportPdf } = useTripPdf(tripPlan, contentRef, {
  beforeExport: async () => {
    exportActiveDays.value = [...activeDays.value]
    isExporting.value = true
    const allDays = tripPlan.value?.days?.map((_, index) => index) || []
    activeDays.value = allDays
    // Programmatic expansion does not fire the collapse @change, so load explicitly.
    await Promise.race([loadNearbyRestaurantsFor(allDays), delay(EXPORT_RESTAURANT_WAIT_MS)])
    await nextTick()
    await delay(COLLAPSE_TRANSITION_MS)
  },
  afterExport: () => {
    activeDays.value = exportActiveDays.value
    isExporting.value = false
  },
})
const handleExportPDF = exportPdf
const actionsDisabled = computed(() => tripStore.modifying || tripStore.chatting || isExporting.value)

// 替换 / 删除相关
const showReplaceDialog = ref(false)
const replaceItemType = ref<'attraction' | 'hotel'>('attraction')
const replaceDayIndex = ref<number>(0)
const replaceItemIndex = ref<number | null>(null)
const replaceTarget = ref<Attraction | Hotel | null>(null)
const replaceCandidates = ref<Array<POI | Hotel>>([])

// 对话式修改
const showChatDrawer = ref(false)

const evaluatorWarnings = computed(() => {
  return tripPlan.value?.evaluator_warnings || []
})

function resetReplaceState() {
  replaceItemType.value = 'attraction'
  replaceDayIndex.value = 0
  replaceItemIndex.value = null
  replaceTarget.value = null
  replaceCandidates.value = []
}

// ===== 操作菜单 =====
async function handleAttractionCommand(cmd: string, dayIndex: number, attrIndex: number) {
  if (cmd === 'replace') {
    openAttractionReplaceDialog(dayIndex, attrIndex)
  } else if (cmd === 'delete') {
    await handleAttractionDelete(dayIndex, attrIndex)
  }
}

function openAttractionReplaceDialog(dayIndex: number, attrIndex: number) {
  const day = tripPlan.value?.days?.[dayIndex]
  if (!day) return
  const target = day.attractions?.[attrIndex]
  if (!target) return

  replaceItemType.value = 'attraction'
  replaceDayIndex.value = dayIndex
  replaceItemIndex.value = attrIndex
  replaceTarget.value = target
  replaceCandidates.value = pickPoiCandidates(tripPlan.value, target)
  showReplaceDialog.value = true
}

function openHotelReplaceDialog(dayIndex: number) {
  const day = tripPlan.value?.days?.[dayIndex]
  if (!day || !day.hotel) return

  replaceItemType.value = 'hotel'
  replaceDayIndex.value = dayIndex
  replaceItemIndex.value = null
  replaceTarget.value = day.hotel
  replaceCandidates.value = pickHotelCandidates(tripPlan.value, day.hotel)
  showReplaceDialog.value = true
}

async function handleAttractionDelete(dayIndex: number, attrIndex: number) {
  if (tripStore.modifying || tripStore.chatting || isExporting.value) return
  try {
    await ElMessageBox.confirm('确定删除该景点？删除后会自动重排当天路线和预算。', '删除确认', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }

  try {
    const res = await tripStore.modifyItem({
      day_index: dayIndex,
      item_type: 'attraction',
      item_index: attrIndex,
      action: 'delete',
    })
    if (res.success) {
      const warning = persistenceWarning(res)
      if (warning) {
        ElMessage.warning(warning)
      } else {
        ElMessage.success('已删除')
      }
    } else {
      ElMessage.error(res.message || '删除失败')
    }
  } catch (error: unknown) {
    ElMessage.error(getErrorMessage(error, '删除失败'))
  }
}

async function confirmReplace(replacement: POI | Hotel) {
  if (tripStore.modifying || tripStore.chatting || isExporting.value) return

  const payload = {
    day_index: replaceDayIndex.value,
    item_type: replaceItemType.value,
    item_index: replaceItemType.value === 'attraction' ? (replaceItemIndex.value ?? undefined) : undefined,
    action: 'replace' as const,
    replacement,
  }

  try {
    const res = await tripStore.modifyItem(payload)
    if (res.success) {
      const warning = persistenceWarning(res)
      if (warning) {
        ElMessage.warning(warning)
      } else {
        ElMessage.success('已替换')
      }
      showReplaceDialog.value = false
    } else {
      ElMessage.error(res.message || '替换失败')
    }
  } catch (error: unknown) {
    ElMessage.error(getErrorMessage(error, '替换失败'))
  }
}

function onActiveDaysChange(value: string | number | Array<string | number>) {
  loadNearbyRestaurantsFor(Array.isArray(value) ? value : [value])
}

// 重新规划
function handleReset() {
  cancelRestore()
  tripStore.reset()
  router.push('/')
}

watch(tripPlan, () => {
  resetNearbyRestaurants()
  loadNearbyRestaurantsFor(activeDays.value)
})

onMounted(async () => {
  await restore()

  if (!tripPlan.value) return

  await loadNearbyRestaurant(0)
})
</script>

<style scoped>
.result-container {
  min-height: 100vh;
  padding: 20px 24px 44px;
  background: #f4f7f5;
}

.header {
  max-width: 1200px;
  margin: 0 auto 18px;
  display: grid;
  grid-template-columns: 160px minmax(0, 1fr) auto;
  align-items: center;
  gap: 16px;
  padding: 16px 18px;
  background: #ffffff;
  border: 1px solid var(--trip-border);
  border-radius: 8px;
  box-shadow: 0 10px 30px rgba(31, 41, 51, 0.08);
}

.title-block {
  text-align: center;
}

.title-block h1 {
  margin: 0;
  color: var(--trip-heading);
  font-size: 24px;
  line-height: 1.2;
  letter-spacing: 0;
}

.title-block span {
  display: block;
  margin-top: 4px;
  color: #6b7b75;
  font-size: 13px;
}

.header-actions {
  display: flex;
  gap: 10px;
}

.empty-state {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 420px;
  background: #ffffff;
  border-radius: 8px;
}

.content {
  max-width: 1200px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.evaluator-alert {
  border-radius: 8px;
}

:deep(.el-card) {
  border: 1px solid var(--trip-border);
  border-radius: 8px;
  box-shadow: 0 12px 32px rgba(31, 41, 51, 0.07);
}

:deep(.el-tag--primary) {
  --el-tag-bg-color: #edf7f3;
  --el-tag-border-color: #cfe7dc;
  --el-tag-text-color: var(--trip-primary);
}

:deep(.el-card__header) {
  padding: 15px 18px;
  border-bottom-color: #edf1ee;
}

:deep(.el-card__body) {
  padding: 18px;
}

.day-title {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.day-date {
  color: #71817a;
  font-size: 14px;
}

.day-theme {
  min-width: 0;
  overflow: hidden;
  color: #263a34;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tips-card p {
  margin: 0;
}

@media (max-width: 760px) {
  .result-container {
    padding: 14px;
  }

  .header {
    grid-template-columns: 1fr;
    justify-items: stretch;
  }

  .title-block {
    text-align: left;
  }

  .header-actions {
    flex-wrap: wrap;
  }
}

/* 对话式修改 */
.chat-fab {
  position: fixed;
  right: 24px;
  bottom: 32px;
  z-index: 1500;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 12px 18px;
  border-radius: 999px;
  background: linear-gradient(135deg, #409eff, #2972ff);
  color: #fff;
  font-size: inherit;
  font-family: inherit;
  font-weight: 600;
  border: none;
  cursor: pointer;
  box-shadow: 0 8px 20px rgba(64, 158, 255, 0.35);
  transition: transform 0.2s ease;
}

.chat-fab:hover {
  transform: translateY(-2px);
}
</style>

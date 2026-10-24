<template>
  <div class="home-container">
    <!-- 头部 -->
    <div class="header">
      <div class="header-kicker">AI Trip Planner</div>
      <h1>智能旅行助手</h1>
      <p>LLM Agent 与确定性规划节点协作，生成路线更紧凑、预算更清晰的旅行计划</p>
    </div>

    <!-- 主内容区 -->
    <div class="main-content">
      <!-- 左侧：表单输入 -->
      <el-card class="form-card" shadow="hover">
        <template #header>
          <div class="card-header">
            <el-icon><Location /></el-icon>
            <span>行程规划</span>
          </div>
        </template>

        <el-form :model="formData" label-position="top" @submit.prevent="handleSubmit">
          <div class="form-section-title">基础信息</div>
          <!-- 城市和日期 -->
          <el-row :gutter="20">
            <el-col :span="12">
              <el-form-item label="目的地城市" required>
                <el-input v-model="formData.city" placeholder="例如：北京、上海、成都">
                  <template #prefix>
                    <el-icon><Location /></el-icon>
                  </template>
                </el-input>
              </el-form-item>
            </el-col>
            <el-col :span="6">
              <el-form-item label="出发日期" required>
                <el-date-picker
                  v-model="formData.start_date"
                  type="date"
                  placeholder="选择日期"
                  style="width: 100%"
                  value-format="YYYY-MM-DD"
                />
              </el-form-item>
            </el-col>
            <el-col :span="6">
              <el-form-item label="返回日期" required>
                <el-date-picker
                  v-model="formData.end_date"
                  type="date"
                  placeholder="选择日期"
                  style="width: 100%"
                  value-format="YYYY-MM-DD"
                />
              </el-form-item>
            </el-col>
          </el-row>

          <!-- 旅行天数显示 -->
          <el-alert
            v-if="travelDays > 0"
            :title="`共 ${travelDays} 天行程`"
            type="info"
            :closable="false"
            show-icon
            style="margin-bottom: 20px"
          />

          <div class="form-section-title">出行偏好</div>
          <!-- 偏好设置 -->
          <el-row :gutter="20">
            <el-col :span="12">
              <el-form-item label="交通方式">
                <el-select v-model="formData.transportation" style="width: 100%">
                  <el-option label="🚇 公共交通" value="公共交通" />
                  <el-option label="🚗 自驾" value="自驾" />
                  <el-option label="🚶 步行" value="步行" />
                  <el-option label="🔀 混合" value="混合" />
                </el-select>
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="住宿偏好">
                <el-select v-model="formData.accommodation" style="width: 100%">
                  <el-option label="💰 经济型酒店" value="经济型酒店" />
                  <el-option label="🏨 舒适型酒店" value="舒适型酒店" />
                  <el-option label="⭐ 豪华酒店" value="豪华酒店" />
                  <el-option label="🏡 民宿" value="民宿" />
                </el-select>
              </el-form-item>
            </el-col>
          </el-row>

          <!-- 旅行偏好 -->
          <el-form-item label="旅行偏好">
            <el-checkbox-group v-model="formData.preferences">
              <el-checkbox label="历史文化" border>🏛️ 历史文化</el-checkbox>
              <el-checkbox label="自然风光" border>🏞️ 自然风光</el-checkbox>
              <el-checkbox label="美食" border>🍜 美食</el-checkbox>
              <el-checkbox label="购物" border>🛍️ 购物</el-checkbox>
              <el-checkbox label="艺术" border>🎨 艺术</el-checkbox>
              <el-checkbox label="休闲" border>☕ 休闲</el-checkbox>
            </el-checkbox-group>
          </el-form-item>

          <!-- 额外要求 -->
          <el-form-item label="额外要求">
            <el-input
              v-model="formData.free_text_input"
              type="textarea"
              :rows="3"
              placeholder="请输入您的额外要求，例如：想去看升旗、需要无障碍设施..."
            />
          </el-form-item>

          <div class="form-section-title">预算与模型</div>
          <!-- 预算设置 -->
          <el-form-item label="预算范围">
            <div class="budget-slider-container">
              <el-slider
                v-model="formData.budget"
                range
                :min="500"
                :max="10000"
                :step="100"
                :format-tooltip="(val: number) => `¥${val}`"
              />
              <div class="budget-display">
                <el-tag type="success">¥{{ budgetRange[0] }}</el-tag>
                <span> - </span>
                <el-tag type="warning">¥{{ budgetRange[1] }}</el-tag>
                <span class="budget-label">（{{ budgetLevel }}）</span>
              </div>
            </div>
          </el-form-item>

          <!-- LLM 选择 -->
          <el-form-item label="AI 模型">
            <el-radio-group v-model="formData.llm_provider">
              <el-radio label="deepseek">DeepSeek</el-radio>
              <el-radio label="aliyun">阿里云百炼</el-radio>
            </el-radio-group>
          </el-form-item>

          <!-- 提交按钮 -->
          <el-form-item>
            <div class="submit-panel">
              <div>
                <div class="submit-title">生成智能行程</div>
                <div class="submit-subtitle">先获取天气，再并行召回景点与酒店并优化路线</div>
              </div>
              <el-button
                v-if="!isLoading && canResume"
                size="large"
                @click="handleResume"
              >
                继续上次规划
              </el-button>
              <el-button
                v-if="!isLoading"
                type="primary"
                size="large"
                @click="handleSubmit"
              >
                <el-icon><Promotion /></el-icon>
                开始规划
              </el-button>
              <el-button
                v-else
                type="danger"
                size="large"
                @click="handleStop"
              >
                <el-icon><Close /></el-icon>
                终止规划
              </el-button>
            </div>
          </el-form-item>
        </el-form>
      </el-card>

      <!-- 右侧：执行进度 -->
      <el-card v-if="isLoading || steps.length > 0" class="progress-card" shadow="hover">
        <template #header>
          <div class="card-header">
            <el-icon><Loading /></el-icon>
            <span>规划工作流进度</span>
          </div>
        </template>

        <el-timeline>
          <el-timeline-item
            v-for="(step, index) in steps"
            :key="index"
            :type="getStepType(step.status)"
            :title="step.node"
          >
            <div class="step-content">
              <div class="step-header">
                <span class="step-name">{{ streamNodeLabel(step.node) }}</span>
                <el-tag :type="getStepType(step.status)" size="small">
                  {{ step.status }}
                </el-tag>
              </div>
              <div v-if="step.message" class="step-message">
                {{ step.message }}
              </div>
              <div v-if="step.error" class="step-error">
                <el-icon><WarningFilled /></el-icon>
                {{ step.error }}
              </div>
            </div>
          </el-timeline-item>
        </el-timeline>

        <!-- 当前状态提示 -->
        <div v-if="isLoading && currentMessage" class="current-status" role="status" aria-live="polite">
          <el-icon class="is-loading" aria-hidden="true"><Loading /></el-icon>
          <span>{{ currentMessage }}</span>
        </div>

        <el-progress
          v-if="isLoading"
          :percentage="progress"
          :status="progressStatus"
          :stroke-width="10"
          style="margin-top: 20px"
        />
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import {
  ElAlert, ElButton, ElCard, ElCheckbox, ElCheckboxGroup, ElCol, ElDatePicker,
  ElForm, ElFormItem, ElIcon, ElInput, ElOption, ElProgress, ElRadio,
  ElRadioGroup, ElRow, ElSelect, ElSlider, ElTag, ElTimeline, ElTimelineItem,
  ElMessage,
} from 'element-plus'
import { Location, Promotion, Loading, WarningFilled, Close } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import { useTripStore } from '@/stores/trip'
import { createTripPlanStream, resumeTripPlanStream } from '@/services/api'
import { getErrorMessage } from '@/utils/errors'
import { streamNodeLabel } from '@/utils/nodeLabels'
import type { TripRequest, AgentStep, StreamingResponse } from '@/types'

const router = useRouter()
const tripStore = useTripStore()

const formData = ref<TripRequest>({
  city: '',
  start_date: '',
  end_date: '',
  travel_days: 1,
  transportation: '公共交通',
  accommodation: '经济型酒店',
  preferences: [],
  free_text_input: '',
  llm_provider: 'deepseek',
  budget: [1000, 3000] as [number, number]
})

const isLoading = ref(false)
const isStopped = ref(false)
const steps = ref<AgentStep[]>([])
const progress = ref(0)
const currentMessage = ref('')
const abortController = ref<AbortController | null>(null)
// 后端在失败事件里标记可续跑时置为 true，下一次提交或成功后清除。
const canResume = ref(false)

// 计算旅行天数
const travelDays = computed(() => {
  if (formData.value.start_date && formData.value.end_date) {
    const start = dayjs(formData.value.start_date)
    const end = dayjs(formData.value.end_date)
    const days = end.diff(start, 'day') + 1
    return days > 0 ? days : 0
  }
  return 0
})

const budgetRange = computed<[number, number]>(() => formData.value.budget ?? [1000, 3000])

// 预算等级
const budgetLevel = computed(() => {
  const max = budgetRange.value[1]
  if (max <= 1500) return '经济出行'
  if (max <= 3000) return '舒适旅行'
  if (max <= 5000) return '品质游玩'
  if (max <= 8000) return '高端体验'
  return '奢华之旅'
})

// 监听日期变化
watch(travelDays, (days) => {
  formData.value.travel_days = days
})

// 进度状态
const progressStatus = computed(() => {
  if (progress.value >= 100) return 'success'
  if (progress.value >= 80) return 'warning'
  return ''
})

// 获取步骤类型
function getStepType(status: string): 'primary' | 'success' | 'danger' | 'info' {
  switch (status) {
    case 'completed': return 'success'
    case 'running': return 'primary'
    case 'failed': return 'danger'
    default: return 'info'
  }
}

function createSessionId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `session_${Date.now()}_${Math.random().toString(36).slice(2)}`
}

// 完成后延迟跳转结果页；离开首页或重新规划时必须取消，避免劫持当前页面。
let navigationTimer: ReturnType<typeof setTimeout> | null = null
let unmounted = false

function clearNavigationTimer() {
  if (navigationTimer !== null) {
    clearTimeout(navigationTimer)
    navigationTimer = null
  }
}

onBeforeUnmount(() => {
  unmounted = true
  clearNavigationTimer()
  abortController.value?.abort()
})

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

type StreamRunner = (
  onMessage: (data: StreamingResponse) => void,
  signal: AbortSignal
) => Promise<void>

// 提交与续跑共用：负责加载状态、终止和错误展示。
async function runPlanning(run: StreamRunner, options: { keepSteps: boolean }) {
  clearNavigationTimer()
  isLoading.value = true
  isStopped.value = false
  canResume.value = false
  if (!options.keepSteps) {
    steps.value = []
    progress.value = 0
  } else {
    // 去掉上一次失败留下的错误步骤，续跑时重新显示进度
    steps.value = steps.value.filter(step => step.node !== 'error')
  }
  currentMessage.value = '正在初始化...'

  // 创建 AbortController 用于取消请求
  abortController.value = new AbortController()

  try {
    await run(handleStreamEvent, abortController.value.signal)

    if (isStopped.value) {
      ElMessage.info('已终止规划')
      steps.value.push({ node: 'stopped', status: 'failed', message: '用户终止' })
    }
  } catch (error: unknown) {
    if (unmounted) return
    if (isAbortError(error)) {
      ElMessage.info('已终止规划')
      steps.value.push({ node: 'stopped', status: 'failed', message: '用户终止' })
    } else {
      const message = getErrorMessage(error, '请求失败')
      console.error('[Home] 请求错误:', error)
      ElMessage.error(message)
      steps.value.push({ node: 'error', status: 'failed', error: message })
    }
  } finally {
    isLoading.value = false
    abortController.value = null
  }
}

// 提交表单
async function handleSubmit() {
  if (!formData.value.city || !formData.value.start_date || !formData.value.end_date) {
    ElMessage.warning('请填写完整信息')
    return
  }

  const start = dayjs(formData.value.start_date)
  const end = dayjs(formData.value.end_date)
  if (!start.isValid() || !end.isValid() || end.isBefore(start, 'day')) {
    ElMessage.warning('返回日期不能早于出发日期')
    return
  }
  if (travelDays.value > 30) {
    ElMessage.warning('单次行程最多支持 30 天，请调整日期范围')
    return
  }

  const sessionId = createSessionId()
  tripStore.setSessionId(sessionId)
  tripStore.setLlmProvider(formData.value.llm_provider)

  await runPlanning(
    (onMessage, signal) => createTripPlanStream(
      { ...formData.value, session_id: sessionId }, onMessage, signal
    ),
    { keepSteps: false }
  )
}

// 从失败的节点继续，沿用同一个会话，不重复已完成的步骤
async function handleResume() {
  const sessionId = tripStore.sessionId
  if (!sessionId) return
  await runPlanning(
    (onMessage, signal) => resumeTripPlanStream(sessionId, onMessage, signal),
    { keepSteps: true }
  )
}

// 处理流式事件
function handleStreamEvent(data: StreamingResponse) {
  // 更新当前消息
  if (data.message) {
    currentMessage.value = data.message
  }

  // 更新进度
  if (data.step) {
    progress.value = Math.max(progress.value, Math.min(data.step * 10, 90))
  }

  // 更新步骤列表
  if (data.node) {
    const existingIndex = steps.value.findIndex(s => s.node === data.node)
    const step: AgentStep = {
      node: data.node,
      status: data.status,
      message: data.message,
      data: data.data || undefined
    }

    if (existingIndex >= 0) {
      steps.value[existingIndex] = step
    } else {
      steps.value.push(step)
    }
  }

  // 处理完成
  if (data.node === 'complete' && data.status === 'completed') {
    progress.value = 100

    if (data.data?.itinerary) {
      tripStore.setTripPlan(data.data.itinerary)

      const warnings: string[] = []
      const persistenceWarning = data.data.persistence_warning
      if (typeof persistenceWarning === 'string' && persistenceWarning) {
        warnings.push(persistenceWarning)
      }
      const evaluationWarnings = data.data.itinerary.evaluator_warnings || []
      if (evaluationWarnings.length > 0) {
        warnings.push(`行程已生成，仍有 ${evaluationWarnings.length} 项需要调整，请查看结果页提示`)
      }
      if (warnings.length > 0) {
        ElMessage.warning(warnings.join('；'))
      } else {
        ElMessage.success('行程规划完成！')
      }

      const completedSessionId = data.session_id || tripStore.sessionId
      clearNavigationTimer()
      navigationTimer = setTimeout(() => {
        navigationTimer = null
        router.push({ name: 'Result', query: { session_id: completedSessionId } })
      }, 500)
    }
  }

  // 处理错误
  if (data.status === 'failed' || data.node === 'error') {
    ElMessage.error(data.message || '规划失败')
    canResume.value = data.data?.resumable === true
  }
}

// 终止规划
function handleStop() {
  if (abortController.value) {
    isStopped.value = true
    abortController.value.abort()
  }
}
</script>

<style scoped>
.home-container {
  min-height: 100vh;
  padding: 36px 24px 48px;
  background:
    linear-gradient(180deg, rgba(227, 238, 232, 0.92), rgba(246, 248, 245, 0.98)),
    url('https://images.unsplash.com/photo-1500530855697-b586d89ba3ee?auto=format&fit=crop&w=1800&q=80') center/cover fixed;
}

.header {
  max-width: 1200px;
  margin: 0 auto 28px;
  color: #1f2933;
}

.header-kicker {
  display: inline-flex;
  padding: 5px 10px;
  margin-bottom: 12px;
  border: 1px solid rgba(47, 111, 95, 0.24);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.72);
  color: var(--trip-primary);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0;
}

.header h1 {
  margin: 0 0 8px;
  font-size: 38px;
  line-height: 1.15;
  letter-spacing: 0;
}

.header p {
  margin: 0;
  color: #52616b;
  font-size: 16px;
}

.main-content {
  max-width: 1200px;
  margin: 0 auto;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 380px;
  gap: 20px;
  align-items: start;
}

.form-card,
.progress-card {
  border: 1px solid rgba(32, 55, 46, 0.1);
  border-radius: 8px;
  box-shadow: 0 18px 48px rgba(31, 41, 51, 0.12);
}

.progress-card {
  position: sticky;
  top: 24px;
}

.form-section-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 4px 0 14px;
  color: var(--trip-primary);
  font-size: 13px;
  font-weight: 800;
}

.form-section-title::before {
  content: '';
  width: 4px;
  height: 14px;
  border-radius: 99px;
  background: var(--trip-primary);
}

:deep(.el-card__header) {
  padding: 16px 20px;
  border-bottom-color: #edf1ee;
}

:deep(.el-card__body) {
  padding: 20px;
}

:deep(.el-form-item) {
  margin-bottom: 18px;
}

:deep(.el-form-item__label) {
  color: #34443f;
  font-weight: 650;
}

:deep(.el-input__wrapper),
:deep(.el-textarea__inner),
:deep(.el-select__wrapper) {
  border-radius: 6px;
  box-shadow: 0 0 0 1px #dce4df inset;
}

:deep(.el-checkbox.is-bordered) {
  height: 36px;
  margin-right: 8px;
  margin-bottom: 8px;
  border-radius: 6px;
  background: #fbfdfb;
}

:deep(.el-checkbox.is-bordered.is-checked) {
  border-color: var(--trip-primary);
  background: #edf7f3;
}

:deep(.el-checkbox__input.is-checked .el-checkbox__inner) {
  border-color: var(--trip-primary);
  background-color: var(--trip-primary);
}

:deep(.el-radio__input.is-checked .el-radio__inner) {
  border-color: var(--trip-primary);
  background: var(--trip-primary);
}

:deep(.el-radio__input.is-checked + .el-radio__label) {
  color: var(--trip-primary);
}

:deep(.el-slider__bar) {
  background-color: var(--trip-primary);
}

:deep(.el-slider__button) {
  border-color: var(--trip-primary);
}

.step-content {
  padding: 6px 0 8px;
}

.step-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.step-name {
  color: #243b35;
  font-weight: 650;
}

.step-error {
  color: #c2413d;
  font-size: 12px;
  margin-top: 6px;
  display: flex;
  align-items: center;
  gap: 4px;
}

.step-message {
  color: #60706a;
  font-size: 13px;
  line-height: 1.55;
  margin-top: 5px;
}

.current-status {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px;
  background: #edf7f3;
  border: 1px solid #d5e9e1;
  border-radius: 6px;
  color: var(--trip-primary);
  font-size: 14px;
  margin-top: 12px;
}

.current-status .el-icon {
  font-size: 16px;
}

.budget-slider-container {
  width: 100%;
}

.budget-display {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  justify-content: center;
}

.budget-label {
  color: #60706a;
  font-size: 13px;
}

.submit-panel {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 16px;
  border: 1px solid #dce9e2;
  border-radius: 8px;
  background: linear-gradient(180deg, #f7fbf8, #eef7f3);
}

.submit-title {
  color: var(--trip-heading);
  font-weight: 800;
}

.submit-subtitle {
  margin-top: 4px;
  color: #60706a;
  font-size: 13px;
}

@media (max-width: 900px) {
  .home-container {
    padding: 24px 14px 36px;
  }

  .header h1 {
    font-size: 30px;
  }

  .main-content {
    grid-template-columns: 1fr;
  }

  .progress-card {
    position: static;
  }
}
</style>

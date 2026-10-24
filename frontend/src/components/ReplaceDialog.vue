<template>
  <el-dialog
    :model-value="modelValue"
    :title="itemType === 'attraction' ? '替换景点' : '替换酒店'"
    width="640px"
    @update:model-value="emit('update:modelValue', $event)"
    @closed="onClosed"
  >
    <div class="replace-dialog">
      <div v-if="target" class="replace-current">
        <span class="replace-label">当前：</span>
        <strong>{{ target.name || '' }}</strong>
      </div>

      <div v-if="candidates.length > 0" class="replace-section">
        <div class="replace-section-title">推荐替代（来自候选池）</div>
        <div class="replace-candidate-list">
          <el-card
            v-for="(item, ci) in candidates"
            :key="`cand-${ci}`"
            class="replace-candidate-card"
            :class="{ active: isSelected(item) }"
            shadow="hover"
            role="button"
            tabindex="0"
            :aria-pressed="isSelected(item)"
            @click="selectReplacement(item)"
            @keydown.enter.prevent="selectReplacement(item)"
            @keydown.space.prevent="selectReplacement(item)"
          >
            <div class="candidate-name">{{ item.name }}</div>
            <div class="candidate-meta">
              <el-tag v-if="candidateCategory(item)" size="small">{{ candidateCategory(item) }}</el-tag>
              <span v-if="item.rating" class="candidate-rating">
                <el-icon><Star /></el-icon>{{ item.rating }}
              </span>
              <span v-if="candidatePriceRange(item)" class="candidate-price">{{ candidatePriceRange(item) }}</span>
            </div>
            <div class="candidate-address">{{ item.address }}</div>
          </el-card>
        </div>
      </div>
      <div v-else class="replace-empty">
        候选池暂无相似推荐，可使用下方搜索自定义。
      </div>

      <div class="replace-section">
        <div class="replace-section-title">
          {{ itemType === 'attraction' ? '自定义搜索景点' : '自定义搜索酒店' }}
        </div>
        <el-autocomplete
          v-model="customQuery"
          :fetch-suggestions="fetchCustomSuggestions"
          :placeholder="itemType === 'attraction' ? '输入景点名称搜索' : '输入酒店类型或名称搜索'"
          clearable
          value-key="name"
          class="replace-search"
          @select="onCustomItemSelect"
        >
          <template #default="{ item }">
            <div class="suggestion-row">
              <strong>{{ item.name }}</strong>
              <span class="suggestion-addr">{{ item.address }}</span>
            </div>
          </template>
        </el-autocomplete>
        <div v-if="customSelected" class="replace-current custom-selected">
          <span class="replace-label">已选自定义：</span>
          <strong>{{ customSelected.name }}</strong>
        </div>
      </div>
    </div>

    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button
        type="primary"
        :loading="loading"
        :disabled="!selectedReplacement"
        @click="confirm"
      >
        确认替换
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { ElAutocomplete, ElButton, ElCard, ElDialog, ElIcon, ElTag } from 'element-plus'
import { Star } from '@element-plus/icons-vue'
import { searchHotels, searchPOI } from '@/services/api'
import type { Attraction, Hotel, POI } from '@/types'

const props = defineProps<{
  modelValue: boolean
  itemType: 'attraction' | 'hotel'
  target: Attraction | Hotel | null
  candidates: Array<POI | Hotel>
  city: string
  loading: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'confirm', replacement: POI | Hotel): void
  (e: 'closed'): void
}>()

const selectedReplacement = ref<POI | Hotel | null>(null)
const customSelected = ref<POI | Hotel | null>(null)
const customQuery = ref('')

let suggestTimer: number | null = null
let suggestRequestId = 0

function isSelected(item: POI | Hotel): boolean {
  if (!selectedReplacement.value) return false
  return selectedReplacement.value.name === item.name
      && selectedReplacement.value.address === item.address
}

function candidateCategory(item: POI | Hotel): string | null | undefined {
  return 'category' in item ? item.category : undefined
}

function candidatePriceRange(item: POI | Hotel): string | undefined {
  return 'price_range' in item ? item.price_range : undefined
}

function resetSelection() {
  selectedReplacement.value = null
  customSelected.value = null
  customQuery.value = ''
}

function cancelPendingSuggestions() {
  suggestRequestId += 1
  if (suggestTimer) {
    clearTimeout(suggestTimer)
    suggestTimer = null
  }
}

function selectReplacement(item: POI | Hotel) {
  resetSelection()
  selectedReplacement.value = item
}

function onCustomItemSelect(item: Record<string, unknown>) {
  const selected = item as unknown as POI | Hotel
  customSelected.value = selected
  selectedReplacement.value = selected
}

function fetchCustomSuggestions(query: string, cb: (items: Array<POI | Hotel>) => void) {
  const requestId = ++suggestRequestId
  if (!query || !props.city) {
    cb([])
    return
  }
  if (suggestTimer) {
    clearTimeout(suggestTimer)
  }
  const itemType = props.itemType
  const city = props.city
  suggestTimer = window.setTimeout(async () => {
    try {
      const res = itemType === 'attraction'
        ? await searchPOI(query, city)
        : await searchHotels(city, query)
      if (requestId !== suggestRequestId) return
      const items: Array<POI | Hotel> = res.data || []
      cb(items.slice(0, 8))
    } catch {
      if (requestId === suggestRequestId) cb([])
    }
  }, 200)
}

function confirm() {
  if (selectedReplacement.value) emit('confirm', selectedReplacement.value)
}

function onClosed() {
  cancelPendingSuggestions()
  resetSelection()
  emit('closed')
}

// 每次重新打开都从空白选择开始。
watch(() => props.modelValue, (open) => {
  if (open) resetSelection()
})

onBeforeUnmount(cancelPendingSuggestions)
</script>

<style scoped>
.replace-dialog {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding-top: 4px;
}

.replace-current {
  padding: 8px 12px;
  background: #f6faf7;
  border: 1px solid #d5e8da;
  border-radius: 6px;
  color: var(--trip-primary);
  font-size: 14px;
}

.replace-current.custom-selected {
  margin-top: 8px;
  background: #fff8e6;
  border-color: #f0d287;
  color: #b07b1d;
}

.replace-label {
  color: #5b7560;
  margin-right: 6px;
}

.replace-section-title {
  margin-bottom: 8px;
  color: #243b35;
  font-weight: 700;
  font-size: 14px;
}

.replace-candidate-list {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.replace-candidate-card:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 2px;
}

.replace-candidate-card {
  cursor: pointer;
  border: 1px solid var(--trip-border);
  transition: border-color 0.2s, box-shadow 0.2s;
}

.replace-candidate-card.active {
  border-color: var(--trip-primary);
  box-shadow: 0 0 0 2px rgba(47, 111, 95, 0.18);
}

.candidate-name {
  color: var(--trip-heading);
  font-weight: 700;
  margin-bottom: 6px;
}

.candidate-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  margin-bottom: 6px;
  font-size: 12px;
  color: #71817a;
}

.candidate-rating {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  color: #ad7035;
}

.candidate-price {
  color: #5f6f69;
}

.candidate-address {
  color: #5f6f69;
  font-size: 12px;
  line-height: 1.4;
  word-break: break-all;
}

.replace-empty {
  padding: 12px;
  text-align: center;
  background: #f6f7f6;
  border-radius: 6px;
  color: #71817a;
  font-size: 13px;
}

.replace-search {
  width: 100%;
}

.suggestion-row {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.suggestion-addr {
  color: #71817a;
  font-size: 12px;
}
</style>

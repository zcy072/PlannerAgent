<template>
  <div class="day-panel">
    <!-- 路线优化摘要 -->
    <div v-if="day.route_summary" class="route-summary">
      <div class="route-metric">
        <el-icon><MapLocation /></el-icon>
        <span>预估路线距离 {{ day.route_summary.estimated_distance_km }} km</span>
      </div>
      <div v-if="day.route_summary.estimated_duration_min != null" class="route-metric">
        <el-icon><Clock /></el-icon>
        <span>预估通行时间 {{ day.route_summary.estimated_duration_min }} 分钟</span>
      </div>
      <div class="route-reason">{{ day.route_summary.optimization_reason }}</div>
    </div>

    <!-- 景点列表 -->
    <el-timeline>
      <el-timeline-item
        v-for="(attraction, attrIndex) in day.attractions"
        :key="attrIndex"
        placement="top"
      >
        <el-card class="spot-card">
          <div class="spot-header">
            <h4>{{ attraction.name }}</h4>
            <div class="spot-header-right">
              <el-tag v-if="attraction.category" size="small">{{ attraction.category }}</el-tag>
              <el-dropdown trigger="click" :disabled="actionsDisabled" @command="(cmd: string) => emit('attractionCommand', cmd, attrIndex)">
                <el-button text size="small" class="item-menu-btn" :aria-label="`${attraction.name} 的更多操作`">
                  <el-icon><MoreFilled /></el-icon>
                </el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="replace">替换为...</el-dropdown-item>
                    <el-dropdown-item
                      command="delete"
                      :disabled="day.attractions.length <= 1"
                    >
                      删除
                    </el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
          </div>
          <div class="spot-info">
            <p v-if="attraction.description">{{ attraction.description }}</p>
            <div class="spot-details">
              <span v-if="attraction.visit_duration">
                <el-icon><Clock /></el-icon>
                游玩时长: {{ attraction.visit_duration }}分钟
              </span>
              <span v-if="attraction.ticket_price">
                <el-icon><Ticket /></el-icon>
                门票约 ¥{{ attraction.ticket_price }}（估算）
              </span>
            </div>
            <div class="spot-address">
              <el-icon><Location /></el-icon>
              {{ attraction.address }}
            </div>
          </div>
        </el-card>
      </el-timeline-item>
    </el-timeline>

    <!-- 餐饮推荐 -->
    <div v-if="day.meals && day.meals.length > 0" class="dining-section">
      <h4><el-icon><Food /></el-icon> 餐饮推荐</h4>
      <el-row :gutter="12">
        <el-col v-for="(meal, mealIndex) in day.meals" :key="mealIndex" :span="8">
          <el-card shadow="hover" class="meal-card">
            <div class="meal-type">{{ mealTypeLabel(meal.type) }}</div>
            <div class="meal-name">{{ meal.name }}</div>
            <div class="meal-cuisine">{{ meal.description }}</div>
            <div v-if="meal.estimated_cost" class="meal-price">人均估算: ¥{{ meal.estimated_cost }}</div>
          </el-card>
        </el-col>
      </el-row>
    </div>

    <div class="dining-section nearby-dining">
      <h4><el-icon><Food /></el-icon> 景点附近真实餐馆</h4>
      <p class="nearby-note">高德周边搜索结果；标注价格为人均消费参考，不等于菜单价。</p>
      <div v-if="restaurantsLoading" class="nearby-note">正在搜索附近餐馆…</div>
      <div v-else-if="restaurantsError" class="nearby-note">{{ restaurantsError }}</div>
      <div v-else-if="restaurants?.length" class="restaurant-list">
        <el-card v-for="restaurant in restaurants" :key="restaurant.id || `${restaurant.name}-${restaurant.address}`" shadow="hover" class="restaurant-card">
          <strong>{{ restaurant.name }}</strong>
          <div class="nearby-note">{{ restaurant.address || '地址暂无' }}</div>
          <div class="nearby-meta">
            <span v-if="restaurant.rating">评分 {{ restaurant.rating }}</span>
            <span v-if="restaurant.cost_reference">人均约 ¥{{ restaurant.cost_reference }}</span>
            <span v-if="restaurant.distance_m != null">{{ restaurant.distance_m }} 米</span>
          </div>
        </el-card>
      </div>
      <div v-else class="nearby-note">附近暂未找到餐馆信息。</div>
    </div>

    <!-- 住宿信息 -->
    <div v-if="day.hotel" class="accommodation-section">
      <h4><el-icon><House /></el-icon> 住宿安排</h4>
      <el-card shadow="hover">
        <div class="hotel-info">
          <div class="hotel-header">
            <div class="hotel-name">{{ day.hotel.name }}</div>
            <el-button text size="small" :disabled="actionsDisabled" @click="emit('replaceHotel')">
              <el-icon><Refresh /></el-icon>
              替换
            </el-button>
          </div>
          <div class="hotel-details">
            <span v-if="day.hotel.type">{{ day.hotel.type }}</span>
            <span v-if="day.hotel.price_range">{{ day.hotel.price_range }}</span>
            <span>{{ day.hotel.estimated_cost ? `每晚约 ¥${day.hotel.estimated_cost}（参考）` : '每晚价格待核实' }}</span>
            <span v-if="day.hotel.rating">
              <el-icon><Star /></el-icon>
              {{ day.hotel.rating }}
            </span>
          </div>
          <div v-if="day.hotel.address" class="hotel-address">
            <el-icon><Location /></el-icon>
            {{ day.hotel.address }}
          </div>
        </div>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import {
  ElButton, ElCard, ElCol, ElDropdown, ElDropdownItem, ElDropdownMenu,
  ElIcon, ElRow, ElTag, ElTimeline, ElTimelineItem,
} from 'element-plus'
import {
  Clock, Food, House, Location, MapLocation, MoreFilled, Refresh, Star, Ticket,
} from '@element-plus/icons-vue'
import type { DayPlan, POI } from '@/types'

defineProps<{
  day: DayPlan
  restaurants?: POI[]
  restaurantsLoading?: boolean
  restaurantsError?: string
  /** 修改中、对话中或导出 PDF 时禁用编辑入口 */
  actionsDisabled?: boolean
}>()

const emit = defineEmits<{
  attractionCommand: [command: string, attrIndex: number]
  replaceHotel: []
}>()

const MEAL_TYPE_LABELS: Record<string, string> = {
  breakfast: '早餐',
  lunch: '午餐',
  dinner: '晚餐'
}
const mealTypeLabel = (type: string) => MEAL_TYPE_LABELS[type] ?? type ?? '用餐'
</script>

<style scoped>
.route-summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  padding: 11px 12px;
  margin-bottom: 16px;
  background: #eef7f1;
  border: 1px solid #d5e8da;
  border-radius: 6px;
  color: #32643c;
  font-size: 13px;
}

.route-metric {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 700;
}

.route-reason {
  color: #5b7560;
}

.spot-card {
  margin-bottom: 10px;
  box-shadow: none;
}

.spot-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}

.spot-header h4 {
  margin: 0;
  color: var(--trip-heading);
  font-size: 16px;
}

.spot-header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.item-menu-btn {
  padding: 4px;
  color: #71817a;
}

.item-menu-btn:hover {
  color: var(--trip-primary);
}

.spot-info p {
  color: #5f6f69;
  line-height: 1.7;
  margin-bottom: 8px;
}

.spot-details {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  color: #728079;
  font-size: 13px;
}

.spot-details span,
.hotel-address {
  display: flex;
  align-items: center;
  gap: 4px;
}

.spot-address {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 8px;
  color: #5f6f69;
  font-size: 13px;
}

.dining-section,
.accommodation-section {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid #edf1ee;
}

.dining-section h4,
.accommodation-section h4 {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 12px;
  color: #243b35;
}

.meal-card {
  min-height: 118px;
  text-align: center;
  box-shadow: none;
}

.meal-type {
  font-size: 12px;
  color: #7a8982;
}

.meal-name {
  color: var(--trip-heading);
  font-weight: 700;
  margin: 6px 0;
}

.meal-cuisine {
  color: #5f6f69;
  font-size: 13px;
  line-height: 1.45;
}

.meal-price {
  color: #ad4d35;
  font-size: 12px;
  margin-top: 6px;
}

.nearby-note {
  margin: 6px 0 10px;
  color: #728079;
  font-size: 12px;
  line-height: 1.5;
}

.restaurant-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 10px;
}

.restaurant-card {
  box-shadow: none;
}

.nearby-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 8px;
  color: #5f6f69;
  font-size: 12px;
}

.hotel-info {
  padding: 4px;
}

.hotel-name {
  color: var(--trip-heading);
  font-weight: 700;
  font-size: 16px;
  margin-bottom: 8px;
}

.hotel-details {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  color: #728079;
  font-size: 13px;
  margin-bottom: 8px;
}

.hotel-address {
  color: #5f6f69;
  font-size: 13px;
}

.hotel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.hotel-header .hotel-name {
  margin-bottom: 0;
}
</style>

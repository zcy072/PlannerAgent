<template>
  <el-card shadow="hover">
    <template #header>
      <div class="card-header">
        <el-icon><Wallet /></el-icon>
        <span>预算估算</span>
      </div>
    </template>
    <el-row :gutter="20" class="budget-row">
      <el-col :span="6">
        <el-statistic title="景点门票估算" :value="budget.total_attractions" suffix="元" />
      </el-col>
      <el-col :span="6">
        <el-statistic title="住宿费用" :value="budget.total_hotels" suffix="元" />
      </el-col>
      <el-col :span="6">
        <el-statistic title="餐饮费用" :value="budget.total_meals" suffix="元" />
      </el-col>
      <el-col :span="6">
        <el-statistic title="交通费用" :value="budget.total_transportation" suffix="元" />
      </el-col>
    </el-row>
    <el-divider />
    <el-statistic title="总预算" :value="budget.total" suffix="元" class="total-budget" />
    <p class="budget-note">按单人估算。未查到门票数据的景点不计入门票费用，不表示免费；交通优先使用模型金额，否则按路线距离估算，不含机场接送等费用，出发前请核实。</p>
  </el-card>
</template>

<script setup lang="ts">
import { ElCard, ElCol, ElDivider, ElIcon, ElRow, ElStatistic } from 'element-plus'
import { Wallet } from '@element-plus/icons-vue'
import type { Budget } from '@/types'

defineProps<{ budget: Budget }>()
</script>

<style scoped>
.budget-row {
  text-align: center;
}

.total-budget {
  text-align: center;
}

.total-budget :deep(.el-statistic__number) {
  color: var(--trip-primary);
  font-size: 34px;
  font-weight: 700;
}

.budget-note {
  margin: 6px 0 10px;
  color: #728079;
  font-size: 12px;
  line-height: 1.5;
}
</style>

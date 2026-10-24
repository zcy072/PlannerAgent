<template>
  <el-card shadow="hover">
    <template #header>
      <div class="card-header">
        <el-icon><Sunny /></el-icon>
        <span>出行天气</span>
      </div>
    </template>
    <div class="weather-list">
      <div v-for="weather in weatherInfo" :key="weather.date" class="weather-item">
        <strong>{{ weather.date }}</strong>
        <span>{{ weather.day_weather || '未知' }} / {{ weather.night_weather || '未知' }}</span>
        <span v-if="hasWeatherForecast(weather)">{{ weather.day_temp }}°C / {{ weather.night_temp }}°C</span>
        <span v-else>暂无温度</span>
        <el-tag v-if="!weather.day_weather || weather.day_weather === '未知'" type="warning" size="small">暂无匹配预报</el-tag>
        <el-tag v-else-if="weather.source" type="info" size="small">数据来自 {{ weather.source }}</el-tag>
      </div>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { ElCard, ElIcon, ElTag } from 'element-plus'
import { Sunny } from '@element-plus/icons-vue'
import type { Weather } from '@/types'

defineProps<{ weatherInfo: Weather[] }>()

// Dates without a forecast are stored with "未知" placeholders, not real numbers.
const hasWeatherForecast = (weather: Weather) =>
  !!weather.day_weather && weather.day_weather !== '未知'
</script>

<style scoped>
.weather-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 8px 18px;
}

.weather-item {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 10px;
  min-width: 0;
  color: #52645b;
  font-size: 13px;
}

.weather-item strong {
  color: #263a34;
}
</style>

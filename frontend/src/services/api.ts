import axios from 'axios'
import { readTripStream } from './tripStream'
import { formatErrorDetail } from '@/utils/errors'
import type {
  TripRequest,
  StreamingResponse,
  ItemModifyRequest,
  ItemModifyResponse,
  ChatRequest,
  ChatResponse,
  Hotel,
  MapSearchResponse,
  POI,
  TripPlan
} from '@/types'

// 开发环境通过 Vite 代理访问；生产环境直接使用同一个环境变量。
const API_BASE_URL = import.meta.env.DEV ? '' : (import.meta.env.VITE_API_BASE_URL || '')

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 300000, // 5分钟
  headers: {
    'Content-Type': 'application/json'
  }
})

// 响应拦截器 - 统一打印错误日志
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    console.error('API Response Error:', error.response?.status, error.response?.data || error.message)
    return Promise.reject(error)
  }
)

// ===========================================
// 旅行规划 API
// ===========================================

/**
 * 读取已持久化的行程，用于结果页刷新后的恢复。
 */
export async function getSavedTripPlan(sessionId: string): Promise<{ success: boolean; data: TripPlan }> {
  const response = await apiClient.get(`/api/trip/result/${encodeURIComponent(sessionId)}`)
  return response.data
}

async function streamTripEndpoint(
  path: string,
  init: RequestInit,
  onMessage: (data: StreamingResponse) => void
): Promise<void> {
  const response = await fetch(`${API_BASE_URL}${path}`, init)

  if (!response.ok) {
    let detail = ''
    try {
      const payload = await response.json() as { detail?: unknown }
      detail = formatErrorDetail(payload.detail)
    } catch {
      // The server may return a non-JSON proxy error.
    }
    throw new Error(detail || `请求失败（HTTP ${response.status}）`)
  }

  await readTripStream(response.body, onMessage)
}

/**
 * 创建旅行计划（流式）
 */
export function createTripPlanStream(
  request: TripRequest,
  onMessage: (data: StreamingResponse) => void,
  signal?: AbortSignal
): Promise<void> {
  return streamTripEndpoint('/api/trip/plan/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
    signal
  }, onMessage)
}

/**
 * 从失败的节点继续规划（流式），不重复已完成的步骤
 */
export function resumeTripPlanStream(
  sessionId: string,
  onMessage: (data: StreamingResponse) => void,
  signal?: AbortSignal
): Promise<void> {
  return streamTripEndpoint(`/api/trip/plan/resume/${encodeURIComponent(sessionId)}`, {
    method: 'POST',
    signal
  }, onMessage)
}

/**
 * 局部修改行程项目（删除/替换景点或酒店）
 */
export async function modifyTripItem(payload: ItemModifyRequest): Promise<ItemModifyResponse> {
  const response = await apiClient.post('/api/trip/items/modify', payload)
  return response.data
}

/**
 * 多轮对话式行程修改
 */
export async function chatWithTrip(payload: ChatRequest): Promise<ChatResponse> {
  const response = await apiClient.post('/api/trip/chat', payload)
  return response.data
}

// ===========================================
// 地图服务 API
// ===========================================

/**
 * 搜索 POI
 */
export async function searchPOI(keywords: string, city: string): Promise<MapSearchResponse<POI>> {
  const response = await apiClient.get('/api/map/poi', { params: { keywords, city } })
  return response.data
}

/**
 * 搜索酒店
 */
export async function searchHotels(city: string, hotelType: string = '酒店'): Promise<MapSearchResponse<Hotel>> {
  const response = await apiClient.get('/api/map/hotels', { params: { city, hotel_type: hotelType } })
  return response.data
}

export async function searchNearbyRestaurants(
  longitude: number,
  latitude: number,
): Promise<MapSearchResponse<POI>> {
  const response = await apiClient.get('/api/map/nearby-restaurants', {
    params: { longitude, latitude },
  })
  return response.data
}

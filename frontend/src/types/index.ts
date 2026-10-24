// TypeScript 类型定义

export interface Location {
  longitude: number
  latitude: number
}

export interface POI {
  id: string
  name: string
  address: string
  location?: Location | null
  category?: string | null
  rating?: number | null
  tel?: string | null
  ticket_price?: number | null
  cost_reference?: number | null
  distance_m?: number | null
  visit_duration?: number | null
  description?: string | null
  image_url?: string | null
}

export interface Weather {
  date: string
  day_weather: string
  night_weather: string
  day_temp: number | string
  night_temp: number | string
  wind_direction: string
  wind_power: string
  /** 非高德来源时标注（如 Open-Meteo），空表示高德 */
  source?: string
}

export interface Hotel {
  name: string
  address: string
  location?: Location | null
  price_range: string
  rating: string
  type: string
  estimated_cost: number
}

/** `/api/map/poi`、`/hotels`、`/nearby-restaurants` 的统一响应外壳 */
export interface MapSearchResponse<T> {
  success: boolean
  data: T[]
}

export interface Meal {
  /** 常见值为 breakfast / lunch / dinner；后端是普通字符串，LLM 可能给出其他值 */
  type: string
  name: string
  address?: string | null
  description?: string | null
  estimated_cost: number
}

export interface Attraction {
  name: string
  address: string
  location: Location
  visit_duration: number
  description: string
  category?: string | null
  ticket_price: number
}

export interface RouteSummary {
  estimated_distance_km: number
  estimated_duration_min?: number | null
  optimization_reason: string
}

export interface DayPlan {
  date: string
  day_index: number
  description: string
  transportation: string
  accommodation: string
  hotel?: Hotel | null
  attractions: Attraction[]
  meals: Meal[]
  route_summary?: RouteSummary | null
}

export interface Budget {
  total_attractions: number
  total_hotels: number
  total_meals: number
  total_transportation: number
  transportation_is_estimated?: boolean
  total: number
}

export interface EvaluatorReport {
  passed: boolean
  issues: string[]
  suggestions: string[]
  day_issues: Record<string, string[]>
  global_issues: string[]
  non_retryable_issues: string[]
}

export interface TripPlan {
  city: string
  start_date: string
  end_date: string
  days: DayPlan[]
  weather_info: Weather[]
  overall_suggestions: string
  budget?: Budget | null
  pois_pool?: POI[]
  hotels_pool?: Hotel[]
  evaluator_report?: EvaluatorReport | null
  evaluator_warnings?: string[]
  node_traces?: NodeTrace[]
}

export interface TripRequest {
  session_id?: string
  city: string
  start_date: string
  end_date: string
  travel_days: number
  transportation: string
  accommodation: string
  preferences: string[]
  free_text_input?: string
  llm_provider?: LlmProvider
  budget?: [number, number]
}

/** 与后端 `LLMProvider` 枚举一致。 */
export type LlmProvider = 'deepseek' | 'aliyun' | 'openai'

export type TripStatus = 'pending' | 'running' | 'completed' | 'failed'

export interface AgentStep {
  node: string
  status: string
  message?: string
  error?: string
  data?: Record<string, unknown>
}

export interface NodeTrace {
  node: string
  status: string
  duration_ms: number
  summary: string
}

export interface StreamingEventData {
  [key: string]: unknown
  itinerary?: TripPlan
  pois_count?: number
  hotels_count?: number
  weather_days?: number
  route_days?: number
  plan_generated?: boolean
  days_count?: number
  retry_count?: number
  evaluator_report?: EvaluatorReport
  evaluator_issues?: string[]
  persistence_saved?: boolean
  persistence_warning?: string
  resumable?: boolean
}

export interface StreamingResponse {
  session_id: string
  step: number
  node: string
  status: TripStatus
  message: string
  data?: StreamingEventData | null
}

export interface ItemModifyRequest {
  session_id: string
  day_index: number
  item_type: 'attraction' | 'hotel'
  item_index?: number
  action: 'delete' | 'replace'
  replacement?: POI | Hotel | Attraction | Record<string, unknown> | null
  llm_provider?: LlmProvider | null
}

export interface ItemModifyResponse {
  success: boolean
  message: string
  trip_plan?: TripPlan | null
  changed_day_index?: number | null
  persistence_saved: boolean
  persistence_warning?: string | null
}

export interface ChatRequest {
  session_id: string
  message: string
  llm_provider?: LlmProvider | null
}

export type ChatAction = 'delete' | 'replace' | 'answer' | 'clarify'

export interface ChatResponse {
  success: boolean
  reply: string
  trip_plan?: TripPlan | null
  modified: boolean
  action: ChatAction
  persistence_saved?: boolean | null
  persistence_warning?: string | null
}

export interface ChatTurn {
  role: 'user' | 'assistant'
  content: string
  modified?: boolean
  action?: ChatAction
}

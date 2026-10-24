import { defineStore } from 'pinia'
import { ref } from 'vue'
import type {
  TripPlan,
  ItemModifyRequest,
  ItemModifyResponse,
  ChatTurn,
  ChatResponse,
  LlmProvider
} from '@/types'
import { modifyTripItem, chatWithTrip } from '@/services/api'
import { parseLlmProvider } from '@/utils/llmProvider'

const SESSION_STORAGE_KEY = 'trip-planner:last-session-id'
const PROVIDER_STORAGE_KEY = 'trip-planner:llm-provider'

export const useTripStore = defineStore('trip', () => {
  const sessionId = ref<string>('')
  // 首页所选 LLM 供应商；聊天与局部修改沿用它，刷新后从 sessionStorage 恢复。
  const llmProvider = ref<LlmProvider | ''>('')
  const tripPlan = ref<TripPlan | null>(null)
  const modifying = ref(false)
  const chatHistory = ref<ChatTurn[]>([])
  const chatting = ref(false)

  function setSessionId(id: string) {
    if (id !== sessionId.value) {
      // 换会话时旧行程和对话不能带进新会话。
      tripPlan.value = null
      chatHistory.value = []
      modifying.value = false
      chatting.value = false
    }
    sessionId.value = id
    try {
      if (id) {
        window.sessionStorage.setItem(SESSION_STORAGE_KEY, id)
      }
    } catch {
      // Storage can be unavailable in privacy-restricted browser contexts.
    }
  }

  function setLlmProvider(provider: string | null | undefined) {
    const valid = parseLlmProvider(provider)
    llmProvider.value = valid || ''
    try {
      if (valid) {
        window.sessionStorage.setItem(PROVIDER_STORAGE_KEY, valid)
      } else {
        window.sessionStorage.removeItem(PROVIDER_STORAGE_KEY)
      }
    } catch {
      // Storage can be unavailable in privacy-restricted browser contexts.
    }
  }

  function currentLlmProvider(): LlmProvider | undefined {
    if (llmProvider.value) return llmProvider.value
    try {
      return parseLlmProvider(window.sessionStorage.getItem(PROVIDER_STORAGE_KEY))
    } catch {
      return undefined
    }
  }

  function getStoredSessionId(): string {
    try {
      return window.sessionStorage.getItem(SESSION_STORAGE_KEY) || ''
    } catch {
      return ''
    }
  }

  function setTripPlan(plan: TripPlan | null) {
    tripPlan.value = plan
  }

  function reset() {
    sessionId.value = ''
    llmProvider.value = ''
    tripPlan.value = null
    modifying.value = false
    chatHistory.value = []
    chatting.value = false
    try {
      window.sessionStorage.removeItem(SESSION_STORAGE_KEY)
      window.sessionStorage.removeItem(PROVIDER_STORAGE_KEY)
    } catch {
      // Storage can be unavailable in privacy-restricted browser contexts.
    }
  }

  async function modifyItem(payload: Omit<ItemModifyRequest, 'session_id'>): Promise<ItemModifyResponse> {
    if (!sessionId.value) {
      throw new Error('当前没有可修改的行程会话')
    }
    const sid = sessionId.value
    modifying.value = true
    try {
      const res = await modifyTripItem({
        llm_provider: currentLlmProvider(),
        ...payload,
        session_id: sid
      })
      // 请求期间会话已切换或被重置时，丢弃结果，避免写进别的会话。
      if (sid === sessionId.value && res.success && res.trip_plan) {
        tripPlan.value = res.trip_plan
      }
      return res
    } finally {
      if (sid === sessionId.value) modifying.value = false
    }
  }

  async function sendChat(message: string, provider?: LlmProvider): Promise<ChatResponse> {
    if (!sessionId.value) {
      throw new Error('当前没有可对话的行程会话')
    }
    const trimmed = message.trim()
    if (!trimmed) {
      throw new Error('消息不能为空')
    }
    const sid = sessionId.value
    chatHistory.value.push({ role: 'user', content: trimmed })
    chatting.value = true
    try {
      const res = await chatWithTrip({
        session_id: sid,
        message: trimmed,
        llm_provider: provider || currentLlmProvider()
      })
      if (sid === sessionId.value) {
        chatHistory.value.push({
          role: 'assistant',
          content: res.reply,
          modified: res.modified,
          action: res.action
        })
        if (res.modified && res.trip_plan) {
          tripPlan.value = res.trip_plan
        }
      }
      return res
    } catch (err) {
      if (sid === sessionId.value) {
        chatHistory.value.push({
          role: 'assistant',
          content: '抱歉，处理失败，请稍后重试。',
          action: 'answer'
        })
      }
      throw err
    } finally {
      if (sid === sessionId.value) chatting.value = false
    }
  }

  return {
    sessionId,
    llmProvider,
    tripPlan,
    modifying,
    chatHistory,
    chatting,
    setSessionId,
    setLlmProvider,
    getStoredSessionId,
    setTripPlan,
    reset,
    modifyItem,
    sendChat
  }
})

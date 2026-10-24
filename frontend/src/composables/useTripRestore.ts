import { onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { getSavedTripPlan } from '@/services/api'
import { useTripStore } from '@/stores/trip'
import { getErrorMessage } from '@/utils/errors'

/**
 * 刷新或直接打开结果页时，按 session id 从后端恢复已保存的行程。
 * 用请求序号丢弃迟到的返回：切换 session、点击"重新规划"或离开页面后，旧请求不得再写 store 或弹提示。
 */
export function useTripRestore() {
  const route = useRoute()
  const tripStore = useTripStore()
  const isRestoring = ref(false)
  let requestId = 0

  function routeSessionId(): string {
    const value = route.query.session_id
    return typeof value === 'string' ? value : ''
  }

  /** 作废正在进行的恢复请求。 */
  function cancel() {
    requestId++
    isRestoring.value = false
  }

  async function restore() {
    const sessionId = routeSessionId() || tripStore.sessionId || tripStore.getStoredSessionId()
    if (!sessionId) return
    if (tripStore.sessionId === sessionId && tripStore.tripPlan) return

    const current = ++requestId
    tripStore.setTripPlan(null)
    isRestoring.value = true
    try {
      const response = await getSavedTripPlan(sessionId)
      if (current !== requestId) return
      tripStore.setSessionId(sessionId)
      tripStore.setTripPlan(response.data)
    } catch (error: unknown) {
      if (current !== requestId) return
      ElMessage.warning(getErrorMessage(error, '未能恢复已保存的行程'))
    } finally {
      if (current === requestId) isRestoring.value = false
    }
  }

  watch(() => route.query.session_id, restore)
  onBeforeUnmount(cancel)

  return { isRestoring, restore, cancel }
}

import { ref } from 'vue'
import { searchNearbyRestaurants } from '@/services/api'
import { mergeNearbyRestaurants, pickSearchCenters } from '@/utils/restaurants'
import type { POI, TripPlan } from '@/types'

/** 按天懒加载景点附近的餐馆；行程变化后用代数计数器丢弃旧请求的返回。 */
export function useNearbyRestaurants(getTripPlan: () => TripPlan | null | undefined) {
  const restaurants = ref<Record<number, POI[]>>({})
  const loading = ref<Record<number, boolean>>({})
  const errors = ref<Record<number, string>>({})
  let generation = 0
  // Callers (day expansion, PDF export) share one request per day and can await it.
  const inflight = new Map<number, Promise<void>>()

  function load(dayIndex: number): Promise<void> {
    const pending = inflight.get(dayIndex)
    if (pending) return pending
    const request = fetchDay(dayIndex).finally(() => {
      if (inflight.get(dayIndex) === request) inflight.delete(dayIndex)
    })
    inflight.set(dayIndex, request)
    return request
  }

  async function fetchDay(dayIndex: number) {
    if (restaurants.value[dayIndex]) return
    const day = getTripPlan()?.days?.[dayIndex]
    if (!day?.attractions?.length) return

    const centers = pickSearchCenters(day.attractions)
    if (!centers.length) {
      errors.value[dayIndex] = '景点坐标不可用，无法搜索附近餐馆。'
      return
    }

    const requestGeneration = generation
    loading.value[dayIndex] = true
    errors.value[dayIndex] = ''
    try {
      const responses = await Promise.all(
        centers.map((center) => searchNearbyRestaurants(center.longitude, center.latitude)),
      )
      if (requestGeneration !== generation) return
      restaurants.value[dayIndex] = mergeNearbyRestaurants(
        responses.map((response) => response.data || []),
      )
    } catch {
      if (requestGeneration === generation) errors.value[dayIndex] = '附近餐馆搜索暂不可用。'
    } finally {
      if (requestGeneration === generation) loading.value[dayIndex] = false
    }
  }

  function loadMany(dayIndexes: Array<string | number>): Promise<void> {
    return Promise.all(dayIndexes.map((dayIndex) => load(Number(dayIndex)))).then(() => undefined)
  }

  /** 行程被替换或修改后调用：清空缓存，并让仍在途的旧请求失效。 */
  function reset() {
    generation += 1
    inflight.clear()
    restaurants.value = {}
    loading.value = {}
    errors.value = {}
  }

  return { restaurants, loading, errors, load, loadMany, reset }
}

import type { POI } from '@/types'
import { getMapCoordinates } from './coordinates'

const MAX_LOCATIONS_PER_DAY = 3
const MAX_RESTAURANTS_PER_DAY = 12

interface Point {
  longitude: number
  latitude: number
}

/** 取当天前几个坐标有效的景点，作为周边餐馆搜索的中心点。 */
export function pickSearchCenters(
  attractions: Array<{ location?: Partial<Point> | null }> | undefined,
): Point[] {
  const centers: Point[] = []
  for (const attraction of attractions || []) {
    const coordinates = getMapCoordinates(attraction.location)
    if (coordinates) {
      centers.push({ longitude: coordinates[0], latitude: coordinates[1] })
    }
    if (centers.length >= MAX_LOCATIONS_PER_DAY) break
  }
  return centers
}

/** 合并多个中心点的搜索结果：按 id（缺失时按名称+地址）去重，距离近的在前。 */
export function mergeNearbyRestaurants(results: POI[][]): POI[] {
  const seen = new Set<string>()
  return results
    .flat()
    .filter((restaurant) => {
      const key = restaurant.id || `${restaurant.name}|${restaurant.address}`
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
    .sort(
      (left, right) =>
        (left.distance_m ?? Number.MAX_SAFE_INTEGER) - (right.distance_m ?? Number.MAX_SAFE_INTEGER),
    )
    .slice(0, MAX_RESTAURANTS_PER_DAY)
}

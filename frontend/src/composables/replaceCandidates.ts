import type { Attraction, DayPlan, Hotel, POI, TripPlan } from '@/types'

type NamedAddress = Pick<Attraction, 'name' | 'address'>

function itemKey(item: NamedAddress): string {
  return `${item.name}|${item.address}`
}

function collectKeys(
  plan: TripPlan | null | undefined,
  picker: (day: DayPlan) => Array<NamedAddress | null | undefined>,
): Set<string> {
  const keys = new Set<string>()
  plan?.days?.forEach((day) => {
    picker(day).forEach((item) => {
      if (item) keys.add(itemKey(item))
    })
  })
  return keys
}

export function pickPoiCandidates(plan: TripPlan | null | undefined, target: Attraction): POI[] {
  const pool = plan?.pois_pool || []
  if (pool.length === 0) return []

  const usedKeys = collectKeys(plan, (d) => d.attractions || [])
  const targetKey = itemKey(target)
  const filteredPool = pool.filter((p) => {
    const key = itemKey(p)
    return key !== targetKey && !usedKeys.has(key)
  })

  const sameCategory = target.category
    ? filteredPool.filter((p) => p.category === target.category)
    : []
  const result: POI[] = [...sameCategory]

  if (result.length < 3 && target.category) {
    const partial = filteredPool.filter(
      (p) => !result.includes(p) && p.category && p.category.includes(target.category!.slice(0, 2)),
    )
    result.push(...partial)
  }

  if (result.length < 3) {
    const rest = filteredPool
      .filter((p) => !result.includes(p))
      .sort((a, b) => (b.rating || 0) - (a.rating || 0))
    result.push(...rest)
  }

  return result.slice(0, 3)
}

export function pickHotelCandidates(plan: TripPlan | null | undefined, target: Hotel): Hotel[] {
  const pool = plan?.hotels_pool || []
  if (pool.length === 0) return []

  const usedKeys = collectKeys(plan, (d) => (d.hotel ? [d.hotel] : []))
  const targetKey = itemKey(target)
  const filteredPool = pool.filter((h) => {
    const key = itemKey(h)
    return key !== targetKey && !usedKeys.has(key)
  })

  const sameType = target.type ? filteredPool.filter((h) => h.type === target.type) : []
  const result: Hotel[] = [...sameType]
  if (result.length < 3) {
    const rest = filteredPool.filter((h) => !result.includes(h))
    result.push(...rest)
  }
  return result.slice(0, 3)
}

export type MapCoordinates = [longitude: number, latitude: number]

export function getMapCoordinates(location: unknown): MapCoordinates | null {
  if (typeof location !== 'object' || location === null) return null

  const value = location as Record<string, unknown>
  const longitude = value.longitude ?? value.lng
  const latitude = value.latitude ?? value.lat
  if (typeof longitude !== 'number' || !Number.isFinite(longitude) ||
      typeof latitude !== 'number' || !Number.isFinite(latitude)) return null
  if (longitude < -180 || longitude > 180 || latitude < -90 || latitude > 90) return null

  return [longitude, latitude]
}

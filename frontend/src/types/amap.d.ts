// 高德 JS API 1.4.15 的最小类型声明，只覆盖 composables/useTripMap.ts 实际用到的接口。
// 不是官方类型：新增用法时在这里补，而不是退回 any。

export type AMapLngLat = [number, number]

export interface AMapLocationObject {
  lng: number
  lat: number
}

export interface AMapMarker {
  on(event: 'click', handler: () => void): void
  getPosition(): AMapLngLat
}

export interface AMapInfoWindow {
  setContent(content: HTMLElement | string): void
  open(map: AMapMap, position: AMapLngLat): void
  close(): void
}

export interface AMapMap {
  add(marker: AMapMarker): void
  clearMap(): void
  setFitView(overlays: AMapMarker[]): void
  addControl(control: unknown): void
  destroy(): void
}

export interface AMapGeocodeResult {
  geocodes?: Array<{ location: AMapLocationObject }>
}

export interface AMapGeocoder {
  getLocation(
    address: string,
    callback: (status: string, result: AMapGeocodeResult) => void,
  ): void
}

export interface AMapNamespace {
  Map: new (
    container: HTMLElement,
    options: { resizeEnable: boolean; zoom: number; center: AMapLngLat },
  ) => AMapMap
  Marker: new (options: { position: AMapLngLat; title: string }) => AMapMarker
  InfoWindow: new (options: { offset: unknown }) => AMapInfoWindow
  Pixel: new (x: number, y: number) => unknown
  Geocoder: new () => AMapGeocoder
  Scale: new () => unknown
  ToolBar: new () => unknown
  plugin(names: string[], callback: () => void): void
}

declare global {
  interface Window {
    AMap?: AMapNamespace
  }
}

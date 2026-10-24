import { nextTick, onBeforeUnmount, ref, watch, type Ref } from 'vue'
import type { TripPlan } from '@/types'
import type {
  AMapGeocodeResult,
  AMapInfoWindow,
  AMapLngLat,
  AMapMap,
  AMapMarker,
} from '@/types/amap'
import { getMapCoordinates } from '@/utils/coordinates'

// One SDK load is shared by every caller; repeated initMap/retry must not inject duplicate scripts.
let sdkPromise: Promise<void> | null = null

function loadAmapSdk(key: string): Promise<void> {
  if (window.AMap) return Promise.resolve()
  if (sdkPromise) return sdkPromise
  sdkPromise = new Promise<void>((resolve, reject) => {
    const script = document.createElement('script')
    script.dataset.tripAmap = 'true'
    script.src = `https://webapi.amap.com/maps?v=1.4.15&key=${key}&plugin=AMap.Geocoder,AMap.Scale,AMap.ToolBar`
    script.onload = () => resolve()
    script.onerror = (event) => {
      script.remove()
      sdkPromise = null
      reject(event)
    }
    document.head.appendChild(script)
  })
  return sdkPromise
}

/** Drop a stalled SDK load so the next attempt starts from a fresh script tag. */
function resetStalledSdkLoad() {
  if (window.AMap) return
  document.head.querySelector('script[data-trip-amap="true"]')?.remove()
  sdkPromise = null
}

export function useTripMap(tripPlan: Ref<TripPlan | null>, containerRef: Ref<HTMLElement | null>) {
  const mapInstance = ref<AMapMap | null>(null)
  const mapLoaded = ref(false)
  const mapStatus = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const mapError = ref('')
  const mapErrorRetryable = ref(true)
  let disposed = false
  let attempt = 0
  let loadTimer: ReturnType<typeof setTimeout> | null = null
  let infoWindow: AMapInfoWindow | null = null

  function closeInfoWindow() {
    try {
      infoWindow?.close()
    } catch {
      // The window may already be detached from a destroyed map.
    }
  }

  function clearLoadTimer() {
    if (loadTimer) {
      clearTimeout(loadTimer)
      loadTimer = null
    }
  }

  function setMapError(message: string, retryable = true) {
    clearLoadTimer()
    mapLoaded.value = false
    mapStatus.value = 'error'
    mapError.value = message
    mapErrorRetryable.value = retryable
  }

  function createInfoWindowContent(name: unknown, address: unknown): HTMLDivElement {
    const content = document.createElement('div')
    content.style.padding = '10px'

    const title = document.createElement('strong')
    title.textContent = typeof name === 'string' ? name : ''

    const addressLine = document.createElement('div')
    addressLine.textContent = typeof address === 'string' ? address : ''

    content.append(title, addressLine)
    return content
  }

  function redrawMarkers() {
    const AMap = window.AMap
    if (!AMap || !mapInstance.value || !tripPlan.value) return

    const map = mapInstance.value
    closeInfoWindow()
    try {
      map.clearMap()
    } catch {
      // The map may already be tearing down during route navigation.
    }

    const markers: AMapMarker[] = []
    tripPlan.value.days?.forEach((day) => {
      day.attractions?.forEach((attraction) => {
        const coordinates = getMapCoordinates(attraction.location)
        if (!coordinates) return

        const marker = new AMap.Marker({
          position: coordinates,
          title: attraction.name,
        })
        marker.on('click', () => {
          // Reuse one window so clicking around never stacks open popups.
          if (!infoWindow) infoWindow = new AMap.InfoWindow({ offset: new AMap.Pixel(0, -30) })
          infoWindow.setContent(createInfoWindowContent(attraction.name, attraction.address))
          infoWindow.open(map, marker.getPosition())
        })
        markers.push(marker)
        map.add(marker)
      })
    })

    if (markers.length > 0) {
      map.setFitView(markers)
    }
  }

  function destroyMap() {
    disposed = true
    attempt += 1
    clearLoadTimer()
    closeInfoWindow()
    infoWindow = null
    const map = mapInstance.value
    mapInstance.value = null
    mapLoaded.value = false

    if (!map || typeof map.destroy !== 'function') return
    try {
      map.destroy()
    } catch {
      // AMap may already have released the instance during navigation.
    }
  }

  function refreshMapMarkers() {
    if (!mapInstance.value) return
    redrawMarkers()
  }

  function createMap() {
    if (disposed || mapInstance.value) return

    const AMap = window.AMap
    const container = containerRef.value
    if (!AMap || !tripPlan.value || !container) return

    try {
      const city = tripPlan.value.city
      const geocoder = new AMap.Geocoder()

      geocoder.getLocation(city, (status: string, result: AMapGeocodeResult) => {
        if (disposed || mapInstance.value) return

        try {
          let cityCenter: AMapLngLat = [116.397428, 39.90923] // 默认北京

          if (status === 'complete' && result.geocodes?.length) {
            const location = result.geocodes[0].location
            cityCenter = [location.lng, location.lat]
          } else {
            console.warn('[Map] 未找到城市坐标，使用默认值')
          }

          const map = new AMap.Map(container, {
            resizeEnable: true,
            zoom: 12,
            center: cityCenter,
          })

          AMap.plugin(['AMap.Scale', 'AMap.ToolBar'], function () {
            try {
              map.addControl(new AMap.Scale())
              map.addControl(new AMap.ToolBar())
            } catch (error) {
              console.warn('[Map] 地图控件加载失败:', error)
            }
          })

          mapInstance.value = map
          redrawMarkers()
          clearLoadTimer()
          mapStatus.value = 'ready'
        } catch (error) {
          console.error('[Map] 地图创建失败:', error)
          setMapError('地图初始化失败，请稍后重试')
        }
      })
    } catch (error) {
      console.error('[Map] 地图服务初始化失败:', error)
      setMapError('地图服务初始化失败，请稍后重试')
    }
  }

  function loadMapPlugins(currentAttempt: number) {
    const AMap = window.AMap
    if (disposed || currentAttempt !== attempt || !AMap) return
    try {
      AMap.plugin(['AMap.Geocoder', 'AMap.Scale', 'AMap.ToolBar'], () => {
        if (disposed || currentAttempt !== attempt) return
        if (typeof AMap.Geocoder !== 'function' || typeof AMap.Map !== 'function') {
          setMapError('地图插件加载失败，请重试')
          return
        }
        mapLoaded.value = true
        createMap()
      })
    } catch (error) {
      console.error('[Map] 地图插件加载失败:', error)
      setMapError('地图插件加载失败，请重试')
    }
  }

  async function initMap() {
    disposed = false
    const currentAttempt = ++attempt
    clearLoadTimer()
    mapError.value = ''
    mapErrorRetryable.value = true
    mapStatus.value = 'loading'
    if (!tripPlan.value?.city) {
      setMapError('缺少目的地信息，暂时无法加载地图', false)
      return
    }

    await nextTick()

    if (!containerRef.value) {
      console.error('[Map] 找不到地图容器')
      setMapError('地图容器未就绪，请重试')
      return
    }

    const amapKey = import.meta.env.VITE_AMAP_KEY
    if (!amapKey) {
      console.error('[Map] 高德地图 API Key 未配置')
      setMapError('未配置地图服务密钥，暂时无法显示地图', false)
      return
    }

    try {
      loadAmapSdk(amapKey).then(
        () => {
          if (disposed || currentAttempt !== attempt) return
          loadMapPlugins(currentAttempt)
        },
        (error) => {
          console.error('[Map] 高德地图脚本加载失败:', error)
          if (currentAttempt === attempt) setMapError('地图服务连接失败，请检查网络后重试')
        },
      )
      // Cover both paths: a hung script, plugin load or geocoder callback must not spin forever.
      loadTimer = setTimeout(() => {
        if (currentAttempt === attempt && mapStatus.value === 'loading') {
          setMapError('地图服务响应超时，请稍后重试')
        }
      }, 10000)
    } catch (error) {
      console.error('[Map] 地图初始化失败:', error)
      setMapError('地图初始化失败，请稍后重试')
    }
  }

  async function retryMap() {
    if (!mapErrorRetryable.value) return
    resetStalledSdkLoad()
    destroyMap()
    await initMap()
  }

  watch(
    () => tripPlan.value?.days,
    () => {
      if (mapLoaded.value) refreshMapMarkers()
    },
    { deep: true },
  )

  onBeforeUnmount(destroyMap)

  return {
    initMap,
    destroyMap,
    refreshMapMarkers,
    retryMap,
    mapStatus,
    mapError,
    mapErrorRetryable,
  }
}

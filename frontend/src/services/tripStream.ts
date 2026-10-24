import type { StreamingResponse } from '../types/index'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

// Check the structures consumed directly by the result page; domain validation stays on the backend.
function hasRenderableItinerary(value: unknown): boolean {
  if (!isRecord(value) || typeof value.city !== 'string' ||
      typeof value.start_date !== 'string' || typeof value.end_date !== 'string' ||
      !Array.isArray(value.days)) return false
  if (value.evaluator_warnings != null &&
      (!Array.isArray(value.evaluator_warnings) ||
       !value.evaluator_warnings.every(item => typeof item === 'string'))) return false
  return value.days.every(day => isRecord(day) &&
    typeof day.date === 'string' && Number.isInteger(day.day_index) &&
    Array.isArray(day.attractions) && day.attractions.every(attraction => {
      if (!isRecord(attraction) || typeof attraction.name !== 'string' ||
          !isRecord(attraction.location)) return false
      const { longitude, latitude } = attraction.location
      return typeof longitude === 'number' && Number.isFinite(longitude) &&
        longitude >= -180 && longitude <= 180 &&
        typeof latitude === 'number' && Number.isFinite(latitude) &&
        latitude >= -90 && latitude <= 90
    }))
}

// The backend sends no heartbeat and one node (e.g. the POI agent) can stay silent for
// a while, so this must comfortably exceed the longest legitimate gap between events.
export const STREAM_IDLE_TIMEOUT_MS = 180_000

function readWithIdleTimeout(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  idleTimeoutMs: number
): Promise<ReadableStreamReadResult<Uint8Array>> {
  if (!(idleTimeoutMs > 0)) return reader.read()
  return new Promise((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new Error('规划长时间没有进展，连接已中断，请重试')),
      idleTimeoutMs
    )
    reader.read().then(
      result => { clearTimeout(timer); resolve(result) },
      error => { clearTimeout(timer); reject(error) }
    )
  })
}

export async function readTripStream(
  body: ReadableStream<Uint8Array> | null,
  onMessage: (data: StreamingResponse) => void,
  idleTimeoutMs: number = STREAM_IDLE_TIMEOUT_MS
): Promise<void> {
  if (!body) throw new Error('规划响应缺少数据流')
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let terminal = false

  function consume(line: string) {
    if (!line.startsWith('data:')) return
    const event = JSON.parse(line.slice(5).trim()) as StreamingResponse
    if (!event || typeof event.node !== 'string' || typeof event.status !== 'string') {
      throw new Error('规划进度数据格式无效')
    }
    if (event.node === 'complete' && event.status === 'completed') {
      if (!event.data?.itinerary) throw new Error('规划完成响应缺少行程')
      if (!hasRenderableItinerary(event.data.itinerary)) throw new Error('规划行程数据格式无效，请重试')
      terminal = true
    } else if (event.node === 'error') {
      terminal = true
    }
    onMessage(event)
  }

  try {
    while (!terminal) {
      const { done, value } = await readWithIdleTimeout(reader, idleTimeoutMs)
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''
      for (const line of lines) {
        consume(line)
        if (terminal) break
      }
      if (done) {
        if (!terminal && buffer) consume(buffer)
        break
      }
    }
    if (!terminal) throw new Error('规划连接已中断，未收到完整行程，请重试')
  } finally {
    try {
      await reader.cancel()
    } catch {
      // A failed stream can also reject cancellation; preserve the original error.
    } finally {
      reader.releaseLock()
    }
  }
}

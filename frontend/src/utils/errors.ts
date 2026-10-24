interface ApiErrorPayload {
  response?: {
    data?: {
      detail?: unknown
    }
  }
  message?: unknown
}

/** FastAPI `detail`: a plain string, or a list of 422 validation entries. */
export function formatErrorDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (!Array.isArray(detail)) return ''
  return detail.map((item) => {
    if (!item || typeof item !== 'object') return String(item)
    const entry = item as { msg?: unknown; loc?: unknown[] }
    const location = Array.isArray(entry.loc) ? `（${entry.loc.join('.')}）` : ''
    return `${typeof entry.msg === 'string' ? entry.msg : '请求参数无效'}${location}`
  }).join('；')
}

export function getErrorMessage(error: unknown, fallback: string): string {
  if (!error || typeof error !== 'object') return fallback

  const candidate = error as ApiErrorPayload
  const detail = formatErrorDetail(candidate.response?.data?.detail)
  if (detail) return detail
  if (typeof candidate.message === 'string' && candidate.message) return candidate.message
  return fallback
}

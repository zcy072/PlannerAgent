// Conservative browser canvas limits: Safari and mobile browsers reject canvases far
// smaller than Chrome's 32767px / 268M-pixel ceiling, and html2canvas fails silently
// (blank output) when the limit is exceeded.
const MAX_CANVAS_DIMENSION = 16384
const MAX_CANVAS_AREA = 64_000_000

/** Largest html2canvas scale (capped at `preferred`) that keeps the canvas within browser limits. */
export function fitCanvasScale(width: number, height: number, preferred = 2): number {
  if (!(width > 0) || !(height > 0)) return preferred
  const byDimension = MAX_CANVAS_DIMENSION / Math.max(width, height)
  const byArea = Math.sqrt(MAX_CANVAS_AREA / (width * height))
  return Math.min(preferred, byDimension, byArea)
}

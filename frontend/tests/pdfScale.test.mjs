import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import ts from 'typescript'

const source = readFileSync(new URL('../src/utils/pdfScale.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 }
})
const { fitCanvasScale } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)

test('short pages keep the preferred scale', () => {
  assert.equal(fitCanvasScale(1000, 3000), 2)
})
test('tall pages are scaled down to stay under the canvas limits', () => {
  const scale = fitCanvasScale(1000, 12000)
  assert.ok(scale < 2)
  assert.ok(12000 * scale <= 16384)
  assert.ok(1000 * scale * 12000 * scale <= 64_000_000 + 1)
})
test('extremely tall pages still produce a positive scale', () => {
  const scale = fitCanvasScale(1000, 100000)
  assert.ok(scale > 0)
  assert.ok(100000 * scale <= 16384)
})
test('unmeasurable elements fall back to the preferred scale', () => {
  assert.equal(fitCanvasScale(0, 0), 2)
  assert.equal(fitCanvasScale(NaN, 100, 1), 1)
})

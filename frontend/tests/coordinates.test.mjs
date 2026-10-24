import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import ts from 'typescript'

const source = readFileSync(new URL('../src/utils/coordinates.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 }
})
const { getMapCoordinates } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)

test('accepts zero-valued coordinates and Amap coordinate aliases', () => {
  assert.deepEqual(getMapCoordinates({ longitude: 0, latitude: 34 }), [0, 34])
  assert.deepEqual(getMapCoordinates({ lng: 113.6, lat: 0 }), [113.6, 0])
})

test('rejects missing, non-finite, and out-of-range coordinates', () => {
  for (const location of [null, {}, { longitude: NaN, latitude: 34 },
    { longitude: 181, latitude: 34 }, { longitude: 113, latitude: -91 }]) {
    assert.equal(getMapCoordinates(location), null)
  }
})

import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import ts from 'typescript'

const compile = (file) => ts.transpileModule(
  readFileSync(new URL(`../src/utils/${file}`, import.meta.url), 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }
).outputText
const toDataUrl = (code) => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`
// data: modules cannot resolve relative imports, so inline the sibling module.
const outputText = compile('restaurants.ts').replace("'./coordinates'", `'${toDataUrl(compile('coordinates.ts'))}'`)
const { pickSearchCenters, mergeNearbyRestaurants } = await import(
  `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
)

test('pickSearchCenters keeps the first three valid locations', () => {
  const attractions = [
    { location: { longitude: 113, latitude: 34 } },
    { location: null },
    { location: { longitude: NaN, latitude: 34 } },
    { location: { longitude: 0, latitude: 0 } },
    { location: { longitude: 114, latitude: 35 } },
    { location: { longitude: 115, latitude: 36 } },
  ]
  assert.deepEqual(pickSearchCenters(attractions), [
    { longitude: 113, latitude: 34 },
    { longitude: 0, latitude: 0 },
    { longitude: 114, latitude: 35 },
  ])
  assert.deepEqual(pickSearchCenters(undefined), [])
})

test('mergeNearbyRestaurants dedupes, sorts by distance and caps at 12', () => {
  const merged = mergeNearbyRestaurants([
    [{ id: 'a', name: 'A', address: 'x', distance_m: 300 }, { name: 'B', address: 'y', distance_m: 100 }],
    [{ id: 'a', name: 'A', address: 'x', distance_m: 50 }, { name: 'B', address: 'y' }, { id: 'c', name: 'C', address: 'z' }],
  ])
  assert.deepEqual(merged.map((item) => item.name), ['B', 'A', 'C'])

  const many = [Array.from({ length: 20 }, (_, i) => ({ id: String(i), name: `R${i}`, address: '', distance_m: i }))]
  assert.equal(mergeNearbyRestaurants(many).length, 12)
})

test('pickSearchCenters rejects out-of-range values and accepts lng/lat aliases', () => {
  assert.deepEqual(
    pickSearchCenters([
      { location: { longitude: 999, latitude: 34 } },
      { location: { lng: 114, lat: 35 } },
    ]),
    [{ longitude: 114, latitude: 35 }],
  )
})

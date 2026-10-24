import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import ts from 'typescript'

// Strip TypeScript using the existing compiler so tests also run on CI's Node 20.
const source = readFileSync(new URL('../src/services/tripStream.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 }
})
const { readTripStream } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)

const itinerary = { city: '郑州', start_date: '2026-10-09', end_date: '2026-10-09',
  days: [{ date: '2026-10-09', day_index: 0, attractions: [
    { name: '景点', location: { longitude: 113.6, latitude: 34.7 } }
  ] }] }
const completion = plan => 'data: ' + JSON.stringify({ node: 'complete', status: 'completed', data: { itinerary: plan } })
const complete = completion(itinerary)
function stream(text) {
  const bytes = new TextEncoder().encode(text)
  return new ReadableStream({ start(controller) {
    for (const byte of bytes) controller.enqueue(new Uint8Array([byte]))
    controller.close()
  } })
}
test('fragmented UTF-8 and final line without newline', async () => {
  const events = []
  const body = stream(': heartbeat\r\n' + complete)
  await readTripStream(body, event => events.push(event))
  assert.equal(events[0].data.itinerary.city, '郑州')
  assert.equal(body.locked, false)
})
test('early EOF and missing body fail', async () => {
  await assert.rejects(readTripStream(stream(''), () => {}), /中断/)
  await assert.rejects(readTripStream(null, () => {}), /缺少/)
})
test('invalid itinerary never reaches page callback', async () => {
  for (const plan of [true, {}, { ...itinerary, days: {} },
    { ...itinerary, days: [null] },
    { ...itinerary, days: [{ date: '2026-10-09', day_index: 0, attractions: [null] }] },
    { ...itinerary, evaluator_warnings: [42] },
    { ...itinerary, days: [{ date: '2026-10-09', day_index: 0, attractions: [
      { name: 'bad', location: { longitude: 999, latitude: 34 } }
    ] }] }]) {
    let delivered = false
    await assert.rejects(readTripStream(stream(completion(plan)), () => { delivered = true }), /格式无效/)
    assert.equal(delivered, false)
  }
})
test('malformed JSON and incomplete completion fail', async () => {
  await assert.rejects(readTripStream(stream('data: bad\n'), () => {}))
  await assert.rejects(readTripStream(stream('data: {"node":"complete","status":"completed"}'), () => {}), /缺少行程/)
})
test('backend failure delivered once without secondary EOF error', async () => {
  let count = 0
  await readTripStream(stream('data: {"node":"error","status":"failed"}\n'), () => count++)
  assert.equal(count, 1)
})
test('callback errors propagate and release reader', async () => {
  const body = stream(complete)
  const error = new Error('callback failed')
  await assert.rejects(readTripStream(body, () => { throw error }), value => value === error)
  assert.equal(body.locked, false)
})
test('transport cancellation propagates', async () => {
  const error = new DOMException('stopped', 'AbortError')
  const body = new ReadableStream({ start(controller) { controller.error(error) } })
  await assert.rejects(readTripStream(body, () => {}), value => value === error)
  assert.equal(body.locked, false)
})
test('a failed subtask does not hide the final itinerary', async () => {
  const events = []
  await readTripStream(stream('data: {"node":"weather","status":"failed"}\n' + complete), event => events.push(event))
  assert.deepEqual(events.map(event => event.node), ['weather', 'complete'])
})
test('completion cancels an open connection without waiting for EOF', { timeout: 1000 }, async () => {
  let cancelled = false
  const body = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(complete + '\n\n'))
    },
    cancel() { cancelled = true }
  })
  await readTripStream(body, () => {})
  assert.equal(cancelled, true)
  assert.equal(body.locked, false)
})
test('a silent stream is aborted after the idle timeout and releases the reader', { timeout: 2000 }, async () => {
  let cancelled = false
  const body = new ReadableStream({ cancel() { cancelled = true } })
  await assert.rejects(readTripStream(body, () => {}, 50), /长时间没有进展/)
  assert.equal(cancelled, true)
  assert.equal(body.locked, false)
})
test('the idle timer restarts after every chunk', { timeout: 2000 }, async () => {
  const events = []
  const body = new ReadableStream({
    async start(controller) {
      const encoder = new TextEncoder()
      controller.enqueue(encoder.encode('data: {"node":"weather","status":"running"}\n'))
      await new Promise(resolve => setTimeout(resolve, 60))
      controller.enqueue(encoder.encode('data: {"node":"weather","status":"completed"}\n'))
      await new Promise(resolve => setTimeout(resolve, 60))
      controller.enqueue(encoder.encode(complete + '\n'))
    }
  })
  await readTripStream(body, event => events.push(event.status), 100)
  assert.deepEqual(events, ['running', 'completed', 'completed'])
})
test('a zero idle timeout disables the watchdog', async () => {
  const events = []
  await readTripStream(stream(complete), event => events.push(event), 0)
  assert.equal(events.length, 1)
})

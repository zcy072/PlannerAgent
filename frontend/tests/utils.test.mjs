import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import ts from 'typescript'

async function load(name) {
  const source = readFileSync(new URL(`../src/utils/${name}.ts`, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 }
  })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}

const { persistenceWarning } = await load('persistence')
const { parseLlmProvider } = await load('llmProvider')
const { streamNodeLabel, traceNodeLabel } = await load('nodeLabels')

test('saved modifications produce no warning', () => {
  assert.equal(persistenceWarning({ persistence_saved: true }), null)
})
test('unsaved modifications use the backend warning, else a default', () => {
  assert.equal(persistenceWarning({ persistence_saved: false, persistence_warning: '磁盘已满' }), '磁盘已满')
  assert.match(persistenceWarning({ persistence_saved: false, persistence_warning: null }), /未保存/)
  assert.match(persistenceWarning({}), /未保存/)
})
test('stream and trace labels are separate maps with raw-name fallback', () => {
  assert.match(streamNodeLabel('planner'), /行程规划/)
  assert.equal(streamNodeLabel('unknown_node'), 'unknown_node')
  assert.equal(traceNodeLabel('planner_agent'), '行程生成')
  assert.equal(traceNodeLabel('planner'), 'planner')
})

test('only backend enum values count as an LLM provider', () => {
  assert.equal(parseLlmProvider('aliyun'), 'aliyun')
  assert.equal(parseLlmProvider('openai'), 'openai')
  assert.equal(parseLlmProvider('gpt-5'), undefined)
  assert.equal(parseLlmProvider(''), undefined)
  assert.equal(parseLlmProvider(null), undefined)
})

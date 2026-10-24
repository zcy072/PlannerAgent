// SSE 事件里的 node 名称（Home 进度时间线）。
const STREAM_NODE_LABELS: Record<string, string> = {
  init: '🚀 初始化',
  weather: '🌤️ 天气工具',
  candidate_collection: '🔍 候选数据收集',
  poi_search: '🔍 景点搜索',
  hotel: '🏨 酒店推荐',
  route_optimizer: '路线优化节点',
  route_verification: '路线校验',
  planner: '📋 行程规划',
  quality_gate: '规则质量检查',
  complete: '✅ 完成',
  stopped: '⏹️ 已终止',
  error: '❌ 错误',
}

// 图节点在 node_traces 里的名称（Result 规划过程面板）。
const TRACE_NODE_LABELS: Record<string, string> = {
  weather: '天气查询',
  candidate_collection: '景点与酒店收集',
  route_optimizer: '路线优化',
  route_verification: '路线校验（高德 MCP）',
  planner_agent: '行程生成',
  quality_gate: '质量检查',
}

export const streamNodeLabel = (node: string): string => STREAM_NODE_LABELS[node] || node

export const traceNodeLabel = (node: string): string => TRACE_NODE_LABELS[node] || node

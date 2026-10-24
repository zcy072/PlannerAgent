import type { LlmProvider } from '@/types'

const LLM_PROVIDERS: readonly LlmProvider[] = ['deepseek', 'aliyun', 'openai']

/** 只接受后端枚举里的值；其他字符串（过期的 sessionStorage 等）按未选择处理。 */
export function parseLlmProvider(value: unknown): LlmProvider | undefined {
  return LLM_PROVIDERS.find((provider) => provider === value)
}

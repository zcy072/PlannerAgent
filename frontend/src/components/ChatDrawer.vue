<template>
  <el-drawer
    :model-value="open"
    @update:model-value="emit('update:open', $event)"
    title="对话式修改行程"
    direction="rtl"
    size="min(420px, 100vw)"
    @opened="scrollToBottom"
  >
    <div class="chat-drawer">
      <div class="chat-tip">
        可以试试：「第二天黄鹤楼换成东湖」「删掉第一天的第二个景点」「第三天的酒店换成希尔顿」
      </div>
      <div ref="chatMessagesEl" class="chat-messages" role="log" aria-live="polite" aria-label="对话记录">
        <div v-if="!tripStore.chatHistory.length" class="chat-empty">
          还没有对话记录，开始第一条消息吧。
        </div>
        <div
          v-for="(turn, idx) in tripStore.chatHistory"
          :key="idx"
          :class="['chat-bubble', turn.role]"
        >
          <div class="bubble-content">{{ turn.content }}</div>
          <div v-if="turn.role === 'assistant' && turn.modified" class="bubble-tag">
            已更新行程
          </div>
        </div>
        <div v-if="tripStore.chatting" class="chat-bubble assistant">
          <div class="bubble-content">
            <el-icon class="is-loading"><Refresh /></el-icon>
            正在思考…
          </div>
        </div>
      </div>
      <div class="chat-input">
        <el-input
          ref="chatInputRef"
          v-model="chatInput"
          aria-label="输入修改行程的指令"
          placeholder="说出你想修改的内容，回车发送"
          :disabled="tripStore.chatting || tripStore.modifying"
          @keydown.enter="handleEnter"
        />
        <el-button
          type="primary"
          :loading="tripStore.chatting"
          :disabled="!chatInput.trim() || tripStore.modifying"
          @click="submit"
        >
          发送
        </el-button>
      </div>
    </div>
  </el-drawer>
</template>

<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { ElButton, ElDrawer, ElIcon, ElInput, ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { useTripStore } from '@/stores/trip'
import { getErrorMessage } from '@/utils/errors'
import { persistenceWarning } from '@/utils/persistence'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'update:open', value: boolean): void }>()

const tripStore = useTripStore()
const chatInput = ref('')
const chatMessagesEl = ref<HTMLElement | null>(null)
const chatInputRef = ref<InstanceType<typeof ElInput> | null>(null)

async function scrollToBottom() {
  await nextTick()
  const el = chatMessagesEl.value
  if (el) el.scrollTop = el.scrollHeight
}

async function submit() {
  const text = chatInput.value.trim()
  if (!text || tripStore.chatting) return
  chatInput.value = ''
  try {
    const res = await tripStore.sendChat(text)
    if (res.modified) {
      const warning = res.persistence_saved === false ? persistenceWarning(res) : null
      if (warning) {
        ElMessage.warning(warning)
      } else {
        ElMessage.success('行程已更新')
      }
    }
  } catch (error: unknown) {
    chatInput.value = text
    ElMessage.error(getErrorMessage(error, '对话失败'))
  } finally {
    scrollToBottom()
    // 发送期间输入框被禁用会丢失焦点，恢复后可以直接继续输入。
    await nextTick()
    chatInputRef.value?.focus()
  }
}

function handleEnter(event: Event | KeyboardEvent) {
  if (!(event instanceof KeyboardEvent)) return
  if (event.isComposing || tripStore.chatting || tripStore.modifying) return
  event.preventDefault()
  void submit()
}

watch(
  () => tripStore.chatHistory.length,
  () => scrollToBottom(),
)
watch(
  () => props.open,
  (v) => {
    if (v) scrollToBottom()
  },
)
</script>

<style scoped>
.chat-drawer {
  display: flex;
  flex-direction: column;
  height: 100%;
  gap: 12px;
}

.chat-tip {
  font-size: 12px;
  color: #606266;
  background: #f4f6fb;
  padding: 8px 12px;
  border-radius: 6px;
  line-height: 1.5;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-right: 4px;
}

.chat-empty {
  text-align: center;
  color: #6b7280;
  padding: 32px 0;
  font-size: 13px;
}

.chat-bubble {
  max-width: 85%;
  padding: 10px 14px;
  border-radius: 12px;
  font-size: 14px;
  line-height: 1.55;
  word-break: break-word;
}

.chat-bubble.user {
  align-self: flex-end;
  background: #1d6fd1;
  color: #fff;
  border-bottom-right-radius: 2px;
}

.chat-bubble.assistant {
  align-self: flex-start;
  background: #f2f3f5;
  color: #303133;
  border-bottom-left-radius: 2px;
}

.bubble-content {
  display: flex;
  align-items: center;
  gap: 6px;
  white-space: pre-wrap;
}

.bubble-tag {
  margin-top: 4px;
  font-size: 11px;
  color: #2f7d1a;
}

.chat-input {
  display: flex;
  gap: 8px;
}

.chat-input :deep(.el-input__wrapper) {
  flex: 1;
}
</style>

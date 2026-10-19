<template>
  <div class="chat-area">
    <!-- 消息列表 -->
    <div class="message-list" ref="messageListRef">
      <!-- 空状态 -->
      <div v-if="displayMessages.length === 0" class="empty-state">
        <div class="empty-icon"><img src="/moto-logo.svg" alt="风驰 标志" /></div>
        <h2>让每一次采购，更有把握</h2>
        <p>从供应商比价到订单协同，您的摩托配件采购助手</p>
        <div class="feature-list">
          <div class="feature-item">
            <span class="feature-icon">📊</span>
            <span>供应商比价分析</span>
          </div>
          <div class="feature-item">
            <span class="feature-icon">🔍</span>
            <span>物料与库存查询</span>
          </div>
          <div class="feature-item">
            <span class="feature-icon">💻</span>
            <span>采购订单协同</span>
          </div>
          <div class="feature-item">
            <span class="feature-icon">📝</span>
            <span>采购报告与建议</span>
          </div>
        </div>
      </div>

      <!-- 消息列表（user / assistant / tool 按时间顺序混合展示）-->
      <div v-else class="messages">
        <template v-for="item in displayMessages" :key="item.key">
          <div v-if="item.kind === 'failure'" class="failure-reply">
            <MessageItem :message="{role:'assistant',channel:'answer',content:(item.message.failure.operation || '业务操作')+'未完成：'+item.message.failure.message+'\n\n'+item.message.failure.action}"/>
            <button v-if="item.message.failure.retryable" :disabled="retryDisabled || !!retrying" @click="$emit('retry-tool', item.message.id)">{{ retrying === item.message.id ? '正在重试…' : '重试此查询' }}</button>
          </div>
          <details v-else-if="item.kind === 'details'" class="execution-details">
            <summary>执行详情（{{ item.messages.length }} 条）</summary>
            <MessageItem v-for="(message, index) in item.messages"
              :key="message.id || index" :message="message"
              :is-streaming="isStreamingForMessage(message)" />
          </details>
          <MessageItem v-else :message="item.message" @render-progress="scrollWhileTyping" @render-complete="$emit('answer-rendered',item.message.id)"
            :is-streaming="isStreamingForMessage(item.message)" />
        </template>
      </div>
      <div v-if="streaming" class="execution-progress" role="status" aria-live="polite">{{ progress || '正在处理您的请求。' }}</div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import MessageItem from './MessageItem.vue'
import { buildDisplayItems } from '../utils/message-display.js'

/**
 * 对话区域组件
 *
 * 显示消息列表（user / assistant / tool 按时间顺序混合展示）
 * 当 showToolCalls 为 false 时，过滤掉 tool 消息只显示 AI 回复
 */

defineEmits(['retry-tool','answer-rendered'])
const props = defineProps({
  progress: {type:String, default:''},
  retrying: {type:String, default:null},
  retryDisabled: {type:Boolean, default:false},
  messages: {
    type: Array,
    default: () => []
  },
  streaming: {
    type: Boolean,
    default: false
  },
  showToolCalls: {
    type: Boolean,
    default: true
  }
})

// 根据开关过滤展示的消息
const displayMessages = computed(() => buildDisplayItems(props.messages, props.showToolCalls))

// 消息列表引用
const messageListRef = ref(null)

/**
 * 判断当前消息是否处于流式输出状态
 * 只有最后一条 assistant 消息在 streaming 时才显示光标
 */
function isStreamingForMessage(msg) {
  if (!props.streaming || msg.role !== 'assistant') return false
  // 找到 messages 中最后一条 assistant 消息
  const lastAssistant = [...props.messages].reverse().find(m => m.role === 'assistant')
  return msg.answerStreaming === true || lastAssistant?.id === msg.id
}

// 监听消息变化，自动滚动到底部
watch(
  () => props.messages.length,
  () => {
    nextTick(() => {
      scrollToBottom()
    })
  }
)

// 深度监听消息内容变化（流式更新时也会触发滚动）
watch(
  () => props.messages,
  () => {
    nextTick(() => {
      scrollToBottom()
    })
  },
  { deep: true }
)

/**
 * 滚动到底部
 */
function scrollWhileTyping(){const list=messageListRef.value;if(list&&list.scrollHeight-list.scrollTop-list.clientHeight<120)nextTick(scrollToBottom)}
function scrollToBottom() {
  if (messageListRef.value) {
    messageListRef.value.scrollTop = messageListRef.value.scrollHeight
  }
}
</script>

<style scoped>
.execution-progress { margin:16px 24px; padding:12px 16px; color:#237767; background:#eaf4ed; border-radius:10px; }
.failure-reply { margin: 0; }

.failure-reply button { padding:8px 16px; border:0; border-radius:8px; background:#238575; color:white; cursor:pointer; }
.failure-reply button:disabled { opacity:.5; cursor:wait; }
.execution-details { margin: 12px 24px; border: 1px solid #dce7df; border-radius: 12px; background: #f6f8f5; }
.execution-details summary { padding: 12px 16px; cursor: pointer; color: #39756b; font-size: 14px; }
.execution-details[open] summary { border-bottom: 1px solid #dce7df; margin-bottom: 16px; }

.chat-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: #ffffff;
}

/* 消息列表 */
.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 24px 0;
}

.message-list::-webkit-scrollbar {
  width: 6px;
}

.message-list::-webkit-scrollbar-track {
  background: transparent;
}

.message-list::-webkit-scrollbar-thumb {
  background: #c6d1c9;
  border-radius: 3px;
}

.message-list::-webkit-scrollbar-thumb:hover {
  background: #82908f;
}

/* 空状态 */
.empty-state {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 24px;
  text-align: center;
  background: linear-gradient(180deg, #f7f8f4 0%, #ffffff 100%);
}

.empty-icon {
  margin-bottom: 24px;
  animation: float 3s ease-in-out infinite;
}

.empty-icon img {
  width: 240px;
  height: 240px;
}

@keyframes float {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-8px); }
}

.empty-state h2 {
  font-size: 28px;
  font-weight: 700;
  background: #238575;
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  margin-bottom: 12px;
}

.empty-state p {
  font-size: 15px;
  color: #697e79;
  margin-bottom: 40px;
}

.feature-list {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
  max-width: 540px;
}

.feature-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 18px 20px;
  background: #ffffff;
  border: 1px solid #e7ebe7;
  border-radius: 14px;
  transition: all 0.2s ease;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
}

.feature-item:hover {
  transform: translateY(-2px);
  border-color: #238575;
  box-shadow: 0 4px 12px rgba(35, 133, 117, 0.1);
}

.feature-icon {
  font-size: 24px;
}

.feature-item span:last-child {
  font-size: 14px;
  color: #24373c;
  font-weight: 500;
}

/* 消息列表 */
.messages {
  max-width: 1200px;
  margin: 0 auto;
}

.chat-area, .empty-state { background: #f7f8f4; }
.empty-icon { animation: none; margin-bottom: 24px; }
.empty-icon img { width: 112px; height: 112px; }
.empty-state h2 { background: none; -webkit-text-fill-color: #24373c; color: #24373c; font-weight: 600; letter-spacing: .5px; }
.feature-item { border-radius: 12px; box-shadow: none; background: #fff; }
.feature-item:hover { transform: none; box-shadow: none; background: #eaf4ed; }
@media (max-width: 700px) {
  .feature-list { grid-template-columns: 1fr; gap: 10px; }
  .empty-state { height: auto; min-height: 100%; padding: 24px 16px; }
  .empty-state h2 { font-size: 22px; }
  .empty-state p { margin-bottom: 22px; }
  .feature-item { padding: 12px 16px; }
}

</style>

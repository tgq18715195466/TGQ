<template>
 <aside class="conversation-sidebar" aria-label="智能助手会话">
  <h2 class="conversation-heading">会话工作区</h2>
    <div class="chat-actions"><button class="new-chat-btn" @click="$emit('new-chat')"><span class="icon">+</span>新建对话</button></div>
    <!-- 搜索框 -->
    <div class="search-box">
      <input
        type="text"
        v-model="searchKeyword"
        placeholder="搜索会话..."
        aria-label="搜索历史会话"
        class="search-input"
      />
    </div>

    <!-- 会话列表 -->
    <div class="session-list">
      <div class="session-list-header">
        <span>历史记录</span>
      </div>

      <div class="session-items">
        <div
          v-for="session in filteredSessions"
          :key="session.thread_id"
          class="session-item"
          :class="{ active: session.thread_id === currentThreadId }"
          @click="$emit('select-session', session.thread_id)"
        >
          <button class="session-info" @click.stop="$emit('select-session',session.thread_id)" :aria-current="session.thread_id===currentThreadId?'true':undefined">
            <span class="session-title">{{ session.title || '未命名会话' }}</span>
            <span class="session-time">{{ formatTime(session.updated_at) }}</span>
          </button>
          <button
            class="delete-btn"
            @click.stop="handleDelete(session.thread_id)"
            title="删除会话"
          >
            ×
          </button>
        </div>

        <!-- 空状态 -->
        <div v-if="sessions.length === 0" class="empty-state">
          <p>暂无会话记录</p>
          <p class="hint">开始一个新对话吧</p>
        </div>
      </div>
    </div>

    <p v-if="sessions.length && !filteredSessions.length" class="no-results">没有匹配的会话</p>
 </aside>
</template>
<script setup>
import { ref, computed } from 'vue'

// Props 定义
const props = defineProps({
  sessions: {
    type: Array,
    default: () => []
  },
  currentThreadId: {
    type: String,
    default: null
  }
})

// Emits 定义
const emit = defineEmits(['select-session', 'new-chat', 'delete-session'])

// 搜索关键词
const searchKeyword = ref('')

// 过滤后的会话列表
const filteredSessions = computed(() => {
  if (!searchKeyword.value) {
    return props.sessions
  }
  return props.sessions.filter(session =>
    (session.title || '').toLowerCase().includes(searchKeyword.value.toLowerCase())
  )
})

/**
 * 格式化时间
 */
function formatTime(timestamp) {
  if (!timestamp) return ''

  const date = new Date(timestamp)
  const now = new Date()
  const diff = now - date

  // 一天内显示相对时间
  if (diff < 24 * 60 * 60 * 1000) {
    const hours = Math.floor(diff / (60 * 60 * 1000))
    if (hours < 1) {
      const minutes = Math.floor(diff / (60 * 1000))
      return minutes < 1 ? '刚刚' : `${minutes} 分钟前`
    }
    return `${hours} 小时前`
  }

  // 超过一天显示日期
  return `${date.getMonth() + 1}/${date.getDate()}`
}

/**
 * 处理删除会话
 */
function handleDelete(threadId) {
  if (confirm('确定要删除这个会话吗？')) {
    emit('delete-session', threadId)
  }
}
</script>
<style scoped>
.conversation-sidebar{width:244px;flex-shrink:0;min-height:0;display:flex;flex-direction:column;background:#f1f5ef;border-right:1px solid #dde7db;color:#34584a;overflow:hidden}.conversation-heading{padding:22px 18px 14px;font-size:14px;font-weight:600;margin:0}.chat-actions{padding:0 16px}.new-chat-btn{display:flex;align-items:center;justify-content:center;gap:8px;width:100%;border:0;border-radius:8px;background:#238575;color:white;font:inherit;font-size:13px;padding:11px;cursor:pointer}.new-chat-btn:hover{background:#1b7062}.icon{font-size:18px}.search-box{padding:16px}.search-input{box-sizing:border-box;width:100%;border:1px solid #d7e2d3;background:#fff;border-radius:8px;padding:10px 12px;font:inherit;font-size:13px;color:#34584a}.search-input:focus{outline:2px solid #7bb69e;outline-offset:1px}.session-list{flex:1;min-height:0;overflow:auto}.session-list-header{font-size:11px;color:#7c9386;padding:0 18px 10px}.session-items{padding:0 10px 16px}.session-item{display:flex;align-items:center;gap:4px;border:1px solid transparent;border-radius:9px;padding:3px 5px;margin-bottom:5px;cursor:pointer}.session-item:hover{background:#e8eee3}.session-item.active{background:#e0eee1;border-color:#b9d7bf}.session-info{flex:1;min-width:0;text-align:left;border:0;background:transparent;color:inherit;font:inherit;padding:10px 6px;cursor:pointer}.session-title{display:block;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;font-size:13px}.session-time{display:block;font-size:11px;color:#829385;margin-top:6px}.delete-btn{border:0;background:transparent;color:#8a9d8e;font-size:19px;cursor:pointer;padding:4px 6px}.delete-btn:hover{background:#fbe8e3;color:#a54438;border-radius:5px}.session-info:focus-visible,.delete-btn:focus-visible,.new-chat-btn:focus-visible{outline:2px solid #238575;outline-offset:1px}.empty-state,.no-results{font-size:12px;color:#8c9c8e;text-align:center;padding:20px 8px}.hint{font-size:11px}
@media(max-width:1000px){.conversation-sidebar{width:196px}}@media(max-width:700px){.conversation-sidebar{width:146px}.conversation-heading{padding:16px 10px}.chat-actions,.search-box{padding:10px}.session-items{padding:0 4px}.session-item{padding:2px}.session-list-header{padding:4px 10px}.search-input{padding:9px 6px}}
</style>

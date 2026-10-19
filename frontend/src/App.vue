<template>
  <div class="app-container">
    <!-- 侧边栏 -->
    <Sidebar
      :navigation-groups="navigationGroups"
      :active-group="activeGroup.key"
      @navigate-group="navigateGroup"
    />

    <!-- 主内容区 -->
    <main class="main-content">
      <header class="workspace-header"><span class="workspace-breadcrumb">风驰工作台 <span aria-hidden="true">/</span> <strong>{{ activeGroup.label }}</strong></span><div class="identity-bar"><span>{{ identity }} · {{ roleLabels[auth.user?.role] }}</span><button @click="navigateWorkspace({view:'password'})">修改密码</button><button @click="logout" :disabled="loggingOut">{{ loggingOut?'正在退出…':'退出登录' }}</button></div></header>
      <p v-if="logoutError" class="run-error" role="alert">{{ logoutError }}</p>
      <nav class="workspace-nav" :aria-label="activeGroup.label + '功能导航'"><button v-for="tab in activeGroup.tabs" :key="tab.key" :class="{active:view===tab.key}" :aria-current="view===tab.key?'page':undefined" @click="navigateWorkspace({view:tab.key})">{{ tab.label }}</button></nav>
      <AccountSettings v-if="['password','users'].includes(view)" :key="view" :view="view" />
      <Operations v-else-if="operationViews.includes(view)" :view="view" :initial-part="operationPart" :agent-busy="isStreaming || isResuming || !!interruptData" @ask-agent="startReplenishment" />
      <Dashboard v-else-if="view !== 'chat'" :view="view" :initial-filter="dashboardFilter" :busy="isStreaming || isResuming || !!interruptData" @navigate="navigateWorkspace" @replenish="openPurchaseRequest" @agent-replenish="startReplenishment" />
      <div v-show="view === 'chat'" class="assistant-layout">
      <ConversationSidebar
      :sessions="sessions"
      :current-thread-id="currentThreadId"
      @select-session="handleSelectSession"
      @new-chat="handleNewChat"
      @delete-session="handleDeleteSession"
      />
      <div class="chat-workspace" :class="{'interrupt-open':pendingInterrupts.length && !supplementClosed}">
      <!-- 对话区域 -->
      <ChatArea
        :messages="messages"
        :streaming="isStreaming || isResuming"
        :progress="progress"
        :retrying="retryingTool"
        :retry-disabled="isStreaming || isResuming || !!interruptData"
        @retry-tool="handleRetryTool"
        @answer-rendered="markAnswerRendered"
        :show-tool-calls="showToolCalls"
      />

      <div v-if="runError" class="run-reply"><MessageItem :message="{role:'assistant',channel:'answer',content:runError}"/><button :disabled="isStreaming || isResuming || !!retryingTool" @click="refreshRunState">刷新执行状态</button></div>
      <div v-if="blockedInterrupts.length" class="run-reply permission-reply"><MessageItem :message="{id:'permission-reply',role:'assistant',channel:'answer',content:interruptPermissionMessage(blockedInterrupts[0])}"/><button :disabled="isStreaming || isResuming || !!retryingTool" @click="handleNewChat">{{ auth.user?.role==='warehouse' ? '新建仓管对话' : '新建对话' }}</button></div>
      <!-- ★ 人工介入中断横幅（数据补充 / HITL 审批） -->
      <div v-if="pendingInterrupts.length" v-show="!supplementClosed" class="interrupt-workspace">
      <InterruptBanner
        v-for="pending in pendingInterrupts"
        :key="pending.interrupt_id"
        :interrupt-data="pending"
        :submitting="isResuming"
        @resume="handleResume"
        @close="closeSupplement"
      />

      </div>
      <div v-if="pendingInterrupts.length && supplementClosed" class="supplement-paused" role="status"><span>采购信息尚未提交，您可以稍后继续补充。</span><button ref="reopenSupplement" @click="supplementClosed=false">继续补充信息</button></div>
      <!-- 输入区域：中断激活时隐藏，正常/流式时显示 -->
      <InputArea ref="inputAreaRef"
        v-if="!interruptData"
        @send="handleSend"
        @stop="handleStop"
        @toggle-tool-calls="showToolCalls = $event"
        :disabled="false"
        :streaming="isStreaming"
        :show-tool-calls="showToolCalls"
      />
      </div>
      </div>
    </main>
  </div>
</template>

<script setup>
import {upsertAnswer, upsertBusinessResult} from './utils/answer-stream.js'
import { sameAssistantSource, procurementDisplayMessages } from './utils/message-display.js'
import { ref, onMounted, onBeforeUnmount, computed, watch, nextTick } from 'vue'
import { pendingFromHistory, canHandleInterrupt, interruptPermissionMessage } from './utils/approval.js'
import { auth, identity, can, clearSession, userStorageKey } from './auth/state.js'
import { navigationFor, roleLabels } from './auth/permissions.js'
import { authRequest } from './api/http.js'
import AccountSettings from './components/AccountSettings.vue'
import Dashboard from './components/Dashboard.vue'
import Operations from './components/Operations.vue'
import { replenishmentDraft } from './utils/dashboard.js'
import Sidebar from './components/Sidebar.vue'
import ConversationSidebar from './components/ConversationSidebar.vue'
import ChatArea from './components/ChatArea.vue'
import InputArea from './components/InputArea.vue'
import InterruptBanner from './components/InterruptBanner.vue'
import MessageItem from './components/MessageItem.vue'
import { streamChat, resumeChat, retryTool } from './api/chat.js'
import { getSessions, getMessages, deleteSession } from './api/history.js'

/**
 * DeepAgent 聊天应用主组件
 *
 * 消息流按时间顺序混合展示：
 *   [user] → [assistant 文本] → [tool 调用] → [assistant 文本] → ...
 * 支持 Human-in-the-Loop 中断：
 *   ... → [interrupt] → [用户决策/补充] → [继续]
 */

// ============================================================
// 状态定义
// ============================================================

const view=ref('overview'), dashboardFilter=ref(''), inputAreaRef=ref(null)
const operationViews=['receiving','stock','master','requests'], operationPart=ref(null)
function openPurchaseRequest(row){if(!can('procurement'))return;operationPart.value=row.id;view.value='requests'}
const navigationGroups=computed(()=>navigationFor(auth.user))
const activeGroup=computed(()=>navigationGroups.value.find(group=>group.tabs.some(tab=>tab.key===view.value))||navigationGroups.value[0]||{key:'home',label:'首页',tabs:[]})
const rememberedViews={}
watch(view,value=>{rememberedViews[activeGroup.value.key]=value})
function navigateGroup(key){
 const group=navigationGroups.value.find(item=>item.key===key)
 if(group && group.key!==activeGroup.value.key)navigateWorkspace({view:rememberedViews[key]||group.tabs[0].key})
}
function navigateWorkspace(target){if(!navigationGroups.value.some(g=>g.tabs.some(t=>t.key===target.view)))return;operationPart.value=null;view.value=target.view;dashboardFilter.value=target.filter||''}
async function startReplenishment(row){
 if(!can('procurement')||isStreaming.value||isResuming.value||interruptData.value)return
 handleNewChat();view.value='chat';await nextTick();inputAreaRef.value?.setDraft(replenishmentDraft(row))
}
// 会话列表
const sessions = ref([])
const storageKey=userStorageKey('erp.active-thread'), sessionGeneration=auth.generation
let alive=true
const loggingOut=ref(false),logoutError=ref('')
async function logout(){
 if(loggingOut.value)return
 loggingOut.value=true;logoutError.value=''
 try { await authRequest('logout',{}); clearSession() }
 catch(e){logoutError.value=e.message}
 finally{loggingOut.value=false}
}
onBeforeUnmount(()=>{alive=false;abortController?.abort();sessions.value=[];messages.value=[];interruptData.value=null})
// 当前会话 ID
const currentThreadId = ref(null)
// 消息列表（混合 user/assistant/tool，按时间顺序）
const messages = ref([])
// 是否正在流式输出
const isStreaming = ref(false)
const progress = ref('')
const retryingTool = ref(null)
const runError = ref('')
let activeAnswerId = null
const outputCallbacks = {
  onProgress: event => { progress.value = event.message; if(event.type==='procurement_progress')messages.value=procurementDisplayMessages(messages.value,[{workflow_type:'procurement'}]) },
  onAnswerDelta: event => { activeAnswerId=upsertAnswer(messages.value,event,false).id; progress.value='正在生成业务答复。' },
  onAnswer: event => { activeAnswerId=upsertAnswer(messages.value,event,true).id },
  onBusinessResult: event => { const message=upsertBusinessResult(messages.value,event); if(message)activeAnswerId=message.id },
  onToolError: event => { const message = messages.value.find(m => m.id === event.tool_call_id); if(message){message.failure=event.failure; message.tool_status='error'} }
}
function markAnswerRendered(id){const message=messages.value.find(item=>item.id===id);if(message)message.animate=false}
function reconcileDone(data){
 if(data.aborted || !data.id)return
 const message=upsertAnswer(messages.value,data,true)
 activeAnswerId=message.id
 // Preserve the rendered bubble while final model text types out. History
 // restores the same metadata when the user next opens the conversation.
 message.wasStreamed=true
}
function hasLiveAnswer(){return messages.value.some(message=>message.id===activeAnswerId && message.wasStreamed)}
async function refreshRunState(){ if(currentThreadId.value) await loadMessages(currentThreadId.value) }
async function handleRetryTool(toolId){
 if(isStreaming.value || isResuming.value || retryingTool.value || interruptData.value)return
 retryingTool.value=toolId; runError.value=''
 try{await retryTool(currentThreadId.value,toolId); await loadMessages(currentThreadId.value)}
 catch(error){runError.value=error.message}
 finally{retryingTool.value=null}
}
// 是否正在处理中断恢复
const isResuming = ref(false)
// 是否显示工具调用
const showToolCalls = ref(true)
// 中断数据（null = 无中断，object = 有中断等待处理）
const interruptData = ref(null)
const supplementClosed=ref(false),reopenSupplement=ref(null)
function closeSupplement(){if(isResuming.value)return;supplementClosed.value=true;nextTick(()=>reopenSupplement.value?.focus?.())}
const allPendingInterrupts = computed(() => interruptData.value?.interrupts || (interruptData.value ? [interruptData.value] : []))
const pendingInterrupts = computed(() => allPendingInterrupts.value.filter(item=>canHandleInterrupt(auth.user,item)))
const blockedInterrupts = computed(() => allPendingInterrupts.value.filter(item=>!canHandleInterrupt(auth.user,item)))
watch(()=>pendingInterrupts.value.map(item=>item.interrupt_type+':'+item.interrupt_id).join('|'),()=>{supplementClosed.value=false})
watch(currentThreadId, value => {
  try { value ? localStorage.setItem(storageKey, value) : localStorage.removeItem(storageKey) } catch {}
})
// AbortController 用于停止流式请求
let abortController = null

// ============================================================
// 生命周期
// ============================================================

/**
 * 组件挂载时加载会话列表
 */
onMounted(async () => {
  await loadSessions()
  if(!alive || sessionGeneration!==auth.generation)return
  let selected
  try { selected = localStorage.getItem(storageKey) } catch {}
  if (selected && sessions.value.some(session => session.thread_id === selected)) {
    await handleSelectSession(selected, false)
  }
})

// ============================================================
// 会话管理
// ============================================================

/**
 * 加载会话列表
 */
async function loadSessions() {
  if(!alive || sessionGeneration!==auth.generation)return
  try {
    const response = await getSessions(1, 100)
    if(!alive || sessionGeneration!==auth.generation)return
    sessions.value = response.sessions || []
  } catch (error) {
    console.error('[App] 加载会话列表失败:', error)
    sessions.value = []
  }
}

/**
 * 选择会话
 */
async function handleSelectSession(threadId, navigate = true) {
  if (navigate) view.value = 'chat'
  if (retryingTool.value || isStreaming.value || isResuming.value || threadId === currentThreadId.value) return

  runError.value = ''
  currentThreadId.value = threadId
  interruptData.value = null  // 切换会话时清除中断状态
  await loadMessages(threadId)
}

/**
 * 新建对话
 */
function handleNewChat() {
  view.value = 'chat'
  if (retryingTool.value || isStreaming.value || isResuming.value) return
  // 如果有正在进行的请求，取消它
  if (abortController) {
    abortController.abort()
    abortController = null
  }
  isStreaming.value = false
  isResuming.value = false
  interruptData.value = null
  currentThreadId.value = null
  messages.value = []
  runError.value = ''
}

/**
 * 删除会话
 */
async function handleDeleteSession(threadId) {
  if (retryingTool.value || isStreaming.value || isResuming.value) return
  try {
    await deleteSession(threadId)
    await loadSessions()

    if (currentThreadId.value === threadId) {
      handleNewChat()
    }
  } catch (error) {
    console.error('[App] 删除会话失败:', error)
    alert('删除会话失败，请重试')
  }
}

/**
 * 加载会话消息
 */
async function loadMessages(threadId) {
  if(!alive || sessionGeneration!==auth.generation)return
  try {
    const response = await getMessages(threadId)
    if (!alive || threadId !== currentThreadId.value) return
    messages.value = procurementDisplayMessages(response.messages || [],response.interrupts || [])
    interruptData.value = pendingFromHistory(response)
  } catch (error) {
    console.error('[App] 加载会话消息失败:', error)
    messages.value = []
  }
}

// ============================================================
// 停止对话
// ============================================================

/**
 * 停止当前对话
 */
function handleStop() {
  if (abortController) {
    abortController.abort()
    // Keep controls locked until the aborted request finishes its cleanup.
    console.log('[App] 用户停止了对话')
  }
}

// ============================================================
// 消息发送
// ============================================================

/**
 * 发送消息
 */
async function handleSend(message) {
  if (retryingTool.value || isStreaming.value || isResuming.value || interruptData.value) return
  if (!currentThreadId.value) currentThreadId.value = crypto.randomUUID()

  runError.value = ''
  progress.value = '正在处理您的请求。'
  activeAnswerId = null
  // 添加用户消息
  messages.value.push({
    id: `user-${Date.now()}`,
    role: 'user',
    content: message
  })

  // 清除中断状态（如果有）
  interruptData.value = null

  // 设置流式状态
  isStreaming.value = true

  let stopped = false
  // 创建新的 AbortController
  abortController = new AbortController()

  try {
    const result = await streamChat(
      message,
      currentThreadId.value,
      {
        ...outputCallbacks,
        // 接收到 token 时的回调
        onToken: (content, source) => {
          const lastMsg = messages.value[messages.value.length - 1]
          if (sameAssistantSource(lastMsg, source)) {
            // 追加到已有的 assistant 消息
            lastMsg.content += content
            lastMsg.source = source
          } else {
            // 前一条是 tool 或没有消息，创建新的 assistant 消息
            messages.value.push({
              id: `assistant-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
              role: 'assistant',
              content: content,
              source: source || 'main'
            })
          }
        },

        // 工具开始调用时的回调
        onToolStart: (tool) => {
          messages.value.push({
            id: tool.id,
            role: 'tool',
            tool_name: tool.name,
            args: '',
            text: '',
            images: [],
            source: tool.source || 'main',
            tool_status: 'calling'
          })
          messages.value=procurementDisplayMessages(messages.value)
        },

        // 工具参数片段的回调
        onToolArgs: (args, source, toolId) => {
          // 从后向前查找最后一个 calling 状态的 tool，支持嵌套工具调用
          for (let i = messages.value.length - 1; i >= 0; i--) {
            if (messages.value[i].role === 'tool' && messages.value[i].tool_status === 'calling' && (!toolId || messages.value[i].id === toolId)) {
              messages.value[i].args += args
              break
            }
          }
        },

        // 工具结果返回时的回调
        onToolResult: (tool) => {
          let toolMsg = null
          for (let i = messages.value.length - 1; i >= 0; i--) {
            if (messages.value[i].role === 'tool' && messages.value[i].id === tool.id) {
              toolMsg = messages.value[i]
              break
            }
          }
          if (toolMsg) {
            toolMsg.text = tool.text || ''
            toolMsg.images = tool.images || []
            toolMsg.tool_status = 'done'
          }
        },

        // 工具调用结束时的回调
        onToolEnd: (tool) => {
          if (tool) {
            let toolMsg = null
            for (let i = messages.value.length - 1; i >= 0; i--) {
              if (messages.value[i].role === 'tool' && messages.value[i].id === tool.id) {
                toolMsg = messages.value[i]
                break
              }
            }
            if (toolMsg && toolMsg.tool_status === 'calling') {
              toolMsg.tool_status = 'done'
            }
          }
          // 兜底：将所有仍在 calling 的工具标记为 done
          for (const m of messages.value) {
            if (m.role === 'tool' && m.tool_status === 'calling') {
              m.tool_status = 'done'
            }
          }
        },

        // ★ 中断检测回调
        onInterrupt: (data) => {
          console.log('[App] 检测到中断:', data.interrupt_type)
          // 兜底：将所有 calling 状态的工具标记为 done
          for (const m of messages.value) {
            if (m.role === 'tool' && m.tool_status === 'calling') {
              m.tool_status = 'done'
            }
          }
          // 清理空的 assistant 消息
          messages.value = messages.value.filter(m => {
            if (m.role === 'assistant' && !m.content && !m.business_results?.length) return false
            return true
          })
          // 设置中断数据，触发 InterruptBanner 显示
          interruptData.value = data
          messages.value=procurementDisplayMessages(messages.value,data.interrupts || [data])
          if (data.thread_id) currentThreadId.value = data.thread_id
        },

        // 流结束时的回调
        onDone: (data) => {
          reconcileDone(data)
          if(data.aborted){stopped=true; runError.value='执行已停止。已显示的查询结果已保留，请刷新执行状态确认是否有待处理操作。'}
          // 确保所有工具调用都标记为完成
          for (const m of messages.value) {
            if (m.role === 'tool' && m.tool_status === 'calling') {
              m.tool_status = 'done'
            }
          }
          // 清理空内容的 assistant 消息（流式过程中产生的残留）
          messages.value = messages.value.filter(m => {
            if (m.role === 'assistant' && !m.content && !m.business_results?.length && !data.aborted) {
              return false
            }
            return true
          })
          if (data.aborted) {
            const lastMsg = messages.value[messages.value.length - 1]
            if (lastMsg && lastMsg.role === 'assistant' && !lastMsg.content && !lastMsg.business_results?.length) {
              lastMsg.content = '（对话已停止）'
            }
          }
          // 仅在非中断完成时更新 thread_id
          if (data.thread_id && !currentThreadId.value) {
            currentThreadId.value = data.thread_id
            loadSessions()
          }
        },

        // 发生错误时的回调
        onError: (error) => { runError.value = error.message || '本次处理未完成，请刷新执行状态。' }
      },
      abortController.signal
    )
  } catch (error) {
    if (error.name === 'AbortError') {
      console.log('[App] 请求已被取消')
    } else {
      console.error('[App] 发送消息失败:', error)
    }
  } finally {
    isStreaming.value = false
    abortController = null
    await loadSessions()
    if (alive && currentThreadId.value && !stopped && (runError.value || !hasLiveAnswer())) await loadMessages(currentThreadId.value)
  }
}

// ============================================================
// 中断恢复
// ============================================================

/**
 * 处理中断恢复（由 InterruptBanner 触发）
 *
 * @param {Object} resumeData - 恢复数据：
 *   - 数据补充: { supplement: "..." }
 *   - HITL 审批: { decisions: [{ type: "approve" }] }
 */
async function handleResume(resumeData) {
  if (isResuming.value || !currentThreadId.value) return
  const selected=resumeData?.responses ? Object.keys(resumeData.responses) : [resumeData?.interrupt_id]
  if(!selected.length || selected.some(id=>!allPendingInterrupts.value.some(item=>item.interrupt_id===id && canHandleInterrupt(auth.user,item))))return

  isResuming.value = true
  runError.value = ''
  progress.value = '正在继续处理您的请求。'
  activeAnswerId = null

  let stopped = false
  // 创建新的 AbortController
  abortController = new AbortController()

  try {
    const result = await resumeChat(
      currentThreadId.value,
      resumeData,
      {
        ...outputCallbacks,
        onToken: (content, source) => {
          const lastMsg = messages.value[messages.value.length - 1]
          if (sameAssistantSource(lastMsg, source)) {
            lastMsg.content += content
            lastMsg.source = source
          } else {
            messages.value.push({
              id: `assistant-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
              role: 'assistant',
              content: content,
              source: source || 'main'
            })
          }
        },

        onToolStart: (tool) => {
          messages.value.push({
            id: tool.id,
            role: 'tool',
            tool_name: tool.name,
            args: '',
            text: '',
            images: [],
            source: tool.source || 'main',
            tool_status: 'calling'
          })
          messages.value=procurementDisplayMessages(messages.value)
        },

        onToolArgs: (args, source, toolId) => {
          for (let i = messages.value.length - 1; i >= 0; i--) {
            if (messages.value[i].role === 'tool' && messages.value[i].tool_status === 'calling' && (!toolId || messages.value[i].id === toolId)) {
              messages.value[i].args += args
              break
            }
          }
        },

        onToolResult: (tool) => {
          for (let i = messages.value.length - 1; i >= 0; i--) {
            if (messages.value[i].role === 'tool' && messages.value[i].id === tool.id) {
              messages.value[i].text = tool.text || ''
              messages.value[i].images = tool.images || []
              messages.value[i].tool_status = 'done'
              break
            }
          }
        },

        onToolEnd: (tool) => {
          if (tool) {
            for (let i = messages.value.length - 1; i >= 0; i--) {
              if (messages.value[i].role === 'tool' && messages.value[i].id === tool.id && messages.value[i].tool_status === 'calling') {
                messages.value[i].tool_status = 'done'
                break
              }
            }
          }
          for (const m of messages.value) {
            if (m.role === 'tool' && m.tool_status === 'calling') {
              m.tool_status = 'done'
            }
          }
        },

        // ★ 恢复后仍可能再次中断（如：先数据补充 → 再 HITL 审批）
        onInterrupt: (data) => {
          console.log('[App] 恢复后再次中断:', data.interrupt_type)
          for (const m of messages.value) {
            if (m.role === 'tool' && m.tool_status === 'calling') {
              m.tool_status = 'done'
            }
          }
          messages.value = messages.value.filter(m => {
            if (m.role === 'assistant' && !m.content && !m.business_results?.length) return false
            return true
          })
          interruptData.value = data
          messages.value=procurementDisplayMessages(messages.value,data.interrupts || [data])
          if (data.thread_id) currentThreadId.value = data.thread_id
        },

        onDone: (data) => {
          reconcileDone(data)
          if(data.aborted){stopped=true; runError.value='执行已停止。已显示的查询结果已保留，请刷新执行状态确认是否有待处理操作。'}
          for (const m of messages.value) {
            if (m.role === 'tool' && m.tool_status === 'calling') {
              m.tool_status = 'done'
            }
          }
          messages.value = messages.value.filter(m => {
            if (m.role === 'assistant' && !m.content && !m.business_results?.length) return false
            return true
          })
          // 非中断完成 → 清除中断状态
          if (!data.interrupted) {
            interruptData.value = null
            // 更新 thread_id
            if (data.thread_id && !currentThreadId.value) {
              currentThreadId.value = data.thread_id
              loadSessions()
            }
          }
        },

        onError: (error) => { runError.value = error.message || '本次处理未完成，请刷新执行状态。' }
      },
      abortController.signal
    )
  } catch (error) {
    if (error.name === 'AbortError') {
      console.log('[App] 恢复请求已被取消')
    } else {
      console.error('[App] 恢复请求失败:', error)
    }
  } finally {
    isResuming.value = false
    abortController = null
    await loadSessions()
    if (alive && currentThreadId.value && !stopped && (runError.value || !hasLiveAnswer())) await loadMessages(currentThreadId.value)
  }
}
</script>

<style scoped>
.supplement-paused{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:16px 24px;background:#fffbeb;color:#604615;border-top:1px solid #e6c78b;font-size:14px}.supplement-paused button{padding:9px 14px;border:1px solid #d4c59d;border-radius:7px;background:white;color:#604615;cursor:pointer;white-space:nowrap}@media(max-width:650px){.supplement-paused{flex-wrap:wrap}}
.run-error { margin:12px 24px; padding:14px; color:#9a3412; background:#fff7ed; border:1px solid #fed7aa; border-radius:10px; }
.run-error button { margin-left:12px; cursor:pointer; }
.app-container {
  display: flex;
  height: 100vh;
  height: 100dvh;
  background: #f7f8f4;
  position: relative;
  overflow: hidden;
}

.main-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-width: 0;
  background: #ffffff;
  position: relative;
  border-left: 1px solid #e7ebe7;
  box-shadow: 0 0 20px rgba(0, 0, 0, 0.05);
}
</style>

<style>
.identity-bar{display:flex;align-items:center;gap:10px;font-size:12px;color:#365e4f;flex-wrap:wrap}.workspace-header{display:flex;justify-content:space-between;align-items:center;gap:12px;min-height:58px;padding:0 28px;background:#fff;border-bottom:1px solid #edf1eb;flex-shrink:0}.workspace-breadcrumb{font-size:13px;color:#84958d}.workspace-breadcrumb>span{margin:0 12px;color:#c0cdc4}.workspace-breadcrumb strong{color:#365e4f;font-weight:500}.workspace-caption{font-size:12px;color:#8b9c91;letter-spacing:1px}
.workspace-nav{display:flex;gap:24px;padding:0 28px;border-bottom:1px solid #dce7df;background:#fff;overflow-x:auto;flex-shrink:0}.workspace-nav button{padding:15px 2px;border:0;border-bottom:3px solid transparent;background:transparent;color:#617c72;cursor:pointer;font-size:14px;white-space:nowrap}.workspace-nav button.active{border-bottom-color:#238575;color:#1d7868;font-weight:600}.workspace-nav button:hover{color:#1d7868}.workspace-nav button:focus-visible{outline:2px solid #238575;outline-offset:-3px}.assistant-layout{display:flex;flex:1;min-height:0;min-width:0;overflow:hidden}.interrupt-workspace{flex:1 1 0;min-height:0;overflow-y:auto;overscroll-behavior:contain}.chat-workspace.interrupt-open .chat-area{display:none}.chat-workspace{min-width:0;display:flex;flex-direction:column;flex:1;min-height:0;overflow:hidden}
@media(max-width:700px){.workspace-header{padding:0 16px}.workspace-caption{display:none}.workspace-nav{padding:0 16px;gap:18px}}
</style>

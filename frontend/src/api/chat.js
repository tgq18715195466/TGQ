import { apiFetch as fetch } from './http.js'
import { mergeBusinessResults } from '../utils/answer-stream.js'
/**
 * 对话 API 模块
 *
 * 提供流式对话、中断恢复和会话状态查询接口
 */

const API_BASE = '/api/chat'

/**
 * 流式对话
 *
 * 使用 fetch + ReadableStream 接收流式响应（支持 POST 请求）
 *
 * @param {string} message - 用户消息
 * @param {string|null} threadId - 会话 ID，为空则创建新会话
 * @param {Object} callbacks - 回调函数对象
 * @param {Function} callbacks.onToken - 接收到 token 时的回调 (content: string, source: string) => void
 * @param {Function} callbacks.onToolStart - 工具开始调用时的回调 (tool: object) => void
 * @param {Function} callbacks.onToolArgs - 工具参数片段的回调 (args: string, source: string) => void
 * @param {Function} callbacks.onToolResult - 工具结果返回时的回调 (tool: object) => void
 * @param {Function} callbacks.onToolEnd - 工具调用结束时的回调 (tool: object) => void
 * @param {Function} callbacks.onInterrupt - 检测到中断时的回调 (interruptData: object) => void
 * @param {Function} callbacks.onBusinessResult - 业务事实回调 ({type:'business_result', id:答案消息ID, result:业务结果}) => void
 * @param {Function} callbacks.onAnswerDelta - 模型分析片段回调 ({id, content}) => void
 * @param {Function} callbacks.onAnswer - 最终答案回调 ({id, content, business_results?}) => void
 * @param {Function} callbacks.onDone - 流结束时的回调 (data: object) => void
 * @param {Function} callbacks.onError - 发生错误时的回调 (error: Error) => void
 * @param {AbortSignal} signal - 可选的 AbortSignal，用于取消请求
 * @returns {Promise} 返回包含 thread_id, content, tool_calls 的结果
 */
export async function streamChat(message, threadId = null, callbacks = {}, signal = null) {
  // 用于收集完整内容
  let fullContent = ''
  let toolCalls = []
  // 工具调用栈，支持嵌套工具调用（主代理调 task → 子代理调工具）
  let toolStack = []

  try {
    // 使用 fetch 发送 POST 请求，支持 AbortController
    const response = await fetch(`${API_BASE}/stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        message: message,
        thread_id: threadId
      }),
      signal: signal  // 支持取消请求
    })

    if (!response.ok) {
      const detail = await response.json().catch(() => ({}))
      throw new Error(typeof detail.detail === "string" ? detail.detail : `请求未完成（状态码 ${response.status}），请刷新执行状态。`)
    }

    return await _processStream(response, threadId, callbacks, fullContent, toolCalls, toolStack)

  } catch (error) {
    if (error.name === 'AbortError') {
      // 用户主动取消，返回已收集的内容
      console.log('[ChatAPI] 请求已被用户取消')
      const result = {
        thread_id: threadId,
        content: fullContent,
        tool_calls: toolCalls,
        aborted: true
      }
      callbacks.onDone?.(result)
      return result
    }
    console.error('[ChatAPI] 流式请求失败:', error)
    callbacks.onError?.(error)
    throw error
  }
}

/**
 * 流式中断恢复
 *
 * 当中断发生后，前端收集用户决策/补充数据，通过此函数恢复 Agent 执行。
 *
 * @param {string} threadId - 会话 ID
 * @param {Object} resumeData - 恢复数据，格式取决于中断类型：
 *   - 数据补充: { supplement: "用户输入的补充信息" }
 *   - HITL 审批: { decisions: [{ type: "approve" }] } 或 [{ type: "reject" }]
 * @param {Object} callbacks - 回调函数对象（与 streamChat 相同）
 * @param {AbortSignal} signal - 可选的 AbortSignal，用于取消请求
 * @returns {Promise} 返回包含 thread_id, content, tool_calls 的结果
 */
export async function resumeChat(threadId, resumeData, callbacks = {}, signal = null) {
  let fullContent = ''
  let toolCalls = []
  let toolStack = []

  try {
    const response = await fetch(`${API_BASE}/${threadId}/resume`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ resume: resumeData }),
      signal: signal
    })

    if (!response.ok) {
      const detail = await response.json().catch(() => ({}))
      throw new Error(typeof detail.detail === "string" ? detail.detail : `恢复请求失败: ${response.status}`)
    }

    return await _processStream(response, threadId, callbacks, fullContent, toolCalls, toolStack)

  } catch (error) {
    if (error.name === 'AbortError') {
      console.log('[ChatAPI] 恢复请求已被用户取消')
      const result = {
        thread_id: threadId,
        content: fullContent,
        tool_calls: toolCalls,
        aborted: true
      }
      callbacks.onDone?.(result)
      return result
    }
    console.error('[ChatAPI] 恢复请求失败:', error)
    callbacks.onError?.(error)
    throw error
  }
}

/**
 * 处理流式响应的内部函数（streamChat 和 resumeChat 共用）
 */
async function _processStream(response, threadId, callbacks, fullContent, toolCalls, toolStack) {
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let pending = null
  let answerId = null
  let businessResults = []
  let failureToolCallIds = []
  let businessResultsMode = null
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) throw new Error('连接提前结束，请重新打开会话检查执行状态')
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop()
      for (const line of lines) {
        if (!line.startsWith('data:')) continue
        let data
        try { data = JSON.parse(line.slice(5).trim()) }
        catch { continue }
        switch (data.type) {
          case 'progress':
          case 'procurement_progress':
            callbacks.onProgress?.(data)
            break
          case 'tool_error':
            callbacks.onToolError?.(data)
            break
          case 'answer_delta':
            answerId = data.id || answerId
            businessResults = mergeBusinessResults(businessResults, data.business_results)
            fullContent += data.content
            callbacks.onAnswerDelta?.(data)
            break
          case 'answer':
            answerId = data.id || answerId
            businessResults = mergeBusinessResults(businessResults, data.business_results, data.business_results_mode === 'replace')
            if (data.business_results_mode === 'replace') businessResultsMode = 'replace'
            if (Array.isArray(data.failure_tool_call_ids)) failureToolCallIds = data.failure_tool_call_ids
            if (typeof data.content === 'string') fullContent = data.content
            callbacks.onAnswer?.(data)
            break
          case 'business_result':
            if (!data.id || !data.result || typeof data.result !== 'object' || Array.isArray(data.result)) break
            answerId = data.id
            businessResults = mergeBusinessResults(businessResults, [data.result])
            callbacks.onBusinessResult?.(data)
            break
          case 'token':
            fullContent += data.content
            callbacks.onToken?.(data.content, data.source)
            break
          case 'tool_start': {
            const tool = { id: data.tool_call_id, name: data.tool_name, args: '', source: data.source, text: '', images: [] }
            toolStack.push(tool)
            callbacks.onToolStart?.(tool)
            break
          }
          case 'tool_args': {
            const tool = toolStack.find(t => t.id === data.tool_call_id)
            if (tool) tool.args += data.args
            callbacks.onToolArgs?.(data.args, data.source, data.tool_call_id)
            break
          }
          case 'tool_result': {
            const index = toolStack.findIndex(t => t.id === data.tool_call_id)
            if (index >= 0) {
              const [tool] = toolStack.splice(index, 1)
              tool.text = data.text || ''; tool.images = data.images || []
              toolCalls.push(tool); callbacks.onToolResult?.(tool)
            }
            break
          }
          case 'tool_end':
            callbacks.onToolEnd?.({id: data.tool_call_id, name: data.tool_name, source: data.source})
            break
          case 'interrupt':
            pending = data
            callbacks.onInterrupt?.(data)
            break
          case 'done': {
            businessResults = mergeBusinessResults(businessResults, data.business_results, data.business_results_mode === 'replace')
            if (data.business_results_mode === 'replace') businessResultsMode = 'replace'
            if (Array.isArray(data.failure_tool_call_ids)) failureToolCallIds = data.failure_tool_call_ids
            const result = {thread_id: data.thread_id || threadId, content: typeof data.content === 'string' ? data.content : fullContent, tool_calls: toolCalls, interrupted: Boolean(data.interrupted), interrupt_data: pending}
            if (data.id || answerId) result.id = data.id || answerId
            if (businessResults.length || Array.isArray(data.business_results)) result.business_results = businessResults
            if (failureToolCallIds.length || Array.isArray(data.failure_tool_call_ids)) result.failure_tool_call_ids = failureToolCallIds
            if (businessResultsMode) result.business_results_mode = businessResultsMode
            callbacks.onDone?.(result)
            return result
          }
          case 'error': throw new Error(data.message)
        }
      }
    }
  } finally {
    await reader.cancel().catch(() => {})
    reader.releaseLock()
  }
}

/**
 * 获取会话状态
 *
 * @param {string} threadId - 会话 ID
 * @returns {Promise} 返回会话状态对象
 */
export async function getChatState(threadId) {
  const response = await fetch(`${API_BASE}/${threadId}`)

  if (!response.ok) {
    throw new Error('获取会话状态失败')
  }

  return response.json()
}

/**
 * 获取会话历史状态列表
 *
 * @param {string} threadId - 会话 ID
 * @param {number} limit - 返回的最大状态数量
 * @returns {Promise} 返回状态历史列表
 */
export async function getChatHistory(threadId, limit = 50) {
  const response = await fetch(`${API_BASE}/${threadId}/history?limit=${limit}`)

  if (!response.ok) {
    throw new Error('获取会话历史失败')
  }

  return response.json()
}

export async function retryTool(threadId, toolId) {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(threadId)}/tools/${encodeURIComponent(toolId)}/retry`, {method:'POST'})
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '查询重试失败，请稍后再试。')
  return data
}

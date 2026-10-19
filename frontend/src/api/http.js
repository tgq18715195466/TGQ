import { auth, clearSession, sessionSignal } from '../auth/state.js'
import { tabSession, newTabSession, saveTabSession } from '../auth/tab-session.js'
export function friendlyError(detail, fallback='请求未完成，请您稍后重试。') {
 if (typeof detail === 'string' && /[\u4e00-\u9fff]/.test(detail)) return detail
 if (Array.isArray(detail)) return '提交信息不符合要求，请您检查必填项和输入格式。'
 return fallback
}
export async function apiFetch(url, options={}) {
 const generation=auth.generation
 const assertSession=()=>{if(generation!==auth.generation){const error=new Error('会话已切换');error.name='AbortError';throw error}}
 const signal=options.signal?AbortSignal.any([options.signal,sessionSignal()]):sessionSignal()
 const headers=new Headers(options.headers)
 if(!headers.has('X-ERP-Session'))headers.set('X-ERP-Session',tabSession())
 let response
 try { response=await globalThis.fetch(url,{...options,headers,credentials:'same-origin',signal}) }
 catch(error) { if(error.name==='AbortError')throw error; throw new Error('网络连接失败，请您检查网络后重试。') }
 assertSession()
 if(response.status===401) {
  if(auth.user)clearSession('您的登录已失效，请重新登录。')
  const error=new Error(url.endsWith('/login')?'用户名或密码不正确，或账号已停用。':'您的登录已失效，请重新登录。');error.status=401;throw error
 }
 if(response.status===403)throw new Error('您没有执行此操作的权限，请联系管理员。')
 const readJson=response.json.bind(response)
 response.json=async()=>{const data=await readJson();assertSession();return data}
 return response
}
export async function authRequest(path, body, method=body?'POST':'GET') {
 // A copied tab may inherit sessionStorage. Every successful login gets a new
 // selector so it cannot replace the original tab's cookie, even in that case.
 const newSession=['login','setup'].includes(path)?newTabSession():null
 const headers={...(body?{'Content-Type':'application/json'}:{}),...(newSession?{'X-ERP-Session':newSession}:{})}
 const response=await apiFetch('/api/auth/'+path,{method,headers,...(body?{body:JSON.stringify(body)}:{})})
 const data=await response.json().catch(error=>{if(error.name==='AbortError')throw error;return {}})
 if(!response.ok){const error=new Error(friendlyError(data.detail,'账户操作未完成，请您检查输入后重试。'));error.status=response.status;throw error}
 if(newSession)saveTabSession(newSession)
 return data
}

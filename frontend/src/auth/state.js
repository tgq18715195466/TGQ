import { reactive, computed } from 'vue'
import { hasPermission } from './permissions.js'
export const auth = reactive({ user:null, ready:false, setupRequired:false, generation:0, notice:'' })
export const identity = computed(()=>auth.user ? `${auth.user.display_name || auth.user.username}（${auth.user.username}）` : '')
export const can = permission => hasPermission(auth.user, permission)
export const userStorageKey = key => `${key}:${auth.user?.id ?? 'anonymous'}`
let sessionController = new AbortController()
export const sessionSignal = () => sessionController.signal
export function clearSession(message='') {
 sessionController.abort()
 sessionController = new AbortController()
 try {
  if(auth.user){localStorage.removeItem(userStorageKey('erp.active-thread'));sessionStorage.removeItem(userStorageKey('erp.pending-operation'))}
 } catch {}
 auth.generation++
 auth.user=null
 auth.notice=message
 try { localStorage.removeItem('erp.active-thread'); sessionStorage.removeItem('erp.pending-operation') } catch {}
}
export function setUser(user) { clearSession(); auth.user=user; auth.setupRequired=false }

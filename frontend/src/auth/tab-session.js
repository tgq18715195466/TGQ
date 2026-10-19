// Only this non-secret cookie selector is stored in the tab. Credentials stay HttpOnly.
const storageKey = 'erp.tab-session'
let memorySession
export const newTabSession = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('')

export function saveTabSession(value) {
 try { sessionStorage.setItem(storageKey, value) } catch { memorySession = value }
}

export function tabSession() {
 let value
 try { value = sessionStorage.getItem(storageKey) } catch { value = memorySession }
 if (!/^[0-9a-f]{32}$/.test(value || '')) {
  value = newTabSession()
  saveTabSession(value)
 }
 return value
}

export function sessionArtifactUrl(value) {
 try {
  const origin = globalThis.location?.origin || 'http://localhost'
  const url = new URL(value, origin)
  if (url.origin !== origin || !/^\/api\/artifacts\/[^/]+$/.test(url.pathname)) return value
  url.searchParams.set('erp_session', tabSession())
  return url.pathname + url.search + url.hash
 } catch { return value }
}

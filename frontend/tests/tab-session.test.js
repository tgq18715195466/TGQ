import test from 'node:test'
import assert from 'node:assert/strict'
import { apiFetch, authRequest } from '../src/api/http.js'
import { sessionArtifactUrl, tabSession } from '../src/auth/tab-session.js'
import { component, mount, settle } from './component-helper.js'

function storage(copy=[]) {
 const entries=new Map(copy)
 return {entries,getItem:key=>entries.get(key)??null,setItem:(key,value)=>entries.set(key,value),removeItem:key=>entries.delete(key)}
}
test('each tab keeps its request identity through refresh and another tab login',async()=>{
 const original={fetch:globalThis.fetch,sessionStorage:globalThis.sessionStorage}
 const calls=[]
 globalThis.fetch=async(url,options)=>{calls.push({url,scope:new Headers(options.headers).get('X-ERP-Session')});return new Response(JSON.stringify({user:{id:1}}))}
 try {
  const approver=storage(),buyer=storage()
  globalThis.sessionStorage=approver
  await authRequest('login',{username:'approver',password:'example'})
  await apiFetch('/api/auth/me')
  const approverScope=calls.at(-1).scope
  assert.match(approverScope,/^[a-f0-9]{32}$/)
  globalThis.sessionStorage=buyer
  await authRequest('login',{username:'buyer',password:'example'})
  await apiFetch('/api/auth/me')
  assert.notEqual(calls.at(-1).scope,approverScope)
  globalThis.sessionStorage=storage(approver.entries) // Reload keeps sessionStorage.
  await apiFetch('/api/auth/me')
  assert.equal(calls.at(-1).scope,approverScope)
  const cloned=storage(approver.entries) // Duplicated tabs inherit storage initially.
  globalThis.sessionStorage=cloned
  await authRequest('login',{username:'buyer',password:'example'})
  assert.notEqual(calls.at(-1).scope,approverScope)
  globalThis.sessionStorage=approver
  await apiFetch('/api/auth/me')
  assert.equal(calls.at(-1).scope,approverScope)
  assert.ok(!JSON.stringify([...approver.entries]).includes('example'))
 } finally { Object.assign(globalThis,original) }
})

test('failed login leaves this tab session selection intact',async()=>{
 const original={fetch:globalThis.fetch,sessionStorage:globalThis.sessionStorage}
 globalThis.sessionStorage=storage()
 const scopes=[]
 globalThis.fetch=async(url,options)=>{scopes.push(new Headers(options.headers).get('X-ERP-Session'));return new Response('{}',{status:url.endsWith('/login')?401:200})}
 try {
  await apiFetch('/api/auth/me')
  assert.match(scopes[0],/^[a-f0-9]{32}$/)
  await assert.rejects(authRequest('login',{username:'buyer',password:'wrong'}))
  await apiFetch('/api/auth/me')
  assert.equal(scopes[0],scopes.at(-1))
 } finally { Object.assign(globalThis,original) }
})

test('artifact URLs select this tab without adding session selectors to external URLs',()=>{
 const original=globalThis.sessionStorage
 globalThis.sessionStorage=storage()
 try {
  const scoped=sessionArtifactUrl('/api/artifacts/chart.svg?download=1')
  assert.equal(scoped,`/api/artifacts/chart.svg?download=1&erp_session=${tabSession()}`)
  assert.equal(sessionArtifactUrl('https://external.example/api/artifacts/chart.svg'),'https://external.example/api/artifacts/chart.svg')
  assert.equal(sessionArtifactUrl('/api/auth/me'),'/api/auth/me')
  assert.equal(sessionArtifactUrl('data:image/png;base64,AAA'),'data:image/png;base64,AAA')
 } finally { globalThis.sessionStorage=original }
})

test('rendered charts, attachment links and tool images use this page session',async()=>{
 const original=globalThis.sessionStorage
 globalThis.sessionStorage=storage()
 const Markdown=await component('components/MarkdownRenderer.vue')
 const Message=await component('components/MessageItem.vue')
 const chart='/api/artifacts/chart.svg',report='/api/artifacts/report.md'
 const markdown=mount(Markdown,{content:`![图表](${chart})\n\n[下载报告](${report})`})
 try {
  await settle()
  const html=markdown.nodes().find(n=>n.props?.innerHTML)?.props.innerHTML
  assert.ok(html.includes(sessionArtifactUrl(chart)))
  assert.ok(html.includes(sessionArtifactUrl(report)))
 } finally { markdown.unmount() }
 const tool=mount(Message,{message:{id:'chart',role:'tool',tool_name:'generate_visualization',images:[chart],tool_status:'done'}})
 try {
  await settle()
  assert.ok(tool.nodes().some(n=>n.tagName==='IMG'&&n.props.src===sessionArtifactUrl(chart)))
 } finally { tool.unmount();globalThis.sessionStorage=original }
})

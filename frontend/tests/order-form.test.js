import test from 'node:test'
import assert from 'node:assert/strict'
import { initialOrderValues, orderFormGroups, orderFormErrors, orderFormResponse } from '../src/utils/order-form.js'
import {settle} from './component-helper.js'
import {setUser} from '../src/auth/state.js'
test.beforeEach(()=>setUser({id:1,username:'buyer',role:'purchaser'}))

const fields = [
  { path: '0.supplierId', name: 'supplierId', label: '供应商编号（ID）', type: 'integer', min: 1, required: true, value: 15, order_index: 0, detail_index: null },
  { path: '0.expectedDeliveryDate', name: 'expectedDeliveryDate', label: '预计交货日期', type: 'date', min: '2026-10-06', required: true, value: '2099-01-01', order_index: 0, detail_index: null },
  { path: '0.orderDetail.0.quantity', name: 'quantity', label: '数量', type: 'integer', min: 1, required: true, value: 2, order_index: 0, detail_index: 0 },
  { path: '0.orderDetail.0.unitPrice', name: 'unitPrice', label: '单价', type: 'decimal', min: 0, required: true, value: 0, order_index: 0, detail_index: 0 },
]
const item = { interrupt_id: 'one', fields }
test('prefill retains zero and groups orders and detail rows without collisions', () => {
  assert.equal(initialOrderValues(item)['0.orderDetail.0.unitPrice'], '0')
  const more = { ...item, fields: [...fields, { ...fields[2], path: '0.orderDetail.1.quantity', detail_index: 1 }, { ...fields[0], path: '1.supplierId', order_index: 1 }] }
  const groups = orderFormGroups(more)
  assert.equal(groups.length, 2)
  assert.equal(groups[0].details.length, 2)
  assert.equal(groups[1].fields[0].path, '1.supplierId')
})
for (const [path, value] of [
  ['0.supplierId', '1.5'], ['0.supplierId', '9007199254740992'],
  ['0.orderDetail.0.quantity', '0'], ['0.orderDetail.0.quantity', '-2'],
  ['0.orderDetail.0.unitPrice', 'NaN'], ['0.orderDetail.0.unitPrice', 'Infinity'],
  ['0.orderDetail.0.unitPrice', '-0.1'], ['0.expectedDeliveryDate', '2099-02-30'],
  ['0.expectedDeliveryDate', '2020-01-01'], ['0.expectedDeliveryDate', '20990101'],
]) {
  test(`reject invalid ${path}: ${value}`, () => {
    assert.ok(orderFormErrors(item, { ...initialOrderValues(item), [path]: value })[path])
  })
}
test('structured response keeps exact money string and ID; no approval decision', () => {
  const values = { ...initialOrderValues(item), '0.orderDetail.0.unitPrice': '12.300000000000000001' }
  const result = orderFormResponse(item, values, '  包装待确认  ')
  assert.deepEqual(result, { interrupt_id: 'one', values, supplement: '包装待确认' })
  assert.throws(() => orderFormResponse(item, { ...values, '0.supplierId': '' }, '备注不能替代必填字段'))
})
test('explicit free supplement preserves legacy interface for uncertainty', () => {
  assert.deepEqual(orderFormResponse(item, {}, '供应商待确认', true), { interrupt_id: 'one', supplement: '供应商待确认' })
  assert.throws(() => orderFormResponse(item, {}, ' ', true))
  assert.deepEqual(initialOrderValues({}), {})
})

// Compile and SSR-render the real SFC, not a copied test-only template.
import { readFile } from 'node:fs/promises'
import { parse, compileScript } from '@vue/compiler-sfc'
import { createSSRApp } from 'vue'
import { renderToString } from 'vue/server-renderer'
const componentUrl = new URL('../src/components/InterruptBanner.vue', import.meta.url)
const { descriptor } = parse(await readFile(componentUrl, 'utf8'))
const compiled = compileScript(descriptor, { id: 'forms-test', inlineTemplate: true, templateOptions: { ssr: true } })
const source = compiled.content.replace(/from (['"])([^'"]+)\1/g, (_, quote, specifier) => `from ${quote}${specifier.startsWith('.') ? new URL(specifier, componentUrl).href : import.meta.resolve(specifier)}${quote}`)
const Banner = (await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)).default

test('actual banner renders Chinese typed inputs, prefill, uncertainty option and approval notice', async () => {
  const html = await renderToString(createSSRApp(Banner, { interruptData: { ...item, interrupt_type: 'order_info_supplement', missing_fields: 'quantity', collected_data: 'supplierId=15' } }))
  assert.match(html, /订单 1/)
  assert.match(html, /明细 1/)
  assert.match(html, /供应商编号（ID）/)
  assert.match(html, /<select[^>]*aria-label="订单1 供应商"/)
  assert.doesNotMatch(html, /type="number"[^>]*aria-label="订单1 供应商"/)
  assert.match(html, /type="date"/)
  assert.match(html, /value="0"/)
  assert.match(html, /信息尚不确定，改用自由补充/)
  assert.match(html, /须由其他审批人正式审核/)
})
test('old interrupt still renders a free supplement textarea', async () => {
  const html = await renderToString(createSSRApp(Banner, { interruptData: { interrupt_id: 'old', interrupt_type: 'order_info_supplement', missing_fields: '物料信息' } }))
  assert.match(html, /补充说明（必填）/)
  assert.doesNotMatch(html, /class="order-group"/)
})

import { createRenderer, h, nextTick, reactive } from 'vue'
const domCompiled = compileScript(descriptor, { id: 'forms-runtime', inlineTemplate: true })
const domSource = domCompiled.content.replace(/from (['"])([^'"]+)\1/g, (_, quote, specifier) => `from ${quote}${specifier.startsWith('.') ? new URL(specifier, componentUrl).href : import.meta.resolve(specifier)}${quote}`)
const RuntimeBanner = (await import(`data:text/javascript;base64,${Buffer.from(domSource).toString('base64')}`)).default
function mountBanner() {
  const previousFetch=globalThis.fetch
  globalThis.fetch=async url=>new Response(JSON.stringify(url.startsWith('/api/dashboard/suppliers')?{records:[{id:15,name:'供应商甲',status:1},{id:42,name:'供应商乙',status:1}],total:2}:{code:200,data:{records:[],total:0}}),{headers:{'Content-Type':'application/json'}})
  // Minimal host for Vue's actual component state/events, with no browser/network.
  const element = type => ({ type, tagName: type.toUpperCase(), children: [], props: {}, value: '', addEventListener() {}, removeEventListener() {}, getRootNode() { return globalThis.document } })
  const renderer = createRenderer({
    createElement: element, createText: text => ({ text }), createComment: text => ({ text }),
    setText: (node, text) => { node.text = text }, setElementText: (node, text) => { node.text = text; node.children = [] },
    patchProp: (node, key, before, after) => { node.props[key] = after; if (key === 'value' || key === 'type') node[key] = after },
    insert(node, parent, anchor) { if (node.parent) this.remove(node); node.parent = parent; const i = parent.children.indexOf(anchor); parent.children.splice(i < 0 ? parent.children.length : i, 0, node) },
    remove(node) { const i = node.parent?.children.indexOf(node); if (i >= 0) node.parent.children.splice(i, 1) },
    parentNode: node => node.parent, nextSibling: node => node.parent?.children[node.parent.children.indexOf(node) + 1],
  })
  const root = element('root')
  const state = reactive({ item: { ...item, interrupt_type: 'order_info_supplement' }, submitting: false })
  const emitted = []
  const app = renderer.createApp({ render: () => h(RuntimeBanner, { interruptData: state.item, submitting: state.submitting, onResume: value => emitted.push(value) }) })
  app.mount(root)
  const nodes = () => { const result = []; const walk = node => { result.push(node); (node.children || []).forEach(walk) }; walk(root); return result }
  return { state, emitted, app, nodes, restore(){globalThis.fetch=previousFetch}, submit: () => nodes().find(n => n.tagName === 'BUTTON' && n.props.class?.includes('supplement-btn')).props.onClick() }
}

test('component retains draft on same interrupt refresh; new interrupt resets draft', async () => {
  const documentBefore = globalThis.document
  const DocumentBefore = globalThis.Document
  const ShadowRootBefore = globalThis.ShadowRoot
  globalThis.Document = class Document {}
  globalThis.ShadowRoot = class ShadowRoot {}
  globalThis.document = Object.assign(new globalThis.Document(), { activeElement: null })
  const mounted = mountBanner()
  try {
    await settle()
    const input = mounted.nodes().find(n => n.props?.['aria-label']?.includes('供应商'))
    input.props.onChange({ target: { value: '42' } })
    await nextTick()
    mounted.state.item = { ...mounted.state.item }
    await nextTick()
    mounted.submit()
    assert.equal(mounted.emitted[0].values['0.supplierId'], '42')
    mounted.state.item = { ...mounted.state.item, interrupt_id: 'new' }
    await settle()
    mounted.submit()
    assert.equal(mounted.emitted[1].values['0.supplierId'], '15')
    assert.equal(mounted.emitted[1].interrupt_id, 'new')
  } finally { mounted.app.unmount();mounted.restore(); globalThis.document = documentBefore; globalThis.Document = DocumentBefore; globalThis.ShadowRoot = ShadowRootBefore }
})

test('component blocks invalid fields and submitting state; explicit free mode emits text only', async () => {
  const documentBefore = globalThis.document
  const DocumentBefore = globalThis.Document
  const ShadowRootBefore = globalThis.ShadowRoot
  globalThis.Document = class Document {}
  globalThis.ShadowRoot = class ShadowRoot {}
  globalThis.document = Object.assign(new globalThis.Document(), { activeElement: null })
  const mounted = mountBanner()
  try {
    await settle()
    mounted.nodes().find(n => n.props?.['aria-label']?.includes('数量')).props.onInput({ target: { value: '0' } })
    mounted.submit()
    await nextTick()
    assert.equal(mounted.emitted.length, 0)
    assert.ok(mounted.nodes().some(n => n.props?.['aria-invalid'] === true))
    mounted.nodes().find(n => n.type === 'checkbox').props['onUpdate:modelValue'](true)
    mounted.nodes().find(n => n.tagName === 'TEXTAREA').props['onUpdate:modelValue']('数量待确认')
    await nextTick()
    mounted.state.submitting = true
    await nextTick()
    mounted.submit()
    assert.equal(mounted.emitted.length, 0)
    mounted.state.submitting = false
    await nextTick()
    mounted.submit()
    assert.deepEqual(mounted.emitted[0], { interrupt_id: 'one', supplement: '数量待确认' })
  } finally { mounted.app.unmount();mounted.restore(); globalThis.document = documentBefore; globalThis.Document = DocumentBefore; globalThis.ShadowRoot = ShadowRootBefore }
})


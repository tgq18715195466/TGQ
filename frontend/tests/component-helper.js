import { readFile } from 'node:fs/promises'
import { parse, compileScript } from '@vue/compiler-sfc'
import { createRenderer, h, nextTick } from 'vue'
const cache=new Map()
export async function component(path,ssr=false){
 const url=new URL('../src/'+path,import.meta.url)
 async function compile(url){
  const key=url.href+ssr;if(cache.has(key))return cache.get(key)
  const {descriptor}=parse(await readFile(url,'utf8'))
  let source=compileScript(descriptor,{id:url.pathname,inlineTemplate:true,templateOptions:{ssr,compilerOptions:{hoistStatic:false}}}).content
  for(const match of [...source.matchAll(/from (['"])([^'"]+)\1/g)]){
   const spec=match[2], resolved=spec.startsWith('.')?new URL(spec,url).href:import.meta.resolve(spec)
   const target=spec.endsWith('.vue')?await compile(new URL(resolved)):resolved
   source=source.replace(match[0],`from '${target}'`)
  }
  const data='data:text/javascript;base64,'+Buffer.from(source).toString('base64');cache.set(key,data);return data
 }
 return (await import(await compile(url))).default
}
export const textOf=node=> (node.text||'')+(node.children||[]).map(textOf).join('')
export async function settle(){for(let i=0;i<12;i++){await new Promise(resolve=>setImmediate(resolve));await nextTick()}}
export function mount(Component,props={}){
 const original={document:globalThis.document,Document:globalThis.Document,ShadowRoot:globalThis.ShadowRoot}
 globalThis.Document=class Document {};globalThis.ShadowRoot=class ShadowRoot {};globalThis.document=Object.assign(new Document(),{activeElement:null,createElement(){return {textContent:'',get innerHTML(){return this.textContent.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;')}}}})
 const element=type=>({type,tagName:type.toUpperCase(),children:[],props:{},style:{},value:'',options:[],addEventListener(){},removeEventListener(){},getRootNode(){return globalThis.document},scrollIntoView(){},focus(){}})
 function remove(node){const index=node.parent?.children.indexOf(node);if(index>=0)node.parent.children.splice(index,1)}
 const renderer=createRenderer({
  createElement:element,createText:text=>({text}),createComment:()=>({text:''}),setText:(node,text)=>node.text=text,setElementText:(node,text)=>{node.text=text;node.children=[]},
  patchProp:(node,key,before,after)=>{node.props[key]=after;if(key==='value'||key==='type')node[key]=after},
  insert(node,parent,anchor){remove(node);node.parent=parent;const index=parent.children.indexOf(anchor);parent.children.splice(index<0?parent.children.length:index,0,node);if(parent.tagName==='SELECT')parent.options=parent.children},remove,
  parentNode:node=>node.parent,nextSibling:node=>node.parent?.children[node.parent.children.indexOf(node)+1],
 })
 const root=element('root'),app=renderer.createApp({render:()=>h(Component,props)})
 // Unexpected Vue warnings are test failures; preserve enough context to diagnose.
 app.config.warnHandler=message=>{throw new Error(message)}
 app.mount(root)
 const nodes=()=>{const all=[];const visit=node=>{all.push(node);(node.children||[]).forEach(visit)};visit(root);return all}
 const button=label=>nodes().find(n=>n.tagName==='BUTTON'&&(n.props['aria-label']===label||textOf(n).trim()===label))
 return {root,nodes,button,text:()=>textOf(root),unmount(){app.unmount();Object.assign(globalThis,original)},async click(label){const node=button(label);if(!node)throw Error('Button missing: '+label);await node.props.onClick?.({stopPropagation(){},preventDefault(){}});await settle()},async submit(index=0){const node=nodes().filter(n=>n.tagName==='FORM')[index];await node.props.onSubmit({preventDefault(){}});await settle()},async field(label,value){const node=nodes().find(n=>n.tagName==='LABEL'&&textOf(n).trim().startsWith(label));const input=node?.children.find(n=>['INPUT','SELECT','TEXTAREA'].includes(n.tagName));if(!input)throw Error('Field missing: '+label);input.props['onUpdate:modelValue'](value);await nextTick()}}
}

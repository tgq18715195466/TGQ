import {businessStatusText} from './status-display.js'
// Display-only projection. Keep transport fields in the execution state, never in UI copy.
const privateFields=new Set(['requestid','idempotencykey','checkpointid','threadid','createdby','actor','reviewversion','expectedversion','token','authorization'])
const normalized=key=>String(key).replace(/[_ -]/g,'').toLowerCase()
export function publicData(value){
 if(Array.isArray(value))return value.map(publicData)
 if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).filter(([key])=>!privateFields.has(normalized(key))).map(([key,data])=>[key,publicData(data)]))
 return value
}
export function publicText(text=''){
 const marker='(?:幂等(?:键|号)|request[_ ]?id|requestId|idempotency[_ ]?key|checkpoint[_ ]?id|thread[_ ]?id)'
 return businessStatusText(String(text).replace(new RegExp('[（(][^\\n（）()]*'+marker+'[^\\n（）()]*[）)]','gi'),'').replace(new RegExp('`?'+marker+'`?[\\s：:=]+(?:`[^`\\n]+`|"[^"\\n]+"|[A-Za-z0-9_.:/-]+)','gi'),''))
}
export function publicToolText(text=''){
 if(typeof text!=='string')return JSON.stringify(publicData(text),null,2)
 try{return JSON.stringify(publicData(JSON.parse(text)),null,2)}catch{return /^[\s]*[\[{]/.test(text)?'正在整理参数…':publicText(text)}
}
export const isInternalProbe=name=>['read_file','write_file','edit_file','ls','glob','grep','compact_conversation'].includes(name)

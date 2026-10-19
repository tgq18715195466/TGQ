import {orderStatus,supplierStatus} from './dashboard.js'
const labels=/(订单状态|供应商(?:合作)?状态|合作状态)([*`]*\s*[:：=]\s*)[*`]*([0-9]+)[*`]*(?:\s*[（(][^）)\n]*[）)][*`]*)?/g
const mapping=kind=>kind==='supplier'?supplierStatus:orderStatus
const supplierLabel=label=>label.includes('供应商')||label==='合作状态'
function statusName(value,kind){
 const match=value.match(/^\s*[*`]*([0-9]+)[*`]*\s*(?:[（(][^）)]*[）)][*`]*)?\s*$/)
 return match?(mapping(kind)[match[1]]||'未知状态'):value
}
export function businessStatusText(text){
 let columns={},fenced=false
 return text.split('\n').map(original=>{
  if(original.trimStart().startsWith('```'))fenced=!fenced
  if(fenced)return original
  let line=original.replace(labels,(_,label,punctuation,code)=>label+punctuation+(mapping(supplierLabel(label)?'supplier':'order')[code]||'未知状态'))
  if(!line.trim().startsWith('|')){columns={};return line}
  const cells=line.split('|'),headers=cells.map(c=>c.trim().replace(/[*`]/g,''))
  if(cells.length>=3 && ['订单状态','供应商合作状态','供应商状态','合作状态'].includes(headers[1]) && /^\s*[*`]*[0-9]/.test(cells[2])){
   cells[2]=' '+statusName(cells[2],supplierLabel(headers[1])?'supplier':'order').trim()+' '
  }else{
   const identified={}
   headers.forEach((header,index)=>{
    if(!header.includes('状态'))return
    if(header.includes('供应商')||header.includes('合作'))identified[index]='supplier'
    else if(header.includes('订单'))identified[index]='order'
    else if(header==='状态'){
     if(headers.some(h=>/订单|下单|order/i.test(h)))identified[index]='order'
     else if(headers.some(h=>h.includes('供应商')))identified[index]='supplier'
    }
   })
   if(Object.keys(identified).length)columns=identified
   else Object.entries(columns).forEach(([index,kind])=>{if(index<cells.length)cells[index]=' '+statusName(cells[index],kind).trim()+' '})
  }
  return cells.join('|')
 }).join('\n')
}

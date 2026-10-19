import test from 'node:test'
import assert from 'node:assert/strict'
import { buildDisplayItems, sameAssistantSource } from '../src/utils/message-display.js'

test('main answer appears once; child and tools are execution details', () => {
 const messages=[{id:'u',role:'user'}, {id:'t',role:'tool',source:'main'}, {id:'c',role:'assistant',source:'child',content:'report'}, {id:'m',role:'assistant',source:'main',content:'report'}]
 const items=buildDisplayItems(messages,true)
 assert.deepEqual(items.filter(x=>x.kind==='message').map(x=>x.message.id), ['u','m'])
 assert.deepEqual(items.find(x=>x.kind==='details').messages.map(x=>x.id), ['t','c'])
 assert.deepEqual(buildDisplayItems(messages,false).find(x=>x.kind==='details').messages.map(x=>x.id), ['c'])
 assert.deepEqual(buildDisplayItems(JSON.parse(JSON.stringify(messages)),true),items)
})
test('execution details remain separate across user turns', () => {
 const items=buildDisplayItems([{role:'assistant',source:'child',id:'a'},{role:'user',id:'u'},{role:'assistant',source:'child',id:'b'}],false)
 assert.equal(items.filter(x=>x.kind==='details').length,2)
})
test('never merge child text into main answer; legacy absent source means main', () => {
 assert.equal(sameAssistantSource({role:'assistant',source:'child'},'main'),false)
 assert.equal(sameAssistantSource({role:'assistant'},'main'),true)
 assert.equal(sameAssistantSource({role:'tool',source:'main'},'main'),false)
})
test('English workflow narration is localized during streaming and history replay',()=>{
 for(const text of ["I'll start by reading your preferences file.","I’ll delegate this inventory alert query to the procurement analyst.","I have your preferences. Now let me delegate this query to the procurement analyst."]){
  for(let i=1;i<=text.length;i++){
   const input={id:'progress',role:'assistant',source:'main',content:text.slice(0,i)}
   const shown=buildDisplayItems([input],true)[0].message.content
   assert.ok(!/[a-z]/i.test(shown),`${i}: ${shown}`)
   assert.equal(input.content,text.slice(0,i))
  }
 }
})
test('real business values and user messages are preserved',()=>{
 for(const content of ['物料：PC2010，库存 12 件。','| Part | Stock |\n| ABC | 12 |','IBM','Inventory is 12 units.']){
  assert.equal(buildDisplayItems([{role:'assistant',content}],true)[0].message.content,content)
 }
 const content="I'll start by reading your preferences file."
 assert.equal(buildDisplayItems([{role:'user',content}],true)[0].message.content,content)
})

import test from 'node:test'
import assert from 'node:assert/strict'
import {upsertAnswer} from '../src/utils/answer-stream.js'
import {createTypewriter} from '../src/utils/typewriter.js'
test('chunks append to one answer; confirmation replaces, never duplicates',()=>{
 const messages=[]
 upsertAnswer(messages,{id:'a',content:'库存'},false)
 assert.equal(messages[0].content,'库存')
 upsertAnswer(messages,{id:'a',content:'10件。'},false)
 upsertAnswer(messages,{id:'a',content:'库存10件。'},true)
 assert.equal(messages.length,1)
 assert.equal(messages[0].content,'库存10件。')
 assert.equal(messages[0].answerStreaming,false)
 assert.equal(messages[0].animate,false)
})

test('a complete referenced report starts the typewriter before done arrives',()=>{
 const messages=[]
 const report='供应商已终止合作，离合器片不可采购。'
 const message=upsertAnswer(messages,{id:'report',content:report},true)
 assert.equal(message.wasStreamed,true)
 assert.equal(message.answerStreaming,false)
 const jobs=new Map();let key=0;const frames=[]
 const writer=createTypewriter({onUpdate:text=>frames.push(text),schedule:fn=>{jobs.set(++key,fn);return key},cancel:id=>jobs.delete(id)})
 writer.start(message.content,message.wasStreamed)
 assert.equal(frames[0],'')
 assert.ok(jobs.size>0)
 while(jobs.size){const [id,fn]=jobs.entries().next().value;jobs.delete(id);fn()}
 assert.equal(frames.at(-1),report)
 writer.dispose()
})

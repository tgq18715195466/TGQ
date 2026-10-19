import test from 'node:test'
import assert from 'node:assert/strict'
import {createTypewriter} from '../src/utils/typewriter.js'
function fixture(){let id=0;const tasks=new Map(),frames=[];let completed=0;const writer=createTypewriter({onUpdate:(text,active)=>frames.push({text,active}),onDone:()=>completed++,schedule:fn=>{tasks.set(++id,fn);return id},cancel:id=>tasks.delete(id)});return {writer,tasks,frames,get completed(){return completed},tick(){const [id,fn]=tasks.entries().next().value;tasks.delete(id);fn()}}}
test('live answer progressively renders without corrupting Chinese or emoji; eventually exact Markdown',()=>{
 const f=fixture(),answer='供应商👨‍👩‍👧‍👦清单\n\n|物料|数量|\n|---|---|\n|离合器|20|'
 f.writer.start(answer);assert.equal(f.frames[0].text,'');f.tick();assert.notEqual(f.frames.at(-1).text,answer);while(f.tasks.size)f.tick();assert.equal(f.frames.at(-1).text,answer);assert.equal(f.frames.at(-1).active,false);assert.equal(f.completed,1)
})
test('history displays at once; show full completes once; disposal cancels pending updates',()=>{
 const f=fixture();f.writer.start('历史回复',false);assert.equal(f.frames.at(-1).text,'历史回复');assert.equal(f.tasks.size,0)
 f.writer.start('实时回复很长很长');f.writer.finish();assert.equal(f.frames.at(-1).text,'实时回复很长很长');assert.equal(f.tasks.size,0);assert.equal(f.completed,1)
 f.writer.start('另一会话');f.writer.dispose();assert.equal(f.tasks.size,0)
})
test('replacing an answer cancels prior animation and long responses finish within 800 frames',()=>{
 const f=fixture();f.writer.start('旧的消息');f.writer.start('新消息');while(f.tasks.size)f.tick();assert.equal(f.frames.at(-1).text,'新消息')
 f.writer.start('长'.repeat(12000));let ticks=0;while(f.tasks.size){f.tick();ticks++}assert.ok(ticks<=800);assert.equal(f.frames.at(-1).text.length,12000)
})

test('growing SSE answer preserves rendered prefix and catches up without restarting',()=>{
 const jobs=new Map();let id=0,text=''
 const writer=createTypewriter({onUpdate:value=>text=value,schedule:fn=>{jobs.set(++id,fn);return id},cancel:key=>jobs.delete(key)})
 const tick=()=>{const [key,fn]=jobs.entries().next().value;jobs.delete(key);fn()}
 writer.start('库存',true);tick();assert.equal(text,'库存')
 writer.update('库存有10件。',true)
 assert.equal(text,'库存');tick();assert.equal(text,'库存有1')
 writer.update('库存有10件。查询完成。',true)
 assert.equal(text,'库存有1')
 while(jobs.size)tick()
 assert.equal(text,'库存有10件。查询完成。')
 writer.dispose()
})

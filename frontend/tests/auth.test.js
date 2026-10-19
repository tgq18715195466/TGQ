import test from 'node:test'
import assert from 'node:assert/strict'
import { navigationFor, canReview, hasPermission } from '../src/auth/permissions.js'
const user = role => ({role,actor:'user:7'})
const tabs = role => navigationFor(user(role)).flatMap(g=>g.tabs.map(t=>t.key))
test('role navigation matches business access',()=>{
 assert.deepEqual(tabs('warehouse'),['overview','chat','orders','requests','inventory','receiving','stock','suppliers','password'])
 assert.deepEqual(tabs('purchaser'),['overview','chat','orders','requests','inventory','suppliers','password'])
 assert.ok(tabs('approver').includes('master'));assert.ok(!tabs('approver').includes('users'))
 assert.ok(tabs('admin').includes('users'));assert.deepEqual(navigationFor(null),[])
 assert.equal(hasPermission(user('warehouse'),'procurement'),false)
})
test('review requires approval permission and blocks own actor',()=>{
 assert.equal(canReview(user('purchaser'),{actor:'user:8'}),false)
 assert.equal(canReview(user('admin'),{actor:'user:7'}),false)
 assert.equal(canReview(user('approver'),{actor:'user:8'}),true)
})
test('server permissions can narrow role capabilities, never expand them',()=>{
 assert.equal(hasPermission({...user('admin'),permissions:[]},'users'),false)
 assert.equal(hasPermission({...user('warehouse'),permissions:['users']},'users'),false)
})
test('order owner id and disabled identities cannot approve',()=>{
 assert.equal(canReview({...user('admin'),id:7},{created_by:7}),false)
 assert.equal(hasPermission({...user('admin'),enabled:false},'users'),false)
})

test('warehouse procurement read access never grants create, change, review or master access',()=>{
 const warehouse={...user('warehouse'),permissions:['warehouse','read_inventory','read_procurement','chat']}
 assert.equal(hasPermission(warehouse,'read_procurement'),true)
 for(const permission of ['procurement','approve','master','users'])assert.equal(hasPermission(warehouse,permission),false)
 assert.equal(canReview(warehouse,{actor:'other'}),false)
 assert.ok(!navigationFor({...warehouse,permissions:['read_inventory']}).flatMap(g=>g.tabs).some(t=>['orders','requests','suppliers'].includes(t.key)))
})

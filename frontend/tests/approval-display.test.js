import test from 'node:test'
import assert from 'node:assert/strict'
import {formatFieldName,orderDetails,displayArgs,detailSubtotal,formatFieldValue} from '../src/utils/approval-display.js'
test('both API and MCP field styles render Chinese and structured detail',()=>{
 for(const key of ['supplier_id','supplierId']) assert.equal(formatFieldName(key),'供应商编号（ID）')
 const args={supplier_id:31,expected_delivery_date:'2026-10-10',order_detail:[{partId:1,quantity:20,unitPrice:0}]}
 assert.equal(displayArgs(args).order_detail,undefined)
 assert.equal(orderDetails(args)[0].partId,1)
 assert.equal(detailSubtotal(orderDetails(args)[0]),'0.00')
 assert.ok(!formatFieldValue('unknown',{a:1}).includes('[object Object]'))
})

import {purchaseConfirmationRows} from '../src/utils/approval-display.js'
test('purchase confirmation uses business names and confirmed prices, all labels Chinese',()=>{
 const rows=purchaseConfirmationRows({part_id:1,quantity:20,unit_price:3905,expected_date:'2026-10-09',reason:'缺货',manual:true},{name:'活塞环组件',part_code:'PC2001',supplier_name:'测试供应商',purchase_price:1})
 assert.equal(rows.find(r=>r[0]==='采购单价')[1],'¥3,905.00');assert.equal(rows.find(r=>r[0]==='采购金额')[1],'¥78,100.00')
 assert.equal(rows.find(r=>r[0]==='供应商')[1],'测试供应商');assert.ok(rows.every(r=>!/[A-Za-z_]/.test(r[0])))
 assert.equal(formatFieldName('expected_date'),'预计交货日期');assert.equal(formatFieldValue('manual',true),'主动补货')
})

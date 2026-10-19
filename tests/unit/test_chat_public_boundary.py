from types import SimpleNamespace
import pytest
from langgraph.store.memory import InMemoryStore
from agent.middlewares.context_injection import ContextInjectionMiddleware
from api_view.output_protocol import tool_failure, terminal_answer
from langchain_core.messages import AIMessage

@pytest.mark.asyncio
async def test_new_user_preferences_ready_before_model_and_preserved():
 store=InMemoryStore();runtime=SimpleNamespace(context=SimpleNamespace(user_id='user-42',username='buyer'),store=store)
 middleware=ContextInjectionMiddleware()
 await middleware.abefore_agent({},runtime)
 item=await store.aget(('user-42',),'/user-42/preferences.md')
 assert item is not None and 'preferred_language: zh' in '\n'.join(item.value['content'])
 await store.aput(('user-42',),'/user-42/preferences.md',{'content':['preferred_output: table']})
 await middleware.abefore_agent({},runtime)
 assert (await store.aget(('user-42',),'/user-42/preferences.md')).value['content']==['preferred_output: table']
 assert await store.aget(('user-43',),'/user-42/preferences.md') is None

def test_internal_file_probe_not_business_failure():
 assert tool_failure('read_file',"Error: File '/user-2/preferences.md' not found",'error') is None
 assert tool_failure('ls',"Error: Path '/skills/order': path_not_found",'error') is None
 assert tool_failure('order_create',{'ok':False,'error':{'code':'SUPPLIER_NOT_ACTIVE'}},'error')['code']=='SUPPLIER_NOT_ACTIVE'

def test_answer_hides_generated_keys_preserves_business_fields():
 content='草稿已备好（幂等键 `replenish-PC2001-2026-001`），数量 20，供应商 SUP00026。\n订单编号：PO2026001。'
 answer=terminal_answer({'messages':[AIMessage(content=content)]})
 assert '幂等键' not in answer and 'replenish-PC2001' not in answer
 assert '数量 20' in answer and 'SUP00026' in answer and 'PO2026001' in answer

def test_order_skill_directory_exists():
 from pathlib import Path
 import yaml
 config=yaml.safe_load(Path('src/agent/subagents/configs/procurement_order.yaml').read_text(encoding='utf-8'))
 for folder in config['skills']:
  assert (Path('src')/folder.lstrip('/')/'SKILL.md').is_file()

def test_business_statuses_are_names_in_tables_and_prose():
 from api_view.output_protocol import public_answer
 text='| 属性 | 内容 |\n| --- | --- |\n| 订单状态 | **5**（已完成/结案） |\n| 供应商合作状态 | 3（已终止） |\n\n| 订单编号 | 数量 | 状态 |\n| --- | --- | --- |\n| PO123 | 30 | 4 |\n| PO456 | 14 | 2 |\n\n订单状态：1（待审核），供应商状态=2（暂停）。'
 out=public_answer(text)
 assert '| 订单状态 | 已取消 |' in out
 assert '| 供应商合作状态 | 已终止 |' in out
 assert '| PO123 | 30 | 已入库 |' in out
 assert '| PO456 | 14 | 已审核 |' in out
 assert '订单状态：待审核' in out and '供应商状态=暂停合作' in out
 assert '已完成/结案' not in out

def test_supplier_status_table_does_not_change_ids_or_other_numbers():
 from api_view.output_protocol import public_answer
 text='| 供应商编号 | 名称 | 状态 | 金额 |\n| --- | --- | --- | --- |\n| SUP00015 | 配件公司 | 3 | 123.00 |'
 assert '| SUP00015 | 配件公司 | 已终止 | 123.00 |' in public_answer(text)
 assert public_answer('数量 3，订单编号 PO5，金额 123。')=='数量 3，订单编号 PO5，金额 123。'

def test_status_as_first_column_and_ambiguous_status_are_safe():
 from api_view.output_protocol import public_answer
 assert public_answer('| 订单状态 | 订单编号 |\n| --- | --- |\n| 5 | PO5 |').endswith('| 已取消 | PO5 |')
 assert public_answer('| 数量 | 状态 |\n| --- | --- |\n| 3 | 2 |').endswith('| 3 | 2 |')  # Cannot guess another domain's status.

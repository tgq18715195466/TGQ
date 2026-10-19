import json
from types import SimpleNamespace
import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from api_view.api import chat

class Loader:
    def __init__(self, chunks, pending=None):
        self.chunks=chunks; self.saved=[]; self.pending=pending or []; self.agent=self
    def create_config(self,*a,**kw): return {}
    async def get_display_messages(self,*a): return self.saved
    async def save_display_messages(self,key,messages): self.saved=list(messages); return True
    async def get_pending_interrupts(self,*a): return self.pending
    async def astream(self,**kw):
        for chunk in self.chunks: yield chunk

def msg(token,ns=()): return {'type':'messages','ns':ns,'data':(token,{})}
def values(messages): return {'type':'values','ns':(),'data':{'messages':messages}}
async def run(monkeypatch,chunks,pending=None):
    loader=Loader(chunks,pending); monkeypatch.setattr(chat,'agent_loader',loader)
    events=[json.loads(x[6:]) async for x in chat.stream_chat_response('库存查询','separation')]
    return events,loader

async def test_only_terminal_business_answer_is_public(monkeypatch):
    pre=AIMessageChunk(id='pre',content='A completely new English planning sentence.')
    call=AIMessageChunk(id='pre',content='',tool_call_chunks=[{'name':'supplier_query','id':'c1','index':0,'args':'{}'}])
    answer=AIMessage(id='final',content='共有三家供应商。')
    events,loader=await run(monkeypatch,[msg(pre),msg(call),msg(ToolMessage(name='supplier_query',tool_call_id='c1',content='{"ok":true,"data":[]}')),msg(AIMessageChunk(id='child',content='private analysis'),('tools:child',)),msg(AIMessageChunk(id='final',content=answer.content)),values([HumanMessage(content='query'),answer])])
    assert not any(e['type']=='token' for e in events)
    assert [e['content'] for e in events if e['type']=='answer']==[answer.content]
    assert [m['content'] for m in loader.saved if m['role']=='assistant']==[answer.content]
    assert any(e['type']=='progress' and '正在' in e['message'] for e in events)
    assert events[-1]['content']==answer.content

async def test_interrupted_run_never_emits_prelude_as_answer(monkeypatch):
    pending=[{'type':'interrupt','interrupt_id':'i','interrupt_type':'hitl_approval'}]
    events,loader=await run(monkeypatch,[msg(AIMessageChunk(content='Let us create an order',id='x'))],pending)
    assert not any(e['type'] in {'answer','token'} for e in events)
    assert not any(m['role']=='assistant' for m in loader.saved)

async def test_tool_failure_has_safe_retry_descriptor(monkeypatch):
    chunks=[msg(AIMessageChunk(id='m',content='',tool_call_chunks=[{'name':'supplier_query','id':'c','index':0,'args':'{}'}])),msg(ToolMessage(name='supplier_query',tool_call_id='c',content='{"ok":false,"error":{"code":"ERP_UNAVAILABLE","message":"connection refused","retryable":true}}'))]
    events,loader=await run(monkeypatch,chunks)
    error=next(e for e in events if e['type']=='tool_error')
    assert error['failure']['retryable'] is True
    assert '服务' in error['failure']['message']
    failure=next(m for m in loader.saved if m.get('id') == 'c')['failure']
    assert {k:v for k,v in failure.items() if k != 'answer_id'}==error['failure']
    assert failure['answer_id']==next(m for m in loader.saved if m['role']=='assistant')['id']

async def test_retry_rejects_write_or_successful_tool(monkeypatch):
    loader=Loader([]); monkeypatch.setattr(chat,'agent_loader',loader)
    from fastapi import HTTPException
    for name,failure in [('order_create',{'retryable':True}),('supplier_query',None)]:
        loader.saved=[{'id':'c','role':'tool','tool_name':name,'args':'{}','failure':failure}]
        with pytest.raises(HTTPException) as exc:
            await chat.retry_tool('s','c',x_user_id='anonymous')
        assert exc.value.status_code==409

async def test_successful_retry_uses_saved_args_once_and_persists_answer(monkeypatch):
    loader=Loader([]); monkeypatch.setattr(chat,'agent_loader',loader)
    loader.saved=[{'id':'c','role':'tool','tool_name':'supplier_query','args':'{"supplier_id":7}', 'failure':{'retryable':True}}]
    calls=[]
    async def invoke(name,args):
        calls.append((name,args))
        return [{'type':'text','text':'{"ok":true,"data":[{"id":7,"name":"测试供应商"}]}'}]
    monkeypatch.setattr(chat,'invoke_readonly_tool',invoke)
    result=await chat.retry_tool('s','c',x_user_id='anonymous')
    assert result['ok'] and result['answer']['business_results'][0]['records'][0]['name']=='测试供应商'
    assert loader.saved[0]['failure'] is None
    from fastapi import HTTPException
    with pytest.raises(HTTPException): await chat.retry_tool('s','c',x_user_id='anonymous')
    assert calls==[('supplier_query',{'supplier_id':7})]

async def test_pending_approval_blocks_retry_and_never_invokes_tool(monkeypatch):
    loader=Loader([],pending=[{'interrupt_id':'i'}]); monkeypatch.setattr(chat,'agent_loader',loader)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await chat.retry_tool('s','c',x_user_id='anonymous')
    assert exc.value.status_code==409

async def test_retry_failure_remains_actionable(monkeypatch):
    loader=Loader([]); monkeypatch.setattr(chat,'agent_loader',loader)
    loader.saved=[{'id':'c','role':'tool','tool_name':'part_query','args':'{}','failure':{'retryable':True}}]
    async def invoke(*args): raise TimeoutError()
    monkeypatch.setattr(chat,'invoke_readonly_tool',invoke)
    result=await chat.retry_tool('s','c',x_user_id='anonymous')
    assert not result['ok'] and result['failure']['retryable']
    assert not any(m['role']=='assistant' for m in loader.saved)

async def test_only_last_root_answer_wins_over_child_and_tool_preludes(monkeypatch):
    pre=AIMessage(content='Unseen narration',tool_calls=[{'name':'task','args':{},'id':'c'}])
    chunks=[values([pre]),{'type':'values','ns':('tools:child',),'data':{'messages':[AIMessage(content='child report')]}},values([pre,ToolMessage(content='report',tool_call_id='c'),AIMessage(content='完整业务结果')])]
    events,loader=await run(monkeypatch,chunks)
    assert [e['content'] for e in events if e['type']=='answer']==['完整业务结果']

async def test_tool_bearing_terminal_message_is_not_answer(monkeypatch):
    pre=AIMessage(content='Creating order',tool_calls=[{'name':'order_create','args':{},'id':'c'}])
    events,loader=await run(monkeypatch,[values([pre])])
    assert not any(e['type']=='answer' for e in events)

async def test_real_graph_stream_produces_one_terminal_answer(monkeypatch):
    from langchain.agents import create_agent
    from langchain_core.language_models import BaseChatModel
    from langchain_core.outputs import ChatResult, ChatGeneration
    from langchain_core.tools import tool
    from langgraph.checkpoint.memory import InMemorySaver
    from api_view.agent_loader import AgentLoader
    class OfflineModel(BaseChatModel):
        @property
        def _llm_type(self): return 'offline-output-test'
        def bind_tools(self,tools,**kwargs): return self
        def _generate(self,messages,stop=None,run_manager=None,**kwargs):
            answer=AIMessage(content='真实图最终库存为3件。') if any(isinstance(m,ToolMessage) for m in messages) else AIMessage(content='New unseen English narration.',tool_calls=[{'name':'supplier_query','args':{},'id':'lookup'}])
            return ChatResult(generations=[ChatGeneration(message=answer)])
    @tool
    def supplier_query():
        """Synthetic read only query."""
        return {'ok':True,'data':[{'id':3}]}
    class Repo:
        saved=[]
        async def get_display_messages(self,key): return self.saved
        async def save_display_messages(self,key,messages): self.saved=list(messages); return True
    loader=object.__new__(AgentLoader)
    loader._agent=create_agent(OfflineModel(),tools=[supplier_query],checkpointer=InMemorySaver())
    loader._persistence=Repo()
    monkeypatch.setattr(chat,'agent_loader',loader)
    events=[json.loads(x[6:]) async for x in chat.stream_chat_response('test','realgraph')]
    assert not any(e['type']=='error' for e in events),events
    assert [e['content'] for e in events if e['type']=='answer']==['真实图最终库存为3件。']
    assert all('narration' not in m.get('content','') for m in loader._persistence.saved)

async def test_history_keeps_failure_and_answer_channels(monkeypatch):
    from api_view.api import history
    loader=Loader([])
    loader.saved=[{'id':'tool','role':'tool','tool_name':'supplier_query','tool_status':'error','failure':{'message':'服务不可用','retryable':True}}, {'id':'answer','role':'assistant','content':'业务结果','channel':'answer'}]
    monkeypatch.setattr(history,'agent_loader',loader)
    result=await history.get_session_messages('s',x_user_id='anonymous')
    assert result.messages[0].failure['retryable']
    assert result.messages[1].channel=='answer'

async def test_query_failure_is_preserved_if_later_model_call_crashes(monkeypatch):
    chunks=[msg(AIMessageChunk(id='m',content='',tool_call_chunks=[{'name':'part_query','id':'c','index':0,'args':'{}'}])),msg(ToolMessage(name='part_query',tool_call_id='c',content='{"ok":false,"error":{"code":"ERP_UNAVAILABLE","retryable":true}}'))]
    loader=Loader(chunks)
    async def crashing(**kwargs):
        for chunk in chunks: yield chunk
        raise TimeoutError('sensitive external response')
    loader.astream=crashing
    monkeypatch.setattr(chat,'agent_loader',loader)
    events=[json.loads(x[6:]) async for x in chat.stream_chat_response('query','crash')]
    assert events[-1]['type']=='error' and '超时' in events[-1]['message']
    assert 'sensitive' not in events[-1]['message']
    assert next(m for m in loader.saved if m.get('id') == 'c')['failure']['retryable'] is True

async def test_failed_save_never_announces_answer_or_completion(monkeypatch):
    loader=Loader([values([AIMessage(content='业务结果')])]); monkeypatch.setattr(chat,'agent_loader',loader)
    async def failed_save(*args): return False
    loader.save_display_messages=failed_save
    events=[json.loads(x[6:]) async for x in chat.stream_chat_response('q','unsaved')]
    assert not any(e['type'] in {'answer','done'} for e in events)
    assert events[-1]['type']=='error' and '保存' in events[-1]['message']

async def test_retry_failed_save_returns_error_not_success(monkeypatch):
    from copy import deepcopy
    from fastapi import HTTPException
    loader=Loader([]); monkeypatch.setattr(chat,'agent_loader',loader)
    loader.saved=[{'id':'c','role':'tool','tool_name':'supplier_query','args':'{}','failure':{'retryable':True}}]
    async def read(*args): return deepcopy(loader.saved)
    async def failed_save(*args): return False
    async def invoke(*args): return {'ok':True,'data':[]}
    loader.get_display_messages=read; loader.save_display_messages=failed_save
    monkeypatch.setattr(chat,'invoke_readonly_tool',invoke)
    with pytest.raises(HTTPException) as exc:
        await chat.retry_tool('s','c',x_user_id='anonymous')
    assert exc.value.status_code==503

async def test_cancellation_preserves_already_emitted_failure_and_prior_history(monkeypatch):
    import asyncio
    loader=Loader([]); loader.saved=[{'id':'old','role':'user','content':'previous'}]
    ready=asyncio.Event()
    async def stream(**kwargs):
        yield msg(AIMessageChunk(id='m',content='',tool_call_chunks=[{'name':'part_query','id':'c','index':0,'args':'{}'}]))
        yield msg(ToolMessage(name='part_query',tool_call_id='c',content='{"ok":false,"error":{"code":"ERP_UNAVAILABLE","retryable":true}}'))
        ready.set()
        await asyncio.Event().wait()
    loader.astream=stream; monkeypatch.setattr(chat,'agent_loader',loader)
    async def consume():
        return [item async for item in chat.stream_chat_response('query','cancel')]
    task=asyncio.create_task(consume())
    await ready.wait(); task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert loader.saved[0]['id']=='old'
    assert any(m.get('failure',{}).get('retryable') for m in loader.saved)

async def test_generator_close_also_preserves_failure(monkeypatch):
    chunks=[msg(AIMessageChunk(id='m',content='',tool_call_chunks=[{'name':'part_query','id':'c','index':0,'args':'{}'}])),msg(ToolMessage(name='part_query',tool_call_id='c',content='{"ok":false,"error":{"code":"ERP_UNAVAILABLE","retryable":true}}'))]
    loader=Loader(chunks); monkeypatch.setattr(chat,'agent_loader',loader)
    stream=chat.stream_chat_response('query','close')
    async for raw in stream:
        if json.loads(raw[6:])['type']=='tool_error': break
    await stream.aclose()
    assert any(m.get('failure',{}).get('retryable') for m in loader.saved)

async def test_outer_response_close_saves_before_unlocking(monkeypatch):
    chunks=[msg(AIMessageChunk(id='m',content='',tool_call_chunks=[{'name':'part_query','id':'c','index':0,'args':'{}'}])),msg(ToolMessage(name='part_query',tool_call_id='c',content='{"ok":false,"error":{"code":"ERP_UNAVAILABLE","retryable":true}}'))]
    loader=Loader(chunks); monkeypatch.setattr(chat,'agent_loader',loader)
    response=await chat._open_stream('outer-close','anonymous',None,message='query')
    async for raw in response.body_iterator:
        if json.loads(raw[6:])['type']=='tool_error': break
    await response.body_iterator.aclose()
    assert next(m for m in loader.saved if m.get('id') == 'c')['failure']['retryable']
    lock=chat._thread_locks.get('anonymous:outer-close')
    assert lock is None or not lock.locked()

async def test_anyio_disconnect_cancellation_is_shielded(monkeypatch):
    import anyio
    from contextlib import aclosing
    chunks=[msg(AIMessageChunk(id='m',content='',tool_call_chunks=[{'name':'part_query','id':'c','index':0,'args':'{}'}])),msg(ToolMessage(name='part_query',tool_call_id='c',content='{"ok":false,"error":{"code":"ERP_UNAVAILABLE","retryable":true}}'))]
    loader=Loader(chunks); monkeypatch.setattr(chat,'agent_loader',loader)
    async def persist(key,messages):
        await anyio.sleep(0)
        loader.saved=list(messages)
        return True
    loader.save_display_messages=persist
    with anyio.CancelScope() as scope:
        async with aclosing(chat.stream_chat_response('q','disconnect')) as stream:
            async for raw in stream:
                if json.loads(raw[6:])['type']=='tool_error':
                    scope.cancel()
                    await anyio.sleep(0)
    assert next(m for m in loader.saved if m.get('id') == 'c')['failure']['retryable']


def test_write_failure_explains_business_rejection():
    from api_view.output_protocol import tool_failure
    result=tool_failure('purchase_request_create',{'ok':False,'error':{'code':'ERP_API_ERROR','status':409,'detail':'该物料已有待审批申请，请先处理原申请'}})
    assert '已有待审批申请' in result['message']
    assert not result['retryable']

async def test_business_answer_streams_before_graph_completion(monkeypatch):
    args=json.dumps({'answer':'共有三家供应商。\n供应商清单正在生成。'},ensure_ascii=False)
    head=args.index('供应商清单')
    chunks=[msg(AIMessageChunk(content='',tool_call_chunks=[{'name':'publish_business_answer','id':'public1','index':0,'args':args[:head]}])),msg(AIMessageChunk(content='',tool_call_chunks=[{'name':None,'id':None,'index':0,'args':args[head:]}])),msg(ToolMessage(name='publish_business_answer',tool_call_id='public1',content='共有三家供应商。\n供应商清单正在生成。')),values([ToolMessage(name='publish_business_answer',tool_call_id='public1',content='共有三家供应商。\n供应商清单正在生成。')])]
    loader=Loader(chunks);monkeypatch.setattr(chat,'agent_loader',loader)
    stream=chat.stream_chat_response('供应商查询','streaming')
    early=[]
    async for raw in stream:
        event=json.loads(raw[6:]);early.append(event)
        if event['type']=='answer_delta':
            assert loader.saved==[]  # Graph and persistence have not finished yet.
            assert event['content']=='共有三家供应商。\n'
            break
    else:
        pytest.fail('No incremental answer before completion')
    events=early+[json.loads(raw[6:]) async for raw in stream]
    assert ''.join(e['content'] for e in events if e['type']=='answer_delta')=='共有三家供应商。\n供应商清单正在生成。'
    assert len([e for e in events if e['type']=='answer'])==1
    assert [m['role'] for m in loader.saved].count('assistant')==1
    assert not any(e.get('tool_name')=='publish_business_answer' for e in events)

async def test_child_answer_tool_and_internal_partial_keys_are_not_public(monkeypatch):
    raw=json.dumps({'answer':'结果正常（幂等键 `secret-key`）。\n库存 10 件。'},ensure_ascii=False)
    events,_=await run(monkeypatch,[msg(AIMessageChunk(content='',tool_call_chunks=[{'name':'publish_business_answer','id':'child','index':0,'args':raw}]),('tools:child',))])
    assert not any(e['type']=='answer_delta' for e in events)
    from api_view.answer_stream import BusinessAnswerStream
    decoder=BusinessAnswerStream()
    output=''
    for character in raw:
        output+=decoder.feed(character)
    output+=decoder.finish()
    assert 'secret-key' not in output and '幂等' not in output
    assert '库存 10 件。' in output

async def test_real_graph_direct_answer_tool_streams_and_terminates(monkeypatch):
    from langchain.agents import create_agent
    from langchain_core.language_models import BaseChatModel
    from langchain_core.outputs import ChatResult, ChatGeneration, ChatGenerationChunk
    from langgraph.checkpoint.memory import InMemorySaver
    from api_view.agent_loader import AgentLoader
    from agent.tools.business_answer import publish_business_answer
    class StreamingModel(BaseChatModel):
        @property
        def _llm_type(self):return 'offline-sse-test'
        def bind_tools(self,tools,**kwargs):return self
        def _generate(self,*args,**kwargs):raise AssertionError('Must use actual model stream')
        def _stream(self,messages,stop=None,run_manager=None,**kwargs):
            assert not any(isinstance(m,ToolMessage) for m in messages),'Answer tool must terminate graph without another model call'
            yield ChatGenerationChunk(message=AIMessageChunk(content='',tool_call_chunks=[{'name':'publish_business_answer','id':'out','index':0,'args':'{"answer":"库存为10件。\\n'}]))
            yield ChatGenerationChunk(message=AIMessageChunk(content='',tool_call_chunks=[{'name':None,'id':None,'index':0,'args':'查询完成。"}'}]))
    class Repo:
        saved=[]
        async def get_display_messages(self,key):return self.saved
        async def save_display_messages(self,key,messages):self.saved=list(messages);return True
    loader=object.__new__(AgentLoader)
    loader._agent=create_agent(StreamingModel(),tools=[publish_business_answer],checkpointer=InMemorySaver())
    loader._persistence=Repo();monkeypatch.setattr(chat,'agent_loader',loader)
    events=[json.loads(x[6:]) async for x in chat.stream_chat_response('库存','graphstream')]
    assert not any(e['type']=='error' for e in events),events
    assert ''.join(e['content'] for e in events if e['type']=='answer_delta')=='库存为10件。\n查询完成。'
    assert len([e for e in events if e['type']=='answer'])==1
    assert len([m for m in loader._persistence.saved if m['role']=='assistant'])==1

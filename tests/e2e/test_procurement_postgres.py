"""New procurement ToolNode recovery across real processes; business writes mocked."""
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest

ROOT=Path(__file__).resolve().parents[2]
pytestmark=pytest.mark.skipif(os.getenv('ERP_TEST_POSTGRES')!='1',reason='opt-in local PostgreSQL test')


async def phase(action,thread):
    sys.path.insert(0,str(ROOT/'tests/integration'))
    from test_procurement_tools import State,raw_tools
    from test_procurement_workflow import BusinessService,draft
    from agent.tools.procurement import create_procurement_tools
    from agent.postgres_persistence import PostgresPersistence
    from backend.auth import current_identity
    from langgraph.graph import StateGraph,START,END
    from langgraph.prebuilt import ToolNode
    from langgraph.types import Command
    from langchain_core.messages import AIMessage
    persistence=PostgresPersistence();persistence.required=True
    principal=current_identity.set({'id':999999,'role':'purchaser','enabled':True})
    try:
        await persistence.initialize()
        assert persistence.persistent
        config={'configurable':{'thread_id':thread}}
        if action=='cleanup':
            await persistence.checkpointer.adelete_thread(thread)
            return {'cleaned':True}
        service=BusinessService();builder=StateGraph(State)
        builder.add_node('tools',ToolNode(create_procurement_tools(raw_tools(service))))
        builder.add_edge(START,'tools');builder.add_edge('tools',END)
        g=builder.compile(checkpointer=persistence.checkpointer)
        if action=='suspend':
            r=await g.ainvoke({'messages':[AIMessage(content='',tool_calls=[{'name':'purchase_request_create','args':draft(),'id':'synthetic-purchase'}])]},config)
            assert len(r['__interrupt__'])==1 and service.saved is None
            checkpoints=[c async for c in persistence.checkpointer.alist(config)]
            assert any(c.config['configurable']['checkpoint_ns'] for c in checkpoints)
            return {'pending':True,'pid':os.getpid(),'writes':0}
        pending=(await g.aget_state(config)).interrupts
        assert len(pending)==1 and pending[0].value['workflow_type']=='procurement'
        r=await g.ainvoke(Command(resume={pending[0].id:{'decisions':[{'type':'approve'}]}}),config)
        result=json.loads(r['messages'][-1].content)
        assert result['ok'] and result['verified'] and result['data']['status']=='pending'
        assert not (await g.aget_state(config)).interrupts
        writes=sum(name=='purchase_request_create' for name,_ in service.calls)
        assert writes==1
        return {'verified':True,'pid':os.getpid(),'writes':writes}
    finally:
        current_identity.reset(principal)
        await persistence.close()


def worker(action,thread):
    env={**os.environ,'PYTHONUTF8':'1','PYTHONPATH':str(ROOT/'src')+os.pathsep+str(ROOT)}
    completed=subprocess.run([sys.executable,str(Path(__file__).resolve()),action,thread],cwd=ROOT,env=env,capture_output=True,text=True,timeout=40)
    # Never echo database configuration or unrelated debug output on failure.
    assert completed.returncode==0,'PostgreSQL procurement recovery worker failed'
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_confirmed_procurement_resumes_after_process_restart():
    thread='test-fixed-procurement-'+uuid4().hex
    try:
        before=worker('suspend',thread);after=worker('resume',thread)
        assert before['pid']!=after['pid'] and before['writes']==0 and after['writes']==1
        assert after['verified']
    finally:worker('cleanup',thread)


if __name__=='__main__':
    import asyncio
    if sys.platform=='win32':asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    print(json.dumps(asyncio.run(phase(sys.argv[1],sys.argv[2]))))

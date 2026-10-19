"""Offloaded tool text must not replace the original MCP business evidence."""
import pytest
from langchain_core.messages import ToolMessage

from api_view.api import chat
from test_answer_presentation import REPORT
from test_chat_business_results import Loader, msg, run, summary


NOTICE = ('Tool result too large, the result of this tool call orders was saved '
          'in the filesystem at this path: /large_tool_results/orders')
SUCCESS = {'ok': True, 'data': [{'part_code': 'PC2007', 'part_name': '机油泵', 'status': 4}]}


def result(raw, *, name='order_search_details', status='success', content=NOTICE):
    return ToolMessage(name=name, tool_call_id='orders', content=content,
                       status=status, artifact={'structured_content': raw})


@pytest.mark.parametrize('ns', [(), ('tools:child',)])
@pytest.mark.parametrize('content', [NOTICE, [{'type': 'text', 'text': NOTICE}]])
async def test_offloaded_query_preserves_report_and_all_business_evidence(monkeypatch, ns, content):
    events, loader = await run(monkeypatch, [msg(result(SUCCESS, content=content), ns)] + summary(REPORT))
    assert events[-1]['content'] == REPORT
    assert ''.join(e['content'] for e in events if e['type'] == 'answer_delta') == REPORT
    fact = events[-1]['business_results'][0]
    assert fact['execution']['outcome'] == 'success'
    assert fact['records'] == [{'part_code': 'PC2007', 'part_name': '机油泵', 'status_name': '已入库'}]
    assert fact['result_id'] == 'orders'
    saved = next(m for m in loader.saved if m['role'] == 'assistant')
    assert saved['content'] == REPORT
    assert saved['business_results'] == [fact]
    assert next(e for e in events if e['type'] == 'tool_result')['text'] == NOTICE


async def test_offloaded_business_failure_still_blocks_report_and_reports_actual_reason(monkeypatch):
    raw = {'ok': False, 'error': {'code': 'PERMISSION_DENIED', 'retryable': False}}
    events, _ = await run(monkeypatch, [msg(result(raw))] + summary(REPORT))
    assert events[-1]['business_results'][0]['execution']['outcome'] == 'failure'
    assert '权限' in events[-1]['content']
    assert not any(e['type'] == 'answer_delta' for e in events)
    assert next(e for e in events if e['type'] == 'tool_error')['failure']['code'] == 'PERMISSION_DENIED'


@pytest.mark.parametrize('artifact', [None, {}, {'structured_content': None}, {'structured_content': 'invalid'}])
async def test_missing_evidence_is_not_treated_as_query_success(monkeypatch, artifact):
    token = result(SUCCESS).model_copy(update={'artifact': artifact})
    events, _ = await run(monkeypatch, [msg(token)] + summary(REPORT))
    assert events[-1]['business_results'][0]['execution']['outcome'] == 'unknown'
    assert events[-1]['content'] != REPORT


async def test_tool_error_status_wins_over_success_artifact(monkeypatch):
    events, _ = await run(monkeypatch, [msg(result(SUCCESS, status='error'))] + summary(REPORT))
    assert events[-1]['business_results'][0]['execution']['outcome'] == 'failure'
    assert not any(e['type'] == 'answer_delta' for e in events)


async def test_offloaded_write_acknowledgement_is_still_unverified(monkeypatch):
    raw = {'ok': True, 'data': {'orderNumber': 'PO1', 'status': 1}}
    events, _ = await run(monkeypatch, [msg(result(raw, name='order_create'))] + summary('订单创建成功。'))
    assert events[-1]['business_results'][0]['execution']['outcome'] == 'unknown'
    assert '尚未查询核验' in events[-1]['content']
    assert not any(e['type'] == 'answer_delta' for e in events)


async def test_retry_uses_the_same_structured_evidence(monkeypatch):
    loader = Loader(saved=[{'id': 'orders', 'role': 'tool', 'tool_name': 'order_search_details',
                           'args': '{}', 'failure': {'retryable': True}}])
    monkeypatch.setattr(chat, 'agent_loader', loader)

    async def invoke(*args):
        return result(SUCCESS)

    monkeypatch.setattr(chat, 'invoke_readonly_tool', invoke)
    response = await chat.retry_tool('offload-retry', 'orders', x_user_id='anonymous')
    assert response['ok']
    assert response['business_result']['execution']['outcome'] == 'success'
    assert response['business_result']['records'][0]['part_code'] == 'PC2007'


async def test_real_filesystem_eviction_keeps_full_query_evidence(monkeypatch, tmp_path):
    from deepagents.backends.filesystem import FilesystemBackend
    from deepagents.middleware.filesystem import FilesystemMiddleware
    from fastmcp import Client, FastMCP
    from langchain_mcp_adapters.tools import _convert_call_tool_result
    from mcp_server.tool_errors import business_tool

    raw = {'ok': True, 'data': [{'part_code': f'P{i}', 'part_name': '物料' * 30, 'status': 4}
                               for i in range(200)]}
    server = FastMCP('offloaded-query-regression')

    @server.tool(name='order_search_details')
    @business_tool('order_search_details')
    async def query() -> dict:
        return raw

    async with Client(server) as client:
        content, artifact = _convert_call_tool_result(await client.call_tool_mcp('order_search_details', {}))
    original = ToolMessage(name='order_search_details', tool_call_id='orders', content=content, artifact=artifact)
    middleware = FilesystemMiddleware(backend=FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True),
                                      tool_token_limit_before_evict=100)
    token = await middleware._aintercept_large_tool_result(original)
    assert 'Tool result too large' in token.content
    assert token.artifact['structured_content']['data'] == raw['data']
    events, _ = await run(monkeypatch, [msg(token, ('tools:child',))] + summary(REPORT))
    assert events[-1]['content'] == REPORT
    fact = events[-1]['business_results'][0]
    assert fact['execution']['outcome'] == 'success'
    assert fact['returned_count'] == 200
    assert fact['complete'] is False  # The existing query cap remains in effect.

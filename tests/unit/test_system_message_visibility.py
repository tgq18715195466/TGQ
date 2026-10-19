from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from api_view.api.history import serialize_messages_from_checkpoint

def test_history_does_not_relabel_system_as_assistant():
    result=serialize_messages_from_checkpoint([SystemMessage(content="private context"),HumanMessage(content="问题"),AIMessage(content="答案")])
    assert [m['content'] for m in result if m['role'] in {'user','assistant'}]==['问题','答案']
    assert all(m.get('content')!='private context' for m in result)

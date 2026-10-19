import json

from agent.tools import hitl_tools
from backend.auth import current_identity


def test_request_order_info_emits_supplement_interrupt(monkeypatch):
    captured = {}

    def fake_interrupt(payload):
        captured.update(payload)
        return {"supplement": "数量2，单价10"}

    monkeypatch.setattr(hitl_tools, "interrupt", fake_interrupt)
    token = current_identity.set({'role': 'purchaser'})
    try:
        result = json.loads(hitl_tools.request_order_info.invoke({
            "missing_fields": "quantity, unitPrice", "collected_data": "partId=1"
        }))
    finally:
        current_identity.reset(token)
    assert captured["type"] == "order_info_request"
    assert result == {"supplement": "数量2，单价10"}

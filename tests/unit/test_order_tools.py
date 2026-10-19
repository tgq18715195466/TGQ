from datetime import datetime

from mcp_server.tools.order_tools import _prepare_create_request, _prepare_update_request


def test_create_request_generates_number_and_time():
    result = _prepare_create_request({"orderDetail": [{"partId": 1, "quantity": 1, "unitPrice": 2}]})
    assert result["orderNumber"].startswith("PO")
    datetime.fromisoformat(result["orderTime"])


def test_update_request_never_invents_fields():
    assert _prepare_update_request({"status": 4}) == {"status": 4}


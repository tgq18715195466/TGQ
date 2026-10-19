import pytest
from pydantic import ValidationError

from backend.app import OrderCreate, OrderDetail, OrderUpdate


@pytest.mark.parametrize(
    "detail",
    [
        {"partId": 1, "quantity": 0, "unitPrice": 1},
        {"partId": 1, "quantity": -1, "unitPrice": 1},
        {"partId": 1, "quantity": 1, "unitPrice": -0.01},
    ],
)
def test_order_detail_rejects_invalid_values(detail):
    with pytest.raises(ValidationError):
        OrderDetail.model_validate(detail)


def test_order_create_requires_detail_and_valid_status():
    with pytest.raises(ValidationError):
        OrderCreate.model_validate({"orderDetail": []})
    with pytest.raises(ValidationError):
        OrderCreate.model_validate(
            {"status": 6, "orderDetail": [{"partId": 1, "quantity": 1, "unitPrice": 1}]}
        )


def test_order_update_rejects_empty_payload_and_invalid_status():
    with pytest.raises(ValidationError):
        OrderUpdate.model_validate({})
    with pytest.raises(ValidationError):
        OrderUpdate.model_validate({"status": 0})


def test_create_total_is_derived_from_details():
    request = OrderCreate.model_validate(
        {"supplierId":15,"expectedDeliveryDate":"2099-01-01","orderDetail": [{"partId": 1, "quantity": 2, "unitPrice": 12.5}]}
    )
    assert request.calculated_total == 25


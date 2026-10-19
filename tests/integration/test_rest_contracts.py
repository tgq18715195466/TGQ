from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import app, router

# Keep the production app for schema coverage; isolate request validation from auth.
_validation_app = FastAPI()
_validation_app.include_router(router)


EXPECTED_OPERATIONS = {
    ("get", "/api/suppliers/search"),
    ("get", "/api/parts/page"),
    ("get", "/api/parts/search"),
    ("get", "/api/parts/supplier/{supplier_id}"),
    ("post", "/api/orders/create"),
    ("put", "/api/orders/update/{order_id}"),
    ("get", "/api/orders/search-details"),
    ("get", "/api/inventory/warning"),
}


def test_all_eight_rest_api_contracts_are_exposed():
    schema = TestClient(app).get("/openapi.json").json()
    actual = {(method, path) for path, methods in schema["paths"].items() for method in methods}
    assert EXPECTED_OPERATIONS <= actual


def test_order_validation_returns_real_422_without_database_access():
    client = TestClient(_validation_app)
    response = client.post("/api/orders/create", json={"status": 99, "orderDetail": []})
    assert response.status_code == 422
    assert response.json()["detail"]


def test_empty_update_returns_real_422_without_database_access():
    response = TestClient(_validation_app).put("/api/orders/update/1", json={})
    assert response.status_code == 422


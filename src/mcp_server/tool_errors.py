"""Stable ERP errors and a bounded business sidecar for MCP output."""
from functools import wraps

import httpx
from pydantic import ValidationError

from agent.business_results import normalize_business_result


# Only exact, application-owned rejection text becomes a public reason code.
# Unknown exception bodies and user-controlled validation input remain private.
_BUSINESS_REJECTIONS = {
    '该物料已有待审批申请，请先处理原申请': 'PENDING_PURCHASE_REQUEST',
    '供应商不在合作中，不能申请采购': 'SUPPLIER_NOT_ACTIVE',
    '库存或预警值未知，请先维护库存和主数据': 'INVENTORY_DATA_INCOMPLETE',
    '申请数量超过当前补货建议，请刷新或核实额外需求': 'QUANTITY_EXCEEDS_SUGGESTION',
    '库存或在途订单已变化，申请数量超过当前需求，请重新申请': 'QUANTITY_EXCEEDS_SUGGESTION',
    '交货日期已过期，请重新申请': 'DELIVERY_DATE_EXPIRED',
}


def business_rejection_code(status, detail):
    if status in (401,403):
        return {401:'AUTH_REQUIRED',403:'PERMISSION_DENIED'}[status]
    if status in (409,422) and isinstance(detail,str):
        return _BUSINESS_REJECTIONS.get(detail,'ERP_API_ERROR')
    return 'ERP_API_ERROR'


def query_failure(exc):
    """Classify errors without exposing transport details or caller values."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return {'ok': False, 'error': {'code': {401: 'AUTH_REQUIRED', 403: 'PERMISSION_DENIED'}.get(status, 'ERP_API_ERROR'), 'status': status, 'retryable': status >= 500}}
    if isinstance(exc, httpx.RequestError):
        return {'ok': False, 'error': {'code': 'ERP_UNAVAILABLE', 'retryable': True}}
    if isinstance(exc, ValidationError):
        return {'ok': False, 'error': {'code': 'VALIDATION', 'status': 422, 'retryable': False}}
    return {'ok': False, 'error': {'code': 'INVALID_RESPONSE', 'retryable': True}}


def business_envelope(tool_name, raw_result):
    """Keep original data and existing error codes; attach validated facts."""
    if isinstance(raw_result, list):
        output = {'ok': True, 'data': raw_result}
    elif isinstance(raw_result, dict):
        output = dict(raw_result)
        output.setdefault('data', None)
    else:
        output = query_failure(ValueError('invalid response'))
        output['data'] = None
    business = normalize_business_result(tool_name, output)
    if business is None:
        raise ValueError('Unsupported business tool')
    if 'error' in output:
        raw_error = output['error'] if isinstance(output['error'], dict) else {}
        error = {key: value for key, value in raw_error.items() if key in ('code', 'status', 'retryable')}
        code = error.get('code')
        if not isinstance(code, str) or not code or len(code) > 64 or any(char not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789' for char in code):
            error['code'] = 'ERP_API_ERROR'
        if 'message' in raw_error:
            error['message'] = business.execution.message
        if 'detail' in raw_error:
            error['detail'] = business.execution.message
        output['error'] = error
    output['business'] = business.model_dump(mode='json', exclude_none=True)
    return output


def business_tool(tool_name):
    """Preserve tool signature, context and forwarding while enveloping results."""
    def decorate(function):
        @wraps(function)
        async def wrapped(*args, **kwargs):
            try:
                result = await function(*args, **kwargs)
            except Exception as exc:
                result = query_failure(exc)
            return business_envelope(tool_name, result)
        return wrapped
    return decorate


def erp_result(result):
    """Preserve the API data payload and distinguish known business rejections."""
    if not isinstance(result, dict):
        return query_failure(ValueError('invalid response'))
    code = result.get('code')
    if type(code) is not int:
        return query_failure(ValueError('missing business status'))
    if code == 200:
        return {'ok': True, 'data': result.get('data')}
    if 400 <= code < 600:
        return {'ok': False, 'error': {'code': {401: 'AUTH_REQUIRED', 403: 'PERMISSION_DENIED'}.get(code, 'ERP_API_ERROR'), 'status': code, 'retryable': code >= 500}}
    return query_failure(ValueError('unknown business status'))


def http_error_detail(response):
    """Read optional error details without overriding the known HTTP outcome."""
    try:
        body = response.json()
    except ValueError:
        return {}
    return body.get('detail', {}) if isinstance(body, dict) else {}

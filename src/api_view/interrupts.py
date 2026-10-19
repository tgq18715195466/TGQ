"""Serialize checkpoint interrupts and validate human responses before execution."""
from fastapi import HTTPException
from agent.order_fields import build_order_form, structured_supplement

def format_interrupts(interrupts, thread_id):
    result, seen = [], set()
    for item in interrupts:
        iid = item.id
        if iid in seen:
            continue
        seen.add(iid)
        value = item.value if isinstance(item.value, dict) else {"value": str(item.value)}
        entry = {"type": "interrupt", "thread_id": thread_id, "interrupt_id": iid}
        if value.get('workflow_type') == 'procurement':
            entry['workflow_type'] = 'procurement'
            if 'workflow_action' in value:
                entry['workflow_action'] = value['workflow_action']
        if "action_requests" in value:
            entry.update(interrupt_type="hitl_approval", action_requests=value["action_requests"], review_configs=value.get("review_configs", []))
        elif value.get('type') == 'workflow_recovery' and value.get('workflow_type') == 'procurement':
            entry.update(interrupt_type='workflow_recovery', message=value.get('message', ''), write_attempted=value.get('write_attempted') is True)
        elif value.get("type") == "purchase_info_request":
            entry.update(interrupt_type="order_info_supplement",missing_fields=value.get("missing_fields",""),collected_data=value.get("collected_data",""),fields=[])
        elif value.get("type") == "order_info_request":
            entry.update(interrupt_type="order_info_supplement", missing_fields=value.get("missing_fields", ""), collected_data=value.get("collected_data", ""))
            form = build_order_form(value.get("collected_data", ""), orders=value.get("orders"))
            entry.update(form)
            if value.get("fields"):
                entry["fields"] = value["fields"]
            if "order_call_ids" in value:
                entry["order_call_ids"] = value["order_call_ids"]
        else:
            entry.update(interrupt_type="unknown", interrupt_value=value)
        result.append(entry)
    return result

def snapshot_interrupts(snapshot):
    tasks = getattr(snapshot, "tasks", ()) or ()
    if not tasks:
        return list(getattr(snapshot, "interrupts", ()) or ())
    result = []
    for task in tasks:
        # LangGraph retains old interrupts on completed parallel tasks until
        # the entire superstep finishes. Only unresolved tasks need a decision.
        if getattr(task, "result", None) is not None:
            continue
        result.extend(getattr(task, "interrupts", ()) or ())
        nested = getattr(task, "state", None)
        if nested is not None and not isinstance(nested, dict):
            result.extend(snapshot_interrupts(nested))
    return result


def validate_resume_permissions(pending, responses, user):
    """Authorize the checkpoint's real actions, never user-supplied tool names."""
    from backend.auth import can
    from agent.tools.auth_bridge import TOOL_PERMISSIONS

    for item in pending:
        if item['interrupt_id'] not in responses:
            continue
        kind = item['interrupt_type']
        if kind in {'order_info_supplement', 'workflow_recovery'}:
            permissions = ['procurement']
        elif kind == 'hitl_approval':
            permissions = [TOOL_PERMISSIONS.get(action['name'])
                           for action in item.get('action_requests', [])]
        else:
            permissions = []
        if not permissions or any(not can(user, permission) for permission in permissions):
            raise HTTPException(403, '您的账号没有执行本次操作的权限，请联系相应业务人员处理。')


def validate_resume(pending, payload):
    if not pending:
        raise HTTPException(409, "当前没有待处理操作，请刷新会话。")
    by_id = {item["interrupt_id"]: item for item in pending}
    if "responses" in payload:
        responses = payload["responses"]
    elif "interrupt_id" in payload:
        responses = {payload["interrupt_id"]: {k: v for k, v in payload.items() if k != "interrupt_id"}}
    else:
        raise HTTPException(422, "缺少待处理操作编号，请刷新会话后重试。")
    if not isinstance(responses, dict) or not responses:
        raise HTTPException(422, "请填写补充信息或选择审批决定。")
    normalized = {}
    for iid, response in responses.items():
        if iid not in by_id:
            raise HTTPException(409, "该操作已处理或已失效，请刷新会话。")
        if not isinstance(response, dict):
            raise HTTPException(422, "提交内容格式不正确，请重新填写。")
        item = by_id[iid]
        if item["interrupt_type"] == "hitl_approval":
            decisions = response.get("decisions")
            actions = item["action_requests"]
            if not isinstance(decisions, list) or len(decisions) != len(actions):
                raise HTTPException(422, "请为每项操作选择一个审批决定。")
            configs = {r["action_name"]: r.get("allowed_decisions", ["approve", "reject"]) for r in item.get("review_configs", [])}
            for action, decision in zip(actions, decisions):
                allowed = configs.get(action["name"], ["approve", "reject"])
                if not isinstance(decision, dict) or decision.get("type") not in allowed or decision.get("type") not in {"approve", "reject"}:
                    raise HTTPException(422, "请选择同意或拒绝。")
        elif item["interrupt_type"] == "order_info_supplement":
            if item.get('workflow_type') == 'procurement':
                from agent.procurement_workflow import validate_workflow_supplement
                try:
                    response = validate_workflow_supplement(item, response)
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from exc
            elif "values" in response:
                try:
                    response = {"supplement": structured_supplement(item, response)}
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from exc
            elif not isinstance(response.get("supplement"), str) or not response["supplement"].strip():
                raise HTTPException(422, "补充信息不能为空。")
            else:
                response = {"supplement": response["supplement"]}
        elif item['interrupt_type'] == 'workflow_recovery' and item.get('workflow_type') == 'procurement':
            action = response.get('workflow_action')
            if action not in {'retry', 'cancel'}:
                raise HTTPException(422, '请选择重新核验或取消。')
            response = {'workflow_action': action}
        else:
            raise HTTPException(422, "暂不支持处理此类待办，请检查会话状态。")
        normalized[iid] = response
    return normalized

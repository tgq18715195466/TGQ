"""Require complete order information before asking for write approval."""
import json
from pydantic import ValidationError
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain.agents.middleware.types import hook_config
from langchain_core.messages import AIMessage, ToolMessage, SystemMessage
from datetime import datetime, timezone, timedelta
from langgraph.types import interrupt
from backend.app import OrderCreate
from backend.operations import PurchaseRequest
from agent.order_fields import ALIASES, build_order_form, order_validation_message


class OrderPreflightMiddleware(HumanInTheLoopMiddleware):
    def before_model(self,state,runtime):
        today=datetime.now(timezone(timedelta(hours=8))).date().isoformat()
        return {'messages':[SystemMessage(content=f'当前业务日期（北京时间）：{today}。今天、明天、后天从此日期计算，不猜测当前日期。')]}

    async def abefore_model(self,state,runtime):
        return self.before_model(state,runtime)

    @hook_config(can_jump_to=['model'])
    def after_model(self, state, runtime):
        message = state['messages'][-1]
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                if call['name'] != 'purchase_request_create':continue
                try:
                    PurchaseRequest.model_validate({**call['args'],'actor':'authenticated-user'})
                except ValidationError as exc:
                    labels={'part_id':'物料','quantity':'采购数量','unit_price':'采购单价','expected_date':'预计交货日期','reason':'补货原因','demand':'额外需求量','request_id':'提交编号'}
                    missing=[]
                    for error in exc.errors(include_input=False):
                        field=str(error['loc'][-1]) if error['loc'] else ''
                        text=error.get('msg','').removeprefix('Value error, ')
                        if not any('\u4e00'<=char<='\u9fff' for char in text):text='请补充有效值'
                        missing.append(labels.get(field,'申请信息')+'：'+text)
                    response=interrupt({'type':'purchase_info_request','missing_fields':'\n'.join(missing),'collected_data':json.dumps({labels.get(k,k):v for k,v in call['args'].items() if k!='request_id'},ensure_ascii=False)})
                    return {'messages':[ToolMessage(tool_call_id=item['id'],name=item['name'],content='本批操作未执行，请按用户补充重新整理完整申请，再请求确认。用户补充：'+json.dumps(response,ensure_ascii=False)) for item in message.tool_calls],'jump_to':'model'}
            calls = [call for call in message.tool_calls if call['name'] == 'order_create']
            errors, field_errors = [], {}
            for oi, call in enumerate(calls):
                try:
                    OrderCreate.model_validate(call['args'])
                except ValidationError as exc:
                    for error in exc.errors(include_input=False):
                        parts = [str(ALIASES.get(part, part)) for part in error['loc']]
                        path = '.'.join([str(oi), *parts])
                        field_errors[path] = order_validation_message(error)
                        errors.append(f'订单{oi + 1} ' + '.'.join(parts) + ': ' + field_errors[path])
            if errors:
                orders = [call['args'] for call in calls]
                response = interrupt({
                    'type': 'order_info_request', 'missing_fields': '\n'.join(errors),
                    'collected_data': json.dumps(orders, ensure_ascii=False),
                    'order_call_ids': [call['id'] for call in calls],
                    **build_order_form(orders=orders, errors=field_errors),
                })
                # None of this batch has executed. Return all results before replanning.
                return {'messages': [ToolMessage(
                    tool_call_id=call['id'], name=call['name'],
                    content='未执行。先根据用户补充重新整理完整订单，再申请审批。用户补充：'
                    + json.dumps(response, ensure_ascii=False),
                ) for call in message.tool_calls], 'jump_to': 'model'}
        return super().after_model(state, runtime)

    @hook_config(can_jump_to=['model'])
    async def aafter_model(self, state, runtime):
        return self.after_model(state, runtime)

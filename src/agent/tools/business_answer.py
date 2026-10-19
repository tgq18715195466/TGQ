"""Explicit, read-only output boundary for the main agent."""
from langchain_core.tools import tool
from langchain.tools import ToolRuntime
from langchain_core.messages import HumanMessage, ToolMessage
import json

ANSWER_TOOL = "publish_business_answer"


class AnswerReferenceError(ValueError):
    """The selected report cannot be resolved safely in the current turn."""


ANSWER_INSTRUCTIONS = """
最终业务答复必须调用 publish_business_answer。可以填写 answer；本轮子代理已返回完整、贴题的查询报告时，优先用 source_task_id 引用对应 task 工具调用编号，省略 answer，直接交付已有报告，不再重复改写。source_task_id 只能是本轮 task 的编号，不能使用 publish_business_answer 或其他工具的调用编号；不确定时填写 answer。
此工具是最终答复出口，只能在业务工具执行完毕、无需继续调用其他工具时单独调用一次。
不要与其他工具并行调用。不要在普通消息正文写最终答案或内部思考。
正式正文必须自成一体：以简短结论开头，只展示回答当前问题所需的汇总清单、图表和建议。查询/分析可以使用整理好的 Markdown 表格，不要每查一个物料或供应商就输出一张表。简单说明、失败原因、权限提示用普通文字。原始查询依据默认折叠，不能说“完整清单见上方”来代替正文；实际分页或核验缺失须明确说明。不要暴露工具名、系统上下文、幂等键或内部标识。
子代理的清单、筛选结果和图表链接应保留；没有必要不要在结论、建议中再逐条重复清单。写入结果仍以实际工具核验为准，不自编业务单据表格。
采购建议、报告正文、表格及图表标题使用中文业务名称，不展示数据库/API 字段名、工具名、技能名或内部路径，也不写“以某字段为准”。例如 suggested_request_quantity 表述为“建议新增采购量”，stock_gap 表述为“账面缺口”，supplier-price-urls 表述为“供应商报价渠道”；这些英文标识仅供内部理解。可以说“建议采购量以系统综合核算结果为准，不能直接使用账面缺口”。保留真实物料编码、订单编号、型号和有效业务链接。
直接引用子代理报告前检查是否包含上述内部名称；若包含，不使用 source_task_id 原样交付，应填写 answer，改成中文业务表述后发布，同时保留真实数据、清单和图表链接。
库存预警、采购准入和新增补货需求是三个不同事实。不能由“低库存/断货”推断“可采购”，不能把安全库存减当前库存直接命名为建议采购量；只有供应商合作中、物料启用且无待审批申请才可标为当前可申请采购。表格必须使用业务工具返回的采购准入状态，并扣除在途与待审批数量判断是否仍需新增申请；缺失这些字段时明确为未核验，禁止编造可采购状态。
只回答本轮用户目标；内部查询是判断依据，不逐项复述全部查询过程。办理请求只说明处理结果、直接阻塞原因和必要下一步；用户明确要求清单或分析时才展开相应明细。缺少目标操作权限时说明应联系的业务角色，禁止建议用户确认自己无权执行的操作，更不能用其他业务代替。
所有面向用户的正文和表格只能写状态中文名称，不展示数字状态码或 status=数字。订单状态对应：1待审核、2已审核、3采购中、4已入库、5已取消（5绝不是已完成/结案）；供应商状态对应：1合作中、2暂停合作、3已终止。
未执行、执行失败或结果尚未核验的操作不能宣称成功；采购写入只有真实结果 verified=True 且 verificationOutcome='success' 才能确证成功。用户取消或尚需补充字段应使用中性说明；待补充信息或人工确认仍使用原有对应工具。
"""

@tool(return_direct=True)
def publish_business_answer(answer: str = '', source_task_id: str | None = None, runtime: ToolRuntime = None) -> str:
    """发布本轮最终答复。answer 可包含整理后的清单和图表；或只传 source_task_id 直接交付本轮成功 task 的完整报告。两者互斥。"""
    if not source_task_id:
        if not answer.strip():
            raise ValueError('最终答复不能为空。')
        return answer
    if answer.strip():
        raise ValueError('请选择直接引用报告或自行组织答复，不要重复发布。')
    messages = runtime.state.get('messages', []) if runtime is not None else []
    # Only complete task text from after the last human message may become an
    # answer. A stale opaque ID is common after retries: if exactly one such
    # report exists and the ID matches no current tool, recover that report.
    reports = []
    matched_current_tool = False
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            break
        if not isinstance(message, ToolMessage):
            continue
        if message.tool_call_id == source_task_id:
            matched_current_tool = True
        if message.name != 'task' or message.status == 'error':
            continue
        content = message.content
        if not isinstance(content, str) or not content.strip():
            continue
        try:
            json.loads(content)
        except ValueError:
            reports.append((message.tool_call_id, content))
    selected = next((content for call_id, content in reports if call_id == source_task_id), None)
    if selected is not None:
        return selected
    if not matched_current_tool and len(reports) == 1:
        return reports[0][1]
    raise AnswerReferenceError('无法安全确定本轮子代理文字报告。')

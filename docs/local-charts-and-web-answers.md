# 本地图表与网页答案

## 已实现

- generate_visualization 本地优先支持 bar、column、line、pie。无需配置 CHART_MCP_URL，也无需安装额外绘图库；生成中文 SVG，使用绿色主题。
- 参数为 chart_type 和 chart_config；后者包含 title、data=[{category, value}]。最多 60 行，分组/堆叠明确报错，避免悄悄画错；其他高级类型仅在外部工具已加载时可用。
- 图表保存到 src/download/web（Docker 使用既有 download_data 卷），通过 /api/artifacts/<随机ID>.svg 展示。文件名不可预测，接口拒绝任意路径和路径穿越，SVG 设置 CSP sandbox 和 nosniff。
- 主 Agent、采购分析子 Agent、分析技能与记忆说明统一规定：完整答案直接返回聊天正文。供应商查询先返回完整清单，图表为补充；不再将报告路径或 500 字摘要作为答案。
- 仅按用户要求或留档需要生成报告。download_sandbox_file 发布网页可下载链接，Markdown 等文档以附件下载；用户不必访问主机文件系统。
- 历史回答不会自动重写。建议新建会话后重新提问。

## 验证（2026-10-03）

51 项单元测试通过，包括无图表 MCP 时四种本地图表、中文 XML、标签转义、无效数据、附件访问和报告下载链接。
本地 Agent 已重启，8090 及前端 3000 的合成图表与 Markdown 附件均 HTTP 200；Docker web 已重建、健康检查通过，3001 图表请求 HTTP 200。
未调用外部模型进行本次真实业务联调。完整答案的自然语言输出仍受模型遵循指令情况影响。

附件当前沿用项目本地演示的访问边界，随机链接不等于企业认证。旧沙箱报告需调用 download_sandbox_file 发布后才有网页下载链接。

## 单次答案展示
主 Agent 回答保留在聊天区，子 Agent 和工具消息归入默认关闭的执行详情。前后端按 source 分隔文本，避免流式消息串接。已通过 7 项前端测试、10 项会话恢复测试及生产构建。保留历史消息，不做数据删除。

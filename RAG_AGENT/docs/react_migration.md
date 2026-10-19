# React 前端迁移（2026-09-24）

用户要求项目使用 React 或 Vue。选择 React + TypeScript + Vite，将现有中文业务页面迁移为真正的组件应用；不通过 React 包装旧 DOM 脚本。

保留既有页面布局、API、PostgreSQL数据和无假模型结果的边界。组件按登录/布局、工作台、知识库/文档、问答、评测/设置划分。公共层负责有类型的API客户端、认证、弹窗及SSE解析。路由使用 HashRouter。2026-09-30 已按 `username_password_login_plan.md` 改为用户名密码登录，旧 URL 令牌启动器已移除。

执行计划：
1. 建立 web/package.json、锁文件、严格TypeScript配置、Vite开发代理及构建，先验证认证与SSE回归测试。
2. 将页面迁移为React状态/事件/组件；保留上传、预览、下载、重试、删除、任务取消、会话历史/流式恢复/引用/反馈和评测。
3. FastAPI仅提供web/dist构建产物；更新Docker多阶段构建、CI、本地启动脚本和README。
4. 运行类型检查、React交互与SSE测试、生产构建、后端回归及浏览器验收。移除被替代的原生应用入口。

验收：源代码为TSX组件及hooks，不执行旧app.js；React生产构建由现有8000端口提供，Vite开发服务支持热更新；原有真实数据流程不退化。

实施状态：已完成。2026-09-25 验证14项前端测试、TypeScript检查、Vite生产构建、58项后端测试与本地API冒烟检查；5175临时端口验证Vite开发服务可用（默认启动端口仍为5173）。外部模型与检索服务的真实联调仍按任务书待部署。

参考：React官方 https://react.dev/learn/build-a-react-app-from-scratch ，Vite官方 https://vite.dev/guide/static-deploy 。

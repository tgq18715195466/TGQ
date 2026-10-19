from pathlib import Path

from langchain_openai import ChatOpenAI

from agent.env_utils import (
    DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL,
)
import os

# ---------- 模型配置 ----------
# 主 Agent 模型
MAIN_MODEL = ChatOpenAI(
    streaming=True,
    model=os.getenv("MAIN_MODEL", "deepseek-chat"),
    temperature=float(os.getenv("MAIN_MODEL_TEMPERATURE", "0.3")),
    openai_api_key=DEEPSEEK_API_KEY,
    openai_api_base=DEEPSEEK_BASE_URL,
    max_tokens=2560000,
    extra_body={"thinking": {"type": "disabled"}},
)
# 摘要专用模型（摘要需要稳定输出，temperature 设为较低值）
SUMMARY_MODEL = ChatOpenAI(
    model=os.getenv("SUMMARY_MODEL", "deepseek-chat"),
    temperature=0.3,
    openai_api_key=DEEPSEEK_API_KEY,
    openai_api_base=DEEPSEEK_BASE_URL,
    max_tokens=2560000,
    extra_body={"thinking": {"type": "disabled"}},
)
# ---------- 沙箱配置 ----------
# 远程 OpenSandbox 已移除，改为本地文件后端（见下方 LOCAL_SANDBOX_ROOT）。
# 由 main_agent.py 用 deepagents 的 LocalShellBackend 直接读写本地文件系统。

# ---------- 路径常量 ----------
EXAMPLE_DIR = Path(__file__).parent.parent
print(f'当前代码执行的工作目录为：{EXAMPLE_DIR}')
# 沙箱内技能根路径
SANDBOX_SKILLS_ROOT = "/skills"
# 沙箱内记忆根路径（用户私有记忆存放处）
SANDBOX_MEMORIES_ROOT = "/memories"
# 沙箱内分析中间文件存放目录
SANDBOX_ANALYSIS_ROOT = "/analysis"
# 沙箱内数据文件存放目录
SANDBOX_DATA_ROOT = "/data"
# 本地技能资源目录（项目内的路径，相对于项目根）
LOCAL_SKILLS_DIR = EXAMPLE_DIR / "skills"
# 本地下载目录（从沙箱下载文件的目标路径）
DOWNLOAD_DIR = EXAMPLE_DIR / "download"
# 本地文件后端根目录（替代远程 OpenSandbox，文件与 shell 执行都落在本地）
LOCAL_SANDBOX_ROOT = EXAMPLE_DIR / "local_sandbox"
# 本地子 Agent 配置目录
LOCAL_SUBAGENT_CONFIG_DIR = EXAMPLE_DIR / "agent/subagents"
# 本地的Agent记忆文件
LOCAL_AGENTS_MD = EXAMPLE_DIR / "agent/memory/AGENTS.md"

# ---------- 文件名常量 ----------
# 主 Agent 只读指引文件（上传到沙箱 /AGENTS.md）
AGENTS_MD_FILENAME = "/AGENTS.md"
# 用户偏好文件名（在 /memories/{user_id}/ 下）
USER_PREFERENCES_FILENAME = "preferences.md"

# ---------- 用户技能持久化 ----------
# 技能持久化 StoreBackend 路由路径
PERSISTED_SKILLS_ROOT = "/persisted-skills"
# 技能 StoreBackend 命名空间（按 Agent scope 组织，无用户隔离）
SKILLS_STORE_NAMESPACE = ("skills",)
# 子 Agent 名称 → 技能 scope 目录映射
SCOPE_MAP = {
    "main": "main",
    "procurement-analyst": "procurement",
    "procurement-order": "order",
}

# ---------- 中间件参数 ----------


"""
ERP_OPENCLAW 本地一键启动脚本

按顺序启动 4 个服务：
  1. ERP 最小后端   :8080  (FastAPI + PyMySQL 连本地 MySQL)
  2. MCP Server     :8001+  (FastMCP，ERP 业务工具)
  3. Web API        :8090  (FastAPI + SSE，含 Agent 初始化 30-60 秒)
  4. 前端           :3000  (Vue + Vite)

依赖已本地化的外部服务（不再需要远程服务器）：
  - PostgreSQL → 自动降级为内存 checkpointer
  - OpenSandbox→ 本地文件后端
  - 图表 MCP   → 失效时自动跳过

用法（必须用 conda 的 python，PATH 里的 python 是商店桩）：
    /d/miniconda3/envs/agent/python.exe run_local.py
退出：Ctrl+C
"""

import os
from dotenv import load_dotenv
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable
SRC_DIR = os.path.join(PROJECT_ROOT, "src")

# Windows 控制台默认 GBK，遇到 emoji/箭头等字符会崩，强制 UTF-8 输出
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass

_procs: list = []  # 记录所有子进程，退出时统一清理


def find_available_port(start=8001):
    for port in range(start, 65536):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("No available MCP port")

def _stream(proc: subprocess.Popen, name: str) -> None:
    """实时转发子进程输出，加 [name] 前缀。"""
    def _run():
        for line in proc.stdout:
            if line:
                try:
                    text = line.decode("utf-8", errors="replace").rstrip()
                except Exception:
                    text = line.decode("gbk", errors="replace").rstrip()
                try:
                    print(f"[{name}] {text}")
                except Exception:
                    pass

    threading.Thread(target=_run, daemon=True).start()


def _spawn(name: str, args: list, cwd: str, *, shell: bool = False) -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONPATH"] = SRC_DIR + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUNBUFFERED"] = "1"  # 让子进程实时刷新 stdout，日志能即时转发
    proc = subprocess.Popen(
        args, cwd=cwd, env=env, shell=shell,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0,
    )
    _procs.append(proc)
    _stream(proc, name)
    return proc


def _wait_http(url: str, name: str, timeout: int) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if resp.status == 200:
                    print(f"[System] {name} 就绪 ({url})")
                    return True
        except Exception:
            pass
        time.sleep(2)
        print(f"[System] 等待 {name} ... ({int(time.time() - start)}s)")
    return False


def _wait_port(port: int, name: str, timeout: int) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                print(f"[System] {name} 就绪 (port {port})")
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def _cleanup() -> None:
    print("\n[System] 正在停止所有服务...")
    for p in _procs:
        if p.poll() is None:
            p.terminate()
    for p in _procs:
        try:
            p.wait(timeout=5)
        except Exception:
            p.kill()
    print("[System] 所有服务已停止")


def main() -> None:
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"), override=False)
    for port in (8080, 8090, 3000):
        if find_available_port(port) != port:
            raise RuntimeError(f"Required port {port} is occupied; stop its owner before startup.")
    mcp_port = find_available_port(8001)
    os.environ["MCP_PORT"] = str(mcp_port)
    os.environ["ERP_MCP_URL"] = f"http://127.0.0.1:{mcp_port}/mcp"
    print("=" * 60)
    print("        ERP_OPENCLAW 本地一键启动")
    print("=" * 60)
    print("  1. ERP 后端  :8080")
    print(f"  2. MCP Server:{mcp_port}")
    print("  3. Web API   :8090  (Agent 初始化约 30-60 秒)")
    print("  4. 前端       :3000")
    print("=" * 60)

    # Initialize only missing ledger tables before accepting write requests.
    subprocess.run([PYTHON, "-m", "backend.migrate"], cwd=PROJECT_ROOT, check=True)

    # Internal loopback services must not be routed through an external proxy.
    bypass = os.environ.get("NO_PROXY", os.environ.get("no_proxy", ""))
    os.environ["NO_PROXY"] = ",".join(filter(None, [bypass, "localhost", "127.0.0.1"]))

    # 1. ERP 最小后端
    _spawn("Backend", [PYTHON, "-m", "uvicorn", "backend.app:app",
                       "--host", "0.0.0.0", "--port", "8080"], PROJECT_ROOT)
    if not _wait_http("http://localhost:8080/health", "ERP 后端", timeout=30):
        print("[ERROR] ERP 后端启动失败，请检查 MySQL 是否运行、.env 密码是否正确")
        _cleanup()
        return

    # 2. MCP Server
    _spawn("MCP", [PYTHON, "-m", "mcp_server.server_main"], PROJECT_ROOT)
    if not _wait_port(mcp_port, "MCP Server", timeout=30):
        print("[ERROR] MCP Server 启动失败")
        _cleanup()
        return

    # 3. Web API（FastAPI，Agent 初始化）
    _spawn("WebAPI", [PYTHON, "-m", "uvicorn", "api_view.web_main:app", "--loop", "api_view.event_loop:selector_loop",
                      "--host", "0.0.0.0", "--port", "8090"], PROJECT_ROOT)
    if not _wait_http("http://localhost:8090/health/ready", "Web API（Agent 初始化）", timeout=180):
        print("[ERROR] Web API 启动失败，请查看上方日志")
        _cleanup()
        return

    # 4. 前端
    frontend_dir = os.path.join(PROJECT_ROOT, "frontend")
    if not os.path.exists(os.path.join(frontend_dir, "node_modules")):
        print("[Frontend] 首次运行，正在 npm install ...")
        r = subprocess.run("npm install", cwd=frontend_dir, shell=True,
                           capture_output=True, text=True)
        if r.returncode != 0:
            print("[ERROR] npm install 失败：")
            print(r.stderr)
            _cleanup()
            return
    _spawn("Frontend", "npm run dev -- --strictPort", frontend_dir, shell=True)
    if not _wait_http("http://localhost:3000", "Frontend", timeout=60):
        _cleanup()
        raise RuntimeError("Frontend failed to start")

    print()
    print("=" * 60)
    print("        全部服务已启动！")
    print("    - 前端:      http://localhost:3000")
    print("    - API 文档:  http://localhost:8090/docs")
    print("    - 按 Ctrl+C 停止所有服务")
    print("=" * 60)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        _cleanup()


if __name__ == "__main__":
    try:
        signal.signal(signal.SIGINT, lambda s, f: (_cleanup(), sys.exit(0)))
        signal.signal(signal.SIGTERM, lambda s, f: (_cleanup(), sys.exit(0)))
    except Exception:
        pass
    main()

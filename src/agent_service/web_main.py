"""Web 工作台启动入口。"""

import argparse
from pathlib import Path

import uvicorn

from agent_service.adapters.web.app import create_web_app


def main() -> None:
    parser = argparse.ArgumentParser(description="启动 Agent Service 通用 Web 工作台")
    parser.add_argument("--host", default="127.0.0.1", help="监听主机地址")
    parser.add_argument("--port", type=int, default=8000, help="监听端口")
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.cwd(),
        help="文件工具绑定的工作区根目录",
    )
    args = parser.parse_args()

    app = create_web_app(workspace=args.workspace)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()

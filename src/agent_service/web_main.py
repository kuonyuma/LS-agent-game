"""Web 工作台启动入口。"""

import argparse
from getpass import getpass
from pathlib import Path

import uvicorn

from agent_service.adapters.storage.account_store import AccountStore
from agent_service.adapters.web.app import create_web_app
from agent_service.config.settings import sync_configured_env_vars


def main() -> None:
    parser = argparse.ArgumentParser(description="启动 Agent Service 通用 Web 工作台")
    parser.add_argument("--host", default="127.0.0.1", help="监听主机地址")
    parser.add_argument("--port", type=int, default=8000, help="监听端口")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--local", action="store_true", help="仅通过回环地址打开本机工作台")
    mode.add_argument("--create-admin", metavar="用户名", help="创建首个工作台管理员后退出")
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.cwd(),
        help="文件工具绑定的工作区根目录",
    )
    args = parser.parse_args()
    if args.create_admin is not None:
        password = getpass("管理员密码：")
        if password != getpass("再次输入密码："):
            parser.error("两次密码不一致。")
        accounts = AccountStore(args.workspace.resolve() / ".agent_data")
        try:
            account = accounts.create_account(args.create_admin, password, role="admin")
        except ValueError as exc:
            parser.error(str(exc))
        print(f"管理员 {account.username} 已创建。")
        return
    loopback = args.host in {"127.0.0.1", "localhost", "::1"}
    if args.local and not loopback:
        parser.error("--local 仅支持回环地址。")

    sync_configured_env_vars(("PRESENTON_BASE_URL", "PRESENTON_API_KEY", "DASHSCOPE_IMAGE_BASE_URL"))
    app = create_web_app(workspace=args.workspace, local_mode=args.local, allow_browser_setup=loopback)
    print(app.state.presentation_generation["message"], flush=True)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()

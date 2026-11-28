import asyncio

from agent_service.bootstrap import create_app, create_runner
from agent_service.config.settings import sync_configured_env_vars


async def main():
    sync_configured_env_vars()
    async with create_runner() as runner:
        app = create_app(runner)
        await app.run()


if __name__ == "__main__":
    asyncio.run(main())

# trisul_ai_cli/cli.py
import asyncio
from . import client

def cli_main():
    """Console script entry point: runs client.main() (async) safely."""
    asyncio.run(client.main())


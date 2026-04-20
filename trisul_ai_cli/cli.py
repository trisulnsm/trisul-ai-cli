import argparse
import asyncio
import sys
from .client import TrisulAIClient
from importlib.metadata import version


def docs(args=None):
    print("""Read the full documentation here:
    👉 https://www.trisul.org/blog/trisul-ai-2025-a-new-way-to-interact-with-network-intelligence""")


def cli_main():
    DESCRIPTION = """Trisul AI: Turn raw network data into answers using plain English.

Trisul AI can be operated in two modes:
1. CLI Mode: An interactive terminal-based chat interface.
2. API Mode: A REST API server for integrating Trisul AI into web applications."""

    EPILOG = """
Examples:
  # 1. CLI Mode (Interactive Chat)
  trisul_ai_cli

  # 2. API Mode (REST Server)
  trisul_ai_cli api --port 8200
  trisul_ai_cli api --host 192.168.1.10 --port 8200 
  trisul_ai_cli api --log-level debug

  # 3. Documentation
  trisul_ai_cli docs
    """

    parser = argparse.ArgumentParser(
        prog="trisul_ai_cli", 
        description=DESCRIPTION,
        usage="trisul_ai_cli <COMMAND>", 
        add_help=False, 
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EPILOG
    )

    parser.add_argument("-h", "--help", action="store_true", help="print help")
    parser.add_argument("-v", "-V", "--version", action="version", version=f"Trisul AI CLI - {version('trisul_ai_cli')}")

    subparsers = parser.add_subparsers(dest="command", title="Commands")
    # Available subcommands
    commands = {
        "docs": ("Open Trisul online documentation", docs),
    }

    # Register each subcommand
    for name, (desc, func) in commands.items():
        sp = subparsers.add_parser(name, help=desc)
        sp.set_defaults(func=func)

    # 'api' subcommand — starts the REST API server
    api_parser = subparsers.add_parser(
        "api",
        description=DESCRIPTION,
        help="Start the Trisul AI REST API server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EPILOG
    )
    api_parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind the API server to (default: 0.0.0.0)",
    )
    api_parser.add_argument(
        "--port",
        type=int,
        default=8200,
        help="Port for the API server (default: 8200)",
    )
    api_parser.add_argument(
        "--log-level",
        default="info",
        choices=["debug", "info", "warning", "error", "critical"],
        help="Uvicorn log level (default: info)",
    )

    args, _ = parser.parse_known_args()
    

    # Handle help or no args
    if args.help:
        parser.print_help()
        sys.exit(0)

    # 'api' subcommand
    if args.command == "api":
        from trisul_ai_cli.api_server import start_server
        start_server(host=args.host, port=args.port, log_level=args.log_level)
        return

    # Default behavior (no subcommand → start chat)
    if args.command is None:
        client = TrisulAIClient()
        asyncio.run(client.main())
        return

    # Execute the subcommand
    func = getattr(args, "func", None)
    if func:
        func(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    cli_main()

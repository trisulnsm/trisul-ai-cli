import argparse
import asyncio
import sys
from . import client
from importlib.metadata import version


def docs(args=None):
    print("""Read the full documentation here:
    👉 https://www.trisul.org/blog/trisul-ai-2025-a-new-way-to-interact-with-network-intelligence""")


def cli_main():
    parser = argparse.ArgumentParser(
        prog="trisul_ai_cli", 
        description="Trisul AI CLI", 
        usage="trisul_ai_cli <COMMAND>", 
        add_help=False, 
        formatter_class=argparse.RawTextHelpFormatter,
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

    args, _ = parser.parse_known_args()
    

    # Handle help or no args
    if args.help:
        parser.print_help()
        sys.exit(0)

    # Default behavior (no subcommand → start chat)
    if args.command is None:
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

import argparse
import asyncio
import atexit
import os
import signal
import subprocess
import sys
from pathlib import Path
from .client import TrisulAIClient
from importlib.metadata import version


def _runtime_dir() -> Path:
    log_dir = os.environ.get("TRISUL_AI_LOG_DIR", "").strip()
    if log_dir:
        directory = Path(log_dir)
        directory.mkdir(parents=True, exist_ok=True)
        return directory
    return Path.cwd()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _print_recent_log(log_file: Path, max_bytes: int = 4000) -> None:
    try:
        with open(log_file, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - max_bytes))
            data = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return
    text = data.strip()
    if text:
        print(text, file=sys.stderr)


def _remove_daemon_pidfile(pid_file: str) -> None:
    path = Path(pid_file)
    try:
        if path.read_text(encoding="utf-8").strip() == str(os.getpid()):
            path.unlink()
    except OSError:
        pass


def _start_api_daemon() -> None:
    """Re-exec the API server detached from this terminal."""
    if os.name != "posix":
        print("Daemon mode (-d) is only supported on Unix.", file=sys.stderr)
        sys.exit(1)

    runtime_dir = _runtime_dir()
    pid_file = runtime_dir / "trisul_ai_api.pid"
    log_file = runtime_dir / "trisul_ai_cli.log"

    if pid_file.exists():
        try:
            existing = int(pid_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            existing = None
        if existing and _pid_alive(existing):
            print(
                f"Trisul AI API is already running in the background (pid {existing}).",
                file=sys.stderr,
            )
            sys.exit(1)
        pid_file.unlink(missing_ok=True)

    # Always re-exec as a module. `python -m trisul_ai_cli.cli` sets
    # argv[0] to cli.py, and running that file directly breaks its
    # relative imports.
    argv = [sys.executable, "-m", "trisul_ai_cli.cli"]
    argv.extend(arg for arg in sys.argv[1:] if arg not in ("-d", "--daemon"))
    env = os.environ.copy()
    env["TRISUL_AI_DAEMON_PIDFILE"] = str(pid_file)

    with open(log_file, "ab") as log_handle:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=env,
            cwd=os.getcwd(),
        )
    try:
        proc.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        pid_file.write_text(f"{proc.pid}\n", encoding="utf-8")
        print(f"Trisul AI API started in the background (pid {proc.pid}).")
        print(f"Log file: {log_file}")
        sys.exit(0)

    print(
        f"Trisul AI API exited immediately (code {proc.returncode}).",
        file=sys.stderr,
    )
    _print_recent_log(log_file)
    sys.exit(1)


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
  trisul_ai_cli api -d
  trisul_ai_cli api --port 8200 -d

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
        "-d",
        "--daemon",
        action="store_true",
        help="Run the API server in the background and return immediately",
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
    api_parser.add_argument(
        "--ssl-keyfile",
        default=None,
        help="Path to the SSL key file for HTTPS mode",
    )
    api_parser.add_argument(
        "--ssl-certfile",
        default=None,
        help="Path to the SSL certificate file for HTTPS mode",
    )

    args, _ = parser.parse_known_args()
    

    # Handle help or no args
    if args.help:
        parser.print_help()
        sys.exit(0)

    # 'api' subcommand
    if args.command == "api":
        pidfile = os.environ.get("TRISUL_AI_DAEMON_PIDFILE", "").strip()
        if pidfile:
            signal.signal(signal.SIGHUP, signal.SIG_IGN)
            atexit.register(_remove_daemon_pidfile, pidfile)

            def _stop_daemon(signum, _frame, pid_file=pidfile):
                # Uvicorn handles the signal, then raises it again on the way
                # out. Remove the pid file before that second signal ends the
                # process without running atexit handlers.
                _remove_daemon_pidfile(pid_file)
                signal.signal(signum, signal.SIG_DFL)
                os.kill(os.getpid(), signum)

            signal.signal(signal.SIGTERM, _stop_daemon)
            signal.signal(signal.SIGINT, _stop_daemon)
        if args.daemon:
            _start_api_daemon()
        from trisul_ai_cli.api_server import start_server
        start_server(
            host=args.host, 
            port=args.port, 
            log_level=args.log_level,
            ssl_keyfile=args.ssl_keyfile,
            ssl_certfile=args.ssl_certfile
        )
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

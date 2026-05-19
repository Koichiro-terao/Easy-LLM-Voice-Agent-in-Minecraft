from __future__ import annotations

import argparse
import sys

from mc_server_common import MinecraftServerError, ensure_docker_running, show_box
from mc_server_control import resolve_mc_server_stop_port, show_running_mc_servers, stop_mc_server


def parse_args(argv: list[str]) -> str | None:
    parser = argparse.ArgumentParser(
        description="Stop the running Minecraft server on the specified port."
    )
    parser.add_argument("port", nargs="?", help="Minecraft port. Default is 25565.")
    args = parser.parse_args(argv)
    return args.port


def main(argv: list[str] | None = None) -> int:
    port = parse_args(argv or sys.argv[1:])

    ensure_docker_running()
    if not show_running_mc_servers():
        show_box("NOTICE", "No running servers were found.")
        return 0

    port = resolve_mc_server_stop_port(port)
    stop_mc_server(port, show_status=True)
    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
    except MinecraftServerError as exc:
        show_box("ERROR", str(exc))
        exit_code = 1
    except KeyboardInterrupt:
        print()
        exit_code = 130
    sys.exit(exit_code)

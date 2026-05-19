from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mc_server_common import MinecraftServerError, show_box
from mc_server_control import launch_mc_server, resolve_mc_server_launch_inputs


def parse_args(argv: list[str]) -> tuple[str | None, str | None, str | None]:
    parser = argparse.ArgumentParser(
        description="Launch the Minecraft server for a mode, port, and Minecraft version."
    )
    parser.add_argument("mode_name", nargs="?", help="Mode name. Default is flat.")
    parser.add_argument("port", nargs="?", help="Minecraft port. Default is 25565.")
    parser.add_argument("mc_version", nargs="?", help="Minecraft version. Default is 1.21.")
    args = parser.parse_args(argv)
    return args.mode_name, args.port, args.mc_version


def main(argv: list[str] | None = None) -> int:
    mode_name, port, mc_version = parse_args(argv or sys.argv[1:])
    root_dir = Path(__file__).resolve().parent

    mode_name, port, mc_version = resolve_mc_server_launch_inputs(mode_name, port, mc_version, root_dir)
    return launch_mc_server(mode_name, port, mc_version, root_dir=root_dir, attach=True)


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

import argparse
import sys

from mineflayer_control import DEFAULT_MC_VERSION, LauncherError, launch_mineflayer, resolve_mc_version, show_box


def parse_args(argv: list[str]) -> tuple[str | None, bool]:
    parser = argparse.ArgumentParser(
        description="Launch the mineflayer server for a specific Minecraft version."
    )
    parser.add_argument("mc_version", nargs="?", help="Minecraft version. Default is 1.21.")
    parser.add_argument(
        "-r",
        action="store_true",
        dest="force_rebuild",
        help="Rebuild the Docker image and regenerate the flag cache.",
    )
    args = parser.parse_args(argv)
    return args.mc_version, args.force_rebuild


def main(argv: list[str] | None = None) -> int:
    mc_version, force_rebuild = parse_args(argv or sys.argv[1:])
    mc_version = resolve_mc_version(mc_version)
    return launch_mineflayer(mc_version, force_rebuild=force_rebuild)


if __name__ == "__main__":
    try:
        exit_code = main()
    except LauncherError as exc:
        show_box("ERROR", str(exc))
        exit_code = 1
    except KeyboardInterrupt:
        print()
        exit_code = 130
    sys.exit(exit_code)

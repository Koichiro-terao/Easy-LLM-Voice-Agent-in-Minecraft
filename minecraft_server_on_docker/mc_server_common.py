from __future__ import annotations

import subprocess


CONTAINER_PREFIX = "mc_server_"


class MinecraftServerError(RuntimeError):
    """Raised when a Minecraft server helper script cannot continue."""


def show_box(title: str, *lines: str) -> None:
    print()
    print(f"===== {title} =====")
    for line in lines:
        print(line)
    print("=" * 16)


def run_command(
    args: list[str],
    *,
    capture_output: bool = False,
    quiet: bool = False,
) -> subprocess.CompletedProcess[str]:
    kwargs = {
        "args": args,
        "check": False,
        "text": True,
    }
    if capture_output:
        kwargs["capture_output"] = True
    elif quiet:
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
    return subprocess.run(**kwargs)


def capture_stdout(args: list[str]) -> str:
    result = run_command(args, capture_output=True)
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def ensure_docker_running() -> None:
    result = run_command(["docker", "info"], quiet=True)
    if result.returncode != 0:
        raise MinecraftServerError("Docker Desktop is not running. Please start it and try again.")


def list_running_server_names() -> list[str]:
    result = run_command(
        ["docker", "ps", "--filter", f"name=^/{CONTAINER_PREFIX}", "--format", "{{.Names}}"],
        capture_output=True,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def format_server_display_name(container_name: str) -> str:
    display_name = container_name
    if display_name.startswith(CONTAINER_PREFIX):
        display_name = display_name[len(CONTAINER_PREFIX) :]
    return display_name.replace("_", " / ")

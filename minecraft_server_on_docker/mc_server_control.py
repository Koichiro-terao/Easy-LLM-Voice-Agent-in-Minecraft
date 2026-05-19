from __future__ import annotations

from pathlib import Path

from generate_compose import main as generate_compose_main
from mc_server_common import (
    MinecraftServerError,
    capture_stdout,
    ensure_docker_running,
    format_server_display_name,
    list_running_server_names,
    run_command,
    show_box,
)


DEFAULT_MODE_NAME = "flat"
DEFAULT_PORT = "25565"
DEFAULT_MC_VERSION = "1.21"
NETWORK_NAME = "bnnet"


def resolve_mc_server_launch_inputs(
    mode_name: str | None,
    port: str | None,
    mc_version: str | None,
    root_dir: Path,
) -> tuple[str, str, str]:
    if mode_name is None:
        mode_name = input(f"Enter mode name [{DEFAULT_MODE_NAME}] > ").strip() or DEFAULT_MODE_NAME
    mode_dir = root_dir / mode_name
    if not (mode_dir / "profile.env.yml").is_file():
        raise MinecraftServerError(f'Mode "{mode_name}" was not found.')

    if port is None:
        port = input(f"Enter Minecraft port [{DEFAULT_PORT}] > ").strip() or DEFAULT_PORT
    if not port.isdigit():
        raise MinecraftServerError("Invalid port.")

    if mc_version is None:
        mc_version = input(f"Enter Minecraft version [{DEFAULT_MC_VERSION}] > ").strip() or DEFAULT_MC_VERSION
    return mode_name, port, mc_version


def prepare_mc_server_compose(root_dir: Path, mode_name: str, port: str, mc_version: str) -> tuple[Path, str]:
    mods_dir = root_dir / "_mods" / mc_version
    if not mods_dir.is_dir():
        raise MinecraftServerError(f'Mods directory for Minecraft {mc_version} was not found. Create "{mods_dir}".')

    mods_host_path = f"../../_mods/{mc_version}"
    compose_file = generate_compose_main(mode_name, port, mc_version, mods_host_path)
    container_name = f"mc_server_{mode_name}_{port}"

    inspect = run_command(["docker", "network", "inspect", NETWORK_NAME], quiet=True)
    if inspect.returncode != 0:
        create = run_command(["docker", "network", "create", NETWORK_NAME])
        if create.returncode != 0:
            raise MinecraftServerError(f"Failed to create Docker network {NETWORK_NAME}.")

    return compose_file, container_name


def inspect_mc_server_state(container_name: str, port: str) -> tuple[bool, bool, str | None]:
    running_container_id = capture_stdout(["docker", "ps", "-q", "--filter", f"name=^/{container_name}$"])
    container_id = capture_stdout(["docker", "ps", "-aq", "--filter", f"name=^/{container_name}$"])

    port_in_use_container = None
    result = run_command(["docker", "ps", "--filter", f"publish={port}", "--format", "{{.Names}}"], capture_output=True)
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            name = line.strip()
            if name and name.lower() != container_name.lower():
                port_in_use_container = name
                break

    return bool(running_container_id), bool(container_id), port_in_use_container


def remove_existing_mc_server_container(container_name: str) -> None:
    result = run_command(["docker", "rm", "-f", container_name])
    if result.returncode != 0:
        raise MinecraftServerError(f"Failed to remove existing container {container_name}.")


def ensure_mc_server_running(mode_name: str, port: str, mc_version: str, root_dir: Path | None = None) -> tuple[Path, str]:
    ensure_docker_running()
    if root_dir is None:
        root_dir = Path(__file__).resolve().parent

    compose_file, container_name = prepare_mc_server_compose(root_dir, mode_name, port, mc_version)
    is_running, exists, port_in_use_container = inspect_mc_server_state(container_name, port)

    if is_running:
        show_box(
            "NOTICE",
            "Server is already running.",
            "File changes are not applied until it is stopped.",
        )
        return compose_file, container_name

    if exists:
        show_box(
            "NOTICE",
            "Stopped server found.",
            "Removing old container and starting again.",
            "World data is kept.",
        )
        remove_existing_mc_server_container(container_name)

    if port_in_use_container:
        raise MinecraftServerError(
            f"Port {port} is already in use by another running server: {port_in_use_container}"
        )

    result = run_command(["docker", "compose", "-f", str(compose_file), "up", "-d"])
    if result.returncode != 0:
        raise MinecraftServerError("Failed to start the Minecraft server.")

    show_box(
        "SERVER",
        "Server started.",
        f"Compose file: {compose_file}",
        "Press Ctrl+C in this window to stop the server.",
    )
    return compose_file, container_name


def attach_to_mc_server(container_name: str) -> int:
    result = run_command(["docker", "attach", container_name])
    return result.returncode


def launch_mc_server(
    mode_name: str,
    port: str,
    mc_version: str,
    *,
    root_dir: Path | None = None,
    attach: bool = True,
) -> int:
    _, container_name = ensure_mc_server_running(mode_name, port, mc_version, root_dir=root_dir)
    if not attach:
        return 0
    return attach_to_mc_server(container_name)


def list_running_mc_servers_display() -> list[str]:
    return [format_server_display_name(name) for name in list_running_server_names()]


def show_running_mc_servers() -> bool:
    names = list_running_mc_servers_display()
    if not names:
        return False

    show_box("RUNNING SERVERS", *names)
    return True


def resolve_mc_server_stop_port(port: str | None) -> str:
    if port is None:
        port = input(f"Enter the port you want to stop [{DEFAULT_PORT}] > ").strip() or DEFAULT_PORT
    if not port.isdigit():
        raise MinecraftServerError("Invalid port.")
    return port


def find_running_mc_server_container_by_port(port: str) -> str | None:
    result = run_command(
        ["docker", "ps", "--filter", f"publish={port}", "--format", "{{.Names}}"],
        capture_output=True,
    )
    if result.returncode != 0:
        return None

    for line in result.stdout.splitlines():
        name = line.strip()
        if name:
            return name
    return None


def stop_mc_server(port: str, *, show_status: bool = True) -> bool:
    ensure_docker_running()
    container_name = find_running_mc_server_container_by_port(port)
    if container_name is None:
        if show_status:
            show_box("NOTICE", f"No running server was found on port {port}.")
        return False

    if show_status:
        show_box(
            "SERVER",
            f"Stopping server on port {port}...",
            f"Container: {container_name}",
        )
    result = run_command(["docker", "stop", container_name])
    if result.returncode != 0:
        raise MinecraftServerError("Failed to stop the server.")

    if show_status:
        show_box(
            "SERVER",
            "Server stopped.",
            "File changes will be applied the next time you launch it.",
        )
    return True

from __future__ import annotations

import re
import subprocess
from pathlib import Path


DEFAULT_MC_VERSION = "1.21"
DEFAULT_PORT = "3000"
IMAGE_NAME = "beliefnestjs"
CONTAINER_NAME = "beliefnestjs"


class LauncherError(RuntimeError):
    """Raised when the mineflayer launcher cannot continue."""


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


def ensure_docker_running() -> None:
    result = run_command(["docker", "info"], quiet=True)
    if result.returncode != 0:
        raise LauncherError("Docker Desktop is not running. Please start it and try again.")


def resolve_mc_version(mc_version: str | None) -> str:
    if mc_version is None:
        mc_version = input(f"Enter Minecraft version [{DEFAULT_MC_VERSION}] > ").strip() or DEFAULT_MC_VERSION
    return mc_version


def build_image_if_needed(image: str, mineflayer_dir: Path, force_rebuild: bool) -> None:
    if not force_rebuild:
        inspect = run_command(["docker", "image", "inspect", image], quiet=True)
        if inspect.returncode == 0:
            return

    build_args = ["docker", "build"]
    if force_rebuild:
        build_args.extend(["--pull", "--no-cache"])
    build_args.extend(["-t", image, "-f", str(mineflayer_dir / "Dockerfile"), str(mineflayer_dir)])

    print(f"Building {image} ...")
    result = run_command(build_args)
    if result.returncode != 0:
        raise LauncherError("Build failed.")


def remove_existing_container(container_name: str) -> None:
    run_command(["docker", "rm", "-f", container_name], quiet=True)


def build_flag_cache(image: str, mineflayer_dir: Path, cache_dir: Path, mc_version: str, outfile: Path) -> None:
    print(f'Building flag cache for Minecraft {mc_version}: "{outfile}"')
    result = run_command(
        [
            "docker",
            "run",
            "--rm",
            "--add-host",
            "host.docker.internal:host-gateway",
            "-v",
            f"{mineflayer_dir / 'build_flag_cache.js'}:/app/build_flag_cache.js",
            "-v",
            f"{cache_dir}:/cache",
            "-w",
            "/app",
            image,
            "node",
            "build_flag_cache.js",
            mc_version,
        ]
    )
    if result.returncode != 0:
        raise LauncherError("Cache build failed.")
    if not outfile.exists():
        raise LauncherError(f"Cache build finished but output is missing: {outfile}")


def ensure_flag_cache(image: str, mineflayer_dir: Path, cache_dir: Path, mc_version: str, force_rebuild: bool) -> None:
    cache_dir.mkdir(exist_ok=True)
    outfile = cache_dir / f"cache_{mc_version}.msgpack"

    if not force_rebuild and outfile.exists():
        return

    build_flag_cache(image, mineflayer_dir, cache_dir, mc_version, outfile)


def run_mineflayer(image: str, log_dir: Path, root_dir: Path) -> int:
    print(f"Starting container: {CONTAINER_NAME}")
    result = run_command(
        [
            "docker",
            "run",
            "--name",
            CONTAINER_NAME,
            "--rm",
            "-w",
            "/app",
            "-p",
            f"{DEFAULT_PORT}:{DEFAULT_PORT}",
            "--add-host",
            "host.docker.internal:host-gateway",
            "-v",
            f"{log_dir}:/mf_logs",
            "-v",
            f"{root_dir}:/workspace",
            "-e",
            "BN_DOCKER_LOCALHOST_ALIAS=host.docker.internal",
            "-e",
            "NODE_PATH=/app/node_modules",
            image,
            "node",
            "/workspace/mineflayer/server.js",
            DEFAULT_PORT,
        ]
    )
    return result.returncode


def launch_mineflayer(mc_version: str, *, force_rebuild: bool = False, root_dir: Path | None = None) -> int:
    ensure_docker_running()
    if root_dir is None:
        root_dir = Path(__file__).resolve().parent

    cache_dir = root_dir / ".cache"
    log_dir = root_dir / "mineflayer_logs"
    mineflayer_dir = root_dir / "mineflayer"
    log_dir.mkdir(exist_ok=True)

    image = f"{IMAGE_NAME}:forELA"

    remove_existing_container(CONTAINER_NAME)
    build_image_if_needed(image, mineflayer_dir, force_rebuild)
    ensure_flag_cache(image, mineflayer_dir, cache_dir, mc_version, force_rebuild)
    return run_mineflayer(image, log_dir, root_dir)

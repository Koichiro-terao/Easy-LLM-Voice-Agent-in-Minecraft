from pathlib import Path
import re
import sys


def main(mode_name, mc_port, mc_version, mods_host_path):
    if not mc_port.isdigit():
        raise SystemExit("Usage: python generate_compose.py <mode_name> <mc_port> <mc_version> <mods_host_path>")

    mc_server_dir = Path(__file__).resolve().parent
    mode_dir = (mc_server_dir / mode_name).resolve()
    profile_path = mode_dir / "profile.env.yml"
    if not profile_path.is_file():
        raise SystemExit(f"Profile not found: {profile_path}")

    server_dir = mode_dir / mc_port
    server_dir.mkdir(exist_ok=True)
    (server_dir / "data").mkdir(exist_ok=True)

    text = (mc_server_dir / "docker-compose.base.yml").read_text(encoding="utf-8")
    world_profile = profile_path.read_text(encoding="utf-8").rstrip("\r\n")
    replacements = {
        "__PROJECT_NAME__": f"bn_{mode_name}_{mc_port}",
        "__CONTAINER_NAME__": f"mc_server_{mode_name}_{mc_port}",
        "__MC_PORT__": mc_port,
        "__MC_SERVER_ID__": f"localhost:{mc_port}",
        "__MC_VERSION__": mc_version,
        "__MODS_HOST_PATH__": mods_host_path,
    }
    for key, value in replacements.items():
        text = text.replace(key, value)

    text, world_profile_replacements = re.subn(
        r'(?m)^[ \t]*BN_WORLD_PROFILE_ENV_PLACEHOLDER:\s*(?:""|\'\')\s*$',
        world_profile,
        text,
        count=1,
    )
    if world_profile_replacements != 1:
        raise SystemExit("WORLD_PROFILE placeholder not found in docker-compose.base.yml")

    output = server_dir / "docker-compose.yml"
    output.write_text(text, encoding="utf-8")
    return output


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) != 4:
        raise SystemExit("Usage: python generate_compose.py <mode_name> <mc_port> <mc_version> <mods_host_path>")

    mode_name = args[0]
    mc_port = args[1]
    mc_version = args[2]
    mods_host_path = args[3]
    print(main(mode_name, mc_port, mc_version, mods_host_path))

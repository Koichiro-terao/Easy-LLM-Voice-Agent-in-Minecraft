from __future__ import annotations

import os


DEFAULT_OPERATOR_MC_NAME = os.environ.get("MINEFLAYER_ADMIN_MC_NAME", "admin")


class MinecraftOperatorError(RuntimeError):
    """Raised when automatic operator grant cannot be completed."""


def _resolve_operator_mc_name(operator_mc_name: str | None) -> str:
    name = (operator_mc_name or DEFAULT_OPERATOR_MC_NAME).strip()
    if not name:
        raise MinecraftOperatorError("Operator bot name is empty.")
    return name

def ensure_operator_bot_connected(
    *,
    js_client,
    server_id: str,
    mc_host: str,
    mc_port: int,
    mc_name: str, 
    operator_mc_name: str | None = None,
) -> str:
    operator_mc_name = _resolve_operator_mc_name(operator_mc_name)
    print(f"start js_client.get_all_mc_names")
    player_list = js_client.get_all_mc_names(server_id=server_id, mc_name=mc_name).result()
    print(f"player_list : {player_list }")

    try:
        if operator_mc_name not in player_list:
            js_client.join(
                server_id=server_id,
                mc_name=operator_mc_name,
                mc_port=mc_port,
                mc_host=mc_host,
            )
    except Exception as exc:
        raise MinecraftOperatorError(
            f'Failed to join operator bot "{operator_mc_name}" to {mc_host}:{mc_port}. {exc}'
        ) from exc
    return operator_mc_name


def grant_operator_via_mineflayer(
    *,
    js_client,
    server_id: str,
    mc_name: str,
    operator_mc_name: str | None = None,
) -> str:
    operator_mc_name = _resolve_operator_mc_name(operator_mc_name)
    if mc_name == operator_mc_name:
        return operator_mc_name

    try:
        js_client.exec_mc(
            server_id=server_id,
            mc_name=operator_mc_name,
            commands=[f"/op {mc_name}"],
        )
    except Exception as exc:
        raise MinecraftOperatorError(
            f'Failed to grant operator to "{mc_name}" via operator bot "{operator_mc_name}". {exc}'
        ) from exc
    return operator_mc_name

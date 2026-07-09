from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

import numpy as np

from .belief import BeliefStateHistory, State, advance_state_one_observation
from .visibility import Vec3BoolMap, get_player_visibility, get_block_visibility


def build_filtered_initial_state(source_initial_state: dict) -> dict:
    return {
        "globalTick": int(source_initial_state.get("globalTick", -1)),
        "status": {},
        "blocks": {"__Vec3Map__": []},
        "containers": {"__Vec3Map__": []},
        "events": {},
    }


def filter_status(
    status: dict,
    see_agent_name: str,
    player_visibility_from_agent: dict[str, bool],
    filtered_state: State,
    current_tick: int,
) -> dict:
    filtered_status = {}
    for saw_agent_name, agent_status in status.items():
        if see_agent_name == saw_agent_name:
            filtered_status[saw_agent_name] = deepcopy(agent_status)
            continue
        if player_visibility_from_agent.get(saw_agent_name):
            visible_status = deepcopy(agent_status)
            visible_status.pop("hidden", None)
            current_visible = visible_status.get("visible")
            if current_visible is not None:
                visible_status["last_seen"] = {
                    "tick": current_tick,
                    "visible": deepcopy(current_visible),
                }
            filtered_status[saw_agent_name] = visible_status

    previous_tick = int(filtered_state.global_tick)
    for saw_agent_name, agent_status in filtered_state.status.items():
        if saw_agent_name == see_agent_name or saw_agent_name in filtered_status:
            continue

        current_visible = agent_status.get("visible")
        last_seen = agent_status.get("last_seen")
        if current_visible is None and last_seen is None:
            continue

        filtered_status[saw_agent_name] = {}
        if last_seen is not None:
            filtered_status[saw_agent_name]["last_seen"] = deepcopy(last_seen)
        elif current_visible is not None:
            filtered_status[saw_agent_name]["last_seen"] = {
                "tick": previous_tick if previous_tick >= 0 else current_tick,
                "visible": deepcopy(current_visible),
            }
    return filtered_status


def filter_events(
    events: list[dict],
    see_agent_name: str,
    player_visibility_from_agent: dict[str, bool],
    block_visibility_from_agent: Vec3BoolMap | None,
) -> list[dict]:
    filtered_events = []

    for event in events:
        filtered = event

        if "blockPos" in event and event["blockPos"] is not None and block_visibility_from_agent is not None:
            if not block_visibility_from_agent.has(np.array(event["blockPos"]["__Vec3__"])):
                continue

        if "agentName" in event and event["agentName"] is not None:
            if see_agent_name == event["agentName"]:
                pass
            elif player_visibility_from_agent.get(event["agentName"]):
                filtered = deepcopy(filtered)
                filtered.pop("hidden", None)
            else:
                continue

        filtered_events.append(deepcopy(filtered))

    return filtered_events


def _get_visible_blocks_to_update(
    filtered_state: State,
    source_state: State,
    block_visibility_from_agent: Vec3BoolMap | None,
) -> list[dict]:
    if block_visibility_from_agent is None:
        return []

    updated_blocks = []
    for pos in block_visibility_from_agent.get_all():
        pos_list = pos.tolist()
        if not source_state.blocks.has(pos_list):
            continue

        world_block = source_state.blocks.get(pos_list)
        if filtered_state.blocks.has(pos_list) and filtered_state.blocks.get(pos_list) == world_block:
            continue

        block = {
            "position": {"__Vec3__": pos_list},
            "name": world_block["name"],
        }
        if "stateId" in world_block:
            block["stateId"] = world_block["stateId"]
        if "properties" in world_block:
            block["properties"] = deepcopy(world_block["properties"])
        updated_blocks.append(block)

    return updated_blocks


def obs_agent_perspective(
    obs: dict,
    observer_name: str,
    source_state: State,
    filtered_state: State,
    env_box,
    offset,
) -> dict:
    objective = obs["objective"]

    filtered_obs = deepcopy(obs)

    player_relative_positions = {}
    for agent_name, agent_status in source_state.status.items():
        pos = agent_status.get("visible", {}).get("position", {}).get("__Vec3__")
        player_relative_positions[agent_name] = deepcopy(pos) if pos is not None else None

    player_vis = get_player_visibility(
        env_box,
        offset,
        player_relative_positions,
        source_state.blocks,
        observer_name,
    )

    block_vis = get_block_visibility(
        env_box,
        offset,
        player_relative_positions,
        source_state.blocks,
        observer_name,
    )

    filtered_obs = {
        "globalTick": obs["globalTick"],
        "objective": {
            "status": filter_status(
                objective["status"],
                observer_name,
                player_vis,
                filtered_state,
                int(obs["globalTick"]),
            ),
            "events": filter_events(
                objective["events"],
                observer_name,
                player_vis,
                block_vis,
            ),
            "blocksToUpdate": _get_visible_blocks_to_update(
                filtered_state,
                source_state,
                block_vis,
            ),
        },
    }

    return filtered_obs

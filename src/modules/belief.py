from __future__ import annotations

import argparse
import bisect
import json
import math
import re
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from typing import Optional
from collections import defaultdict

from jinja2 import Environment, StrictUndefined

EXCLUDE_NAMES: list[str] = ["Camera", "admin"]

current_loader = ContextVar("current_loader")


def _vec_to_key(vec: Iterable[int | float]) -> tuple[int, int, int]:
    x, y, z = vec
    return int(x), int(y), int(z)


@dataclass(frozen=True)
class ParsedObservation:
    type: str
    data: dict
    server_id: str
    tick: int | None


@dataclass(frozen=True)
class PlayersTickObservation:
    server_id: str
    tick: int | None
    players: dict


@dataclass(frozen=True)
class IncrementalEventObservation:
    server_id: str
    event_type: str
    mc_name: str | None
    payload: dict


@dataclass(frozen=True)
class InterpretedObservation:
    players_ticks: list[PlayersTickObservation]
    incremental_events: list[IncrementalEventObservation]


class ObservationParser:
    def parse(self, raw_obs: dict, fallback_server_id: str) -> ParsedObservation:
        return ParsedObservation(
            type=raw_obs["type"],
            data=raw_obs["data"],
            server_id=raw_obs.get("server_id", fallback_server_id),
            tick=raw_obs.get("tick"),
        )


class MinecraftObservationInterpreter:
    def _normalize_player_data(self, player_data: dict) -> dict:
        visible = dict(player_data["visible"])
        hidden = dict(player_data.get("hidden", {}))

        visible["position"] = {"__Vec3__": list(player_data["visible"]["position"])}
        visible["velocity"] = {"__Vec3__": list(player_data["visible"]["velocity"])}
        visible["pitch"] = -float(player_data["visible"]["pitch"]) / 180.0 * math.pi
        visible["yaw"] = (-float(player_data["visible"]["yaw"]) + 180.0) / 180.0 * math.pi

        equipment = player_data["visible"].get("equipment", {})
        if isinstance(equipment, list):
            visible["equipment"] = equipment
        else:
            visible["equipment"] = [
                equipment.get("head"),
                equipment.get("chest"),
                equipment.get("legs"),
                equipment.get("feet"),
                equipment.get("mainhand"),
                equipment.get("offhand"),
            ]

        inventory = hidden.get("inventory", {})
        if isinstance(inventory, list):
            inventory_dict: dict[str, int] = {}
            for slot in inventory:
                item_name = slot["item"]
                inventory_dict[item_name] = inventory_dict.get(item_name, 0) + int(slot["count"])
            hidden["inventory"] = inventory_dict
        elif isinstance(inventory, dict):
            hidden["inventory"] = dict(inventory)
        else:
            hidden["inventory"] = {}

        return {
            "name": player_data["name"],
            "visible": visible,
            "hidden": hidden,
        }

    def interpret(self, parsed_obs: ParsedObservation) -> InterpretedObservation:
        players_ticks: list[PlayersTickObservation] = []
        incremental_events: list[IncrementalEventObservation] = []

        if parsed_obs.type == "players_tick":
            players = {}
            for _, player_data in parsed_obs.data["players"].items():
                players[player_data["name"]] = self._normalize_player_data(player_data)
            players_ticks.append(
                PlayersTickObservation(
                    server_id=parsed_obs.server_id,
                    tick=parsed_obs.tick,
                    players=players,
                )
            )
            return InterpretedObservation(players_ticks, incremental_events)

        event_type = parsed_obs.type
        data = parsed_obs.data
        server_id = parsed_obs.server_id

        if event_type == "container_close" and (
            "containerBlock" not in data or data["containerBlock"] != "chest"
        ):
            return InterpretedObservation(players_ticks, incremental_events)

        mc_name = None
        if event_type in ["swing_hand", "craft_item", "container_close"]:
            mc_name = data["playerName"]
        elif event_type in ["player_move_start", "player_move_end"]:
            mc_name = data["player"]["name"]
        elif event_type == "chat":
            tmp_mc_name = data["player"]["name"]
            msg = data["message"]
            if tmp_mc_name == "admin":
                match = re.match(r"^([^:]+) said: (.*)$", msg)
                if not match:
                    raise ValueError(f"Invalid admin chat message: {msg}")
                mc_name = match.group(1)
                data = dict(data)
                data["message"] = match.group(2)
            else:
                mc_name = tmp_mc_name
        elif "playerName" in data:
            mc_name = data["playerName"]

        incremental_events.append(
            IncrementalEventObservation(
                server_id=server_id,
                event_type=event_type,
                mc_name=mc_name,
                payload=data,
            )
        )
        return InterpretedObservation(players_ticks, incremental_events)


class Vec3Map:
    def __init__(self, vec3map: dict | list):
        if isinstance(vec3map, dict) and "__Vec3Map__" in vec3map:
            vec3map = vec3map["__Vec3Map__"]

        self.data: dict[tuple[int, int, int], dict] = {}
        for entry in vec3map:
            value = dict(entry)
            pos = value.pop("position")
            self.data[(int(pos[0]), int(pos[1]), int(pos[2]))] = value

    def set(self, vec: Iterable[int | float], value: dict) -> None:
        self.data[_vec_to_key(vec)] = value

    def get(self, vec: Iterable[int | float]) -> dict:
        return self.data[_vec_to_key(vec)]

    def has(self, vec: Iterable[int | float]) -> bool:
        return _vec_to_key(vec) in self.data

    def delete(self, vec: Iterable[int | float]) -> None:
        del self.data[_vec_to_key(vec)]

    def to_dict(self) -> dict:
        data = []
        for (x, y, z), value in self.data.items():
            row = {"position": [x, y, z]}
            row.update(value)
            data.append(row)
        return {"__Vec3Map__": data}

    def keys(self) -> list[list[int]]:
        return [[x, y, z] for (x, y, z) in self.data.keys()]


class State:
    def __init__(self, state_dict: dict):
        self.global_tick = int(state_dict.get("globalTick", -1))
        self.status = deepcopy(state_dict["status"])
        self.blocks = Vec3Map(deepcopy(state_dict["blocks"]))
        self.containers = Vec3Map(deepcopy(state_dict["containers"]))
        self.events = deepcopy(state_dict["events"])

    def update(self, obs: dict) -> None:
        global_tick = int(obs["globalTick"])
        status = obs["objective"]["status"]
        events = obs["objective"]["events"]
        blocks_to_update = obs["objective"]["blocksToUpdate"]

        has_inventory_info = {}
        for agent_name, agent_status in status.items():
            has_inventory_info[agent_name] = (
                "hidden" in agent_status and "inventory" in agent_status["hidden"]
            )

        self._update_status_state(status, events, has_inventory_info)

        for block in blocks_to_update:
            value = {"name": block["name"]}
            if "stateId" in block:
                value["stateId"] = block["stateId"]
            if "properties" in block and block["properties"] is not None:
                value["properties"] = block["properties"]
            self.blocks.set(block["position"]["__Vec3__"], value)

        self._update_container_state(events, blocks_to_update)

        if events:
            self.events[global_tick] = deepcopy(events)

        self.global_tick = global_tick

    def _update_status_state(self, status: dict, events: list, has_inventory_info: dict) -> None:
        for agent_name, agent_status in status.items():
            self.status.setdefault(agent_name, {"visible": {}, "hidden": {}})
            self.status[agent_name]["visible"] = deepcopy(agent_status["visible"])
            if "hidden" in agent_status:
                self.status[agent_name]["hidden"] = deepcopy(agent_status["hidden"])
            self.status[agent_name].setdefault("hidden", {})
            self.status[agent_name]["hidden"].setdefault("inventory", {})

        def _add(agent_name: str, name: str, count: int) -> None:
            if has_inventory_info.get(agent_name):
                return
            inv = self.status.setdefault(agent_name, {"visible": {}, "hidden": {}})["hidden"].setdefault(
                "inventory", {}
            )
            inv[name] = inv.get(name, 0) + count

        def _remove(agent_name: str, name: str, count: int) -> None:
            if has_inventory_info.get(agent_name):
                return
            inv = self.status.setdefault(agent_name, {"visible": {}, "hidden": {}})["hidden"].setdefault(
                "inventory", {}
            )
            if name not in inv:
                return
            inv[name] -= count
            if inv[name] <= 0:
                del inv[name]

        for event in events:
            event_name = event.get("eventName")
            if event_name == "mineBlock":
                _add(event["agentName"], event["visible"]["blockName"], 1)
            elif event_name == "craftItem":
                _add(event["agentName"], event["visible"]["itemName"], event["visible"]["producedCount"])
                for name, count in event["visible"].get("consumedItems", {}).items():
                    _remove(event["agentName"], name, count)
            elif event_name == "smeltItem":
                _add(
                    event["agentName"],
                    event["visible"]["producedItemName"],
                    event["visible"]["producedCount"],
                )
                for name, count in event["visible"].get("consumedItems", {}).items():
                    _remove(event["agentName"], name, count)
            elif event_name == "getItemFromChest":
                for name, count in event["visible"].get("gotItems", {}).items():
                    _add(event["agentName"], name, count)
            elif event_name == "depositItemIntoChest":
                for name, count in event["visible"].get("depositedItems", {}).items():
                    _remove(event["agentName"], name, count)
            elif event_name == "giveItemToOther":
                _remove(event["agentName"], event["visible"]["itemName"], event["visible"]["count"])
                _add(
                    event["visible"]["otherAgentName"],
                    event["visible"]["itemName"],
                    event["visible"]["count"],
                )
            elif event_name == "receiveItemFromOther":
                _add(event["agentName"], event["visible"]["itemName"], event["visible"]["count"])
                _remove(
                    event["visible"]["otherAgentName"],
                    event["visible"]["itemName"],
                    event["visible"]["count"],
                )

    def _update_container_state(self, events: list, updated_blocks: list) -> None:
        for block in updated_blocks:
            pos = block["position"]["__Vec3__"]
            if block.get("name") == "chest" and not self.containers.has(pos):
                self.containers.set(pos, {})

        for pos in list(self.containers.keys()):
            if self.blocks.has(pos) and self.blocks.get(pos)["name"] != "chest":
                self.containers.delete(pos)

        for event in events:
            if event.get("eventName") not in ["getItemFromChest", "depositItemIntoChest"]:
                continue

            chest_pos = event.get("visible", {}).get("chestPos")
            if chest_pos is None:
                raise ValueError(f"chestPos is not defined. event={json.dumps(event, ensure_ascii=False)}")

            pos = chest_pos["__Vec3__"]
            if event.get("hidden", {}).get("chestItems") is not None:
                self.containers.set(pos, deepcopy(event["hidden"]["chestItems"]))
                continue

            chest_items = {}
            if self.containers.has(pos):
                chest_items = deepcopy(self.containers.get(pos))

            if event["eventName"] == "getItemFromChest":
                for item_name, count in event.get("visible", {}).get("gotItems", {}).items():
                    if item_name not in chest_items:
                        continue
                    if count >= chest_items[item_name]:
                        del chest_items[item_name]
                    else:
                        chest_items[item_name] -= count
            elif event["eventName"] == "depositItemIntoChest":
                for item_name, count in event.get("visible", {}).get("depositedItems", {}).items():
                    chest_items[item_name] = chest_items.get(item_name, 0) + count
            else:
                raise ValueError(f'Invalid event name "{event["eventName"]}"')

            self.containers.set(pos, chest_items)


class ObservationHistory:
    def __init__(self) -> None:
        self.objective_list: list[dict] = []
        self._ticks: list[int] = []

    def add(self, obs: dict) -> None:
        objective = {
            "globalTick": obs["globalTick"],
            "status": deepcopy(obs["objective"]["status"]),
            "events": deepcopy(obs["objective"]["events"]),
            "blocksToUpdate": deepcopy(obs["objective"]["blocksToUpdate"]),
        }
        if "visibility" in obs["objective"]:
            objective["visibility"] = deepcopy(obs["objective"]["visibility"])
        self.objective_list.append(objective)
        self._ticks.append(int(obs["globalTick"]))

    @property
    def latest_tick(self) -> int:
        if not self._ticks:
            return -1
        return self._ticks[-1]

    def _get_idx_of(self, tick: int, strict: bool = False) -> int:
        idx = bisect.bisect_left(self._ticks, tick)
        if strict and (idx >= len(self._ticks) or self._ticks[idx] != tick):
            raise KeyError(f"tick {tick} not found")
        return idx

    def get_objective(self, tick: int, strict: bool = True) -> dict:
        idx = self._get_idx_of(tick, strict=strict)
        return deepcopy(self.objective_list[idx])

    def snapshot(self) -> tuple[list[dict], list[int]]:
        return deepcopy(self.objective_list), deepcopy(self._ticks)


class WorldObservationLoader:
    def __init__(self, *, branch: str, state_dict: dict, objective_list: list[dict], ticks: list[int]):
        self.branch = branch
        self.state_dict = {branch: deepcopy(state_dict)}
        self.objective_list = {branch: deepcopy(objective_list)}
        self.tick_dict = {branch: ticks[-1] if ticks else -1}
        self.tick_list = {branch: deepcopy(ticks)}

    def _assert_branch(self, branch_str: str) -> None:
        if branch_str != self.branch:
            raise KeyError(f'Only "{self.branch}" is available. got="{branch_str}"')

    def get_latest_state(self, branch_str: str) -> tuple[dict, int]:
        self._assert_branch(branch_str)
        state = deepcopy(self.state_dict[branch_str])
        return state, int(state["globalTick"])

    def get_latest_history(self, branch_str: str) -> tuple[dict, int]:
        self._assert_branch(branch_str)
        tick = self.tick_dict[branch_str]
        if tick < 0:
            raise ValueError("No observation history has been recorded yet.")
        objective = deepcopy(self.objective_list[branch_str][-1])
        return objective, int(objective["globalTick"])

    def get_history(self, branch_str: str, tick: int) -> tuple[dict, int]:
        self._assert_branch(branch_str)
        ticks = self.tick_list[branch_str]
        idx = bisect.bisect_left(ticks, tick)
        if idx >= len(ticks) or ticks[idx] != tick:
            raise KeyError(f"tick {tick} not found")
        objective = deepcopy(self.objective_list[branch_str][idx])
        return objective, int(tick)

    def get_previous_block_vis(self, branch_str: str, now_tick: int) -> tuple[None, None]:
        del now_tick
        self._assert_branch(branch_str)
        raise NotImplementedError(
            "This standalone world loader does not record child-belief block visibility."
        )


class StandaloneWorldObservationRuntime:
    def __init__(
        self,
        *,
        env_box: list[list[int]],
        initial_state: dict,
        offset: Optional[list[int]] = None,
        branch: str = "world[default]",
        server_id: str = "world",
    ) -> None:
        self.env_box = deepcopy(env_box)
        self.offset = list(offset or [0, 0, 0])
        self.branch = branch
        self.server_id = server_id

        normalized_state = deepcopy(initial_state)
        normalized_state.setdefault("globalTick", -1)
        normalized_state.setdefault("status", {})
        normalized_state.setdefault("blocks", {"__Vec3Map__": []})
        normalized_state.setdefault("containers", {"__Vec3Map__": []})
        normalized_state.setdefault("events", {})

        self.state = State(normalized_state)
        self.obs_history = ObservationHistory()
        self.parser = ObservationParser()
        self.interpreter = MinecraftObservationInterpreter()

        self.pending_events: list[dict] = []
        self.raw_messages: list[dict] = []

        self.received_latest_tick = int(normalized_state["globalTick"])
        self.received_latest_server_tick: int | None = None

    @classmethod
    def from_world_config(
        cls,
        world_config: dict,
        *,
        offset: Optional[list[int]] = None,
        branch: str = "world[default]",
        server_id: str = "world",
    ) -> "StandaloneWorldObservationRuntime":
        return cls(
            env_box=world_config["envBox"],
            initial_state=world_config["state"],
            offset=offset,
            branch=branch,
            server_id=server_id,
        )

    def create_current_observation_loader(self) -> WorldObservationLoader:
        state_dict = self._to_legacy_format()
        objective_list, ticks = self.obs_history.snapshot()
        return WorldObservationLoader(
            branch=self.branch,
            state_dict=state_dict,
            objective_list=objective_list,
            ticks=ticks,
        )

    def load_from_template(
        self,
        loader: WorldObservationLoader,
        template: str,
        variables: Optional[dict] = None,
        extra_filters: Optional[list] = None,
        allow_filter_override: bool = False,
    ) -> str:
        variables = {} if variables is None else dict(variables)
        variables["branch"] = self.branch
        return load_from_template(
            loader,
            template,
            variables=variables,
            extra_filters=extra_filters,
            allow_filter_override=allow_filter_override,
        )

    def add_raw_observations(self, raw_messages: Iterable[dict], *, finalize: bool = True) -> list[dict]:
        emitted_obs = []
        for raw_message in raw_messages:
            obs = self.add_raw_observation(raw_message)
            if obs is not None:
                emitted_obs.append(obs)
        if finalize:
            flushed = self.finalize_pending_events()
            if flushed is not None:
                emitted_obs.append(flushed)
        return emitted_obs

    def add_raw_observation(self, raw_message: dict, *, fallback_server_id: Optional[str] = None) -> Optional[dict]:
        fallback_server_id = fallback_server_id or self.server_id
        self.raw_messages.append(deepcopy(raw_message))

        if "objective" in raw_message and "visibility" in raw_message:
            return self.add_prebuilt_observation(raw_message, server_tick=raw_message.get("serverTick"))

        if "eventName" in raw_message and "visible" in raw_message:
            self.pending_events.append(deepcopy(raw_message))
            return None

        if raw_message.get("type") == "event_batch":
            last_obs = None
            batch_server_id = raw_message.get("server_id", fallback_server_id)
            for item in raw_message.get("items", []):
                if batch_server_id and "server_id" not in item:
                    item = dict(item)
                    item["server_id"] = batch_server_id
                candidate = self.add_raw_observation(item, fallback_server_id=batch_server_id)
                if candidate is not None:
                    last_obs = candidate
            return last_obs

        parsed = self.parser.parse(raw_message, fallback_server_id)
        interpreted = self.interpreter.interpret(parsed)

        for event_obs in interpreted.incremental_events:
            if event_obs.event_type == "container_close":
                self.pending_events.extend(self._dispatch_container_close(event_obs.payload, event_obs.mc_name))
            else:
                event = self._build_world_event(event_obs)
                if event is not None:
                    self.pending_events.append(event)

        latest_obs = None
        for tick_obs in interpreted.players_ticks:
            latest_obs = self._dispatch_players_tick(tick_obs)

        if latest_obs is None:
            return None
        return _attach_event_summary(latest_obs)

    def add_prebuilt_observation(
        self,
        obs: dict,
        *,
        server_tick: Optional[int] = None,
        resume_from_pause: bool = False,
    ) -> dict:
        normalized_obs = deepcopy(obs)
        if "globalTick" not in normalized_obs:
            normalized_obs["globalTick"] = self._next_global_tick(server_tick, resume_from_pause=resume_from_pause)
        self.received_latest_tick = int(normalized_obs["globalTick"])
        if server_tick is not None:
            self.received_latest_server_tick = int(server_tick)
        self.state.update(normalized_obs)
        self.obs_history.add(normalized_obs)
        return _attach_event_summary(normalized_obs)

    def finalize_pending_events(self, *, server_tick: Optional[int] = None) -> Optional[dict]:
        if not self.pending_events:
            return None

        status = deepcopy(self.state.status)
        if server_tick is None and self.received_latest_server_tick is not None:
            server_tick = self.received_latest_server_tick + 1

        obs = {
            "globalTick": self._next_global_tick(server_tick, resume_from_pause=False),
            "objective": {
                "status": status,
                "events": deepcopy(self.pending_events),
                "blocksToUpdate": self._drain_updated_blocks_from_events(self.pending_events),
            },
            "visibility": {},
        }
        self.pending_events = []
        self.state.update(obs)
        self.obs_history.add(obs)
        return _attach_event_summary(obs)

    def _next_global_tick(self, server_tick: Optional[int], *, resume_from_pause: bool = False) -> int:
        if server_tick is None:
            self.received_latest_tick += 1
            return self.received_latest_tick

        server_tick = int(server_tick)
        if resume_from_pause:
            self.received_latest_server_tick = None

        if self.received_latest_server_tick is None:
            tick_diff = 1
        else:
            tick_diff = server_tick - self.received_latest_server_tick

        if tick_diff <= 0:
            raise ValueError(
                f"server_tick={server_tick}, received_latest_server_tick={self.received_latest_server_tick}"
            )

        self.received_latest_server_tick = server_tick
        self.received_latest_tick += tick_diff
        return self.received_latest_tick

    def _dispatch_players_tick(self, tick_obs: PlayersTickObservation) -> dict:
        status = {}
        for _, player_data in tick_obs.players.items():
            agent_name = player_data["name"]
            rel_player_data = {
                "name": player_data["name"],
                "visible": deepcopy(player_data["visible"]),
                "hidden": deepcopy(player_data.get("hidden", {})),
            }
            abs_position = player_data["visible"]["position"]["__Vec3__"]
            rel_position = [abs_position[i] - self.offset[i] for i in range(3)]
            rel_player_data["visible"]["position"] = {"__Vec3__": rel_position}
            status[agent_name] = rel_player_data

        event_list = deepcopy(self.pending_events)
        self.pending_events = []

        obs = {
            "globalTick": self._next_global_tick(tick_obs.tick, resume_from_pause=False),
            "objective": {
                "status": status,
                "events": event_list,
                "blocksToUpdate": self._drain_updated_blocks_from_events(event_list),
            },
            "visibility": {},
        }
        self.state.update(obs)
        self.obs_history.add(obs)
        return obs

    def _abs_to_rel_vec3(self, vec: Any, *, data_type=int) -> dict:
        if isinstance(vec, str):
            pos = [data_type(part) for part in vec.split(",")]
        else:
            pos = [data_type(v) for v in vec]
        if data_type is not str:
            for i in range(3):
                pos[i] -= self.offset[i]
        return {"__Vec3__": pos}

    def _drain_updated_blocks_from_events(self, event_list: list[dict]) -> list[dict]:
        updated_blocks = []
        for event in event_list:
            if event["eventName"] != "blockUpdate":
                continue

            block = {
                "position": deepcopy(event["blockPos"]),
                "name": event["visible"]["name"],
            }
            if "stateId" in event["visible"]:
                block["stateId"] = event["visible"]["stateId"]
            if "properties" in event["visible"]:
                props = event["visible"]["properties"]
                block["properties"] = {k.lower(): str(v).lower() for k, v in props.items()}

            updated_blocks.append(block)
        return updated_blocks

    def _build_world_event(self, event_obs: IncrementalEventObservation) -> Optional[dict]:
        data = event_obs.payload
        agent_name = event_obs.mc_name

        if event_obs.event_type == "block_update":
            block = {
                "name": data["new"]["blockName"],
                "stateId": data["new"]["stateId"],
            }
            if data["new"].get("properties"):
                block["properties"] = data["new"]["properties"]
            return {
                "eventName": "blockUpdate",
                "blockPos": self._abs_to_rel_vec3(data["pos"]),
                "visible": block,
                "hidden": {},
            }

        if event_obs.event_type == "swing_hand":
            return {
                "eventName": "swingHand",
                "agentName": agent_name,
                "visible": {},
                "hidden": {},
            }

        if event_obs.event_type == "craft_item":
            visible = {
                "itemName": data["item"],
                "producedCount": data.get("count", 1),
                "consumedItems": {name: count for name, count in data.get("consumed", {}).items()},
            }

            if data.get("tablePos") is not None:
                visible["craftingTablePos"] = self._abs_to_rel_vec3(data["tablePos"])

            return {
                "eventName": "craftItem",
                "agentName": agent_name,
                "visible": visible,
                "hidden": {},
            }

        if event_obs.event_type == "player_move_start":
            return {
                "eventName": "moveStart",
                "agentName": agent_name,
                "visible": {
                    "startPos": self._abs_to_rel_vec3(data["from"], data_type=float),
                    "goalPos": {"__Vec3__": ["unknown", "unknown", "unknown"]},
                },
                "hidden": None,
            }

        if event_obs.event_type == "player_move_end":
            return {
                "eventName": "moveEnd",
                "agentName": agent_name,
                "visible": {
                    "startPos": self._abs_to_rel_vec3(data["from"], data_type=float),
                    "goalPos": self._abs_to_rel_vec3(data["to"], data_type=float),
                },
                "hidden": None,
            }

        if event_obs.event_type == "chat":
            return {
                "eventName": "chat",
                "visible": {
                    "agentName": agent_name,
                    "msg": data["message"],
                },
                "hidden": None,
            }

        return None

    def _dispatch_container_close(self, data: dict, agent_name: Optional[str]) -> list[dict]:
        got_items = {}
        deposited_items = {}
        for name, count in data["delta"].items():
            if count < 0:
                got_items[name] = -count
            elif count > 0:
                deposited_items[name] = count

        event_list = []
        hidden_items = {key: value for key, value in data["finalCounts"].items()}
        chest_pos = self._abs_to_rel_vec3(data["pos"])

        if got_items:
            event_list.append(
                {
                    "eventName": "getItemFromChest",
                    "agentName": agent_name,
                    "visible": {
                        "chestPos": chest_pos,
                        "gotItems": got_items,
                    },
                    "hidden": {"chestItems": hidden_items},
                }
            )
        if deposited_items:
            event_list.append(
                {
                    "eventName": "depositItemIntoChest",
                    "agentName": agent_name,
                    "visible": {
                        "chestPos": chest_pos,
                        "depositedItems": deposited_items,
                    },
                    "hidden": {"chestItems": hidden_items},
                }
            )
        if got_items or deposited_items:
            event_list.append(
                {
                    "eventName": "useChest",
                    "agentName": agent_name,
                    "visible": {
                        "chestPos": chest_pos,
                        "gotItems": got_items,
                        "depositedItems": deposited_items,
                    },
                    "hidden": {"chestItems": hidden_items},
                }
            )
        return event_list

    def _to_legacy_format(self) -> dict:
        return {
            "globalTick": self.state.global_tick,
            "status": deepcopy(self.state.status),
            "blocks": self.state.blocks.to_dict(),
            "containers": self.state.containers.to_dict(),
            "events": deepcopy(self.state.events),
        }


def _iter_events(latest_state: dict) -> list[tuple[int, dict]]:
    rows = []
    for tick, events_at_tick in latest_state["events"].items():
        for event in events_at_tick:
            rows.append((int(tick), event))
    rows.sort(key=lambda row: row[0])
    return rows


def _get_event_agent_name(event: dict) -> str | None:
    if "agentName" in event:
        return event["agentName"]
    return event.get("visible", {}).get("agentName")


def _summarize_events(obs: dict) -> dict:
    summary = {
        "has_any_event": False,
        "event_names": [],
        "events_by_type": {},
    }

    for event in obs["objective"]["events"]:
        event_name = event.get("eventName")
        if not event_name:
            continue

        summary["has_any_event"] = True

        if event_name not in summary["event_names"]:
            summary["event_names"].append(event_name)

        agent_name = _get_event_agent_name(event)
        entry = summary["events_by_type"].setdefault(
            event_name,
            {
                "occurred": False,
                "agents": [],
                "messages": [],
            },
        )
        entry["occurred"] = True

        if agent_name is not None and agent_name not in entry["agents"]:
            entry["agents"].append(agent_name)

        if event_name == "chat":
            msg = event.get("visible", {}).get("msg")
            if msg is not None:
                entry["messages"].append(
                    {
                        "agent": agent_name,
                        "msg": msg,
                    }
                )

    return summary

def _attach_event_summary(obs: dict) -> dict:
    obs["event_summary"] = _summarize_events(obs)
    return obs

def get_event_result(obs: dict, event_name: str) -> dict:
    return obs.get("event_summary", {}).get(
        "events_by_type",
        {},
    ).get(
        event_name,
        {
            "occurred": False,
            "agents": [],
            "messages": [],
        },
    )


def get_loader():
    return current_loader.get()

def get_main_agent_name(branch_str: str) -> str:
    main_agent_name = branch_str.split(".")[-1].split("[")[0]
    if main_agent_name == "world":
        raise ValueError("Cannot infer main agent name from world branch.")
    return main_agent_name


def _resolve_agent_name(branch_str: str, agent_name: Optional[str]) -> str:
    if agent_name is not None:
        return agent_name

    loader = get_loader()
    try:
        return get_main_agent_name(branch_str)
    except Exception:
        if len(loader.agent_list) == 1:
            return loader.agent_list[0]
        raise ValueError("agent_name is required when using the world branch with multiple agents.")


def position(branch_str: str, agent_name: Optional[str] = None, ignore_last_seen: bool = True):
    del ignore_last_seen
    loader = get_loader()
    latest_state, _ = loader.get_latest_state(branch_str)
    agent_name = _resolve_agent_name(branch_str, agent_name)
    try:
        return latest_state["status"][agent_name]["visible"]["position"]["__Vec3__"]
    except Exception:
        return "No data"


def thought(branch_str: str) -> str:
    loader = get_loader()
    latest_state, _ = loader.get_latest_state(branch_str)
    lines = []
    for tick, event in _iter_events(latest_state):
        if event.get("eventName") != "think":
            continue
        if "hidden" not in event:
            continue
        lines.append(f't={tick}   {event["agentName"]} thought "{event["hidden"]["msg"]}"')
    return "\n".join(lines) if lines else "No thought"


def _decode_escaped_unicode_text(value: str) -> str:
    if "\\u" not in value and "\\n" not in value and "\\t" not in value:
        return value
    try:
        return json.loads(f'"{value}"')
    except Exception:
        return value


def chat_log(branch_str: str) -> str:
    loader = get_loader()
    latest_state, _ = loader.get_latest_state(branch_str)
    lines = []
    for tick, event in _iter_events(latest_state):
        if event.get("eventName") != "chat":
            continue
        msg = _decode_escaped_unicode_text(event["visible"]["msg"])
        lines.append(f't={tick}   {event["visible"]["agentName"]} said "{msg}"')
    return "\n".join(lines) if lines else "No chats"


def inventory(branch_str: str, agent_name: Optional[str] = None) -> str:
    loader = get_loader()
    latest_state, _ = loader.get_latest_state(branch_str)
    agent_name = _resolve_agent_name(branch_str, agent_name)
    try:
        inv = latest_state["status"][agent_name]["hidden"]["inventory"]
        return str(inv) if inv else "Empty"
    except Exception:
        return "No data"


def equipment(branch_str: str, agent_name: Optional[str] = None) -> str:
    loader = get_loader()
    latest_state, _ = loader.get_latest_state(branch_str)
    agent_name = _resolve_agent_name(branch_str, agent_name)
    try:
        eq = latest_state["status"][agent_name]["visible"]["equipment"]
        return str(eq) if eq else "Empty"
    except Exception:
        return "No data"


def helditem(branch_str: str, agent_name: Optional[str] = None) -> str:
    loader = get_loader()
    latest_state, _ = loader.get_latest_state(branch_str)
    agent_name = _resolve_agent_name(branch_str, agent_name)
    try:
        return str(latest_state["status"][agent_name]["visible"]["equipment"][4])
    except Exception:
        return "No data"


def chests(branch_str: str) -> str:
    loader = get_loader()
    latest_state, _ = loader.get_latest_state(branch_str)
    lines = []
    for row in latest_state["containers"]["__Vec3Map__"]:
        pos = tuple(row["position"])
        items = {key: value for key, value in row.items() if key != "position"}
        lines.append(f"{pos}: {items}")
    return "\n".join(lines) if lines else "No chest data"


def other_players(branch_str: str, agent_name: Optional[str] = None) -> str:
    loader = get_loader()
    latest_state, _ = loader.get_latest_state(branch_str)
    main_agent_name = _resolve_agent_name(branch_str, agent_name)
    info = {}
    for agent_name in latest_state["status"]:
        if agent_name == main_agent_name:
            continue
        if any(agent_name.startswith(prefix) for prefix in EXCLUDE_NAMES): # Admin等関係のないエージェント情報を排除するため：後程修正
            continue
        info[agent_name] = {
            "position": position(branch_str, agent_name=agent_name),
            "helditem": helditem(branch_str, agent_name=agent_name),
            "inventory": inventory(branch_str, agent_name=agent_name),
        }
    return json.dumps(info, ensure_ascii=False, indent=2)


def blocks(branch_str: str, block_names: Optional[list[str]] = None) -> str:
    loader = get_loader()
    latest_state, _ = loader.get_latest_state(branch_str)
    all_blocks = latest_state["blocks"]["__Vec3Map__"]

    if isinstance(block_names, str):
        block_names = [block_names]
    if block_names is None:
        block_names = sorted({block["name"] for block in all_blocks})

    sections = []
    for name in block_names:
        positions = [tuple(block["position"]) for block in all_blocks if block["name"] == name]
        body = "\n".join(map(str, positions)) if positions else "Not observed"
        sections.append(f"{name}:\n{body}")
    return "\n".join(sections)


def block_property(branch_str: str, block_name: str) -> str:
    loader = get_loader()
    latest_state, _ = loader.get_latest_state(branch_str)
    lines = []
    for block in latest_state["blocks"]["__Vec3Map__"]:
        if block["name"] != block_name:
            continue
        props = block.get("properties", {})
        lines.append(f'{block["position"]} : {json.dumps(props, ensure_ascii=False)}')
    return "\n".join(lines) if lines else "No data"


def _event_to_description(event: dict) -> str:
    event_name = event.get("eventName")
    if event_name == "depositItemIntoChest":
        chest_pos = event["visible"]["chestPos"]["__Vec3__"]
        return f'chest:{tuple(chest_pos)} & items:{event["visible"]["depositedItems"]}'
    if event_name == "getItemFromChest":
        chest_pos = event["visible"]["chestPos"]["__Vec3__"]
        return f'chest:{tuple(chest_pos)} & items:{event["visible"]["gotItems"]}'
    if event_name == "chat":
        return f'said "{event["visible"]["msg"]}"'
    if event_name == "moveStart":
        start_pos = event["visible"]["startPos"]["__Vec3__"]
        goal_pos = event["visible"]["goalPos"]["__Vec3__"]
        return f"From {tuple(start_pos)} To {tuple(goal_pos)}"
    if event_name == "moveEnd":
        start_pos = event["visible"]["startPos"]["__Vec3__"]
        goal_pos = event["visible"]["goalPos"]["__Vec3__"]
        return f"From {tuple(start_pos)} To {tuple(goal_pos)}"
    if event_name == "craftItem":
        return f'Crafted {event["visible"]["producedCount"]} {event["visible"]["itemName"]}(s)'
    if event_name == "mineBlock":
        pos = event["visible"]["pos"]["__Vec3__"]
        return f'Mined 1 {event["visible"]["blockName"]} at {tuple(pos)}'
    if event_name == "smeltItem":
        return (
            f'Smelted {event["visible"]["materialName"]} into '
            f'{event["visible"]["producedCount"]} {event["visible"]["producedItemName"]}(s)'
        )
    if event_name == "think":
        return f'thought that "{event["hidden"]["msg"]}"'
    if event_name == "useLever":
        lever_pos = event["visible"]["leverPos"]["__Vec3__"]
        return f'{event["visible"]["type"]} the lever at {tuple(lever_pos)}'
    if event_name == "giveItemToOther":
        return (
            f'gave {event["visible"]["count"]} {event["visible"]["itemName"]} '
            f'to {event["visible"]["otherAgentName"]}'
        )
    if event_name == "receiveItemFromOther":
        return (
            f'received {event["visible"]["count"]} {event["visible"]["itemName"]} '
            f'from {event["visible"]["otherAgentName"]}'
        )
    visible = event.get("visible")
    if visible is not None:
        return json.dumps(visible, ensure_ascii=False)
    return ""


def events(branch_str: str) -> str:
    loader = get_loader()
    latest_state, _ = loader.get_latest_state(branch_str)
    lines = ["time;action;agent_name;description"]
    for tick, event in _iter_events(latest_state):
        event_name = event.get("eventName")
        if event_name in ["think", "blockUpdate", "useChest", "swingHand"]:
            continue
        if event_name == "chat":
            agent_name = event["visible"]["agentName"]
        else:
            agent_name = event.get("agentName")
        if any(agent_name.startswith(prefix) for prefix in EXCLUDE_NAMES): # Admin等関係のないエージェント情報を排除するため：後程修正
            continue
        lines.append(f"{tick};{event_name};{agent_name};{_event_to_description(event)}")
    return "\n".join(lines)


def latest_state_json(branch_str: str) -> str:
    loader = get_loader()
    latest_state, _ = loader.get_latest_state(branch_str)
    return json.dumps(latest_state, ensure_ascii=False, indent=2)


def latest_history_json(branch_str: str) -> str:
    loader = get_loader()
    history, _ = loader.get_latest_history(branch_str)
    return json.dumps(history, ensure_ascii=False, indent=2)


def blocks_and_visibilities(
    branch_str: str,
    block_names: Optional[list[str]] = None,
    blacklist: Optional[list[str]] = None,
    other_branch_str_list=None,
) -> str:
    del other_branch_str_list

    loader = get_loader()
    latest_state, _ = loader.get_latest_state(branch_str)
    all_blocks = latest_state["blocks"]["__Vec3Map__"]

    if isinstance(block_names, str):
        block_names = [block_names]

    if isinstance(blacklist, str):
        blacklist = [blacklist]

    blacklist_set = set(blacklist or [])

    positions_by_name = defaultdict(list)

    for block in all_blocks:
        name = block["name"]

        # blacklist は block_names=None のときだけ使う
        if block_names is None and name in blacklist_set:
            continue

        pos_str = str(tuple(block["position"]))
        positions_by_name[name].append(pos_str)

    if block_names is None:
        target_names = sorted(positions_by_name.keys())
    else:
        target_names = block_names

    sections = []

    for name in target_names:
        positions = positions_by_name.get(name)

        if positions:
            sections.append(
                f"{name}: {json.dumps(positions, ensure_ascii=False)}"
            )
        else:
            sections.append(f"{name}: Not observed")

    return "\n".join(sections)

def events_and_visibilities(branch_str: str, agent_name_i_have: Optional[str] = None) -> str:
    del agent_name_i_have
    loader = get_loader()
    latest_state, _ = loader.get_latest_state(branch_str)

    lines = [
        "time;agent_name;agent_position;action;description",
    ]

    has_rows = False
    for tick in sorted(latest_state["events"], key=int):
        history_at_tick, _ = loader.get_history(branch_str, int(tick))
        status_at_tick = history_at_tick.get("status", {})

        for event in latest_state["events"][tick]:
            event_name = event["eventName"]
            if event_name in ["think", "blockUpdate", "useChest", "swingHand"]:
                continue

            if event_name == "chat":
                agent_name = event["visible"]["agentName"]
            else:
                agent_name = event.get("agentName", "unknown")

            if any(agent_name.startswith(prefix) for prefix in EXCLUDE_NAMES): # Admin等関係のないエージェント情報を排除するため：後程修正
                continue

            try:
                description = _event_to_description(event)
            except Exception:
                description = json.dumps(event, ensure_ascii=False)

            position = "No data"
            try:
                position = json.dumps(
                    status_at_tick[agent_name]["visible"]["position"]["__Vec3__"],
                    ensure_ascii=False,
                )
            except Exception:
                pass

            lines.append(
                f"{int(tick)};{agent_name};{position};{event_name};{description}"
            )
            has_rows = True

    if not has_rows:
        lines.append("No observable events")

    return "\n".join(lines)


FILTERS = [
    position,
    thought,
    chat_log,
    inventory,
    equipment,
    helditem,
    chests,
    other_players,
    blocks,
    blocks_and_visibilities,
    block_property,
    events,
    events_and_visibilities,
    latest_state_json,
    latest_history_json,
]

FILTER_DICT = {func.__name__: func for func in FILTERS}


def load_from_template(
    loader: WorldObservationLoader,
    template: str,
    variables: Optional[dict] = None,
    extra_filters: Optional[list] = None,
    allow_filter_override: bool = False,
) -> str:
    variables = {} if variables is None else dict(variables)
    extra_filters = [] if extra_filters is None else list(extra_filters)

    current_loader.set(loader)

    env = Environment(
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )

    extra_filter_dict = {func.__name__: func for func in extra_filters}
    if not allow_filter_override:
        overridden_keys = set(FILTER_DICT) & set(extra_filter_dict)
        if overridden_keys:
            names = ", ".join(sorted(overridden_keys))
            raise ValueError(f"Cannot override filters without allow_filter_override=True: {names}")

    env.filters = dict(FILTER_DICT, **extra_filter_dict)
    return env.from_string(template).render(variables)


def _load_json_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def render_template_from_files(
    world_config_path: Path,
    raw_observations_path: Path,
    template_path: Path,
    *,
    offset: Optional[list[int]] = None,
) -> str:
    world_config = _load_json_file(world_config_path)
    raw_messages = _load_json_file(raw_observations_path)
    template = template_path.read_text(encoding="utf-8-sig")
    if isinstance(raw_messages, dict):
        raw_messages = [raw_messages]

    runtime = StandaloneWorldObservationRuntime.from_world_config(
        world_config,
        offset=list(offset or [0, 0, 0]),
    )
    runtime.add_raw_observations(raw_messages, finalize=True)
    loader = runtime.create_current_observation_loader()
    return runtime.load_from_template(loader, template)


def build_world_config_from_first_blocks_data(blocks_data):
    if blocks_data.get("type") == "block_snapshot":
        snapshots = [blocks_data]
    else:
        snapshots = [
            item for item in blocks_data["items"]
            if item.get("type") == "block_snapshot"
        ]

    snapshots.sort(key=lambda s: s["data"].get("sequence", 0))

    if not snapshots:
        raise ValueError("block_snapshot not found")

    first = snapshots[0]
    bounds = first["data"]["bounds"]

    blocks = []
    containers = []

    for snapshot in snapshots:
        for item in snapshot["data"]["items"]:
            pos = list(item["pos"])
            block = item["block"]

            entry = {
                "position": pos,
                "name": block["blockName"],
                "stateId": block["stateId"],
            }

            if block.get("properties"):
                entry["properties"] = block["properties"]

            blocks.append(entry)

            if block["blockName"] == "chest":
                containers.append({"position": pos})

    return {
        "envBox": [
            [bounds["min"]["x"], bounds["min"]["y"], bounds["min"]["z"]],
            [bounds["max"]["x"], bounds["max"]["y"], bounds["max"]["z"]],
        ],
        "state": {
            "globalTick": first.get("tick", -1),
            "status": {},
            "blocks": {"__Vec3Map__": blocks},
            "containers": {"__Vec3Map__": containers},
            "events": {},
        },
    }

def build_world_config_from_block_snapshot_buffer(obs, block_snapshot_buffer):
    block_snapshot_items = []
    if obs.get("type") == "block_snapshot":
        block_snapshot_items = [obs]
    else:
        block_snapshot_items = [
            item for item in obs.get("items", [])
            if item.get("type") == "block_snapshot"
        ]

    if not block_snapshot_items:
        return None

    for item in block_snapshot_items:
        data = item["data"]
        request_id = data.get("requestId", "default")
        sequence = data.get("sequence", 0)
        complete = item.get("complete", data.get("complete", False))
        item_count = len(data.get("items", []))

        if request_id not in block_snapshot_buffer:
            block_snapshot_buffer[request_id] = {}

        block_snapshot_buffer[request_id][sequence] = item

        if complete:
            snapshots = [
                block_snapshot_buffer[request_id][seq]
                for seq in sorted(block_snapshot_buffer[request_id])
            ]

            merged_obs = {
                "type": "event_batch",
                "items": snapshots,
            }

            world_config = build_world_config_from_first_blocks_data(merged_obs)

            bounds = data["bounds"]
            expected_count = (
                (bounds["max"]["x"] - bounds["min"]["x"] + 1)
                * (bounds["max"]["y"] - bounds["min"]["y"] + 1)
                * (bounds["max"]["z"] - bounds["min"]["z"] + 1)
            )
            actual_count = len(world_config["state"]["blocks"]["__Vec3Map__"])
            chest_count = len(world_config["state"]["containers"]["__Vec3Map__"])

            snapshot_info = {
                "request_id": request_id,
                "sequences": sorted(block_snapshot_buffer[request_id]),
                "actual_count": actual_count,
                "expected_count": expected_count,
                "chest_count": chest_count,
            }

            del block_snapshot_buffer[request_id]

            return world_config, snapshot_info

    return None
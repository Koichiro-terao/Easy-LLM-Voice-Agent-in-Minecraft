from __future__ import annotations
import json
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from uuid import uuid4

import numpy as np
import websocket

ADMIN_MC_NAME = "admin"


def json_default(o):
    if isinstance(o, np.ndarray):
        if o.shape == (3,):
            return o.tolist()
        raise TypeError(f"Unsupported ndarray shape: {o.shape}, value={o!r}")

    raise TypeError(f"Object is not JSON serializable: type={type(o).__name__}, value={o!r}")

class MineflayerJsClient:
    def __init__(self, port, logger=None, ws_factory=None):
        self.port = port
        self.logger = logger
        self.ws_factory = ws_factory or websocket.WebSocket
        self.ws = None
        self.running = False
        self.receiver_thread = None
        self.receiver_error = None
        self.wait_response_worker_pool = ThreadPoolExecutor()
        self.responses = {}
        self.lock = threading.Lock()

    def _log(self, level, message):
        if self.logger is not None:
            log_method = getattr(self.logger, level, None)
            if callable(log_method):
                log_method(message)

    def connect(self):
        if self.ws is not None:
            return
        self.ws = self.ws_factory()
        self.ws.connect(f"ws://localhost:{self.port}")
        self.running = True
        self.receiver_thread = threading.Thread(target=self._receiver_loop, daemon=True)
        self.receiver_thread.start()

    def _wait_for_response(self, message_id, timeout, command):
        start = time.perf_counter()
        while time.perf_counter() - start < timeout:
            with self.lock:
                if message_id in self.responses:
                    resp = self.responses.pop(message_id)
                    if isinstance(resp, dict) and resp.get("errorMsg") and "success" not in resp:
                        raise RuntimeError(
                            f"Mineflayer command failed (command='{command}'): {resp['errorMsg']}"
                        )
                    return resp
                receiver_error = self.receiver_error
            if receiver_error:
                raise ConnectionError(
                    f"Receiver loop stopped before response (command='{command}'): {receiver_error}"
                )
            time.sleep(0.001)

        raise TimeoutError(f"Response timeout (command:'{command}', timeout_sec:{timeout}, message_id:'{message_id}')")

    def send_command(self, command, js_params, sync=True, timeout=180):
        if self.ws is None:
            raise RuntimeError("MineflayerJsClient is not connected.")

        message_id = str(uuid4())
        msg = {
            "messageId": message_id,
            "command": command,
            "params": js_params,
        }
        self.ws.send(json.dumps(msg, default=json_default))

        if sync:
            result = self._wait_for_response(message_id, timeout, command)
            future = Future()
            future.set_result(result)
        else:
            future = self.wait_response_worker_pool.submit(
                self._wait_for_response, message_id, timeout, command
            )

        return future

    def setup(self, *, can_dig_when_move, move_timeout_sec, stuck_check_interval_sec, stuck_offset_range, sync=True, timeout=180):
        params = {
            "canDigWhenMove": can_dig_when_move,
            "moveTimeoutSec": move_timeout_sec,
            "stuckCheckIntervalSec": stuck_check_interval_sec,
            "stuckOffsetRange": stuck_offset_range,
        }
        return self.send_command("setup", params, sync=sync, timeout=timeout)

    def join(self, *, server_id, mc_name, mc_port, mc_host="localhost", sync=True, timeout=180):
        params = {
            "mcHost": mc_host,
            "mcPort": mc_port,
            "serverId": server_id,
            "mcName": mc_name,
        }
        return self.send_command("join", params, sync=sync, timeout=timeout)

    def leave(self, *, server_id, mc_name, sync=True, timeout=180):
        params = {
            "serverId": server_id,
            "mcName": mc_name,
        }
        return self.send_command("leave", params, sync=sync, timeout=timeout)

    def update_agent_variables(self, *, server_id, mc_name, variables, sync=True, timeout=180):
        params = {
            "serverId": server_id,
            "mcName": mc_name,
            "variables": variables,
        }
        return self.send_command("updateAgentVariables", params, sync=sync, timeout=timeout)

    def set_blocks(self, *, server_id, block_info_list, is_relative, offset, mc_name=ADMIN_MC_NAME, sync=True, timeout=180):
        params = {
            "serverId": server_id,
            "mcName": mc_name,
            "blockInfoList": block_info_list,
            "isRelative": is_relative,
            "offset": offset,
        }
        return self.send_command("setBlocks", params, sync=sync, timeout=timeout)

    def set_containers(self, *, server_id, container_info_list, is_relative, offset, mc_name=ADMIN_MC_NAME, sync=True, timeout=180):
        params = {
            "serverId": server_id,
            "mcName": mc_name,
            "containerInfoList": container_info_list,
            "isRelative": is_relative,
            "offset": offset,
        }
        return self.send_command("setContainers", params, sync=sync, timeout=timeout)

    def teleport(self, *, server_id, mc_name, position, pitch, yaw, offset, teleport_offset=None, admin_mc_name=ADMIN_MC_NAME, sync=True, timeout=180):
        params = {
            "serverId": server_id,
            "adminMcName": admin_mc_name,
            "mcName": mc_name,
            "position": position,
            "pitch": pitch,
            "yaw": yaw,
            "offset": offset,
            "teleportOffset": [0, 1, 0] if teleport_offset is None else teleport_offset,
        }
        return self.send_command("teleport", params, sync=sync, timeout=timeout)

    def set_inventory_and_equipment(self, *, server_id, mc_name, inventory, equipment, admin_mc_name=ADMIN_MC_NAME, sync=True, timeout=180):
        params = {
            "serverId": server_id,
            "adminMcName": admin_mc_name,
            "mcName": mc_name,
            "inventory": inventory,
            "equipment": equipment,
        }
        return self.send_command("setInventoryAndEquipment", params, sync=sync, timeout=timeout)

    def update_mineflayer_tick_rate(self, *, server_id, mc_name, tick_rate, sync=True, timeout=180):
        params = {
            "serverId": server_id,
            "mcName": mc_name,
            "tickRate": tick_rate,
        }
        return self.send_command("updateMineflayerTickRate", params, sync=sync, timeout=timeout)

    def exec_mc(self, *, server_id, mc_name, commands, sync=True, timeout=180):
        params = {
            "serverId": server_id,
            "mcName": mc_name,
            "commands": commands,
        }
        return self.send_command("execMc", params, sync=sync, timeout=timeout)

    def exec_js(self, *, server_id, mc_name, code, primitives, sync=True, timeout=180):
        params = {
            "serverId": server_id,
            "mcName": mc_name,
            "code": code,
            "primitives": primitives,
        }
        return self.send_command("execJs", params, sync=sync, timeout=timeout)

    def _receiver_loop(self):
        while self.running:
            try:
                raw = self.ws.recv()
                if raw == "":
                    self.receiver_error = "Connection closed."
                    self._log("warning", "Connection closed.")
                    break
            except Exception as e:
                self.receiver_error = str(e)
                self._log("warning", f"Receiver error: Failed to receive data. {e}")
                break

            try:
                msg = json.loads(raw)
            except Exception as e:
                self.receiver_error = f"Failed to parse JSON message: {e}"
                self._log("warning", f"Receiver error: Failed to parse json message. {e}")
                break

            try:
                msg_type = msg["type"]
                if msg_type == "response":
                    with self.lock:
                        self.responses[msg["messageId"]] = msg["data"]
                elif msg_type == "heartBeat":
                    pass
                else:
                    raise Exception(f'Unknown msg_type "{msg_type}"')

            except Exception as e:
                self.receiver_error = f"Failed to process receiver message: {e}"
                self._log("warning", f"Receiver error: {e}")
                break

    def close(self, send_close_command=True):
        self.running = False

        if self.ws is None:
            return

        try:
            if send_close_command:
                self.send_command("close", {}, sync=False)
        except Exception as e:
            if self.logger:
                self.logger.warning(f"Failed to send close command to mineflayer: {e}")
        finally:
            self.ws.close()
            self.ws = None

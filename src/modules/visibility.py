from __future__ import annotations

import base64
import math
from typing import Any, Sequence

import numpy as np
from numba import njit, prange


PLAYER_EYE_HEIGHT = 1.6

_TRANSPARENT_BLOCK_NAMES = {
    "air",
    "cave_air",
    "void_air",
    "water",
    "lava",
    "grass",
    "tall_grass",
    "fern",
    "large_fern",
    "dead_bush",
    "vine",
    "ladder",
    "lever",
    "tripwire",
    "tripwire_hook",
    "redstone_wire",
    "torch",
    "wall_torch",
    "soul_torch",
    "soul_wall_torch",
    "rail",
    "powered_rail",
    "detector_rail",
    "activator_rail",
    "seagrass",
    "tall_seagrass",
    "kelp",
    "kelp_plant",
    "sugar_cane",
    "snow",
}


class Vec3BoolMap:
    def __init__(self, range_):
        self.range = (np.array(range_[0]), np.array(range_[1]))
        self.size = self.range[1] - self.range[0] + 1
        total_size = int(np.prod(self.size))
        self.data = np.zeros(total_size, dtype=bool)

    def _is_within_range(self, vec: np.ndarray) -> bool:
        return np.all(vec >= self.range[0]) and np.all(vec <= self.range[1])

    def _to_index(self, vec: np.ndarray) -> int:
        rel = vec - self.range[0]
        return int(rel[0] + self.size[0] * (rel[1] + self.size[1] * rel[2]))

    def _from_index(self, index: int) -> np.ndarray:
        sx, sy, sz = int(self.size[0]), int(self.size[1]), int(self.size[2])
        x = index % sx
        y = (index // sx) % sy
        z = index // (sx * sy)
        return np.array([x, y, z]) + self.range[0]

    def to_base64(self) -> str:
        bits = self.data.astype(np.uint8)
        pad = (-len(bits)) % 8
        if pad:
            bits = np.pad(bits, (0, pad), mode="constant", constant_values=0)
        byte_arr = np.packbits(bits, bitorder="big")
        return base64.b64encode(byte_arr.tobytes()).decode("ascii")

    def from_base64(self, base64_str: str) -> None:
        byte_array = base64.b64decode(base64_str)
        binary_str = "".join(f"{byte:08b}" for byte in byte_array)
        total_size = int(np.prod(self.size))
        self.data = np.array([bit == "1" for bit in binary_str[:total_size]], dtype=bool)

    def add(self, vec) -> None:
        v = np.asarray(vec, dtype=int)
        if v.shape != (3,):
            raise ValueError("vec must be length-3 (x, y, z).")
        if not self._is_within_range(v):
            raise ValueError("Vec3 is out of the specified range.")
        idx = self._to_index(v)
        self.data[idx] = True

    def add_indices(self, idxs, *, safe: bool = False) -> None:
        idxs = np.asarray(idxs)
        if idxs.ndim != 1:
            idxs = idxs.ravel()
        if idxs.dtype != np.int64:
            idxs = idxs.astype(np.int64, copy=False)
        if safe:
            n = self.data.size
            if idxs.size and (idxs.min() < 0 or idxs.max() >= n):
                raise ValueError("index out of range")
        self.data[idxs] = True

    def has(self, vec: np.ndarray) -> bool:
        if not self._is_within_range(vec):
            return False
        return bool(self.data[self._to_index(vec)])

    def get_all(self) -> list[np.ndarray]:
        true_indices = np.flatnonzero(self.data)
        return [self._from_index(int(i)) for i in true_indices]


def _as_v3(v) -> np.ndarray:
    arr = np.asarray(v, dtype=np.float64)
    if arr.shape != (3,):
        raise ValueError(f"Expected shape (3,), got {arr.shape}")
    return arr


def _as_v3_int(v) -> np.ndarray:
    arr = np.asarray(v, dtype=np.int32)
    if arr.shape != (3,):
        raise ValueError(f"Expected shape (3,), got {arr.shape}")
    return arr


def _is_transparent_block(block_name: str, extra_transparent_blocks: set[str]) -> bool:
    if block_name in _TRANSPARENT_BLOCK_NAMES or block_name in extra_transparent_blocks:
        return True
    if "glass" in block_name or "pane" in block_name:
        return True
    if block_name.endswith("_button"):
        return True
    if block_name.endswith("_pressure_plate"):
        return True
    if block_name.endswith("_carpet"):
        return True
    if block_name.endswith("_torch"):
        return True
    if block_name.endswith("_rail"):
        return True
    return False


def _build_block_arrays(
    block_memory: Any,
    extra_transparent_blocks: Sequence[str],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, tuple[np.ndarray, np.ndarray]]:
    extra_transparent = {str(name) for name in extra_transparent_blocks}
    data = block_memory.data
    count = len(data)

    if count == 0:
        empty = np.empty(0, dtype=np.int32)
        range_ = (
            np.array([0, 0, 0], dtype=np.int32),
            np.array([0, 0, 0], dtype=np.int32),
        )
        return empty, empty, empty, empty.astype(np.uint8), range_

    xs = np.empty(count, dtype=np.int32)
    ys = np.empty(count, dtype=np.int32)
    zs = np.empty(count, dtype=np.int32)
    opaque = np.empty(count, dtype=np.uint8)

    min_x = min_y = min_z = None
    max_x = max_y = max_z = None

    for i, (pos, block) in enumerate(data.items()):
        x = int(pos[0])
        y = int(pos[1])
        z = int(pos[2])
        xs[i] = x
        ys[i] = y
        zs[i] = z

        block_name = str(block.get("name", "air"))
        opaque[i] = 0 if _is_transparent_block(block_name, extra_transparent) else 1

        if min_x is None:
            min_x = max_x = x
            min_y = max_y = y
            min_z = max_z = z
            continue

        if x < min_x:
            min_x = x
        elif x > max_x:
            max_x = x
        if y < min_y:
            min_y = y
        elif y > max_y:
            max_y = y
        if z < min_z:
            min_z = z
        elif z > max_z:
            max_z = z

    range_ = (
        np.array([min_x, min_y, min_z], dtype=np.int32),
        np.array([max_x, max_y, max_z], dtype=np.int32),
    )
    return xs, ys, zs, opaque, range_


def _clip_abs_box(
    env_box,
    offset,
    eye_abs_pos: np.ndarray,
    max_dist: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    abs_env_box0 = _as_v3(env_box[0]) + offset
    abs_env_box1 = _as_v3(env_box[1]) + offset

    abs_min_x = int(math.floor(min(max(eye_abs_pos[0] - max_dist, abs_env_box0[0]), abs_env_box1[0])))
    abs_max_x = int(math.floor(max(min(eye_abs_pos[0] + max_dist, abs_env_box1[0]), abs_env_box0[0])))
    abs_min_y = int(math.floor(min(max(eye_abs_pos[1] - max_dist, abs_env_box0[1]), abs_env_box1[1])))
    abs_max_y = int(math.floor(max(min(eye_abs_pos[1] + max_dist, abs_env_box1[1]), abs_env_box0[1])))
    abs_min_z = int(math.floor(min(max(eye_abs_pos[2] - max_dist, abs_env_box0[2]), abs_env_box1[2])))
    abs_max_z = int(math.floor(max(min(eye_abs_pos[2] + max_dist, abs_env_box1[2]), abs_env_box0[2])))

    origin = np.array([abs_min_x, abs_min_y, abs_min_z], dtype=np.int32)
    max_corner = np.array([abs_max_x, abs_max_y, abs_max_z], dtype=np.int32)
    size = max_corner - origin + 1
    return origin, max_corner, size.astype(np.int32, copy=False)


@njit(cache=True, fastmath=True)
def _build_occ_grid_jit(
    xs: np.ndarray,
    ys: np.ndarray,
    zs: np.ndarray,
    opaque: np.ndarray,
    origin: np.ndarray,
    size: np.ndarray,
) -> np.ndarray:
    sx = int(size[0])
    sy = int(size[1])
    sz = int(size[2])
    sxy = sx * sy
    occ = np.zeros(sx * sy * sz, dtype=np.uint8)

    for i in range(xs.shape[0]):
        if opaque[i] == 0:
            continue

        ix = int(xs[i] - origin[0])
        iy = int(ys[i] - origin[1])
        iz = int(zs[i] - origin[2])
        if ix < 0 or ix >= sx or iy < 0 or iy >= sy or iz < 0 or iz >= sz:
            continue
        occ[ix + sx * iy + sxy * iz] = 1

    return occ


def _build_occlusion_data(
    xs: np.ndarray,
    ys: np.ndarray,
    zs: np.ndarray,
    opaque: np.ndarray,
    origin: np.ndarray,
    size: np.ndarray,
) -> np.ndarray:
    return _build_occ_grid_jit(xs, ys, zs, opaque, origin, size)


@njit(cache=True, fastmath=True, inline="always")
def _ray_visible_occ(
    ox: float,
    oy: float,
    oz: float,
    tx: float,
    ty: float,
    tz: float,
    origin: np.ndarray,
    occ: np.ndarray,
    size: np.ndarray,
) -> bool:
    dx = tx - ox
    dy = ty - oy
    dz = tz - oz

    if dx == 0.0 and dy == 0.0 and dz == 0.0:
        return True

    ix = int(math.floor(ox) - origin[0])
    iy = int(math.floor(oy) - origin[1])
    iz = int(math.floor(oz) - origin[2])

    txi = int(math.floor(tx) - origin[0])
    tyi = int(math.floor(ty) - origin[1])
    tzi = int(math.floor(tz) - origin[2])

    if ix == txi and iy == tyi and iz == tzi:
        return True

    step_x = 1 if dx > 0.0 else -1 if dx < 0.0 else 0
    step_y = 1 if dy > 0.0 else -1 if dy < 0.0 else 0
    step_z = 1 if dz > 0.0 else -1 if dz < 0.0 else 0

    voxel_x = math.floor(ox)
    voxel_y = math.floor(oy)
    voxel_z = math.floor(oz)

    next_boundary_x = voxel_x + (1 if step_x > 0 else 0)
    next_boundary_y = voxel_y + (1 if step_y > 0 else 0)
    next_boundary_z = voxel_z + (1 if step_z > 0 else 0)

    inv_dx = 1.0 / dx if dx != 0.0 else math.inf
    inv_dy = 1.0 / dy if dy != 0.0 else math.inf
    inv_dz = 1.0 / dz if dz != 0.0 else math.inf

    t_max_x = (next_boundary_x - ox) * inv_dx if dx != 0.0 else math.inf
    t_max_y = (next_boundary_y - oy) * inv_dy if dy != 0.0 else math.inf
    t_max_z = (next_boundary_z - oz) * inv_dz if dz != 0.0 else math.inf

    t_delta_x = abs(inv_dx) if dx != 0.0 else math.inf
    t_delta_y = abs(inv_dy) if dy != 0.0 else math.inf
    t_delta_z = abs(inv_dz) if dz != 0.0 else math.inf

    sx = int(size[0])
    sy = int(size[1])
    sz = int(size[2])
    sxy = sx * sy

    while True:
        if t_max_x < t_max_y:
            if t_max_x < t_max_z:
                ix += step_x
                t_max_x += t_delta_x
            else:
                iz += step_z
                t_max_z += t_delta_z
        else:
            if t_max_y < t_max_z:
                iy += step_y
                t_max_y += t_delta_y
            else:
                iz += step_z
                t_max_z += t_delta_z

        if ix < 0 or ix >= sx or iy < 0 or iy >= sy or iz < 0 or iz >= sz:
            return True

        if ix == txi and iy == tyi and iz == tzi:
            return True

        if occ[ix + sx * iy + sxy * iz] != 0:
            return False


@njit(cache=True, fastmath=True, parallel=True)
def _mark_visible_blocks_jit(
    eye_pos: np.ndarray,
    origin: np.ndarray,
    occ: np.ndarray,
    size: np.ndarray,
    xs: np.ndarray,
    ys: np.ndarray,
    zs: np.ndarray,
    clip_min: np.ndarray,
    clip_max: np.ndarray,
    max_dist_squared: float,
) -> np.ndarray:
    visible = np.zeros(xs.shape[0], dtype=np.uint8)
    ex = float(eye_pos[0])
    ey = float(eye_pos[1])
    ez = float(eye_pos[2])

    for i in prange(xs.shape[0]):
        x = int(xs[i])
        y = int(ys[i])
        z = int(zs[i])

        if x < clip_min[0] or x > clip_max[0]:
            continue
        if y < clip_min[1] or y > clip_max[1]:
            continue
        if z < clip_min[2] or z > clip_max[2]:
            continue

        tx = x + 0.5
        ty = y + 0.5
        tz = z + 0.5

        dx = tx - ex
        dy = ty - ey
        dz = tz - ez
        if dx * dx + dy * dy + dz * dz > max_dist_squared:
            continue

        if _ray_visible_occ(ex, ey, ez, tx, ty, tz, origin, occ, size):
            visible[i] = 1

    return visible


@njit(cache=True, fastmath=True)
def _collect_visible_indices_jit(
    xs: np.ndarray,
    ys: np.ndarray,
    zs: np.ndarray,
    visible: np.ndarray,
    origin: np.ndarray,
    size: np.ndarray,
) -> np.ndarray:
    count = 0
    for i in range(visible.shape[0]):
        if visible[i] != 0:
            count += 1

    result = np.empty(count, dtype=np.int64)
    sx = int(size[0])
    sy = int(size[1])
    sxy = sx * sy

    j = 0
    for i in range(visible.shape[0]):
        if visible[i] == 0:
            continue
        lx = int(xs[i] - origin[0])
        ly = int(ys[i] - origin[1])
        lz = int(zs[i] - origin[2])
        result[j] = lx + sx * ly + sxy * lz
        j += 1

    return result


def _serialize_vec3boolmap(vec3boolmap: Vec3BoolMap) -> dict:
    return {
        "__Vec3BoolMap__": {
            "range": [
                {"__Vec3__": vec3boolmap.range[0].tolist()},
                {"__Vec3__": vec3boolmap.range[1].tolist()},
            ],
            "base64": vec3boolmap.to_base64(),
        }
    }


def get_player_visibility_all(
    env_box,
    offset,
    player_relative_positions: dict[str, Any],
    block_memory,
    non_existent_agent_names=(),
    max_dist: float = 20.0,
    extra_transparent_blocks=(),
) -> dict[str, dict[str, bool]]:
    visibility: dict[str, dict[str, bool]] = {}
    for see_agent_name in player_relative_positions.keys():
        visibility[see_agent_name] = get_player_visibility(
            env_box,
            offset,
            player_relative_positions,
            block_memory,
            non_existent_agent_names,
            max_dist,
            extra_transparent_blocks,
            see_agent_name
        )

    return visibility


def get_player_visibility(
        env_box,
        offset,
        player_relative_positions: dict[str, Any],
        block_memory,
        see_agent_name: str,
        non_existent_agent_names=(),
        max_dist: float = 20.0,
        extra_transparent_blocks=(),
):
    offset_v = _as_v3(offset)
    max_dist_squared = float(max_dist) * float(max_dist)
    missing_names = {str(name) for name in non_existent_agent_names}
    xs, ys, zs, opaque, _ = _build_block_arrays(block_memory, extra_transparent_blocks)

    absolute_positions: dict[str, np.ndarray | None] = {}
    for name, rel_pos in player_relative_positions.items():
        if rel_pos is None:
            absolute_positions[name] = None
            continue
        absolute_positions[name] = _as_v3(rel_pos) + np.array([0.0, PLAYER_EYE_HEIGHT, 0.0]) + offset_v

    see_pos = absolute_positions[see_agent_name]
    vis = {}
    if see_pos is not None and see_agent_name not in missing_names:
        origin, _, size = _clip_abs_box(env_box, offset_v, see_pos, max_dist)
        occ = _build_occlusion_data(xs, ys, zs, opaque, origin, size)
    else:
        origin = np.array([0, 0, 0], dtype=np.int32)
        size = np.array([1, 1, 1], dtype=np.int32)
        occ = np.zeros(1, dtype=np.uint8)

    for saw_agent_name, saw_pos in absolute_positions.items():
        if see_agent_name == saw_agent_name:
            vis[saw_agent_name] = True
            continue

        if see_agent_name in missing_names or saw_agent_name in missing_names:
            vis[saw_agent_name] = False
            continue

        if see_pos is None or saw_pos is None:
            vis[saw_agent_name] = False
            continue

        dx = float(saw_pos[0] - see_pos[0])
        dy = float(saw_pos[1] - see_pos[1])
        dz = float(saw_pos[2] - see_pos[2])
        if dx * dx + dy * dy + dz * dz > max_dist_squared:
            vis[saw_agent_name] = False
            continue

        vis[saw_agent_name] = bool(
            _ray_visible_occ(
                float(see_pos[0]),
                float(see_pos[1]),
                float(see_pos[2]),
                float(saw_pos[0]),
                float(saw_pos[1]),
                float(saw_pos[2]),
                origin,
                occ,
                size,
            )
        )

    return vis


def get_block_visibility_all(
    env_box,
    offset,
    player_relative_positions: dict[str, Any],
    block_memory,
    non_existent_agent_names=(),
    max_dist: float = 20.0,
    extra_transparent_blocks=(),
) -> dict[str, dict]:

    visible_blocks: dict[str, dict] = {}
    for agent_name in player_relative_positions.keys():
        visible_blocks[agent_name] = get_block_visibility(
            env_box,
            offset,
            player_relative_positions[agent_name],
            block_memory,
            agent_name,
            non_existent_agent_names=non_existent_agent_names,
            max_dist = max_dist,
            extra_transparent_blocks=extra_transparent_blocks,
        )

    return visible_blocks


def get_block_visibility(
    env_box,
    offset,
    player_relative_position,
    block_memory,
    agent_name,
    non_existent_agent_names=(),
    max_dist: float = 20.0,
    extra_transparent_blocks=(),        
) -> Vec3BoolMap:
    offset_v = _as_v3(offset)
    max_dist_squared = float(max_dist) * float(max_dist)
    missing_names = {str(name) for name in non_existent_agent_names}
    xs, ys, zs, opaque, _ = _build_block_arrays(block_memory, extra_transparent_blocks)

    rel_pos = player_relative_position.get(agent_name)

    if rel_pos is None or agent_name in missing_names or xs.size == 0:
        return Vec3BoolMap(
            (
                np.array([0, 0, 0], dtype=np.int32),
                np.array([0, 0, 0], dtype=np.int32),
            )
        )

    eye_pos = _as_v3(rel_pos) + np.array([0.0, PLAYER_EYE_HEIGHT, 0.0]) + offset_v
    origin, max_corner, size = _clip_abs_box(env_box, offset_v, eye_pos, max_dist)
    occ = _build_occlusion_data(xs, ys, zs, opaque, origin, size)

    vec3boolmap = Vec3BoolMap(
        (
            (origin - offset_v).astype(np.int32),
            (max_corner - offset_v).astype(np.int32),
        )
    )

    visible_mask = _mark_visible_blocks_jit(
        eye_pos,
        origin,
        occ,
        size,
        xs,
        ys,
        zs,
        origin,
        max_corner,
        max_dist_squared,
    )
    visible_indices = _collect_visible_indices_jit(xs, ys, zs, visible_mask, origin, size)
    if visible_indices.size:
        vec3boolmap.add_indices(visible_indices)

    return vec3boolmap


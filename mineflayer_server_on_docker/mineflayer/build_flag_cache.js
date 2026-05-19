const fs = require("fs");
const path = require("path");
const msgpack = require("msgpack-lite");

function parseArgs(argv) {
  if (argv.length === 0) {
    return {
      mcVersion: "1.21"
    };
  }
  if (argv.length === 1 && !argv[0].startsWith("-")) {
    return {
      mcVersion: argv[0]
    };
  }
  throw new Error("Usage: node build_flag_cache.js [mc_version]");
}

const { mcVersion: MC_VERSION } = parseArgs(process.argv.slice(2));

const registry = require("prismarine-registry")(MC_VERSION);
const Block = require("prismarine-block")(registry);

const TRANSPARENT_BASE = new Set([
  "glass", "tinted_glass", "glass_pane", "barrier", "ice"
]);

function inferStateCount(registry) {
  let maxStateId = -1;
  const blocksByName = registry.blocksByName || {};
  for (const info of Object.values(blocksByName)) {
    if (!info || typeof info !== "object") continue;
    if (Number.isInteger(info.maxStateId)) {
      maxStateId = Math.max(maxStateId, info.maxStateId);
    }
    if (Number.isInteger(info.defaultState)) {
      maxStateId = Math.max(maxStateId, info.defaultState);
    }
  }
  return maxStateId >= 0 ? maxStateId + 1 : 20000;
}

const N =
  (registry.blocksByStateId && registry.blocksByStateId.length)
    ? registry.blocksByStateId.length
    : inferStateCount(registry);

// ---------- utils ----------

function normalizeProps(props) {
  if (!props || typeof props !== "object") return {};
  const keys = Object.keys(props).sort();
  const out = {};
  for (const k of keys) out[k] = props[k];
  return out;
}

function makeKey(name, props) {
  return `${name}\t${JSON.stringify(normalizeProps(props))}`;
}

function getPropsSafe(block) {
  try {
    const p = block.getProperties ? block.getProperties() : null;
    if (p && typeof p === "object") return p;
  } catch (e) {}
  return {};
}

// ---------- flags ----------

function getFlagsDisk(stateId) {
  const block = Block.fromStateId(stateId, 0);
  if (!block) return [true, false, null];

  if (block.boundingBox === "empty") {
    return [true, false, null];
  }

  const name = String(block.name);

  const isTransparent =
    TRANSPARENT_BASE.has(name) ||
    name.endsWith("_stained_glass") ||
    name.endsWith("_stained_glass_pane");

  if (isTransparent) {
    return [false, true, null];
  }

  let props = {};
  try {
    const p = block.getProperties ? block.getProperties() : null;
    if (p && typeof p === "object") props = p;
  } catch (e) {}

  const isTall =
    name.endsWith("_wall") ||
    name.endsWith("_fence") ||
    (name.endsWith("_fence_gate") && !Boolean(props.open));

  let shapes = [];
  try {
    const s = block.shapes || [];
    shapes = s.map(aabb => Array.from(aabb));
  } catch (e) {
    shapes = [];
  }

  if (isTall) {
    for (const aabb of shapes) {
      if (aabb.length >= 6) aabb[4] = Math.min(aabb[4], 1.0);
    }
  }

  return [false, false, shapes];
}

// ---------- build ----------

const outDir = "/cache";
fs.mkdirSync(outDir, { recursive: true });

const outPath = path.join(outDir, `cache_${MC_VERSION}.msgpack`);

console.log(`Building msgpack cache: version=${MC_VERSION}, N=${N}`);

const flags_table = new Array(N);
const state_id_map = Object.create(null);

for (let i = 0; i < N; i++) {
  const block = Block.fromStateId(i, 0);
  if (!block) continue;

  const [isEmpty, isTransparent, shapes] = getFlagsDisk(i);
  flags_table[i] = [isEmpty, isTransparent, shapes];

  const name = String(block.name);
  const props = normalizeProps(getPropsSafe(block));
  const key = makeKey(name, props);
  state_id_map[key] = i;

  if (i % 2000 === 0) console.log(`  ${i}/${N}`);
}

for (const [name, info] of Object.entries(registry.blocksByName || {})) {
  if (!info || !Number.isInteger(info.defaultState)) continue;
  state_id_map[makeKey(String(name), {})] = info.defaultState;
}

const payload = {
  version: MC_VERSION,
  N,
  flags_table,
  state_id_map
};

fs.writeFileSync(outPath, msgpack.encode(payload));

console.log("written:", outPath);

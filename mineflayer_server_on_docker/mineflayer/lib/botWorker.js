const fs = require('fs').promises;
const path = require('path');
const { parentPort, workerData } = require('worker_threads');

const mineflayer = require('mineflayer');
const Vec3 = require('vec3');

const { WorkerLogger, getFormattedDateTime, handleError, isErrorMessage, loadFromJson, dumpToJson, cloneObj } = require("./utils");
const { teleport, setInventoryAndEquipment, setBlocks, setContainer, clearBox, execMcCommands, enableTransparency, disableTransparency } = require("./mcUtils");


const mcHost = workerData.mcHost;
const mcPort = workerData.mcPort;
const mcName = workerData.mcName;
const canDigWhenMove = workerData.canDigWhenMove ?? true;
const moveTimeoutSec = workerData.moveTimeoutSec ?? 60;
const stuckCheckIntervalSec = workerData.stuckCheckIntervalSec ?? 0.5;
const stuckOffsetRange = workerData.stuckOffsetRange ?? 0.5;
const createBotTimeoutSec = workerData.createBotTimeoutSec ?? 20;
const logDir = workerData.logDir;

// ダメージ・ノックバック関連のデバッグログを有効にする場合は true に変更する
const DEBUG_DAMAGE = false;

const logger = new WorkerLogger(parentPort)
let bot;

function sendResponse(id, data={}, errorMsg=null){
    if([undefined, null].includes(data)){
        data = {};
    }
    if(errorMsg === undefined){
        errorMsg = null;
    }
    parentPort.postMessage({
        type: "response",
        id: id,
        result:{data, errorMsg}
    })
}

function sendSignal(signal, data={}, errorMsg=null){
    if([undefined, null].includes(data)){
        data = {};
    }
    if(errorMsg === undefined){
        errorMsg = null;
    }
    parentPort.postMessage({
        type: "signal",
        id: signal,
        result:{data, errorMsg}
    });
}

function getMcData(){
    const mcData = require("minecraft-data")(bot.version);
    // Accept legacy/display-style aliases in addition to Java registry names.
    const itemAliases = {
        leather_cap: "leather_helmet",
        leather_tunic: "leather_chestplate",
        leather_pants: "leather_leggings",
        lapis_lazuli_ore: "lapis_ore",
    };
    const blockAliases = {
        lapis_lazuli_ore: "lapis_ore",
    };

    for(const [alias, canonicalName] of Object.entries(itemAliases)){
        if(mcData.itemsByName[canonicalName]){
            mcData.itemsByName[alias] = mcData.itemsByName[canonicalName];
        }
    }
    for(const [alias, canonicalName] of Object.entries(blockAliases)){
        if(mcData.blocksByName[canonicalName]){
            mcData.blocksByName[alias] = mcData.blocksByName[canonicalName];
        }
    }

    return mcData
}

try{
    const timeoutId = setTimeout(()=>{
        sendSignal("bot_status", {success: false}, `Failed to create bot "${mcName}" on ${mcHost}:${mcPort}`)
    }, createBotTimeoutSec*1000)
    bot = mineflayer.createBot({
        host: mcHost,
        port: mcPort,
        username: mcName,
        disableChatSigning: true,
        checkTimeoutInterval: 60 * 60 * 1000,
    });
    clearTimeout(timeoutId);
}catch(e){
    logger.critical(`Failed to create bot "${mcName}" on ${mcHost}:${mcPort}`)
    return;
}

bot.once('spawn', async () => {
    logger.debug(`${mcName} has spawned!`)
    sendSignal("bot_status", {success: true})

    parentPort.on('message', async ({id, data}) => {
        const command = data.command;
        const args = data.args;

        logger.debug(`command "${command}" start`);

        let responseData;
        let errorMsg = null;
        try{
            switch (command) {
                case "execute":         responseData = await execute(args); break;
                case "execMcCommands":  _execMcCommands(args); break;
                case "stopMoving":      await stopMoving(args); break;
                case "getAllMcNames":   responseData = getAllMcNames(args); break;
                case "teleport":        await _teleport(args); break;
                case "setInventoryAndEquipment": await _setInventoryAndEquipment(args); break;
                case "setBlocks":       await _setBlocks(args); break;
                case "setContainers":    await _setContainers(args); break;
                case "clearBox":        await _clearBox(args); break;
                case "updateAgentVariables": await updateAgentVariables(args); break;
                case "enableTransparency": _enableTransparency(args); break;
                case "disableTransparency": _disnableTransparency(args); break;
                case "updateMineflayerTickRate": updateMineflayerTickRate(args); break;
                case "close":            await close(); break;
                default:
                    sendResponse(id, {}, `Invalid command "${command}"`);
                    return;
            }
        }catch(e){
            errorMsg = e.stack;
        }
        sendResponse(id, responseData, errorMsg);
        logger.debug(`command "${command}" finished`);
    });

    const { pathfinder } = require("mineflayer-pathfinder");
    bot.loadPlugin(pathfinder);

    bot.on('error', (err) => logger.error(`Error in worker of ${mcName}: ${err.stack}`));
    bot.on('kicked', (reason) => logger.error(`${mcName} kicked: ${JSON.stringify(reason, null, 2)}`));
    bot.on('end', () => logger.info(`${mcName} disconnected.`));
    bot.on('message', (jsonMsg) => {
        const message = jsonMsg.toString();
        if (isErrorMessage(message)) {
            logger.error(`Minecraft command error: ${message}`);
        }
    });

    // ========== ダメージ調査ログ（DEBUG_DAMAGE=true の時のみ出力） ==========
    if (DEBUG_DAMAGE) {
        bot.on('health', () => {
            const p = bot.entity.position, v = bot.entity.velocity;
            logger.info(`[damage-debug] update_health: health=${bot.health.toFixed(1)} pos=(${p.x.toFixed(3)},${p.y.toFixed(3)},${p.z.toFixed(3)}) vel=(${v.x.toFixed(4)},${v.y.toFixed(4)},${v.z.toFixed(4)}) onGround=${bot.entity.onGround}`);
        });
        bot.on('forcedMove', () => {
            const p = bot.entity.position, v = bot.entity.velocity;
            logger.info(`[damage-debug] forcedMove: pos=(${p.x.toFixed(3)},${p.y.toFixed(3)},${p.z.toFixed(3)}) vel=(${v.x.toFixed(4)},${v.y.toFixed(4)},${v.z.toFixed(4)})`);
        });
    }

    // entity_velocity 受信後、サーバーへ送信する座標を数tick分記録する（DEBUG_DAMAGE=true の時のみ有効）
    let logSendPackets = false;
    let logSendTimer = null;
    // 最後に正常送信できた座標を追跡
    let lastValidPos = { x: bot.entity.position.x, y: bot.entity.position.y, z: bot.entity.position.z };
    const origWrite = bot._client.write.bind(bot._client);
    bot._client.write = (name, data) => {
        // 【最終防衛ライン】NaN 座標をサーバーに送らない
        if (['position', 'position_look'].includes(name)) {
            if (!isFinite(data.x) || !isFinite(data.y) || !isFinite(data.z)) {
                logger.warn(`[NaN-BLOCKED] ${name} x=${data.x} y=${data.y} z=${data.z} — packet dropped, resetting to (${lastValidPos.x.toFixed(3)},${lastValidPos.y.toFixed(3)},${lastValidPos.z.toFixed(3)})`);
                // 最後に送信成功した有効な座標と速度にリセット
                bot.entity.position.set(lastValidPos.x, lastValidPos.y, lastValidPos.z);
                bot.entity.velocity.set(0, 0, 0);
                return; // サーバーへ送らない
            }
            // 正常な座標を記録
            lastValidPos = { x: data.x, y: data.y, z: data.z };
        }
        if (DEBUG_DAMAGE && logSendPackets && ['position', 'look', 'position_look', 'flying'].includes(name)) {
            logger.info(`[send-packet] ${name}: x=${data.x != null ? data.x.toFixed(4) : 'n/a'} y=${data.y != null ? data.y.toFixed(4) : 'n/a'} z=${data.z != null ? data.z.toFixed(4) : 'n/a'} onGround=${data.onGround}`);
        }
        origWrite(name, data);
    };
    bot._client.on('entity_velocity', (packet) => {
        const rawVX = packet.velocity !== undefined ? packet.velocity.x : packet.velocityX;
        const rawVY = packet.velocity !== undefined ? packet.velocity.y : packet.velocityY;
        const rawVZ = packet.velocity !== undefined ? packet.velocity.z : packet.velocityZ;
        const nvx = rawVX / 8000, nvy = rawVY / 8000, nvz = rawVZ / 8000;
        if (DEBUG_DAMAGE) {
            const p = bot.entity.position, v = bot.entity.velocity;
            logger.info(`[damage-debug] entity_velocity: entityId=${packet.entityId} botEntityId=${bot.entity.id} isBot=${packet.entityId === bot.entity.id} vel=(${nvx.toFixed(4)},${nvy.toFixed(4)},${nvz.toFixed(4)}) pos=(${p.x.toFixed(3)},${p.y.toFixed(3)},${p.z.toFixed(3)}) curVel=(${v.x.toFixed(4)},${v.y.toFixed(4)},${v.z.toFixed(4)}) onGround=${bot.entity.onGround}`);
        }
        // ノックバックをbotWorkerハンドラからも直接適用（entities.jsの処理と順序に依存しないよう保険として）
        if (packet.entityId === bot.entity.id && isFinite(nvx) && isFinite(nvy) && isFinite(nvz)) {
            bot.entity.velocity.x = nvx;
            bot.entity.velocity.y = nvy;
            bot.entity.velocity.z = nvz;
            if (DEBUG_DAMAGE) {
                logger.info(`[damage-debug] knockback applied: vel=(${nvx.toFixed(4)},${nvy.toFixed(4)},${nvz.toFixed(4)})`);
            }
        }
        // entity_velocity 後 300ms 間、送信パケットをログに記録（DEBUG_DAMAGE=true の時のみ有効）
        if (DEBUG_DAMAGE) {
            logSendPackets = true;
            if (logSendTimer) clearTimeout(logSendTimer);
            logSendTimer = setTimeout(() => { logSendPackets = false; }, 300);
        }
    });
    // ======================================

    let movingTickCounter = 0;
    let stopMovingTickCounter = 0;
    let lastPosition = null;
    const stopMovingTickNum = 20;
    function onTick() {
        //console.log(`#########${bot.username} isMoving=${bot.pathfinder.isMoving()}`)
        if (bot.pathfinder.isMoving()) {
            if(movingTickCounter === 0){
                lastPosition = bot.entity.position.clone();
            }
            movingTickCounter++;
            stopMovingTickCounter = 0;

            if (movingTickCounter % Math.floor(20 * stuckCheckIntervalSec) === 0) {
                const delta = lastPosition.distanceTo(bot.entity.position);
                if(delta < 0.1){
                    const pos = bot.entity.position.clone();
                    const dx = (Math.random() - 0.5) * 2 * stuckOffsetRange // -stuckOffsetRange <= dx <= stuckOffsetRange
                    const dz = (Math.random() - 0.5) * 2 * stuckOffsetRange
                    bot.chat(`/tp @s ${pos.x+dx} ${pos.y} ${pos.z+dz}`);
                    logger.info(`${bot.username} is stuck. Trying to adjust position...`);
                }
                lastPosition = bot.entity.position.clone();
                logger.debug(`movingTickCounter=${movingTickCounter} delta=${delta}`);
            }

            if (movingTickCounter >= 20 * moveTimeoutSec) {
                bot.pathfinder.stop();
                movingTickCounter = 0;
            }
        } else if (stopMovingTickCounter < stopMovingTickNum){
            stopMovingTickCounter++;
        } else {
            movingTickCounter = 0;
            stopMovingTickCounter = 0;
            lastPosition = null;
        }
    }
    bot.on("physicsTick", onTick);

    bot.offsetVec3 = new Vec3(0,0,0);
});

async function execute({code, primitives=[]}){
    const {
        Movements,
        goals: {
            Goal,
            GoalBlock,
            GoalNear,
            GoalXZ,
            GoalNearXZ,
            GoalY,
            GoalGetToBlock,
            GoalLookAtBlock,
            GoalBreakBlock,
            GoalCompositeAny,
            GoalCompositeAll,
            GoalInvert,
            GoalFollow,
            GoalPlaceBlock,
        },
        pathfinder,
        Move,
        ComputedPath,
        PartiallyComputedPath,
        XZCoordinates,
        XYZCoordinates,
        SafeBlock,
        GoalPlaceBlockOptions,
    } = require("mineflayer-pathfinder");
    const { Vec3 } = require("vec3");

    const mcData = getMcData();

    const movements = new Movements(bot, mcData);
    movements.canDig = canDigWhenMove;
    bot.pathfinder.setMovements(movements);

    const primitivesStr = primitives.join("\n\n");
    const wholeCode = "(async () => {" + primitivesStr + "\n" + code + "\n})()";
    let success;
    let errorMsg = null;
    try{
        await eval(wholeCode);
        success = true;
    } catch(e){
        success = false;
        errorMsg = handleError(e, code, primitivesStr);
    }
    bot.pathfinder.stop();

    const suffix = success ? "" : "_error"
    const fileToSave = path.join(logDir, `executed_code_${getFormattedDateTime()}_${mcName}${suffix}.txt`);
    fs.writeFile(fileToSave, code, 'utf8');

    return {success, errorMsg};
}

function _execMcCommands(args){
    args.bot = bot;
    execMcCommands(args);
}

function stopMoving({}){
    // GoalChanged Exception is thrown in the executed code
    bot.pathfinder.setGoal(null);
}

function getAllMcNames(){
    return [...Object.keys(bot.players)];
}

async function _teleport(args){
    args.bot = bot;
    args.position = new Vec3(...args.position);
    args.offset = new Vec3(...args.offset);
    if(args.teleportOffset){
        args.teleportOffset = new Vec3(...args.teleportOffset);
    }
    await teleport(args);
}

async function _setInventoryAndEquipment(args){
    args.bot = bot;
    await setInventoryAndEquipment(args);
}

async function _setBlocks(args){
    args.bot = bot;
    const blockInfoList = []
    for(const b of args.blockInfoList){
        b.position = new Vec3(...b.position);
        blockInfoList.push(b)
    }
    await setBlocks(args);
}

async function _setContainers(args){
    args.bot = bot;
    const promises = [];
    for(const c of args.containerInfoList){
        const containerInfo = cloneObj(c);
        delete containerInfo.position;
        const params = {
            bot,
            pos: new Vec3(...c.position),
            items: containerInfo,
            isRelative: args.isRelative,
            offset: args.offset,
        };
        p = setContainer(params);
        promises.push(p);
    }
    await Promise.all(promises);
}

async function _clearBox(args){
    args.bot = bot;
    await clearBox(args);
}

function updateAgentVariables(args){
    const variables = args.variables;
    for(const name in variables){
        bot[name] = loadFromJson(variables[name]);
    }
}

function _enableTransparency(args){
    args.bot = bot;
    enableTransparency(args);
}

function _disnableTransparency(args){
    args.bot = bot;
    disableTransparency(args);
}

function updateMineflayerTickRate(args){
    bot.updateTickRate(args.tickRate);
}

async function close(){
    bot.end();
}

logger.info(`${mcName}'s botWorker created`);

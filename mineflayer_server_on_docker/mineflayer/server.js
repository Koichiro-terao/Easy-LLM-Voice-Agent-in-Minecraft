const path = require('path')
const log4js = require('log4js')
const WebSocket = require('ws');

const { getFormattedDateTime, PersistentWorker } = require("./lib/utils");

const BOTWORKER_FILE = __dirname + "/lib/botWorker.js"
const LOG_DIR = "/mf_logs"
const DEFAULT_PORT = 3000;
const PORT = process.argv[2] || DEFAULT_PORT;
const WS_PING_INTERVAL_MS = 2000;
const WS_MAX_MISSED_PONGS = 2;
const wss = new WebSocket.Server({ port: PORT });

let logger = null;
let isSetupCompleted = false;

const conf = {};
const workers = {};
let closingPromise = null;

function getWorkerKey(serverId, mcName) {
    return `${serverId}\n${mcName}`;
}

function parseWorkerKey(workerKey) {
    const [serverId, mcName] = workerKey.split("\n");
    return { serverId, mcName };
}

function hasActiveWorkers() {
    return Object.keys(workers).length > 0;
}

function resetServerState(resetMessage="Server closed and reset") {
    isSetupCompleted = false;
    logger = null;
    for (const k in conf) delete conf[k];
    console.log(resetMessage);
}

function resolveMcHost(host) {
    const alias = process.env.BN_DOCKER_LOCALHOST_ALIAS;
    if (alias && (host === "localhost" || host === "127.0.0.1")) {
        return alias;
    }
    return host;
}

function buildCommandError(command, params, err) {
    const original = (err && (err.stack || err.message)) ? (err.stack || err.message) : String(err);

    if (command === "join") {
        const mcHost = params?.mcHost ?? "<unknown-host>";
        const mcPort = params?.mcPort ?? "<unknown-port>";
        const mcName = params?.mcName ?? "<unknown-name>";

        return [
            `Failed to join Minecraft server ${mcHost}:${mcPort} as \"${mcName}\".`,
            "Mineflayer could not connect or initialize the bot.",
            "Check that the Minecraft server/world is running and reachable, then retry.",
            `Original error: ${original}`,
        ].join(" ");
    }

    return `Command \"${command}\" failed. ${original}`;
}

function getErrorDetail(result) {
    if (!result) return "No response payload";
    if (typeof result === "string") return result;
    if (result.errorMsg) return result.errorMsg;
    if (result.message) return result.message;
    if (result.stack) return result.stack;
    return JSON.stringify(result);
}

async function postMessageToWorker(serverId, mcName, command, args={}, transferList=[]){
    if(!(serverId in workers)){
        throw new Error(`serverId "${serverId}" does not exist. List of serverId...${Object.keys(workers)}`);
    }
    if(!(mcName in workers[serverId])){
        throw new Error(`mcName "${mcName}" does not exist. List of mcName...${Object.keys(workers[serverId])}`);
    }

    const result = await workers[serverId][mcName].postMessage({
        "command": command,
        "args": args,
    }, transferList);
    if(result.errorMsg){
        throw new Error(result.errorMsg);
    }
    return result.data;
}

async function terminateWorker(serverId, mcName) {
    if(!(serverId in workers)){
        throw new Error(`serverId "${serverId}" does not exist. List of serverId...${Object.keys(workers)}`);
    }
    if(!(mcName in workers[serverId])){
        throw new Error(`mcName "${mcName}" does not exist. List of mcName...${Object.keys(workers[serverId])}`);
    }

    const worker = workers[serverId][mcName];
    if (worker) {
        await worker.terminate();
    }

    delete workers[serverId][mcName];
    if (Object.keys(workers[serverId]).length === 0) {
        delete workers[serverId];
    }
}

async function setup({ 
    canDigWhenMove, 
    moveTimeoutSec, 
    stuckCheckIntervalSec, 
    stuckOffsetRange, 
    consoleLogLevel="info",
}){
    if (isSetupCompleted) {
        if (logger) {
            logger.warn("setup() has already been called. Initialization is skipped to prevent duplicate setup.");
        }
        return;
    }
    
    log4js.configure({
        appenders : {
            file : {type : 'file', filename : path.join(LOG_DIR, `mineflayer_${getFormattedDateTime()}.log`)},
            console: { type: 'console' },
            consoleFilter: {
                type: 'logLevelFilter',
                appender: 'console',
                level: consoleLogLevel,
            },
        },
        categories : {
            default : {appenders : ['file', 'consoleFilter'], level : 'trace'},
        }
    });

    logger = log4js.getLogger("mineflayer");
    
    conf.canDigWhenMove = canDigWhenMove;
    conf.moveTimeoutSec = moveTimeoutSec;
    conf.stuckCheckIntervalSec = stuckCheckIntervalSec;
    conf.stuckOffsetRange = stuckOffsetRange;

    isSetupCompleted = true;
    logger.info("Mineflayer server setup completed");
}

async function join({
    mcHost,
    mcPort,
    serverId,
    mcName,
}){
    mcHost = resolveMcHost(mcHost);

    const workerData = {
        mcHost,
        mcPort,
        mcName,
        canDigWhenMove: conf.canDigWhenMove,
        moveTimeoutSec: conf.moveTimeoutSec,
        stuckCheckIntervalSec: conf.stuckCheckIntervalSec,
        stuckOffsetRange: conf.stuckOffsetRange,
        logDir: LOG_DIR,
    }

    const workerLogger = log4js.getLogger(`${mcName}`);

    const maxTrial = 5;
    for(let i = 0; i < maxTrial; i++){
        const worker = new PersistentWorker(BOTWORKER_FILE, { workerData: workerData }, mcName, workerLogger);
        if(!(serverId in workers)){
            workers[serverId] = {};
        }
        workers[serverId][mcName] = worker;
        let result;
        try{
            result = await worker.waitForSignal("bot_status", 60);
        }catch(e){
            result = e;
        }
        if(result?.data?.success){
            break;
        }
        if(i === maxTrial - 1){
            const detail = getErrorDetail(result);
            throw new Error(`Failed to create bot \"${mcName}\" on ${mcHost}:${mcPort}. ${detail}`);
        }
        logger.warn(`${getErrorDetail(result)}. Trying again...`)
        await worker.terminate();

    }

    return { serverId, mcName };
}

async function leave({serverId, mcName}){
    try {
        await postMessageToWorker(serverId, mcName, "close");
    } finally {
        await terminateWorker(serverId, mcName);
        if (!hasActiveWorkers()) {
            resetServerState("All bots left. Server reset to idle state.");
        }
    }
}
    
async function execJs({serverId, mcName, code, primitives}){
    const result = await postMessageToWorker(serverId, mcName, "execute", {code, primitives});

    return {
        success: result.success,
        errorMsg: result.errorMsg,
    };
}

async function getAllMcNames({serverId, mcName}){
    return await postMessageToWorker(serverId, mcName, "getAllMcNames", {});
}

async function stopMoving({serverId, mcName}){
    await postMessageToWorker(serverId, mcName, "stopMoving", {});
}

async function execMc({serverId, mcName, commands}){
    await postMessageToWorker(serverId, mcName, "execMcCommands", {commands})
}

async function updateAgentVariables({serverId, mcName, variables}){
    await postMessageToWorker(serverId, mcName, "updateAgentVariables", {variables});
}

async function setBlocks({serverId, mcName, blockInfoList, isRelative, offset}){
    await postMessageToWorker(serverId, mcName, "setBlocks", {blockInfoList, isRelative, offset});
}

async function setContainers({serverId, mcName, containerInfoList, isRelative, offset}){
    await postMessageToWorker(serverId, mcName, "setContainers", {containerInfoList, isRelative, offset});
}

async function teleport({serverId, adminMcName, mcName, position, pitch, yaw, offset, teleportOffset}){
    await postMessageToWorker(serverId, adminMcName, "teleport", {mcName, position, pitch, yaw, offset, teleportOffset});
}

async function setInventoryAndEquipment({serverId, adminMcName, mcName, inventory, equipment}){
    await postMessageToWorker(serverId, adminMcName, "setInventoryAndEquipment", {mcName, inventory, equipment});
}

async function updateMineflayerTickRate({serverId, mcName, tickRate}){
    await postMessageToWorker(serverId, mcName, "updateMineflayerTickRate", {tickRate});
}

async function close({}) {
    if (closingPromise) {
        return closingPromise;
    }

    closingPromise = (async () => {
        for (const serverId in workers) {
            for (const mcName in workers[serverId]) {
                try {
                    const worker = workers[serverId][mcName];
                    if (worker) {
                        // Best effort: do not block "close" on dead/unresponsive workers.
                        worker.postMessage({ command: "close", args: {} }, [], 500).catch(() => {});
                        await worker.terminate();
                    }
                } catch (e) {
                    logger?.warn(`Failed to close worker ${serverId}/${mcName}: ${e.message}`);
                }
            }
        }

        for (const k in workers) delete workers[k];
        resetServerState("Server closed and reset");
    })();

    try {
        await closingPromise;
    } finally {
        closingPromise = null;
    }
}


wss.on('connection', (ws) => {
  let pingCheckId = null;
  let missedPongs = 0;
  let closedHandled = false;
  const ownedWorkers = new Set();

  ws.isAlive = true;
  ws.on('pong', () => {
    ws.isAlive = true;
    missedPongs = 0;
  });

  const onSocketClosed = async () => {
    if (closedHandled) return;
    closedHandled = true;
    if (pingCheckId) {
      clearInterval(pingCheckId);
      pingCheckId = null;
    }
    try {
      for (const workerKey of [...ownedWorkers]) {
        const { serverId, mcName } = parseWorkerKey(workerKey);
        try {
          await leave({ serverId, mcName });
        } catch (e) {
          if (logger) {
            logger.warn(`Failed to clean up worker ${serverId}/${mcName} on websocket close: ${e.message}`);
          } else {
            console.warn(`Failed to clean up worker ${serverId}/${mcName} on websocket close: ${e.message}`);
          }
        } finally {
          ownedWorkers.delete(workerKey);
        }
      }
    } catch (e) {
      if (logger) {
        logger.warn(`Cleanup on websocket close failed: ${e.message}`);
      } else {
        console.warn(`Cleanup on websocket close failed: ${e.message}`);
      }
    }
  };

  ws.on('message', async (message) => {
    let payload;
    try {
        payload = JSON.parse(message);
    } catch (e) {
        const errorMsg = `Invalid JSON message: ${e.message}`;
        if (logger) logger.warn(errorMsg);
        ws.send(JSON.stringify({ type: "response", messageId: null, data: { errorMsg } }));
        return;
    }

    const {messageId, command, params} = payload;

    if (!isSetupCompleted && command !== 'setup') {
        const errorMsg = 'Setup must be completed before making other requests.'
        if (logger) logger.warn(errorMsg);

        const msg = {type: "response", messageId, data: {errorMsg}}
        ws.send(JSON.stringify(msg));
        return;
    }

    let response = null;
    try {
        switch(command){
            case "setup": await setup(params); break;
        
            case "execJs": response = await execJs(params); break;
            case "getAllMcNames": response = await getAllMcNames(params); break;
            case "stopMoving": await stopMoving(params); break;
            case "execMc": await execMc(params); break;

            case "join":
                response = await join(params);
                ownedWorkers.add(getWorkerKey(params.serverId, params.mcName));
                break;
            case "leave":
                await leave(params);
                ownedWorkers.delete(getWorkerKey(params.serverId, params.mcName));
                break;

            case "updateAgentVariables": await updateAgentVariables(params); break;

            case "setBlocks": await setBlocks(params); break;
            case "setContainers": await setContainers(params); break;
            case "teleport": await teleport(params); break;
            case "setInventoryAndEquipment": await setInventoryAndEquipment(params); break;
            case "updateMineflayerTickRate": await updateMineflayerTickRate(params); break;

            case "close": await close(params);  break;
            default: throw new Error(`Unknown command "${command}"`);
        }

        if(response === undefined){
            throw new Error(`response undefined. command=${command}`);
        }

        const msg = {type: "response", messageId, data: response}
        ws.send(JSON.stringify(msg));
    } catch (e) {
        const errorMsg = buildCommandError(command, params, e);
        if (logger) {
            logger.error(errorMsg);
        } else {
            console.error(errorMsg);
        }
        const msg = {type: "response", messageId, data: {errorMsg}};
        ws.send(JSON.stringify(msg));
        await close({});
    }
  });

  ws.on('close', onSocketClosed);
  ws.on('error', onSocketClosed);

  pingCheckId = setInterval(() => {
    if (ws.readyState !== WebSocket.OPEN) return;
    if (!ws.isAlive) {
      missedPongs += 1;
      if (missedPongs >= WS_MAX_MISSED_PONGS) {
        ws.terminate();
      }
      return;
    }
    ws.isAlive = false;
    try {
      ws.ping();
    } catch (e) {
      ws.terminate();
    }
  }, WS_PING_INTERVAL_MS)
});

console.log(`Server started on port ${PORT}`);

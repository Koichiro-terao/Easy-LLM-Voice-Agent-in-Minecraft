const fs = require('fs');
const { Worker } = require('worker_threads');
const Vec3 = require('vec3');
const log4js = require('log4js');


class WorkerLogger{
    constructor(parentPort){
        this.parentPort = parentPort;
    }

    postLog(level, msg){
        this.parentPort.postMessage({type: "log", result: {level, msg}});
    }

    trace(msg){
        this.postLog("trace", msg);
    }

    debug(msg){
        this.postLog("debug", msg);
    }

    info(msg){
        this.postLog("info", msg);
    }

    warn(msg){
        this.postLog("warn", msg);
    }

    error(msg){
        this.postLog("error", msg);
    }

    fatal(msg){
        this.postLog("fatal", msg);
    }
}


class LoggingWrapper {
    constructor(target, logger) {
        return new Proxy(target, {
            get(obj, prop) {
                const original = obj[prop];
                if (typeof original === 'function') {
                    return function (...args) {
                        logger.debug(`Calling: ${prop}()`);
                        const result = original.apply(obj, args);

                        if (result instanceof Promise) {
                            return result
                                .then(res => {
                                    logger.debug(`Finished: ${prop}()`);
                                    return res;
                                })
                        }

                        logger.debug(`Finished: ${prop}()`);
                        return result;
                    };
                }
                return original;
            }
        });
    }
}

class PersistentWorker {
    constructor(workerFile, args={}, mcName, logger=null) {
        this.worker = new Worker(workerFile, args);
        this.mcName = mcName;

        this.callbacks = new Map();
        this.onExitBound = this.onExit.bind(this);
        this.nextId = 1;
        this.initialized = false;

        if(logger){
            this.parentLogger = logger;
            this.workerLogger = log4js.getLogger(`${logger.category}.botWorker`)
        } else{
            this.parentLogger = log4js.getLogger()
            this.workerLogger = log4js.getLogger()
        }

        this.worker.on('message', ({ type, id, result }) => {
            //this.parentLogger.debug(`message from worker! type=${type}, id=${id}, result=${JSON.stringify(result)}`)
            let msg;
            switch(type){
                case "log":
                    const level = result.level;
                    msg = result.msg;
                    switch(level){
                        case "trace": this.workerLogger.trace(msg); break;
                        case "debug": this.workerLogger.debug(msg); break;
                        case "info":  this.workerLogger.info(msg);  break;
                        case "warn":  this.workerLogger.warn(msg);  break;
                        case "error": this.workerLogger.error(msg); break;
                        case "fatal": this.workerLogger.fatal(msg); break;
                        default: throw Error(`Invalid logger level ${level}`);
                    }
                    break;

                case "signal": // fall through
                case "response":
                    this.parentLogger.debug(`registered callbacks=[${[...this.callbacks.keys()]}]`)
                    const callback = this.callbacks.get(id);
                    if (callback) {
                        callback.resolve(result);
                        this.callbacks.delete(id);
                    }
                    break;

                default: throw new Error(`Invalid type ${type}.`)
            }
        });
        this.worker.on('error', (err) => {
            const msg = `Uncaught Exception occurred in worker of ${this.mcName}: ${err.stack}`;
            this.workerLogger.error(msg);
            for (const [id, callback] of this.callbacks.entries()) {
                try {
                    callback.reject(new Error(msg));
                } catch (e) {
                    ;
                }
                this.callbacks.delete(id);
            }
        });

        this.worker.on('exit', this.onExitBound);
    }

    postMessage(data, transferList=[], timeout = 0) {
        return new Promise((resolve, reject) => {
            const id = this.nextId++;
            const timeoutId = timeout > 0 ? setTimeout(() => {
                if (this.callbacks.has(id)) {
                    this.callbacks.get(id).reject(new Error("Timeout exceeded"));
                    this.callbacks.delete(id);
                }
            }, timeout) : null;

            this.callbacks.set(id, {
                resolve: (result) => {
                    if (timeoutId) clearTimeout(timeoutId);
                    resolve(result);
                },
                reject: (error) => {
                    if (timeoutId) clearTimeout(timeoutId);
                    reject(error);
                },
            });

            this.worker.postMessage({ id, data }, transferList);
        });
    }

    waitForSignal(signal, timeout = 0){
        return new Promise((resolve, reject) => {
            const timeoutId = timeout > 0 ? setTimeout(() => {
                if (this.callbacks.has(signal)) {
                    this.callbacks.get(signal).reject(new Error("Timeout exceeded"));
                    this.callbacks.delete(signal);
                }
            }, timeout*1000) : null;

            this.callbacks.set(signal, {
                resolve: (result) => {
                    if (timeoutId) clearTimeout(timeoutId);
                    resolve(result);
                    this.parentLogger.debug(`signal "${signal}" resolved`)
                },
                reject: (error) => {
                    if (timeoutId) clearTimeout(timeoutId);
                    const errorText =
                        (error && (error.stack || error.message))
                            ? (error.stack || error.message)
                            : String(error);
                    reject({
                        data: { success: false },
                        errorMsg: errorText
                    });
                    this.parentLogger.debug(`signal "${signal}" rejected`)
                },
            });
        });
    }

    onExit(error) {
        const msg = `Worker terminated. Error code : ${error}`;
        if(this.initialized){
            this.workerLogger.error(msg);
        } else {
            this.workerLogger.warn(msg);
            for(const p of this.callbacks.values()){
                try{
                    p.reject(new Error(msg));
                } catch(e){
                    ;
                }
            }
        }
    }

    async terminate() {
        this.worker.removeListener('exit', this.onExitBound);
        await this.worker.terminate();
        this.worker.on('exit', this.onExitBound);
    }
}

function roundVec3(vec, decimalPlaces) {
    const factor = Math.pow(10, decimalPlaces);
    return new Vec3(
      Math.round(vec.x * factor) / factor,
      Math.round(vec.y * factor) / factor,
      Math.round(vec.z * factor) / factor
    );
}

function hasVec3NaN(vec) {
    return isNaN(vec.x) || isNaN(vec.y) || isNaN(vec.z);
}

function vecArrToStr(arr){
    return `(${arr[0]},${arr[1]},${arr[2]})`;
}

function dumpToJson(obj, {argList=[], sortedMapRange=null, ignoreKeys=[],}={}){
    return JSON.stringify(obj, (key, value) => {
        if (ignoreKeys.includes(key)) {
            return undefined;
        }

        if(isVec3(value)){
            return { __Vec3__: [value.x, value.y, value.z] };
        }
        return value;
    }, ...argList);
}

function loadFromJson(jsonStr, {setStateId=false, Block=null}={}){
    return JSON.parse(jsonStr, (key, value) => {
        if (value?.__Vec3__) {
            const [x, y, z] = value.__Vec3__;
            return new Vec3(x, y, z);
        }
        return value;
    });
}

function cloneObj(obj){
    return loadFromJson(dumpToJson(obj));
}

function getChunkCornersInBox({envBox, isRelative=true, offset=null}) {
    const CHUNK_SIZE = 16;

    let absEnvBox;
    if(isRelative){
        if(!offset){ 
            throw new Error(`Specify offset when isRelative is set true.`);
        }
        absEnvBox = [envBox[0].plus(offset), envBox[1].plus(offset)];
    } else {
        absEnvBox = envBox;
    }

    let [minCoords, maxCoords] = absEnvBox;

    minCoords = minCoords.toArray();
    maxCoords = maxCoords.toArray();

    const [minX, minY, minZ] = minCoords;
    const [maxX, maxY, maxZ] = maxCoords;

    const minChunkX = Math.floor(minX / CHUNK_SIZE);
    const maxChunkX = Math.floor(maxX / CHUNK_SIZE);
    const minChunkZ = Math.floor(minZ / CHUNK_SIZE);
    const maxChunkZ = Math.floor(maxZ / CHUNK_SIZE);

    const chunks = [];
    for (let chunkX = minChunkX; chunkX <= maxChunkX; chunkX++) {
        for (let chunkZ = minChunkZ; chunkZ <= maxChunkZ; chunkZ++) {
            const chunkMinX = chunkX * CHUNK_SIZE;
            const chunkMaxX = chunkMinX + CHUNK_SIZE - 1;
            const chunkMinZ = chunkZ * CHUNK_SIZE;
            const chunkMaxZ = chunkMinZ + CHUNK_SIZE - 1;

            chunks.push({
                minCorner: new Vec3(Math.max(chunkMinX, minX), minY, Math.max(chunkMinZ, minZ)),
                maxCorner: new Vec3(Math.min(chunkMaxX, maxX), maxY, Math.min(chunkMaxZ, maxZ)),
            });
        }
    }

    return chunks;
}

function isVec3(obj){
    if(!obj) return false;
    return (obj.constructor?.name === "Vec3")
}

/* for agentName, mcName and branchName */
function containsInvalidCharacters(str) {
    const regex = /[^a-zA-Z0-9_]/;
    return regex.test(str);
}

function getFormattedDateTime() {
    const now = new Date();

    const year = now.getFullYear();
    const month = String(now.getMonth() + 1).padStart(2, '0');
    const day = String(now.getDate()).padStart(2, '0');

    const hours = String(now.getHours()).padStart(2, '0');
    const minutes = String(now.getMinutes()).padStart(2, '0');
    const seconds = String(now.getSeconds()).padStart(2, '0');

    return `${year}${month}${day}_${hours}${minutes}${seconds}`;
}

function handleError(err, code, programs) {
    let stack = err.stack;
    if (!stack) {
        return err;
    }
    console.log(stack);
    const final_line = stack.split("\n")[1];
    const regex = /<anonymous>:(\d+):\d+\)/;

    const programs_length = programs.split("\n").length;
    let match_line = null;
    for (const line of stack.split("\n")) {
        const match = regex.exec(line);
        if (match) {
            const line_num = parseInt(match[1]);
            if (line_num >= programs_length) {
                match_line = line_num - programs_length;
                break;
            }
        }
    }
    if (!match_line) {
        return err.message;
    }
    let f_line = final_line.match(
        /\((?<file>.*):(?<line>\d+):(?<pos>\d+)\)/
    );
    if (f_line && f_line.groups && fs.existsSync(f_line.groups.file)) {
        const { file, line, pos } = f_line.groups;
        const f = fs.readFileSync(file, "utf8").split("\n");
        // let filename = file.match(/(?<=node_modules\\)(.*)/)[1];
        let source = file + `:${line}\n${f[line - 1].trim()}\n `;

        const code_source =
            "at " +
            code.split("\n")[match_line - 1].trim() +
            " in your code";
        return source + err.message + "\n" + code_source;
    } else if (
        f_line &&
        f_line.groups &&
        f_line.groups.file.includes("<anonymous>")
    ) {
        const { file, line, pos } = f_line.groups;
        let source =
            "Your code" +
            `:${match_line}\n${code.split("\n")[match_line - 1].trim()}\n `;
        let code_source = "";
        if (line < programs_length) {
            source =
                "In your program code: " +
                programs.split("\n")[line - 1].trim() +
                "\n";
            code_source = `at line ${match_line}:${code
                .split("\n")
                [match_line - 1].trim()} in your code`;
        }
        return source + err.message + "\n" + code_source;
    }
    return err.message;
}

function isErrorMessage(msg){
    const patterns = [
        "<--[HERE]",
        "Could not set the block"
    ]
    for(const p of patterns){
        if(msg.includes(p)){
            return true;
        }
    }
    return false;
}

const sleep_ms = (time) => new Promise((resolve) => setTimeout(resolve, time));

module.exports = { WorkerLogger, LoggingWrapper, PersistentWorker, roundVec3, hasVec3NaN, vecArrToStr, dumpToJson, loadFromJson, cloneObj, getChunkCornersInBox, isVec3, containsInvalidCharacters, getFormattedDateTime, handleError, isErrorMessage, sleep_ms }
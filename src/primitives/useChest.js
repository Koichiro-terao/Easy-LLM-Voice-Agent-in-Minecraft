async function getItemFromChest(bot, chestPosition, itemsToGet) {
    // return if chestPosition is not Vec3
    if (!(chestPosition instanceof Vec3)) {
        throw new Error("chestPosition for getItemFromChest must be a Vec3");
    }
    if (!checkValidPosition(chestPosition, bot)){
        throw new Error(`chestPosition (${chestPosition}) for getItemFromChest is out of environment (${bot.envBox}). Perhaps you forgot to account for the offset.`);
    }
    await moveToChest(bot, chestPosition);
    const chestBlock = bot.blockAt(chestPosition);
    const chest = await bot.openContainer(chestBlock);
    const initialItems = getInventoryDict()
    let gotItems = {};
    for (const name in itemsToGet) {
        const itemByName = mcData.itemsByName[name];
        if (!itemByName) {
            await think(bot, `No item named ${name}`);
            continue;
        }

        const item = chest.findContainerItem(itemByName.id);
        if (!item) {
            await think(bot, `I don't see ${name} in this chest`);
            continue;
        }
        try {
            await chest.withdraw(item.type, null, itemsToGet[name]);
            gotItems[name] = itemsToGet[name];
        } catch (err) {
            await think(bot, `Not enough ${name} in chest.`);
        }
    }
    await bot.waitForTicks(10)
    const chestItems = await closeChest(bot, chestBlock);
    
    await restoreInitialInventory(bot, initialItems)
    for(const name in gotItems){
        bot.chat(`/give @s ${name} ${gotItems[name]}`)
        await bot.waitForTicks(1)
    }
}

async function depositItemIntoChest(bot, chestPosition, itemsToDeposit) {
    // return if chestPosition is not Vec3
    if (!(chestPosition instanceof Vec3)) {
        throw new Error(
            "chestPosition for depositItemIntoChest must be a Vec3"
        );
    }
    if (!checkValidPosition(chestPosition, bot)){
        throw new Error(`chestPosition (${chestPosition}) for depositItemIntoChest is out of environment (envBox=${bot.envBox}, offset=${bot.offsetVec3}). Perhaps you forgot to account for the offset.`);
    }
    await moveToChest(bot, chestPosition);
    const chestBlock = bot.blockAt(chestPosition);
    const chest = await bot.openContainer(chestBlock);
    const initialItems = getInventoryDict()
    let depositedItems = {};
    for (const name in itemsToDeposit) {
        const itemByName = mcData.itemsByName[name];
        if (!itemByName) {
            await think(bot, `No item named ${name}`);
            continue;
        }
        const item = bot.inventory.findInventoryItem(itemByName.id);
        if (!item) {
            await think(bot, `No ${name} in inventory`);
            continue;
        }
        try {
            await chest.deposit(item.type, null, itemsToDeposit[name]);
            depositedItems[name] = itemsToDeposit[name];
        } catch (err) {
            await think(bot, `Not enough ${name} in inventory.`);
        }
    }
    await bot.waitForTicks(10)
    const chestItems = await closeChest(bot, chestBlock);

    await restoreInitialInventory(bot, initialItems)
    for(const name in depositedItems){
        bot.chat(`/clear @s ${name} ${depositedItems[name]}`)
        await bot.waitForTicks(1)
    }
}

async function moveToChest(bot, chestPosition) {
    if (!(chestPosition instanceof Vec3)) {
        throw new Error(
            "chestPosition for moveToChest must be a Vec3"
        );
    }
    if (!checkValidPosition(chestPosition, bot)){
        throw new Error("chestPosition for moveToChest is out of environment. Perhaps you forgot to account for the offset.");
    }
    const chestBlock = bot.blockAt(chestPosition);
    await bot.pathfinder.goto(
        new GoalLookAtBlock(chestBlock.position, bot.world, {})
    );
    return chestBlock;
}

async function listItemsInChest(bot, chestBlock) {
    const chest = await bot.openContainer(chestBlock);
    const items = chest.containerItems();
    const itemDict = items.reduce((acc, obj) => {
        if (acc[obj.name]) {
            acc[obj.name] += obj.count;
        } else {
            acc[obj.name] = obj.count;
        }
        return acc;
    }, {});
    return itemDict;
}

async function closeChest(bot, chestBlock) {
    let itemDict = {};
    try {
        itemDict = await listItemsInChest(bot, chestBlock);
        const chest = await bot.openContainer(chestBlock);
        await chest.close();
    } catch (err) {
        await bot.closeWindow(chestBlock);
    }
    await bot.waitForTicks(5) // Prevent chests from remaining open
    return itemDict;
}

function itemByName(items, name) {
    for (let i = 0; i < items.length; ++i) {
        const item = items[i];
        if (item && item.name === name) return item;
    }
    return null;
}

function getInventoryDict(){
    let inventoryDict = {}
    bot.inventory.items().forEach(item => {
        if(!inventoryDict[item.name]){
            inventoryDict[item.name] = 0
        }
        inventoryDict[item.name] += item.count
    });
    return inventoryDict
}

function itemToObs(item) {
    if (!item) return null;
    return item.name;
}

async function restoreInitialInventory(bot, initialItems) {
    for (let i = 0; i < 36; i++) {
        bot.chat(`/item replace entity @s container.${i} with minecraft:fishing_rod[minecraft:custom_name='{"text":"dummy item"}'] 36`);
    }
    await bot.waitForTicks(1);
    
    for (let i = 0; i < 36; i++) {
        bot.chat(`/item replace entity @s container.${i} with air`);
    }
    await bot.waitForTicks(1);

    for (const name in initialItems) {
        const count = initialItems[name];
        bot.chat(`/give @s ${name} ${count}`);
        await bot.waitForTicks(1);
    }
}

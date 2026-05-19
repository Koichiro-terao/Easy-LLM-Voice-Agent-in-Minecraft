async function craftItem(bot, name, craftingTablePos, count = 1) {
    // return if name is not string
    if (typeof name !== "string") {
        throw new Error("name for craftItem must be a string");
    }
    // return if count is not number
    if (typeof count !== "number") {
        throw new Error("count for craftItem must be a number");
    }
    if (!(craftingTablePos instanceof Vec3)) {
        throw new Error("craftingTablePos for craftItem must be a Vec3");
    }
    if (!checkValidPosition(craftingTablePos, bot)){
        throw new Error("craftingTablePos for craftItem is out of environment. Perhaps you forgot to account for the offset.");
    }
    const itemByName = mcData.itemsByName[name];
    if (!itemByName) {
        throw new Error(`No item named ${name}`);
    }
    
    const craftingTable = bot.blockAt(craftingTablePos);
    if (!craftingTable) {
        await think(bot, "Craft without a crafting table");
    } else {
        await bot.pathfinder.goto(
            new GoalLookAtBlock(craftingTable.position, bot.world)
        );
    }

    let craftedCount = 0;
    const recipe = bot.recipesFor(itemByName.id, null, 1, craftingTable)[0];
    if (recipe) {
        await think(bot, `I can make ${name}`);
        await bot.waitForTicks(1);
        try {
            for(let i = 0; i < count; i++){
                await bot.craft(recipe, 1, craftingTable);
                await bot.waitForTicks(1);
                craftedCount++;
            }
        } catch (err) {
            // shortage of ingredients
            ;
        } 

        if(craftedCount){
            await think(bot, `I did the recipe for ${name} ${craftedCount} times`);
            await bot.waitForTicks(1);
        } else {
            await think(bot, `I cannot do the recipe for ${name}`);
        }
    } else {
        await failedCraftFeedback(bot, name, itemByName, craftingTable);
    }
}

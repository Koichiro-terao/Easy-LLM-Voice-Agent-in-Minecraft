async function goToPosition(bot, position, dist = 2) {
    if (!(position instanceof Vec3)) {
        throw new Error(`position for goToPosition must be a Vec3`);
    }

    if (!checkValidPosition(position, bot)) {
        throw new Error("position for goToPosition is out of environment. Perhaps you forgot to account for the offset.");
    }

    try {
        await bot.pathfinder.goto(
            new GoalNear(position.x, position.y, position.z, dist)
        );
    } catch (err) {
        if (err.message && err.message.includes('goal was changed')) {
            return; // moving is stopped
        }
        throw err;
    }
}
async function goToPosition(bot, position, dist=2) {
    if (!(position instanceof Vec3)) {
        throw new Error(`position for goToPosition must be a Vec3`);
    }
    if (!checkValidPosition(position, bot)){
        throw new Error("position for goToPosition is out of environment. Perhaps you forgot to account for the offset.");
    }
    
    let error = null
    for(let d = 0; d <= dist; d+=1){
        done = false
        try{
            await bot.pathfinder.goto(
                new GoalNear(position.x, position.y, position.z, d)
            );
            done = true
        }catch(err){
            error = err;
            if (err.message.includes('goal was changed')) { // when moving is stopped
                break;
            }
        }
        
        if(done){
            break
        }
    }

    if(!done){
        throw error
    }
}
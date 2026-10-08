const assert=require('node:assert/strict');
const Voice=require('../utils/voice');
function fixture(){let time=0,tick,stops=0;const events={},states=[],files=[],errors=[];const manager={onStart:f=>events.start=f,onStop:f=>events.stop=f,onError:f=>events.error=f,onInterruptionBegin:f=>events.interruption=f,start:options=>{manager.options=options;},stop:()=>stops++};const voice=new Voice({manager,onState:s=>states.push(s),onComplete:f=>files.push(f),onError:e=>errors.push(e),now:()=>time,setTimer:f=>{tick=f;return 1;},clearTimer:()=>{tick=null;}});return {voice,manager,events,states,files,errors,time:t=>time=t,tick:()=>tick&&tick(),stops:()=>stops};}
const file={tempFilePath:'test.mp3',fileSize:10000,duration:2400};
{
 const f=fixture();f.voice.start();assert.equal(f.voice.phase,'starting');f.voice.start();f.events.start();f.time(2500);f.tick();assert.equal(f.states.at(-1).seconds,2);f.voice.finish();assert.equal(f.stops(),1);f.events.stop(file);assert.equal(f.files.length,1);assert.equal(f.voice.phase,'idle');assert.equal(f.manager.options.duration,119000);
}
for(const action of ['cancel','dispose']){const f=fixture();f.voice.start();f.events.start();f.voice[action]();f.events.stop(file);assert.equal(f.files.length,0);assert.equal(f.errors.length,0);}
{
 const f=fixture();f.voice.start();f.voice.cancel();f.events.start();f.events.stop(file);assert.equal(f.files.length,0);
}
{
 const f=fixture();f.voice.start();f.events.start();f.events.interruption();f.events.stop(file);assert.equal(f.files.length,0);
}
{
 const f=fixture();f.voice.start();f.events.start();f.time(119000);f.tick();assert.equal(f.stops(),1);f.events.stop(file);assert.equal(f.files.length,1);
}
{
 const f=fixture();f.voice.start();f.events.start();f.events.stop({...file,duration:100});assert.equal(f.files.length,0);assert.equal(f.errors.length,1);f.voice.start();f.events.error();assert.equal(f.voice.phase,'idle');assert.equal(f.errors.length,2);
}
console.log('录音流程检查通过：完成、取消、离开页面、启动时取消、系统中断、限时及异常。');

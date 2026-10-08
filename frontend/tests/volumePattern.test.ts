import { test } from 'node:test';
import assert from 'node:assert/strict';
import { eventAt, eventsAt } from '../src/domain/volumePattern';
const event = {id:'e',time:'2026-08-01T15:00:00+08:00',state:'confirmed',label:'确认条件满足',assessed_at:'2026-08-03T15:00:00+08:00',trace:[
 {time:'2026-08-01T15:00:00+08:00',state:'event',label:'发现放量上影'},
 {time:'2026-08-02T15:00:00+08:00',state:'breakout_pending',label:'突破待确认'},
 {time:'2026-08-03T15:00:00+08:00',state:'confirmed',label:'确认条件满足'}]};
test('replay excludes future states, labels and trace without changing the response',()=>{
 const current=eventAt(event,'2026-08-01T15:00:00+08:00');
 assert.equal(current.state,'event');assert.equal(current.label,'发现放量上影');assert.equal(current.trace.length,1);
 assert.equal(current.assessed_at,event.time);assert.equal(event.state,'confirmed');assert.equal(event.trace.length,3);
 assert.equal(eventAt(event,'2026-08-02T07:00:00Z').state,'breakout_pending');
 assert.equal(eventsAt([event],'2026-07-30T15:00:00+08:00').length,0);
});

const assert=require('node:assert/strict');
const fs=require('node:fs');
let store={};let responses=[];
global.wx={getStorageSync:k=>store[k],setStorageSync:(k,v)=>store[k]=v,removeStorageSync:k=>delete store[k],request:options=>{responses.push(options);return {};}};
const api=require('../utils/api');
async function run(){
  const expected=[{type:'assistant',payload:{text:'中文分块 🟢'}},{type:'done',payload:{}}];
  const wire=Buffer.from(expected.map(e=>'data: '+JSON.stringify(e)+'\n\n').join(''));
  for(let split=1;split<wire.length;split++){
    const got=[];const reader=new api.EventReader(e=>got.push(e));
    for(let i=0;i<wire.length;i+=split){const b=wire.subarray(i,i+split);reader.push(b.buffer.slice(b.byteOffset,b.byteOffset+b.byteLength));}
    reader.push(null,true);assert.deepEqual(got,expected);
  }
  const pending=api.login('test-only');responses.pop().success({statusCode:200,data:{ok:true},cookies:['trading_session=temporary; HttpOnly; Path=/']});await pending;
  assert.equal(api.hasSession(),true);
  const q=api.request('/api/models');const r=responses.pop();assert.equal(r.header.Cookie,'trading_session=temporary');r.success({statusCode:200,data:{models:[]}});await q;
  const expired=api.request('/api/models');responses.pop().success({statusCode:401,data:{detail:'Unauthorized'}});await assert.rejects(expired,/过期/);assert.equal(api.hasSession(),false);
  const cfg=JSON.parse(fs.readFileSync(__dirname+'/../project.config.json','utf8'));assert.equal(cfg.appid,'wxa98b45223e9819c2');assert.equal(cfg.setting.urlCheck,true);
  console.log('小程序协议检查通过：任意中文分块、登录凭证、过期处理、AppID。');
}
run().catch(e=>{console.error(e);process.exitCode=1;});

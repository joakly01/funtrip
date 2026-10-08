const assert=require('node:assert/strict');
let definition;global.Page=p=>definition=p;
const api=require('../utils/api');
api.hasSession=()=>true;
let uploadCalls=0;
api.upload=async()=>{uploadCalls++;if(uploadCalls===1)throw new Error('网络中断');return {id:'test-audio',kind:'audio',transcript:'转写测试',name:'test.mp3'};};
require('../pages/home/index');
function page(){return {...definition,data:JSON.parse(JSON.stringify(definition.data)),setData(values){Object.assign(this.data,values);}};}
async function run(){
 const p=page();p.data.text='原有草稿';p.pendingVoice={tempFilePath:'test.mp3'};p.data.voiceRetry=true;
 await p.transcribeVoice();assert.equal(p.data.voiceRetry,true);assert.equal(p.data.uploading,false);assert.equal(p.data.text,'原有草稿');assert.ok(p.pendingVoice);
 await p.transcribeVoice();assert.equal(p.data.text,'原有草稿\n转写测试');assert.equal(p.data.attachments.length,1);assert.equal(p.pendingVoice,null);assert.equal(p.data.voiceRetry,false);assert.equal(p.data.messages.length,0);
 p.data.text='用户核对后的文字';let body;
 p.data.models=[{id:'test',vision:false}];api.stream=async(b,emit)=>{body=b;emit({type:'done',payload:{}});};
 await p.send();assert.equal(body.text,'用户核对后的文字');assert.deepEqual(body.attachment_ids,['test-audio']);
 const denied=page();api.request=async()=>({voice_ready:true});global.wx={getPrivacySetting:o=>o.success({needAuthorization:false}),getSetting:o=>o.success({authSetting:{'scope.record':false}})};
 await denied.voice();assert.equal(denied.data.micDenied,true);assert.equal(denied.data.recording,false);assert.equal(denied.data.voicePending,false);
 let cancelled=0;denied.voiceController={cancel:()=>cancelled++};denied.onHide();assert.equal(cancelled,1);
 const consent=page();global.wx.getPrivacySetting=o=>o.success({needAuthorization:true,privacyContractName:'隐私指引'});const wait=consent.privacy();await Promise.resolve();assert.equal(consent.data.showPrivacy,true);consent.agreePrivacy();await wait;assert.equal(consent.data.showPrivacy,false);
 console.log('录音页面检查通过：失败保留、重试、草稿合并、核对后发送、权限拒绝、页面离开和隐私同意。');
}
run().catch(e=>{console.error(e);process.exitCode=1;});

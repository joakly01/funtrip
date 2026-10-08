const { baseURL } = require('../config');
const KEY = 'trading-session';
function headers() { return { Cookie: wx.getStorageSync(KEY) || '' }; }
function forget() { wx.removeStorageSync(KEY); }
function checked(res) {
  if (res.statusCode === 401) { forget(); throw new Error('登录已过期，请重新登录'); }
  if (res.statusCode < 200 || res.statusCode >= 300) throw new Error((res.data && res.data.detail) || '服务暂时无法处理，请稍后重试');
  return res.data;
}
function request(path, data, method = 'GET') {
  return new Promise((resolve, reject) => wx.request({ url: baseURL + path, data, method,
    header: headers(), timeout: 30000,
    success(res) { try { resolve(checked(res)); } catch (e) { reject(e); } },
    fail() { reject(new Error('暂时无法连接，请检查网络或服务域名配置')); }
  }));
}
async function login(password) {
  const res = await new Promise((resolve, reject) => wx.request({ url: baseURL + '/api/login', method: 'POST', data: { password },
    success: resolve, fail: () => reject(new Error('暂时无法连接，域名和 HTTPS 可能尚未就绪')) }));
  checked(res);
  const values = res.cookies || [res.header && (res.header['Set-Cookie'] || res.header['set-cookie']) || ''];
  const match = values.join(';').match(/(?:^|[;,]\s*)trading_session=([^;\s,]+)/);
  if (!match) throw new Error('未收到登录凭证，请检查服务配置');
  wx.setStorageSync(KEY, 'trading_session=' + match[1]);
}
function upload(filePath) {
  return new Promise((resolve,reject) => wx.uploadFile({url:baseURL+'/api/attachments',filePath,name:'file',header:headers(),timeout:180000,
    success(res) { try { res.data=JSON.parse(res.data);resolve(checked(res)); } catch(e){reject(e);} },
    fail(){reject(new Error('附件上传失败，请重试'));}
  }));
}
// 保留跨网络块的 UTF-8 字节，避免中文和 emoji 被截断。
class EventReader {
  constructor(emit) { this.emit=emit; this.bytes=[]; this.text=''; }
  push(buffer, final=false) {
    this.bytes=this.bytes.concat(Array.from(new Uint8Array(buffer || new ArrayBuffer(0))));
    let i=0, out='';
    while(i<this.bytes.length) {
      const b=this.bytes[i]; let n=b<128?1:b>=194&&b<224?2:b>=224&&b<240?3:b>=240&&b<245?4:1;
      if(i+n>this.bytes.length && !final) break;
      let cp=n===1?(b<128?b:65533):(b&((1<<(7-n))-1));
      let valid=i+n<=this.bytes.length;
      for(let j=1;j<n&&valid;j++){const c=this.bytes[i+j];if((c&192)!==128)valid=false;else cp=(cp<<6)|(c&63);}
      if(!valid || (n===2&&cp<128)||(n===3&&cp<2048)||(n===4&&cp<65536)||cp>1114111||(cp>=55296&&cp<=57343)){cp=65533;n=1;}
      out+=String.fromCodePoint(cp);i+=n;
    }
    this.bytes=this.bytes.slice(i);this.feed(out);
  }
  feed(text) {
    this.text+=text.replace(/\r\n/g,'\n');
    let end;
    while((end=this.text.indexOf('\n\n'))>=0){
      const block=this.text.slice(0,end);this.text=this.text.slice(end+2);
      const data=block.split('\n').filter(x=>x.startsWith('data:')).map(x=>x.slice(5).trimStart()).join('\n');
      if(data) this.emit(JSON.parse(data));
    }
  }
}
function stream(body, emit) {
  return new Promise((resolve,reject) => {
    let received=false, parseError=null;
    const reader=new EventReader(emit);
    const task=wx.request({url:baseURL+'/api/router/ask',method:'POST',data:body,header:headers(),enableChunked:true,timeout:300000,
      success(res){try{checked(res);if(parseError)throw parseError;if(!received&&typeof res.data==='string')reader.feed(res.data);reader.push(null,true);resolve();}catch(e){reject(e);}},
      fail(){reject(new Error('连接中断。请查看对话历史确认结果，避免重复记录'));}
    });
    if(task.onChunkReceived) task.onChunkReceived(res=>{received=true;try{reader.push(res.data);}catch(e){parseError=e;}});
  });
}
module.exports={request,login,upload,stream,EventReader,forget,hasSession:()=>!!wx.getStorageSync(KEY)};

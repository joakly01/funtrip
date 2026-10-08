const api=require('../../utils/api');
const VoiceRecorder=require('../../utils/voice');
function id(){return 'mini_'+Date.now()+'_'+Math.random().toString(36).slice(2,10);}
const kinds={plan:'交易计划',trade:'交易记录',position:'持仓记录',review:'复盘',emotion:'心态记录',observation:'市场观察',note:'随手笔记',learning:'学习笔记',hypothesis:'候选认识',evolution:'体系演变',profile:'个人背景',proposal:'修改提案',strategy:'交易规则',research:'研究资料',analysis:'分析记录'};
const layers=[['dao','道','方向与认识'],['fa','法','规则与计划'],['shu','术','方法与实践'],['qi','器','资料与工具']];
Page({
  data:{logged:false,password:'',tab:'chat',text:'',messages:[],attachments:[],busy:false,uploading:false,recording:false,voicePhase:'idle',voiceSeconds:0,voiceTime:'00:00',voicePending:false,voiceRetry:false,micDenied:false,showPrivacy:false,models:[],modelIndex:0,sessionId:'',query:'',group:'all',records:[],total:0,page:1,layers:[],rules:[],detail:null,error:'',status:'',remember:true},
  onLoad(){this.setData({logged:api.hasSession(),sessionId:id()});if(this.data.logged)this.initialize();},
  onShow(){this.openingPrivacy=false;},
  onHide(){if(this.openingPrivacy)return;this.voiceVersion=(this.voiceVersion||0)+1;if(this.voiceController)this.voiceController.cancel();this.disagreePrivacy();},
  onUnload(){this.disposed=true;this.voiceVersion=(this.voiceVersion||0)+1;if(this.voiceController)this.voiceController.dispose();this.disagreePrivacy();},
  async initialize(){try{const m=await api.request('/api/models');this.setData({models:m.models.filter(x=>x.configured),error:''});this.setData({modelIndex:Math.max(0,this.data.models.findIndex(x=>x.id===m.active))});}catch(e){this.showError(e);}},
  showError(e){this.setData({error:e.message||'暂时无法处理',logged:api.hasSession()});},
  input(e){this.setData({[e.currentTarget.dataset.field]:e.detail.value});},
  async login(){if(!this.data.password)return;this.setData({busy:true,error:''});try{await api.login(this.data.password);this.setData({logged:true,password:''});await this.initialize();}catch(e){this.showError(e);}finally{this.setData({busy:false});}},
  async logout(){if(this.data.busy||this.data.uploading||this.data.recording||this.data.voicePending||this.data.voiceRetry)return;try{await api.request('/api/logout',{},'POST');}catch(e){}api.forget();this.setData({logged:false,messages:[],attachments:[],detail:null,text:'',sessionId:id()});},
  switchTab(e){if(this.data.recording||this.data.voicePending||this.data.voiceRetry){this.setData({error:'请先完成或取消录音'});return;}this.setData({tab:e.currentTarget.dataset.tab,detail:null,error:''});if(this.data.tab==='library')this.loadLibrary();if(this.data.tab==='system')this.loadSystem();},
  async changeModel(e){if(this.data.busy||this.data.uploading)return;const i=Number(e.detail.value);try{await api.request('/api/models/select',{model:this.data.models[i].id},'POST');this.setData({modelIndex:i});}catch(err){this.showError(err);}},
  remember(e){this.setData({remember:e.detail.value});},
  newChat(){if(this.data.busy||this.data.uploading||this.data.recording||this.data.voicePending||this.data.voiceRetry)return;this.setData({sessionId:id(),messages:[],attachments:[],text:'',error:''});},
  async history(){if(this.data.busy||this.data.uploading||this.data.recording||this.data.voicePending||this.data.voiceRetry)return;try{const rows=await api.request('/api/sessions');if(!rows.length){wx.showToast({title:'还没有历史对话',icon:'none'});return;}wx.showActionSheet({itemList:rows.slice(0,6).map(x=>x.title||'未命名对话'),success:async r=>{try{const row=rows[r.tapIndex];const messages=await api.request('/api/sessions/'+encodeURIComponent(row.id));this.setData({sessionId:row.id,messages,attachments:[],text:'',scrollId:'message-'+(messages.length-1)});}catch(e){this.showError(e);}}});}catch(e){this.showError(e);}},
  async send(){if(this.data.busy||this.data.uploading||this.data.recording||this.data.voicePending||this.data.voiceRetry||(!this.data.text.trim()&&!this.data.attachments.length))return;
    const model=this.data.models[this.data.modelIndex];if(!model){this.setData({error:'请先在服务端配置可用模型'});return;}
    if(!model.vision&&this.data.attachments.some(x=>x.kind==='image')){this.setData({error:'当前模型不能读取图片，请切换支持图片的模型'});return;}
    const body={session_id:this.data.sessionId,message_id:id(),text:this.data.text.trim(),remember:this.data.remember,model:model.id,attachment_ids:this.data.attachments.map(x=>x.id)};
    const messages=this.data.messages.concat([{role:'user',text:body.text,attachments:this.data.attachments},{role:'assistant',text:''}]);const index=messages.length-1;
    this.setData({messages,text:'',attachments:[],busy:true,error:'',status:'正在思考',scrollId:'message-'+index});let done=false;
    try{await api.stream(body,event=>{if(event.type==='assistant'){messages[index].text+=event.payload.text||'';this.setData({messages,scrollId:'message-'+index,status:''});}if(event.type==='tool_use')this.setData({status:'正在查阅与整理资料'});if(event.type==='error')this.setData({error:event.payload.text||'模型处理失败'});if(event.type==='done')done=true;});if(!done)throw new Error('回复未完整送达，请从历史对话确认结果');}catch(e){this.showError(e);}finally{this.setData({busy:false,status:''});}
  },
  async privacy(){
    if(!wx.getPrivacySetting)return;
    const setting=await new Promise((resolve,reject)=>wx.getPrivacySetting({success:resolve,fail:()=>reject(new Error('隐私指引暂时无法读取，请稍后重试'))}));
    if(!setting.needAuthorization)return;
    return new Promise((resolve,reject)=>{this.privacyWait={resolve,reject};this.setData({showPrivacy:true,privacyTitle:setting.privacyContractName||'隐私保护指引'});});
  },
  agreePrivacy(){this.setData({showPrivacy:false});if(this.privacyWait){this.privacyWait.resolve();this.privacyWait=null;}},
  disagreePrivacy(){if(this.privacyWait){this.privacyWait.reject(new Error('已取消，可以继续使用文字对话'));this.privacyWait=null;}if(!this.disposed)this.setData({showPrivacy:false});},
  openPrivacy(){this.openingPrivacy=true;wx.openPrivacyContract({fail:()=>{this.openingPrivacy=false;this.setData({error:'请先在微信后台配置隐私保护指引'});}});},
  openMicSettings(){wx.openSetting({success:r=>this.setData({micDenied:!r.authSetting['scope.record']})});},
  async addFiles(files){this.setData({uploading:true,error:''});try{for(const f of files){if(this.data.attachments.length>=6)throw new Error('每次最多添加 6 个附件');if(f.size>20*1024*1024)throw new Error('单个附件不能超过 20 MB');const a=await api.upload(f.tempFilePath||f.path);this.setData({attachments:this.data.attachments.concat(a)});if(a.kind==='audio'&&a.transcript)this.setData({text:[this.data.text,a.transcript].filter(Boolean).join('\n')});}}catch(e){this.showError(e);}finally{this.setData({uploading:false});}},
  async attach(){if(this.data.busy||this.data.uploading||this.data.recording||this.data.voicePending||this.data.voiceRetry)return;try{await this.privacy();wx.showActionSheet({itemList:['选择图片','选择聊天中的文件或录音'],success:r=>{if(r.tapIndex===0)wx.chooseMedia({count:Math.max(1,6-this.data.attachments.length),mediaType:['image'],success:x=>this.addFiles(x.tempFiles)});else wx.chooseMessageFile({count:Math.max(1,6-this.data.attachments.length),type:'file',success:x=>this.addFiles(x.tempFiles)});}});}catch(e){this.showError(e);}},
  remove(e){if(this.data.busy||this.data.uploading)return;this.setData({attachments:this.data.attachments.filter((_,i)=>i!==Number(e.currentTarget.dataset.index))});},
  async voice(){
    if(this.data.recording){this.voiceController.finish();return;}
    if(this.data.busy||this.data.uploading||this.data.voicePending||this.data.voiceRetry)return;
    if(this.data.attachments.length>=6){this.setData({error:'附件已满，请先移除一个附件再录音'});return;}
    const version=this.voiceVersion=(this.voiceVersion||0)+1;
    this.setData({voicePending:true,error:''});
    try{
      const status=await api.request('/api/attachments/status');if(!status.voice_ready)throw new Error('服务器语音转写尚未就绪，可以先输入文字');
      await this.privacy();if(version!==this.voiceVersion||this.disposed)return;
      const setting=await new Promise((resolve,reject)=>wx.getSetting({success:resolve,fail:()=>reject(new Error('无法读取麦克风权限'))}));
      if(setting.authSetting['scope.record']===false){this.setData({micDenied:true});throw new Error('麦克风权限已关闭，点击下方“打开设置”后允许录音');}
      await new Promise((resolve,reject)=>wx.authorize({scope:'scope.record',success:resolve,fail:()=>{this.setData({micDenied:true});reject(new Error('需要麦克风权限才能录音，你仍可使用文字输入'));}}));
      if(version!==this.voiceVersion||this.disposed)return;
      if(!this.voiceController)this.voiceController=new VoiceRecorder({manager:wx.getRecorderManager(),
        onState:({phase,seconds})=>this.setData({voicePhase:phase,recording:phase!=='idle',voiceSeconds:seconds,voiceTime:String(Math.floor(seconds/60)).padStart(2,'0')+':'+String(seconds%60).padStart(2,'0')}),
        onComplete:file=>{this.pendingVoice=file;this.setData({voiceRetry:true});this.transcribeVoice();},
        onError:message=>this.setData({error:message})});
      this.setData({micDenied:false});this.voiceController.start();
    }catch(e){if(!this.disposed)this.showError(e);}finally{if(!this.disposed)this.setData({voicePending:false});}
  },
  cancelVoice(){this.voiceVersion=(this.voiceVersion||0)+1;if(this.voiceController)this.voiceController.cancel();},
  async transcribeVoice(){
    if(!this.pendingVoice||this.data.uploading||this.data.busy)return;
    if(this.data.attachments.length>=6){this.setData({error:'请先移除一个附件，再重试转写'});return;}
    this.setData({uploading:true,error:'',status:'正在把语音转成文字…'});
    try{const a=await api.upload(this.pendingVoice.tempFilePath);if(this.disposed)return;
      if(!a.transcript)throw new Error('没有识别到清晰的讲话，请重新录制');
      this.setData({attachments:this.data.attachments.concat(a),text:[this.data.text,a.transcript].filter(Boolean).join('\n'),voiceRetry:false,status:'转写完成，请核对文字后发送'});this.pendingVoice=null;
    }catch(e){if(!this.disposed){this.showError(e);this.setData({status:'转写未完成，录音已保留'});}}finally{if(!this.disposed)this.setData({uploading:false});}
  },
  discardVoice(){if(this.data.uploading)return;this.pendingVoice=null;this.setData({voiceRetry:false,status:'',error:''});},
  async loadLibrary(append=false){try{const page=append?this.data.page+1:1;const r=await api.request('/api/library',{query:this.data.query,group:this.data.group,page,size:12});const rows=r.records.map(x=>({...x,summary:x.body?.text||x.body?.summary||x.body?.title||x.kind,kindLabel:kinds[x.kind]||'资料',title:x.body?.title||x.body?.symbol||kinds[x.kind]||'资料'}));this.setData({records:append?this.data.records.concat(rows):rows,total:r.total,page});}catch(e){this.showError(e);}},
  search(){this.loadLibrary();},more(){this.loadLibrary(true);},filter(e){this.setData({group:e.currentTarget.dataset.group});this.loadLibrary();},
  async loadSystem(){try{const r=await api.request('/api/overview');this.setData({layers:layers.map(([key,title,subtitle])=>({key,title,subtitle,items:(r.layers[key]||[]).slice(0,10)})),rules:r.strategy?.body?.rules||[]});}catch(e){this.showError(e);}},
  async openRecord(e){try{const r=await api.request('/api/records/'+encodeURIComponent(e.currentTarget.dataset.id));const body=r.body||{};this.setData({detail:{title:body.title||body.symbol||'资料详情',sections:Object.keys(body).filter(k=>k!=='metadata').map(k=>({label:({text:'原文',summary:'摘要',title:'标题',rules:'规则',claims:'认识与依据',source_id:'来源记录',origin:'来源',url:'来源链接',created_at:'创建时间',recorded_at:'记录时间',quantity:'数量',price:'价格',side:'方向',notes:'备注',effective_at:'生效时间',evidence:'证据',risk:'风险',reason:'理由',symbol:'标的',status:'状态',follow_up:'后续回看'})[k]||'补充资料',text:typeof body[k]==='string'?body[k]:JSON.stringify(body[k],null,2)}))}});}catch(e){this.showError(e);}},
  closeDetail(){this.setData({detail:null});}
});

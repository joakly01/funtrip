# 使用与部署

## 本机

Python 3.11以上，推荐3.12。在项目根目录：

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m cloud_trading
```

浏览器打开http://127.0.0.1:8097。当前个人配置已填写，登录密码在config/app.env的TRADING_PASSWORD。新环境先从app.env.example复制模板。应用不会读取原引擎环境文件。

## 0.5.0 日常使用

- **工作台**：写下正在思考的问题，保存为一个可持续推进的专题；“接着上次”恢复到最近打开的内容。“随手聊”继续支持语音、图片、文件。
- **知识库**：按学习专题、知识笔记、观察与复盘查阅；笔记可筛选历史整理、经典导读和自己写的内容。来源保留引用时的版本，自动整理先等你校对。
- **交易体系**：交易手册、我的方法、纪律与修订、工具箱。个人方法描述范围、条件、步骤、失效与风险；软件技能在讨论背后协助。
- **伙伴与成长**：设置回答偏好，回看反馈、采用过的整理和相关复盘。“整理新增资料”把历史记录和已有经典整理为有来源的草稿，重复操作不会覆盖自己的编辑。

一条完整路径：工作台提出问题 → 在专题写知识卡 → 从知识卡“看看下次怎样用”形成方法 → 从方法“准备一次观察” → 填写并锁定事前判断 → 补充实际情况与复盘 → 回到方法完善，或提出体系修改。只阅读资料、只观察而不交易都可以。

每个对象的“一起讨论”会带入正文、所属专题、关联来源与案例，有独立聊天历史。AI 整理建议先显示对照，点击“采用这份建议”才更新正文，旧版仍可回查；正文变化后不能直接套用过期建议。关闭“允许归集到个人资料”时不保存业务记录或整理建议，聊天历史仍保留。

“锁定事前记录”保留方法和纪律快照，之后不能修改原判断、条件与计划，只能补充实际发生的情况。已完成复盘可以补充修订；支持、反例、暂难判断由本人选择，一次案例不自动证明方法有效。

“提出体系修改”先准备完整的新条款并写明原因，再查看前后对照与依据；“确认采用以上变更”才使新纪律生效，也可以暂缓或不采用。新增文字纪律需要人工判断，不会自动转换为数值条件。

编辑草稿、未发送的对话和修订草稿暂存在当前设备；离开后可继续。点击保存后正文存到云端，同一账号的其他设备可读取。电脑 Enter 发送、Shift+Enter 换行；手机按钮发送。当前回复结束后可切换导航或模型。

需要多个角度时，主伙伴会邀请学习、计划检查、复盘或体系回顾伙伴，分别执行技能与共享工具，再汇总结果。对话的“协作记录”可查看实际完成情况。“希望调整”的反馈及相关复盘会带入后续同类任务；重要纪律仍由你审阅决定。完整说明见 [0.5.0 设计与实现](product-companion-0.5.md)。

## 图片、文件与语音

输入区的“图片”“文件”“语音”支持以下方式：

- 图片：JPG、PNG、WebP、GIF，支持选择、粘贴与拖入；缩略图可打开预览。Kimi 和 DeepSeek Flash 已验证可读图，DeepSeek Pro 当前按文字模型使用。图片发送前检查模型，不会自动改用其他模型。动态图片读取第一帧；用于模型的图像最长边缩到 3,072 像素，原文件保留。
- 文件：PDF、DOCX、XLSX、TXT、Markdown、CSV、JSON。上传后显示读取字数，点击可看内容预览与下载原文件。PDF 最多读取前 60 页，每份文档最多前 60,000 字，每次新附件文字最多 120,000 字，超出范围会提示；Excel 最多 3,000 个非空行，公式读取文件内已缓存的结果。扫描或加密 PDF 暂不支持提取文字，请上传页面截图或解密文件。暂不支持 HEIC、旧版 DOC/XLS、压缩包与视频理解。
- 语音：点击“语音”录音，完成后自动转为文字。发送前核对证券代码、金额与数字，可直接修改文字。也可上传 MP3、WAV、M4A、WebM、OGG、FLAC、AAC 等录音；最长 2 分钟，录音保留且可播放。直接录音需 HTTPS 或 localhost/127.0.0.1，浏览器也需允许麦克风；HTTP 云入口点击“语音”后使用“上传录音”。

每次最多 6 个附件，单个不超过 20 MB。上传、读取或转写期间禁止发送；失败可点击重试或移除。可以只发附件，也可以加文字说明。附件保存在对应聊天中，恢复历史后可预览和继续追问；模型请求最多带入最近 6 张图片与 120,000 字附件上下文，较早且超出范围的内容会提示未加载。不要把自动识别结果直接视为已确认成交事实。

录音由 [faster-whisper](https://github.com/SYSTRAN/faster-whisper) 的 Whisper base 模型在本机或本应用云服务器 CPU 上转写，录音不发给语言模型服务；用户发送的文字、文件提取内容和图片按选择交给已配置模型。语音输入默认按中文转写，不再自动猜测语言，避免短中文录音被误判为其他语言。转写后仍可核对、编辑再发送；本版未提供多语言语音切换。首次安装模型（约 145 MB）：

```sh
.venv/bin/python scripts/install_voice.py
```

模型默认保存在 runtime/models/faster-whisper-base，可通过 TRADING_ASR_MODEL_DIR 指定。服务启动时不加载语音模型，第一次转写时加载，随后复用。云主机无下载网络时，在本机安装后发布增加 `--voice-model data/cloud/runtime/models/faster-whisper-base`。新的依赖需同时更新 Linux 离线包。

## 模型切换

网页登录后，用“当前模型”选择Kimi、DeepSeek Flash或DeepSeek Pro。选择立即保存，重启后仍保留。当前回复期间禁止切换，下一条消息固定使用发送时的模型，同一会话的历史继续保留。

config/models.toml管理公开配置；config/app.env保存TRADING_KIMI_API_KEY、TRADING_DEEPSEEK_API_KEY等密钥。默认模型由TRADING_ACTIVE_MODEL指定，已保存的个人选择优先。增加模型时新增一个[[models]]段，设置唯一id、label、protocol、base_url、model和key_env；协议为openai或claude。模型已实际确认支持图片时设置vision=true，默认不启用。填好密钥后重启应用。

模型失败会明确报错，不返回模拟回复，也不自动改用其他模型。未配置的选项不可选。

## 数据与备份

原资料在data/trading，网页资料在data/cloud/trading；历史迁移是一份副本，两者不会同步。日常选择一个入口写入。

首次导入目标目录必须为空：

```sh
.venv/bin/python -m cloud_trading import-data --source data/trading
```

停止应用后备份：

```sh
.venv/bin/python -m cloud_trading backup
```

备份位于data/cloud/backups，包含knowledge.sqlite、runtime.sqlite、library.sqlite（如存在）、settings.json（如存在）、exports及attachments。恢复时先停止服务并另存当前数据，再把knowledge.sqlite放trading目录，runtime.sqlite和library.sqlite放runtime目录，settings.json和attachments放runtime目录，exports放trading目录，然后启动。模型密钥需单独保管；离线语音模型不重复放入每次备份，可重新安装。

消息失败或服务中断后，先查历史及已保存资料，再发新消息继续，不自动重放可能已完成的工具写入。

## Ubuntu原生部署

服务器需Python 3.11以上、venv、Nginx、可执行sudo的用户；22可连接。安装发布工具依赖：

```sh
.venv/bin/pip install -r requirements-dev.txt
ssh ubuntu@49.232.190.52
.venv/bin/python scripts/deploy.py --host 49.232.190.52 --user ubuntu
```

SSH首次连接需要核对主机身份。发布脚本使用已知主机记录，密码只在输入时使用，不保存；可通过--key-file使用已有密钥。

默认部署目录/home/ubuntu/trading-partner：releases保存代码版本、current指向当前版本、.venv为运行环境、config/app.env为个人配置、data为持久资料。专属trading-partner与trading-partner-web两个systemd服务负责应用和网页；应用在127.0.0.1:8097运行，专属Nginx在80提供网页。发布脚本检测冲突，不停止其他应用、不修改系统原有Nginx站点。

首次明确允许上传已有个人资料时，可增加--include-data；目标已有数据库时拒绝覆盖。平常更新不要使用此参数。发布会单独注入模型及登录配置，不把密码写入代码包。

服务器日常维护：

```sh
sudo systemctl status trading-partner trading-partner-web
sudo journalctl -u trading-partner -n 80
sudo systemctl restart trading-partner
```

云端备份：

```sh
sudo systemctl stop trading-partner
cd /home/ubuntu/trading-partner/current
/home/ubuntu/trading-partner/.venv/bin/python -m cloud_trading backup --env-file /home/ubuntu/trading-partner/config/app.env
sudo systemctl start trading-partner
```

完成后再复制备份目录。勿在应用写入时直接复制两个数据库。

更新时使用同一发布命令，保留共享数据。回滚先停止应用，将current指向previous-release记录的版本，重启两项服务。不要把运行数据库还原成旧代码版本附带的空文件。

## Docker备选

提供标准Dockerfile与compose.yaml。Docker镜像只含代码和公开模型列表，模型及个人密码从config/app.env注入，./data/cloud挂载为/data：

```sh
docker compose up -d --build
```

容器端口仅映射到主机127.0.0.1:8097，需另配网页代理。当前指定云主机没有Compose插件，使用上面的原生部署。本应用现已部署 joakly.site 的 HTTPS 证书并启用 TRADING_SECURE_COOKIE=1。云安全组 TCP 443 已放行，公网 HTTPS、登录和语音状态检查通过。


## 当前云端验收

应用已发布到49.232.190.52的独立目录，专属两项服务启动成功；已通过加密SSH在服务器回环地址验证网页登录、模型切换和50条可检索资料，两个数据库完整性检查通过。公网匿名健康检查为200。

2026-10-03 已部署用户提供的证书，HTTP 跳转 HTTPS，服务器内部证书校验和健康检查通过；证书在 2027-01-01 到期。外网检查连接重置，用户确认云安全组 TCP 443 未放行，需在腾讯云控制台放行后复查。早期公网明文密码测试曾被自动审批拒绝并改用加密 SSH 完成。紧急诊断可使用 SSH 本地转发（登录 Cookie 已设 Secure，普通 HTTP 转发不用于登录；可转发 443 并保留域名证书校验）：

```sh
ssh -L 8443:127.0.0.1:443 ubuntu@49.232.190.52
```

保持该连接，在本机打开http://localhost:8099。网页密码与个人资料经过SSH加密传输。

云主机默认包镜像无法解析，直连官方包源TLS失败；这次通过SSH上传官方Linux依赖包后离线安装。后续更新可在本机下载Linux依赖包并给发布脚本传入--wheelhouse路径；不是关闭TLS检查：

```sh
.venv/bin/pip download --only-binary=:all: --platform manylinux2014_x86_64 --platform manylinux_2_28_x86_64 --implementation cp --python-version 312 --abi cp312 --abi abi3 --dest /tmp/trading-cloud-wheels -r requirements.txt greenlet
.venv/bin/python scripts/deploy.py --host 49.232.190.52 --user ubuntu --wheelhouse /tmp/trading-cloud-wheels
```

2026-10-03 15:30 复查：用户已放行 TCP 443，公网 HTTPS、登录页、登录接口、Secure Cookie、模型列表与语音服务状态正常，先前连接重置已解除。网页版可通过 `https://joakly.site/` 使用录音；小程序另需微信后台合法域名配置和真机验收。

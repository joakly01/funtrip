# 有趣的旅途 · 我的交易伙伴

个人使用的交易智能体，手机网页、电脑网页与微信小程序由同一套前端源码生成。复用agent-runtime执行核心，交易记忆、聊天历史均使用SQLite。

统一前端源码在 `frontend/src/`，构建见 [frontend/README.md](frontend/README.md)。网页当前版本为 0.6.0：工作台围绕持续推进的问题，知识库接入有来源的历史整理与经典导读，交易体系按交易理念、交易准则、交易方法、交易工具展开，并用观察复盘检验实践。技能驱动的专职伙伴按需实际协作，使用偏好、反馈和复盘经验改善后续协助；当前版本增加分步编辑、可调整归属、工作台整理提醒、筛选分页和引用回查；详情见 [0.6.0 可用性提升](docs/product-usability-0.6.md) 与 [0.5.0 协作与成长实现](docs/product-companion-0.5.md)。旧网页 `/legacy` 保留备用。按最新安排，小程序备案尚未完成，本轮暂停小程序调试与发布。

## 运行

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m cloud_trading
```

云端应用使用 [joakly.site](https://joakly.site/)，已复制获准迁移的资料。证书已部署，源站 HTTPS 正常；2026-10-04 曾观察到腾讯云网站备案拦截；16:50 复验 HTTP 已正常跳转 HTTPS，但外网 HTTPS 握手仍中断，当前原因待进一步确认。发布状态见验收记录。

个人配置在`config/app.env`，模板为`config/app.env.example`；模型目录为`config/models.toml`。本机入口：http://127.0.0.1:8097。登录后直接在“当前模型”中切换Kimi、DeepSeek Flash或DeepSeek Pro，选择会保存，聊天历史继续保留。

## 工程结构

```text
cloud_trading/         网页、登录、会话接口、模型切换及核心装配
frontend/             网页与小程序共用 Vue / uni-app 源码
miniprogram/           早期小程序参考（当前统一版不从此构建）
trading_agent/         交易业务、资料存储和原有MCP入口
vendor/runtime_core/  裁剪后的原引擎依赖，源码校验清单
skills/               五个工作流程
roles/                主协调与道法术器角色
knowledge/            经典知识及出处
config/               模型列表、个人配置及模板
scripts/              云端发布和真实模型隔离验收
deploy/              专属systemd服务与Nginx配置模板
docs/                使用部署说明与功能设计
tests/               隔离行为测试
data/                私人资料、运行数据库、研究记录与备份
```

[当前版本：可用性提升](docs/product-usability-0.6.md) · [协作与成长](docs/product-companion-0.5.md) · [0.4.0 主线与生命周期](docs/product-growth-implementation.md) · [总体设计](docs/product-growth-blueprint.md) · [使用与部署](docs/usage.md) · [整理与验收](docs/maintenance.md)

原工具入口仍可使用`python -m trading_agent mcp`。当前技能发现链接和Codex工具配置已适配标准模块入口。

每项工具标题旁都有「? 帮助」，按需打开用途、操作步骤、判断逻辑、结果解释与使用边界；详细计算参数再折叠一层。分析工具另有“资料中的分析思路”，展示视频/参考资料名称、作者或发布方、可打开的原始来源、按需展开的原意摘要及本工具的实现对应；视频段落标注时间，作者观点与工具参数分别保留。帮助与工具共用侧栏，返回保留输入、计算结果与图表，不触发资料写入。

交易体系 → 交易工具现有九项工具，新增大趋势分析（周线看方向、日线看位置、各阶段观察与行动条件），并保留图表画线与资金参与判断：真实日线及分钟线、自动推荐并绘制趋势通道/横盘区间/关键价位、按证券和周期保存标注、自动资金参与程度与量价方向判断、观察模式推荐及计算依据，并可将结果带入伙伴对话。新增趋势与量价检查：分项核对上涨结构、反复放量、阴线量能和用户指定起点的收盘涨幅；来源与代理参数见 [趋势量价视频学习](docs/learning/trend-volume-selection.md)。新增放量后的结构观察：识别事件、冻结参考价区、自动画线、逐日回看守位与再次突破；方法来源、参数与未实现边界见 [视频学习与结构工具](docs/learning/volume-hold-reconfirmation.md)。使用方式与数据边界见 [图表与资金工具](docs/trading-tools.md) 与 [大趋势分析](docs/trend-analysis.md)。

支持按需研究、记录、找回、更正、计划检查和复盘；关闭“积累到我的资料”时聊天历史仍保存。后台盯盘、自动下单、券商同步和完整企业资料联网研究尚未启用。

对话支持文字、图片、文档与语音转文字。Kimi 和 DeepSeek Flash 可读图；PDF、Word、Excel、文本会提取内容并提示读取范围。录音最长 2 分钟，先核对转写再发送。原文件保存在聊天附件中，可预览、下载、继续追问；每次最多 6 个附件、单个 20 MB。

语音识别模型安装：`.venv/bin/python scripts/install_voice.py`。服务器使用本地 CPU 转写，不需额外语音 API 密钥。浏览器直接录音需要 HTTPS 或本机地址；需公网 HTTPS 恢复后再验证云端浏览器直接录音；本机 localhost 可以使用。

微信小程序首版已建立，AppID 已配置，服务域名 `joakly.site`。导入、能力范围及发布步骤见 [小程序说明](docs/miniprogram.md)。尚未上传到微信或完成真机验收。

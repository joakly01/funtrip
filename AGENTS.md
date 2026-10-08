# 工程约定

这是个人交易伙伴网页与业务工具工程；工程开发讨论不写入交易记忆。

- 网页入口：`python -m cloud_trading`；工具/MCP入口：`python -m trading_agent`。
- 使用根目录`.venv`，运行依赖在`requirements.txt`，发布工具依赖在`requirements-dev.txt`。
- `cloud_trading`负责网页、核心装配及模型切换；`trading_agent`负责交易资料和确定性业务。
- `vendor/runtime_core`是用户提供的引擎依赖闭包，保持原源码；场景逻辑通过独立扩展接入。保留文件校验见manifest.json。
- `config/models.toml`为公开模型列表；`config/app.env`为私有配置，密钥不输出、不放进代码包或文档。
- `data/trading`保留原MCP资料；网页使用`data/cloud`，云主机数据在独立应用目录下的`data`。不做双向同步。
- 不把工程验收对话当真实交易。模型验收使用临时数据库，脚本为`scripts/verify_model.py`。
- 交易事实更正保留原话和版本；候选认识、提案不能自动成为正式纪律。
- 发布只操作本应用目录与专属服务；检查端口冲突并保留共享数据。个人数据迁移按当前会话明确授权处理。
- 自动测试：`.venv/bin/python -m unittest discover -s tests -q`。能力边界与运行说明在README和docs中。

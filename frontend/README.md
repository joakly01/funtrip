# 统一前端 · 0.4.0

`src/` 是手机网页、桌面网页和微信小程序唯一的前端源码。uni-app / Vue 3 / TypeScript 编译两个目标；Python 后端、模型配置、SQLite 和核心引擎继续共用。

安装依赖：`npm ci`。根目录执行 `.venv/bin/python scripts/build_frontend.py` 会运行类型检查、共享逻辑测试、生成 H5 和微信包，并复制 H5 到 Python 发布资源目录。云主机不需要安装 Node.js。

本轮小程序备案未完成，暂停调试与发布；使用 `.venv/bin/python scripts/build_frontend.py --web-only` 只验收、构建和发布网页，现有小程序构建产物不会更新。

开发网页：`npm run dev:h5`，代理本地 8097 后端。生产 H5 挂载 `/next/`，旧网页 `/legacy` 保留备用。开发小程序：`npm run dev:mp-weixin`，导入 `dist/dev/mp-weixin`；发布包导入 `dist/build/mp-weixin`。不要编辑生成的 JS/WXML/WXSS；改动只进入 `src/` 后重新构建。

微信 AppID 在 manifest.json。模型密钥、访问密码和 SSH 凭据均不进入前端。小程序只保存服务端签发的登录 Cookie，本机输入草稿和未发送附件标识分别保留，不跨端同步；登录失效重新解锁。

`domain/` 统一资料类型、四层体系、状态及可控 Markdown；`components/` 统一三个工作区及详情；`platform/` 仅适配网络、附件、录音和微信隐私授权。平台差异由条件编译隔离。

小程序必须配置 `https://joakly.site` 的 request、uploadFile、downloadFile 合法域名，并按真实用途补齐隐私指引。编译成功不代表微信真机录音、权限、文件选择及审核已经通过。

0.4.0 主入口使用 `GrowthWorkspace`，专题/知识/方法/实践共用 `GrowthDetail`、`GrowthEditor`；`ProposalPanel` 处理用户审阅。原始资料仍用 `LibraryView` / `RecordDetail`，工具使用 `ToolPanel`，对话使用 `ChatView` 并按对象隔离会话与本机草稿。

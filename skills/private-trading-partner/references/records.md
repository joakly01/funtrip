# 记录与记忆

## 原文到分类记录

先 `inbox_capture(message_id,text)`，保存的是用户实际输入，不要重写成推断。稳定 message_id 供同一次请求重试，新的用户输入使用新编号。成功取得 source_id 后调用 `records_commit`。

items 的每个条目字段：

- `item_key`：该条原文内稳定唯一编号，如 diary-1。
- `kind`：note/trade/plan/emotion/learning/observation/hypothesis/review/research/position/profile/evolution。
- `text`：整理内容，未知保留未知。
- `excerpt`：原文中真实存在的连续引文。
- `nature`：user_report/external_fact/opinion/hypothesis/plan/interpretation。
- `tags`、`security_ids`：可选字符串数组；代码须经 security_resolve 核实。
- `horizon`：short/medium_long/unknown。
- `event_at`：带时区 ISO 时间或 null。只有“今天”时不能编造具体成交时刻；可保留日期在 fields。
- `fields`：附加结构字段。
- `refs`：其他实际对象编号。

trade 只用于本人已成交自述，nature 必须为 user_report，fields 至少包括 `execution_status: reported_executed` 和 `side: buy/sell/unknown`。price、quantity 不知道时为 null；计划用 plan。

同一条输入可以有交易、情绪、观察多种记录，保持 source_id 一致。原文中提到的股票解析失败不影响原文保存，先留待澄清信息。

## 检索与更正

用 memory_search 按原始词、证券、kind 过滤，必要时改写查询。服务首版为本地全文包含匹配，不是语义搜索，不要把没有命中当作从未记录。再用 memory_read 读取完整原文和版本。

更正先保存用户此次更正原文，使用 record_amend 指向正确对象及 expected_version。只更正用户明确说错的内容，保留理由。patch.fields 只需提供变更字段。读取回执的 needs_recheck，必要时重新计算依赖结论。

历史查询用 known_before 或具体 version，避免后来更正的信息泄漏到当时判断。

memory_read 的 reference_ids 可继续读取原文和更正来源，dependent_ids 显示依赖对象。对象 ID 是稳定标识，分类以当前 kind 为准，不能根据 plan_ 等初始前缀判断更正后的类型。

## 持仓

positions_get 只返回历史快照与交易自述，未做券商对账。单笔成交不能重建完整账户。整理持仓使用 position 类记录，保留日期、账户范围、现金是否完整及原始凭证来源。risk_compute 仅计算明确输入，缺全账户分母时不编造比例。

需要可读文件时调用 knowledge_export；需要备份时调用 knowledge_backup。导出只是视图，不能直接覆盖数据库或拿导出视图充当新增用户证据。

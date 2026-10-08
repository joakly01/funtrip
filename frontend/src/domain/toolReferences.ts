/** 学习来源的观点归纳，与工具代理参数分别呈现；不作为已确认个人纪律。 */
export interface ToolReference {
  title: string;
  publisher: string;
  kind: '视频' | '参考资料';
  url: string;
  basis: string;
  summary: string;
  sections: { label: string; text: string }[];
  application: string;
}
export const toolReferences: Record<string, ToolReference[]> = {
  selection: [{
    title: '高效选股必备条件', publisher: '资金拆解k总', kind: '视频',
    url: 'https://www.xiaohongshu.com/explore/6aad32e10000000028028860',
    basis: '依据已核对的画面、字幕与图示归纳，非逐字引文；未独立完成音频逐字转录。',
    summary: '先看上涨趋势，再看反复放量与回撤量能，最后检查近期涨幅与追高压力。作者把它作为候选筛选思路，没有给出完整交易计划。',
    sections: [
      {label:'00:01—00:50 · 先看趋势', text:'作者优先观察高低点逐步抬高的上涨结构；沿5日均线走的小趋势也可观察。“大小趋势都可”要保留，不能改成只允许长期上涨，也不能因此忽略周期。'},
      {label:'00:51—01:36 · 看重复行为', text:'把进攻、整理、回调、再进攻连起来看，关注多次放量。有些底部放量日不是上涨日，因此作者的意思不能概括为“所有放量都必须上涨”。'},
      {label:'01:37—02:17 · 单根天量不够', text:'一次放量中买卖双方都在交易。作者用反例强调，不能仅凭一根大成交量就断定资金仍在或正在吸筹，还要观察后续价格与重复行为。'},
      {label:'02:18—02:46 · 理想与宽松量价', text:'作者偏好上涨放量、阴线缩量；约02:42—02:45也允许较弱的接近平量配合。接近平量是宽松情形，不能等同于理想缩量。'},
      {label:'02:47—03:24 · 少追已涨很多的候选', text:'作者希望近期一段上涨幅度小于20%，通常不宜追已经上行后又快速拉升的情形；情绪交易被作为另一语境提到，没有展开成完整例外策略。作者没有定义统一天数或涨幅起点。'},
      {label:'03:25—04:05 · 为什么看涨幅', text:'作者把较大浮盈与兑现压力联系起来，以20%作为经验心理界限，并提到连板后的压力。这属于作者解释，本次没有独立证据把20%确认为普遍心理阈值。'},
    ],
    application: '工具采用趋势、量价、指定起点涨幅三项检查。5日均线条件、放量门槛、事件分组和阴线容差为实现选择；“上涨放量事件”只是对部分线索的代理，不覆盖视频中的全部底部放量。20%来自作者参考，但收盘计算口径与起点由工具和用户明确，不能称为真实获利筹码占比。与“放量后的结构观察”配合时保留两段视频对回撤量能的不同语境。',
  }],
  pattern: [{
    title: '散户入门最简单但却最容易吃涨停的方法', publisher: '资金拆解k总', kind: '视频',
    url: 'https://www.xiaohongshu.com/explore/6980722f0000000021032aa8',
    basis: '依据已核对的画面、字幕与图示归纳，非逐字引文；案例证券、完整日期和周期未确证，不能复算案例收益。',
    summary: '作者把放量上影、后续守位、整理回撤和再次突破连起来解释资金行为，再按形态成熟度筛选候选。重点是放量之后的价格响应。',
    sections: [
      {label:'00:48—01:32 · 放量上影与后续响应', text:'作者先讨论放量带上影可能存在卖出，再结合后续价格没有明显坍塌并重新上涨，倾向于把此前行为解释为试盘。这是结合后续走势的解释，不能称为放量当天已经确定的资金事实。'},
      {label:'01:35—02:59 · 支撑附近的整理回撤', text:'关注连续阴线、支撑附近回撤与再次放量，把部分下跌理解为卖压释放。作者没有规定回撤只能缩量；放量回撤也要结合位置和后续响应理解。'},
      {label:'03:09—03:29 · 突破与回踩', text:'作者讨论突破时初步参与、回踩后的增加参与。这是其案例中的操作思路，没有形成适用于所有股票的统一仓位、退出和费用规则。'},
      {label:'03:47—04:23 · 候选逐步成熟', text:'先收入候选池，形态成熟后进入重点池，持续筛选。发现异常、值得重点跟踪和实际参与是递进的不同阶段。'},
    ],
    application: '工具把可观察部分实现为“放量上影→守位整理→突破待确认→后续确认”，并保留失败和到期。事件高低点只是自动参考锚点，不冒称作者全部手工支撑、成本或筹码区；2倍放量、20%上影、3根守位、波幅容差和期限都是工具参数。逐日回看不将后来上涨追写成早期已确认试盘，也不自动执行作者的参与动作；持久候选池尚未实现。',
  }],
  trend: [
    {title:'Dow Theory · 道氏理论说明', publisher:'StockCharts ChartSchool', kind:'参考资料', url:'https://chartschool.stockcharts.com/table-of-contents/market-analysis/dow-theory', basis:'对参考资料的中文摘要，非逐字引文；此页是理论说明。', summary:'把主要趋势、逆向的次级波动和日常波动分开，并用逐步抬高或降低的高低点观察趋势。', sections:[{label:'主要趋势与次级波动',text:'上涨中的回调、下跌中的反弹属于不同层次的运动；局部反向波动不直接意味着主要趋势改变。'}, {label:'结构与确认',text:'上涨看高点和低点逐步抬高，下跌看二者逐步降低；趋势变化要有后续结构证据。完整道氏理论还包含工业与运输指数相互确认等要求。'}], application:'本工具借鉴周期分层和高低点思想，用单只股票的周线作背景、日线补位置；没有实现完整道氏指数确认模型。左右各2根转折确认、均线周期、斜率和拟合阈值由本工具定义。'},
    {title:'DecisionPoint Trend Model · 趋势模型', publisher:'StockCharts ChartSchool', kind:'参考资料', url:'https://chartschool.stockcharts.com/table-of-contents/trading-strategies-and-models/decisionpoint-trend-model', basis:'对参考资料的中文摘要，非逐字引文。', summary:'把短、中、长期趋势分层，用较快和较慢的均线及价格相对位置描述方向；大周期定背景，小周期帮助观察变化。', sections:[{label:'周期配合',text:'资料强调不同周期相互关联；短期变化可能先出现，但需要放在长期趋势中理解。价格和均线出现逆向关系时，应重新核对方向。'}], application:'仅借鉴多周期与快慢均线的思路。原文使用EMA组合，包含17/43周、50/200日等口径；本工具使用10/40周和20/120日简单均线，并加入拟合与高低点核对，不能称为复刻原模型。'},
  ],
  chart: [
    {title:'Support & Resistance · 支撑与阻力', publisher:'StockCharts ChartSchool', kind:'参考资料', url:'https://chartschool.stockcharts.com/table-of-contents/chart-analysis/support-and-resistance', basis:'对参考资料的中文摘要，非逐字引文。', summary:'用过去的高低点和价格反应定位供需可能变化的位置；支撑与阻力可以理解为区域，仍可能被突破。', sections:[{label:'从历史反应找参考区',text:'此前反应低点可提供支撑线索，高点可提供阻力线索。价格有短暂越界和波动，精确单一价位可能不够；支撑失守后也可能转为阻力。'}], application:'工具借鉴历史转折与价区思想。高低价分位、1.2倍波幅聚类、4根间隔和最多4组价位是本应用的可重复代理条件，资料没有规定这些数值。'},
    {title:'Trend Lines · 趋势线', publisher:'StockCharts ChartSchool', kind:'参考资料', url:'https://chartschool.stockcharts.com/table-of-contents/chart-analysis/trend-lines', basis:'对参考资料的中文摘要，非逐字引文。', summary:'传统趋势线连接价格转折点：上涨线连接抬高的低点，下跌线连接降低的高点，并看后续触碰是否支持它。', sections:[{label:'画线与验证',text:'两个点可以形成候选线，资料以第三次接触作为验证；趋势线被穿越需要继续核对，不能等同于主要趋势已经反转。图表尺度也会影响长期线条。'}], application:'手动画线支持连接两个位置；自动“趋势通道”采用收盘统计拟合及残差分位，并非传统转折点连线。资料的画线思想与本工具的自动算法分别保留，R²和波幅阈值属于本应用设定。'},
  ],
  activity: [{title:'Chaikin Money Flow · CMF', publisher:'StockCharts ChartSchool', kind:'参考资料', url:'https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/chaikin-money-flow-cmf', basis:'对指标说明的中文摘要，非逐字引文；指标由Marc Chaikin提出。', summary:'通过收盘在当根高低价区间的位置，再以成交量加权，观察一段时间内的买卖压力线索，并与价格方向核对。', sections:[{label:'收盘位置与成交量',text:'收盘靠近当根上部时贡献偏正，靠近下部时贡献偏负；累加一段期间的量加权位置，再除以同期总量。正负及与价格方向的配合用于观察压力。'}, {label:'为何需要其他证据',text:'指标可能在零轴附近反复变化；它主要看当根收盘位置，跳空时可能与实际价格涨跌方向不一致，因此不宜孤立使用。'}], application:'工具使用20根CMF，并额外比较近5根量能、价格变化和上涨K线量占比。活跃程度分档、±0.1方向阈值及六种推荐模式都是本应用的组合规则，不是原资料给出的主力识别方法；不会据此计算真实净流入或确认资金身份。'}],
};

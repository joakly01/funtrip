"""Observable participation patterns. No holder identity or actual fund flows."""
import statistics


def participation(data, activity):
    bars = [b for b in data['bars'] if b.get('is_final')]
    out = dict(algorithm_version='fund-observation-v1', as_of=bars[-1]['time'] if bars else None,
               available=False, degree='暂无法判断', participation='资料不足', bias='方向待判断',
               recommended='basic', reason='需要至少26根完整K线及有效成交量。', metrics={},
               identity='当前无法确认。未接入逐笔成交、委托及持有人身份数据；没有持续放量也不能排除主力参与。',
               evidence=[], alternatives=['新闻事件、市场整体波动、散户交易也可能产生相似量价表现。'],
               next_checks=[], parameters={'high_ratio_5':1.8,'medium_ratio_5':1.2,'low_ratio_5':0.8,'cmf_bias':0.1},
               boundary='介入程度仅描述相对成交活跃度，不是主力仓位、净买入金额或吸筹/出货结论。',
               modes=[dict(id=k,title=t,matched=False,reason='条件未满足',checks=[])
                      for k,t in [('selling','卖压增强'),('divergence','量价分歧'),('breakout','放量突破'),('persistent','持续参与'),('pullback','缩量回落'),('basic','基础观察')]])
    if len(bars)<26 or not activity['volume_available']:
        out['reason'] = '完整K线或成交量不足，不能判断参与变化。'
        return out
    vv = [b.get('volume',b.get('volume_raw')) for b in bars]
    baseline = statistics.fmean(vv[-25:-5])
    if baseline <= 0:
        out['reason'] = '对照期成交量为零，不能计算相对参与程度。'
        return out
    ratio5 = statistics.fmean(vv[-5:])/baseline
    elevated = sum(v >= baseline*1.2 for v in vv[-5:])
    r = activity['volume_ratio_20']
    cmf, delta, up = activity['cmf_20'],activity['price_change_5'],activity['up_volume_share_20']
    atr = statistics.fmean(max(bars[i]['high']-bars[i]['low'],abs(bars[i]['high']-bars[i-1]['close']),abs(bars[i]['low']-bars[i-1]['close'])) for i in range(len(bars)-14,len(bars)))
    gap = any(abs(bars[i]['open']-bars[i-1]['close']) > max(3*atr, .1*bars[i-1]['close']) for i in range(len(bars)-5,len(bars)))
    previous_high = max(b['high'] for b in bars[-21:-1])
    out['metrics'] = dict(relative_volume_5=ratio5,elevated_bars_5=elevated,previous_20_high=previous_high)
    out['evidence'] = [f'近5根平均量 / 此前20根均量：{ratio5:.2f}倍；其中{elevated}根≥对照均量的1.2倍。',
                       f'近5根价格变化{delta:.1%}，CMF20={cmf:.2f}，上涨K线量占比{up:.1%}。']
    if gap:
        out.update(reason='最近5根有明显跳空，先核对除权与事件，暂停综合介入判断。',
                   next_checks=['核对跳空日期、除权和公告；目前使用不复权价格。'])
        return out
    degree = '较高' if ratio5>=1.8 else '有所增强' if ratio5>=1.2 else '偏低' if ratio5<.8 else '常态'
    sustained = ratio5 >= 1.2 and elevated >= 3
    direction = '偏买方的量价配合' if cmf >= .1 and delta > 0 and up >= .55 else '偏卖方的量价配合' if cmf <= -.1 and delta < 0 and up <= .45 else '方向不一致／暂不明确'
    out.update(available=True, degree=degree, bias=direction,
               participation='有持续放量迹象' if sustained else '最新单根放量，持续性待确认' if r is not None and r>=1.5 else '尚无持续放量迹象')
    matched = [
        ((sustained or (r is not None and r>=1.5)) and direction=='偏卖方的量价配合',
         '参与增强伴随价格回落、CMF偏低及下跌K线量占优，优先观察卖压。', ['检查后续价格与卖压是否缓和；这不能确认某个主力正在出货。']),
        ((cmf >= .1 and delta < 0) or (cmf <= -.1 and delta > 0) or (r is not None and r>=1.5 and bars[-1]['close']<=bars[-2]['close']),
         '价格与收盘位置或最新放量方向不一致，先看分歧。', ['对照下一批完整K线是否修复分歧；不能仅凭CMF认定吸筹或出货。']),
        (r is not None and r>=1.5 and bars[-1]['close']>previous_high,
         '最新完整收盘越过此前20根最高价，且成交量≥此前20根均量1.5倍。', ['观察后续完整收盘能否保持在突破价位上方，以及量能是否延续。']),
        (sustained,
         '近5根均量≥对照均量1.2倍，且至少3根同样放量。', ['把参与增强与价格方向一起看；下跌放量也属于参与增强。']),
        (ratio5<.8 and delta<0,
         '近5根均量低于对照期0.8倍，同时价格回落。', ['观察价格是否止稳；缩量回落不自动等于洗盘。']),
        (True,'没有更明确的模式，先跟踪参与变化和方向。', ['继续对照价格、相对量和CMF，等待模式条件完整。']),
    ]
    for mode,(match,reason,checks) in zip(out['modes'],matched):
        mode.update(matched=bool(match),reason=reason,checks=checks)
    chosen = next(m for m in out['modes'] if m['matched'])
    out.update(recommended=chosen['id'], reason=chosen['reason'], next_checks=chosen['checks'])
    out['alternatives'] += ['放量也可能是多空换手或集中卖出；涨价缩量也可能来自流动性减少。']
    return out

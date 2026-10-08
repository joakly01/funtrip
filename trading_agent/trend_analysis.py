"""Read-only, retrospective multi-horizon trend observations; no trading signals."""
from collections import OrderedDict
from statistics import fmean
from zoneinfo import ZoneInfo

from .analysis import validate_bars
from .common import DomainError, stamp

LABELS = {'up': '上涨趋势', 'range': '盘整阶段', 'down': '下跌趋势', 'unclear': '方向待确认'}
PLAYBOOKS = {
    'up': {'title': '顺着趋势，等待条件', 'observe': '观察回调是否守住前一确认低点，随后能否重新向上；同时核对量价与大盘环境。',
           'prepare': '空仓：等待回调企稳或突破后的确认。持仓：按原计划跟踪关键低点与退出条件。先算可承受损失，再决定是否参与。',
           'avoid': '连续上涨后只因怕错过而追入；把均线本身当成买入指令。',
           'reassess': '周线跌破前一确认低点，或周线均线转弱、日线持续走低时，重新检查上涨判断。'},
    'range': {'title': '明确边界，耐心等选择', 'observe': '观察反复出现的区间上下沿，以及突破后能否维持；区间中部通常缺少清晰的收益风险边界。',
              'prepare': '空仓：优先等待区间边缘或突破后的确认。持仓：明确区间失守时如何退出；区间交易只有符合自己的已验证方法时才考虑。',
              'avoid': '在区间中部反复追涨杀跌；把一次越界直接当成新趋势。',
              'reassess': '有效越过区间后形成新的高低点结构，且周线方向逐渐一致时，重新判断阶段。'},
    'down': {'title': '先控制风险，再看转折', 'observe': '观察反弹是否仍受前一确认高点压制，低点是否继续下移；反弹强弱与大趋势分开看。',
             'prepare': '空仓：等待低点不再下移、随后突破前一确认高点等转折证据。持仓：优先核对既定退出条件和可承受损失，不靠补仓解决判断错误。',
             'avoid': '因为跌得多就认为便宜；把短期反弹直接当成反转。',
             'reassess': '周线低点抬高、高点被突破，均线止跌转升后，再评估是否进入新阶段。'},
    'unclear': {'title': '保留判断，补齐证据', 'observe': '先区分历史不足、方向冲突和价格异常；有日线方向也不代表周线大趋势已确认。',
                'prepare': '明确自己的交易周期，等待结构与均线趋于一致；历史不足时先补数据，再做大趋势判断。',
                'avoid': '为每只股票强行贴标签；把“方向不明”当成“横盘”。',
                'reassess': '新增完成周线后重新查看；若出现价格异常，先核对复权口径与独立行情来源。'},
}


def weekly_bars(bars, fetched_at):
    """Discard possibly partial first week and the current Shanghai calendar week."""
    groups = OrderedDict()
    current_week = stamp(fetched_at).astimezone(ZoneInfo('Asia/Shanghai')).date().isocalendar()[:2]
    for bar in bars:
        week = stamp(bar['time']).astimezone(ZoneInfo('Asia/Shanghai')).date().isocalendar()[:2]
        groups.setdefault(week, []).append(bar)
    result = []
    for i, (week, rows) in enumerate(groups.items()):
        if i == 0 or week >= current_week:
            continue
        result.append({'time': rows[-1]['time'], 'open': rows[0]['open'], 'close': rows[-1]['close'],
                       'high': max(r['high'] for r in rows), 'low': min(r['low'] for r in rows),
                       'is_final': True, 'source_days': len(rows)})
    return result


def average(values, period, end=None):
    end = len(values) if end is None else end
    return fmean(values[end-period:end]) if end >= period else None


def classify(bars, interval):
    fast, slow, shift, window = (10, 40, 4, 52) if interval == '1w' else (20, 120, 10, 120)
    closes = [b['close'] for b in bars]
    plotted = []
    for i, b in enumerate(bars):
        plotted.append({k: b[k] for k in ('time', 'close', 'high', 'low')} | {
            'fast': average(closes, fast, i+1), 'slow': average(closes, slow, i+1)})
    out = {'state': 'unclear', 'label': LABELS['unclear'], 'interval': interval, 'count': len(bars),
           'required': slow+shift, 'as_of': bars[-1]['time'] if bars else None,
           'fast_period': fast, 'slow_period': slow, 'evidence': [], 'counter_evidence': [],
           'levels': [], 'chart': plotted[-(104 if interval == '1w' else 160):]}
    if len(bars) < slow+shift:
        out['evidence'] = [f'至少需要 {slow+shift} 根完成K线，当前只有 {len(bars)} 根。']
        return out
    recent = bars[-window:]
    ys = [b['close'] for b in recent]
    n = len(ys); mx = (n-1)/2; my = fmean(ys)
    slope = sum((i-mx)*(y-my) for i, y in enumerate(ys))/sum((i-mx)**2 for i in range(n))
    variance = sum((y-my)**2 for y in ys)
    r2 = 1-sum((y-(my+slope*(i-mx)))**2 for i, y in enumerate(ys))/variance if variance else 0
    atr = fmean(max(recent[i]['high']-recent[i]['low'], abs(recent[i]['high']-recent[i-1]['close']),
                    abs(recent[i]['low']-recent[i-1]['close'])) for i in range(n-14, n))
    short = average(closes, fast); long = average(closes, slow)
    long_change = long/average(closes, slow, len(closes)-shift)-1
    short_change = short/average(closes, fast, len(closes)-shift)-1
    distance = slope*(n-1)/max(atr, 1e-9)
    # Two candles on either side must confirm a turning point; the newest two never qualify.
    highs, lows = [], []
    for i in range(2, n-2):
        if all(recent[i]['high'] > recent[j]['high'] for j in range(i-2, i+3) if j != i):
            highs.append(recent[i])
        if all(recent[i]['low'] < recent[j]['low'] for j in range(i-2, i+3) if j != i):
            lows.append(recent[i])
    structure = 'unknown'
    if len(highs) >= 2 and len(lows) >= 2:
        hh = highs[-1]['high'] > highs[-2]['high']
        hl = lows[-1]['low'] > lows[-2]['low']
        lh = highs[-1]['high'] < highs[-2]['high']
        ll = lows[-1]['low'] < lows[-2]['low']
        structure = 'up' if hh and hl else 'down' if lh and ll else 'mixed'
    up = closes[-1] > short > long and long_change > .005 and short_change > 0 and distance > 2 and r2 >= .35
    down = closes[-1] < short < long and long_change < -.005 and short_change < 0 and distance < -2 and r2 >= .35
    range_ok = abs(long_change) <= .01 and abs(short_change) <= .02 and r2 < .25 and len(highs) >= 2 and len(lows) >= 2
    state = 'up' if up and structure != 'down' else 'down' if down and structure != 'up' else 'range' if range_ok and structure not in ('up', 'down') else 'unclear'
    out.update(state=state, label=LABELS[state], metrics={'last_close': closes[-1], 'fast_average': short,
        'slow_average': long, 'slow_change': long_change, 'short_change': short_change, 'slope_shift': shift,
        'regression_r2': r2, 'move_atr': distance, 'window': n, 'structure': structure,
        'return_20': closes[-1]/closes[-21]-1})
    suffix = '周' if interval == '1w' else '日'
    position = '在短、长均线之上' if closes[-1] > max(short, long) else '在短、长均线之下' if closes[-1] < min(short, long) else '位于两条均线之间'
    out['evidence'] = [f'最近收盘{position}；观察 {fast}/{slow} {suffix}均线。',
                       f'{slow}{suffix}均线较 {shift} 根前变化 {long_change:+.1%}。',
                       {'up': '确认高点与低点均抬高。', 'down': '确认高点与低点均降低。',
                        'mixed': '确认高低点方向不一致。', 'unknown': '尚无两组确认高低点；结构证据不足。'}[structure]]
    if structure == 'mixed':
        out['counter_evidence'].append('高低点结构仍有分歧；均线方向需要后续结构验证。')
    if structure == 'unknown':
        out['counter_evidence'].append('趋势判断主要来自均线与方向性，仍需高低点结构验证。')
    out['evidence'].append(f'最近20根完成K线收盘变化 {closes[-1]/closes[-21]-1:+.1%}；短期变化与大方向分开看。')
    if state == 'unclear':
        out['counter_evidence'].append('均线位置、斜率与高低点尚未形成一致方向，不能据此确认趋势或盘整。')
    for kind, points, key, label in [('high', highs, 'high', '前一确认高点'), ('low', lows, 'low', '前一确认低点')]:
        if points:
            out['levels'].append({'kind': kind, 'price': points[-1][key], 'time': points[-1]['time'], 'label': label})
    return out


def analyse_trend(data):
    quality = validate_bars(data)
    if not quality['valid']:
        raise DomainError('INVALID_MARKET_DATA', '; '.join(quality['errors']))
    if data.get('interval') != '1d' or data.get('adjustment') != 'forward':
        raise DomainError('UNSUPPORTED_TREND_BASIS', '大趋势分析需要前复权日线，未自动混用其他口径')
    bars = [b for b in data['bars'] if b.get('is_final')]
    weekly = weekly_bars(bars, data['fetched_at'])
    long = classify(weekly, '1w'); medium = classify(bars, '1d')
    state = long['state']
    warnings = list(data.get('warnings', []))
    # Split-like discontinuity, regardless of being labelled adjusted by the provider.
    gaps = any(abs(bars[i]['open']/bars[i-1]['close']-1) > .35 for i in range(max(1, len(bars)-60), len(bars)))
    if gaps:
        state = 'unclear'
        long.update(state='unclear', label=LABELS['unclear'])
        long['counter_evidence'].append('最近60根日线出现超过35%的开盘跳变；先核对价格与复权，暂缓大趋势定性。')
    context = '周线与日线方向一致' if state == medium['state'] and state in ('up', 'down', 'range') else '不同周期尚未形成一致方向'
    short_return = medium.get('metrics', {}).get('return_20', 0)
    if state == 'up' and (medium['state'] == 'down' or short_return < -.02): context = '周线上涨，日线回调；先观察回调能否止住'
    if state == 'down' and (medium['state'] == 'up' or short_return > .02): context = '周线下跌，日线反弹；尚不能视为大趋势反转'
    if state in ('up', 'down') and medium['state'] in ('range', 'unclear') and abs(short_return) <= .02: context = '周线方向仍在，日线正在整理或出现分歧'
    if state == 'range' and medium['state'] == 'up': context = '周线盘整，日线走强；观察能否越过区间并维持'
    if state == 'range' and medium['state'] == 'down': context = '周线盘整，日线走弱；观察区间下沿是否失守'
    return {'algorithm_version': 'multi-horizon-trend-v1', 'state': state, 'label': LABELS[state],
            'context': context, 'weekly': long, 'daily': medium, 'playbooks': PLAYBOOKS,
            'basis': {'security_id': data['security_id'], 'source': data.get('source'), 'source_url': data.get('source_url'),
                      'fetched_at': data['fetched_at'], 'as_of': bars[-1]['time'] if bars else None,
                      'from': bars[0]['time'] if bars else None, 'adjustment': 'forward',
                      'adjustment_anchor': data.get('adjustment_anchor'), 'daily_count': len(bars), 'weekly_count': len(weekly),
                      'weekly_note': '由完成日线聚合；剔除起始可能不完整的一周与当前自然周。未核验交易日历完整性。'},
            'warnings': warnings, 'boundary': '价格趋势的定性观察，参数未经收益回测；不预测涨跌，也不自动形成买卖指令或个人纪律。'}

"""Point-in-time volume/price sequence inspired by a source, not its proprietary rule.

Every event owns fixed anchors and a chronological trace. No investor identity,
profitability, automatic order or personal discipline is inferred from OHLCV.
"""
import math
import statistics

from .analysis import validate_bars
from .common import DomainError, stamp

VERSION = 'volume-hold-v1'
SOURCE = 'https://www.xiaohongshu.com/explore/6980722f0000000021032aa8'
LABELS = {'event': '发现放量上影', 'holding': '整理守位中',
          'breakout_pending': '突破待确认', 'confirmed': '确认条件满足',
          'failed': '结构失效', 'expired': '观察到期'}


def analyse_volume_pattern(data, volume_ratio=2.0, max_wait=20, as_of=None):
    if isinstance(volume_ratio, bool) or not isinstance(volume_ratio, (int, float)) or not math.isfinite(volume_ratio) or not 1.2 <= volume_ratio <= 5:
        raise DomainError('INVALID_PARAMETER', '放量门槛支持1.2—5倍')
    if isinstance(max_wait, bool) or not isinstance(max_wait, int) or not 5 <= max_wait <= 40:
        raise DomainError('INVALID_PARAMETER', '观察期限支持5—40根完成日线')
    cutoff = stamp(as_of) if as_of else None
    # Validation and calculations only see the requested prefix, including in replay.
    prefix = {**data, 'bars': [b for b in data['bars'] if b.get('is_final') and
                              (cutoff is None or stamp(b['time']) <= cutoff)]}
    out = {'algorithm_version': VERSION, 'source_url': SOURCE, 'available': False,
           'as_of': prefix['bars'][-1]['time'] if prefix['bars'] else None,
           'security_id': data['security_id'], 'data_source': data.get('source'),
           'adjustment': data.get('adjustment'), 'count': len(prefix['bars']), 'events': [],
           'parameters': {'baseline': 20, 'volume_ratio': volume_ratio, 'upper_wick_min': .2,
                          'min_hold': 3, 'max_wait': max_wait, 'tolerance_atr': .25,
                          'breakout_atr': .15, 'breakout_volume_ratio': 1.2},
           'boundary': '保留放量上影、守位、回撤、再突破的观察顺序；数值是工具参数，非作者明示规则。主力试盘、洗盘与成本是解释假设。效果未经回测，不是买卖指令。',
           'unknowns': ['未独立核对行情提供方价格及交易日历完整性', '需另查大趋势、板块与事件背景',
                        '首版未复刻作者手工成本区与筹码分布判断，只用事件高低点作明确参考',
                        '回看基于当前来源的历史序列，不是当时保存的事前快照'],
           'reason': ''}
    if data.get('interval') != '1d':
        out['reason'] = '首版只观察完成日线；分钟成交量需要同一时段的独立基准。'
        return out
    if not prefix['bars']:
        out['reason'] = '所选时点没有完成日线。'
        return out
    quality = validate_bars(prefix)
    if not quality['valid']:
        raise DomainError('INVALID_MARKET_DATA', '; '.join(quality['errors']))
    bars = prefix['bars']
    volume_field = 'volume' if all('volume' in b for b in bars) else 'volume_raw'
    if not all(volume_field in b for b in bars):
        out['reason'] = '成交量字段口径不一致；暂不混用标准量与提供方原生量。'
        return out
    volumes = [b[volume_field] for b in bars]
    if any(not isinstance(v, (float, int)) or isinstance(v, bool) or not math.isfinite(v) or v <= 0 for v in volumes):
        out['reason'] = '存在缺失、异常或零成交量；先核对停牌与数据，不输出结构判断。'
        return out
    if len(bars) < 21:
        out['reason'] = '至少需要21根完成日线，才能与此前20根成交量比较。'
        return out
    # Ratios compare native units within one provider/series, never currency inflow.
    out['volume_basis'] = '同一来源与日线的原生量比；不换算实际资金流入。'
    if data.get('adjustment') not in {'none', 'forward'}:
        out['reason'] = '复权口径不支持，暂不跨口径计算。'
        return out
    for i in range(1, len(bars)):
        preceding = bars[max(0, i-14):i]
        atr = statistics.fmean(b['high']-b['low'] for b in preceding)
        if abs(bars[i]['open']-bars[i-1]['close']) > max(5*atr, bars[i-1]['close']*.18):
            out['reason'] = '窗口存在明显跳空，先核对除权或事件；本次不跨缺口判断。'
            return out
    for i in range(20, len(bars)):
        window = bars[i-20:i]
        atr = statistics.fmean(max(bars[j]['high']-bars[j]['low'],
                                   abs(bars[j]['high']-bars[j-1]['close']),
                                   abs(bars[j]['low']-bars[j-1]['close'])) for j in range(i-14, i))
        if abs(bars[i]['open']-bars[i-1]['close']) > max(5*atr, bars[i-1]['close']*.18):
            out['reason'] = '窗口存在明显跳空，先核对除权或事件；本次不跨缺口判断。'
            out['events'] = []
            return out
        b = bars[i]
        baseline = statistics.median(volumes[i-20:i])
        ratio = volumes[i]/baseline
        width = b['high']-b['low']
        wick = (b['high']-max(b['open'], b['close']))/width if width else 0
        if ratio < volume_ratio or wick < .2 or atr <= 0:
            continue
        support, pressure = b['low'], b['high']
        tolerance = min(atr*.25, support*.05)
        event = {'id': 'volume_' + b['time'], 'time': b['time'],
                 'volume_ratio': ratio, 'upper_wick': wick, 'atr': atr,
                 'levels': {'support': support, 'pressure': pressure,
                            'support_low': max(support-tolerance, support*.01),
                            'support_high': support+tolerance, 'known_at': b['time'],
                            'basis': '初始参考区取放量事件K线低点/高点；不称为庄家成本，未复刻作者所有手工选点。'},
                 'context': '此前20根收盘方向：' + ('上移' if window[-1]['close'] > window[0]['close'] else '下移' if window[-1]['close'] < window[0]['close'] else '持平'),
                 'trace': [], 'evidence': [f'相对此前20根成交量中位数{ratio:.2f}倍，上影占振幅{wick:.0%}。'],
                 'counter_evidence': ['放量上影同时可能对应卖出压力；守位也不能识别资金身份。']}
        state = 'event'
        down_streak = bearish_streak = hold_count = 0
        def add(j, reason, observation):
            event['trace'].append({'time': bars[j]['time'], 'state': state,
                                   'label': LABELS[state], 'reason': reason, 'observation': observation,
                                   'down_streak': down_streak, 'bearish_streak': bearish_streak,
                                   'support_test': j > i and bars[j]['low'] <= support+tolerance and bars[j]['close'] >= support-tolerance,
                                   'hold_count': hold_count,
                                   'volume_ratio': volumes[j]/baseline})
        add(i, '只标记候选，等待后续响应。', '尚未观察回撤')
        breakout = None
        for j in range(i+1, min(len(bars), i+max_wait+1)):
            row = bars[j]
            relative = volumes[j]/baseline
            down = row['close'] < bars[j-1]['close']
            down_streak = down_streak+1 if down else 0
            bearish_streak = bearish_streak+1 if row['close'] < row['open'] else 0
            observation = ('缩量回撤' if relative < .8 else '放量回撤，承接待核对' if relative >= 1.2 else '常量回撤') if down else '未形成收盘回撤'
            reason = '继续观察价格是否守位，不把回撤解释直接当成洗盘。'
            if row['close'] < support-tolerance:
                state, reason = 'failed', '收盘失守事件低点参考区。'
            elif breakout is not None and row['close'] < pressure-atr*.15:
                state, reason = 'failed', '突破后收盘跌回原压力区下方，突破结构失效。'
            elif breakout is not None and row['close'] >= pressure:
                state = 'confirmed'
                reason = ('回踩参考压力附近后收盘守住；只说明确认条件满足。' if row['low'] <= pressure+atr*.15 else
                          '突破后的后续收盘仍在参考压力上方；尚未出现价区回踩，只说明收盘确认条件满足。')
            elif breakout is not None:
                state, reason = 'breakout_pending', '突破后仍在容差区内，尚未确认站稳。'
            elif hold_count >= 3 and row['close'] > pressure+atr*.15 and relative >= 1.2:
                breakout = j
                state, reason = 'breakout_pending', '至少3根区内守位日线后放量收盘越过事件高点，等待后续守住。'
            elif support-tolerance <= row['close'] <= pressure+atr*.15:
                hold_count += 1
                if hold_count >= 3:
                    state = 'holding'
            else:
                hold_count = 0
                state, reason = 'event', '尚未形成至少3根连续区内守位日线，继续观察；直接拉升不算整理后的再突破。'
            if j-i == max_wait and state not in {'confirmed', 'failed'}:
                state, reason = 'expired', '达到预设观察期限，尚未满足确认条件。'
            add(j, reason, observation)
            if state in {'failed', 'expired'}:
                break
        event.update(state=state, label=LABELS[state], assessed_at=event['trace'][-1]['time'],
                     invalidation=f'收盘低于{support-tolerance:.2f}参考区下沿；突破后跌回{pressure-atr*.15:.2f}下方；{max_wait}根内未确认则到期。')
        out['events'].append(event)
    out.update(available=True, reason='已按时间顺序识别候选；失败与到期事件一并保留。' if out['events'] else '当前窗口没有满足放量上影门槛的候选；不强行推荐。')
    return out

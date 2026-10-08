"""Source-inspired observations, explicit proxy parameters, no buy/sell decisions."""
import math
import statistics
from datetime import date
from decimal import Decimal

from .analysis import validate_bars
from .common import DomainError

VERSION = 'trend-volume-check-v1'
SOURCE = 'https://www.xiaohongshu.com/explore/6aad32e10000000028028860'


def day(value):
    try:
        if not isinstance(value, str) or len(value) != 10:
            raise ValueError()
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise DomainError('INVALID_ARGUMENT', '日期请使用 YYYY-MM-DD')


def analyse_selection(data, lookback=40, volume_ratio=1.5, start_date=None, as_of=None):
    if isinstance(lookback, bool) or not isinstance(lookback, int) or lookback not in (20, 40, 60):
        raise DomainError('INVALID_ARGUMENT', '观察范围支持20、40或60根日线')
    if isinstance(volume_ratio, bool) or not isinstance(volume_ratio, (int, float)) or not math.isfinite(volume_ratio) or not 1.2 <= volume_ratio <= 5:
        raise DomainError('INVALID_ARGUMENT', '放量门槛支持1.2—5倍')
    cutoff = day(as_of) if as_of else None
    start = day(start_date) if start_date else None
    bars = [b for b in data['bars'] if b.get('is_final') and (not cutoff or b['time'][:10] <= cutoff)]
    prefix = {**data, 'bars': bars}
    out = {'algorithm_version': VERSION, 'available': False, 'reason': '', 'checks': [], 'events': [], 'parameters': {'lookback': lookback, 'volume_ratio': volume_ratio, 'baseline': 20, 'min_groups': 2},
           'as_of': bars[-1]['time'] if bars else None, 'source_url': SOURCE,
           'boundary': '视频启发的量价筛查；数值参数为工具代理规则，未验证交易效果。小趋势不等于大趋势反转，达标不等于买入，不识别主力身份或真实筹码成本。'}
    if data.get('interval') != '1d' or data.get('adjustment') not in ('none', 'forward'):
        out['reason'] = '需要同一复权口径的完成日线。'
        return out
    if len(bars) < lookback + 20:
        out['reason'] = f'至少需要{lookback+20}根完成日线，含观察前20根成交量基准。'
        return out
    quality = validate_bars(prefix)
    if not quality['valid']:
        raise DomainError('INVALID_MARKET_DATA', '; '.join(quality['errors']))
    field = 'volume' if all('volume' in b for b in bars) else 'volume_raw'
    volumes = [b.get(field) for b in bars]
    if any(not isinstance(v, (float, int)) or isinstance(v, bool) or not math.isfinite(v) or v <= 0 for v in volumes):
        out['reason'] = '成交量缺失、混用字段或存在零量；先核对停牌和数据。'
        return out
    for i in range(max(1, len(bars)-lookback-20), len(bars)):
        atr = statistics.fmean(b['high']-b['low'] for b in bars[max(0,i-14):i])
        if abs(bars[i]['open']-bars[i-1]['close']) > max(5*atr, bars[i-1]['close']*.18):
            out['reason'] = '观察范围存在明显跳空；先核对除权与事件，不跨缺口定性。'
            return out
    recent = bars[-lookback:]
    closes = [b['close'] for b in bars]
    ma5 = [statistics.fmean(closes[i-4:i+1]) for i in range(len(bars)-8, len(bars))]
    above = sum(closes[-5+i] >= ma5[-5+i] for i in range(5))
    small_up = all(ma5[i] > ma5[i-1] for i in range(len(ma5)-3, len(ma5))) and above >= 4
    highs, lows = [], []
    # Two right-hand completed bars are required; unconfirmed newest turns stay absent.
    for i in range(2, len(recent)-2):
        for key, target, compare in [('high', highs, lambda a,b:a>b), ('low', lows, lambda a,b:a<b)]:
            if all(compare(recent[i][key], recent[j][key]) for j in range(i-2,i+3) if j != i):
                target.append({'time':recent[i]['time'], 'price':recent[i][key], 'known_at':recent[i+2]['time']})
    structure = 'unknown'
    if len(highs) >= 2 and len(lows) >= 2:
        hh, hl = highs[-1]['price'] > highs[-2]['price'], lows[-1]['price'] > lows[-2]['price']
        lh, ll = highs[-1]['price'] < highs[-2]['price'], lows[-1]['price'] < lows[-2]['price']
        structure = 'up' if hh and hl else 'down' if lh and ll else 'mixed'
    broken = bool(lows and closes[-1] < lows[-1]['price'])
    trend_state = 'watch' if broken or structure == 'down' else 'pass' if structure == 'up' or small_up else 'unknown'
    out['checks'].append({'id':'trend','title':'上涨结构','state':trend_state,
        'summary': '结构走弱，先复查' if trend_state == 'watch' else '高低点抬高' if structure == 'up' else '5日均线小趋势线索' if small_up else '上涨证据尚不足',
        'detail': f'最近5根收盘有{above}根在5日均线上；均线近3次变化' + ('连续向上。' if small_up else '未满足连续向上及收盘条件。') + ' 两组高低点：' + {'up':'均抬高','down':'均降低','mixed':'方向不一','unknown':'不足'}[structure] + ('；最新收盘低于最近确认低点。' if broken else '。'),
        'highs': highs[-2:], 'lows': lows[-2:], 'small_up': small_up, 'structure': structure, 'ma5': ma5[-1]})
    observed, groups, bears, previous_event = [], 0, [], -2
    for i in range(len(bars)-lookback, len(bars)):
        b = bars[i]; baseline = statistics.median(volumes[i-20:i]); ratio = volumes[i]/baseline
        rising = b['close'] > bars[i-1]['close']; bearish = b['close'] < b['open']
        qualified = rising and ratio >= volume_ratio
        if qualified:
            if i != previous_event+1: groups += 1
            previous_event = i
            out['events'].append({'time':b['time'],'ratio':ratio,'close':b['close'],'group':groups,'bearish':bearish})
        if bearish: bears.append(ratio)
        observed.append({'time':b['time'],'ratio':ratio,'rising':rising,'bearish':bearish,'qualified':qualified})
    bear_median = statistics.median(bears) if bears else None
    heavy_bears = sum(v >= volume_ratio for v in bears)
    volume_state = 'watch' if groups < 2 or heavy_bears or (bear_median is not None and bear_median > 1.05) else 'unknown' if len(bears) < 2 else 'pass'
    out['checks'].append({'id':'volume','title':'反复放量与回撤量能','state':volume_state,
        'summary': '量价配合有待复查' if volume_state == 'watch' else '阴线样本不足' if volume_state == 'unknown' else '反复放量，回撤量能较温和',
        'detail': f'{lookback}根观察日线中，上涨放量{len(out["events"])}根、分为{groups}组；阴线{len(bears)}根，其中放量阴线{heavy_bears}根。' + (f'阴线量比中位数{bear_median:.2f}倍。' if bear_median is not None else '没有阴线量能样本。') + '逐根量比对照此前20根成交量中位数，分母不含当天；相邻放量日归同一组。',
        'groups':groups,'bear_count':len(bears),'heavy_bears':heavy_bears,'bear_median':bear_median,'observations':observed})
    changes = {str(n):closes[-1]/closes[-n-1]-1 for n in (5,20,60) if len(closes)>n}
    gain, anchor = None, None
    if start:
        anchor = next((b for b in bars if b['time'][:10] == start), None)
        if anchor is None or anchor['time'] == bars[-1]['time']:
            raise DomainError('INVALID_ARGUMENT', '涨幅起点须为截至观察日之前、已载入的完成交易日')
        gain = float(Decimal(str(closes[-1]))/Decimal(str(anchor['close']))-1)
    gain_state = 'unknown' if gain is None else 'pass' if 0 <= gain < .20 else 'watch'
    out['checks'].append({'id':'gain','title':'近期涨幅与追涨压力','state':gain_state,
        'summary': '先选择这段上涨的起点' if gain is None else '这段收盘涨幅小于20%' if gain_state == 'pass' else '这段涨幅需重新评估',
        'detail': '不自动猜作者的短期起点。' if gain is None else f'从{start}收盘到{bars[-1]["time"][:10]}收盘变化{gain:+.2%}。' + ('上涨已达到20%，与作者优先筛选条件有差异。' if gain >= .20 else '负收益不代表低风险。' if gain < 0 else '仍需检查起点是否符合自己的观察周期。'),
        'start_date':start,'start_close':anchor['close'] if anchor else None,'gain':gain,'changes':changes,
        'note':'20%是作者经验线，未独立验证为普遍心理阈值；收盘到收盘是本工具代理口径，不是获利筹码占比。'})
    states = [c['state'] for c in out['checks']]
    out.update(available=True, state='watch' if 'watch' in states else 'unknown' if 'unknown' in states else 'pass',
               label='存在需要复查的条件' if 'watch' in states else '仍有条件待核对' if 'unknown' in states else '观察条件齐备，继续核对计划',
               reason='依次检查背景、量价、涨幅；三项不合成庄家概率或买入评分。',
               from_date=recent[0]['time'], dates=[b['time'][:10] for b in bars],
               counter_evidence=['短线上涨可能只是较大下跌趋势中的反弹，需另看周线。','新闻、市场反弹和不同参与者换手也能形成反复放量。','20%经验线与量比参数没有收益回测支持。'])
    return out

"""Reusable, unit-independent observations; never investor identity or cash inflow."""
import math
import statistics

from .analysis import validate_bars
from .common import DomainError
from .fund_signals import participation

CMF_SOURCE = 'https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/chaikin-money-flow-cmf'


def study(data):
    quality = validate_bars(data)
    if not quality['valid']:
        raise DomainError('INVALID_MARKET_DATA', '; '.join(quality['errors']))
    bars = [b for b in data['bars'] if b.get('is_final')]
    if not bars:
        raise DomainError('INSUFFICIENT_DATA', '没有已完成K线')
    out = {'count': len(bars), 'interval': data['interval'], 'price_change_5': bars[-1]['close']/bars[-6]['close']-1 if len(bars)>=6 else None,
           'price_change_20': bars[-1]['close']/bars[-21]['close']-1 if len(bars)>=21 else None,
           'volume_ratio_20': None, 'cmf_20': None, 'up_volume_share_20': None,
           'volume_available': False, 'observations': [], 'series': [], 'algorithm_version': 'market-activity-v1',
           'boundary': '量价指标是资金参与的间接线索，不识别投资者身份，不计算实际资金净流入。',
           'formula_source': CMF_SOURCE}
    if len(bars) < 21:
        out['observations'] = ['完整K线少于21根，先展示价格图；资金活跃度指标暂不可计算。']
        out['participation'] = participation(data, out)
        return out
    # Native volume is only compared within this same series. No share/yuan conversion.
    volumes = [b.get('volume', b.get('volume_raw')) for b in bars]
    available = all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0 for v in volumes)
    if not available or sum(volumes[-20:]) <= 0:
        out['observations'] = ['成交量缺失、异常或近20根为零，资金活跃度指标暂不可计算。']
        out['participation'] = participation(data, out)
        return out
    total = sum(volumes[-20:])
    baseline = statistics.fmean(volumes[-21:-1])
    ratio = volumes[-1]/baseline if baseline else None
    cmf = sum((0 if b['high'] == b['low'] else (2*b['close']-b['high']-b['low'])/(b['high']-b['low']))*v
              for b, v in zip(bars[-20:], volumes[-20:]))/total
    up = sum(volumes[i] for i in range(len(bars)-20, len(bars)) if bars[i]['close'] > bars[i-1]['close'])/total
    out.update(volume_available=True, volume_ratio_20=ratio, cmf_20=cmf, up_volume_share_20=up)
    if ratio is not None:
        out['observations'].append(f'最新完整K线成交量为此前20根均量的{ratio:.2f}倍；比较的是同一来源、同一周期。')
    out['observations'].append(f'近20根K线中，上涨K线占成交量的{up:.1%}；这不是主动买入占比。')
    out['observations'].append('近20根量加权收盘位置偏向区间' + ('上部' if cmf > 0 else '下部' if cmf < 0 else '中部') + '；跳空时可能与价格涨跌方向不同。')
    for i in range(len(bars)):
        window = bars[max(0, i-19):i+1]
        vv = volumes[max(0, i-19):i+1]
        denominator = sum(vv)
        value = sum((0 if b['high'] == b['low'] else (2*b['close']-b['high']-b['low'])/(b['high']-b['low']))*v for b,v in zip(window,vv))/denominator if i >= 19 and denominator else None
        out['series'].append({'time': bars[i]['time'], 'cmf_20': value})
    out['participation'] = participation(data, out)
    return out

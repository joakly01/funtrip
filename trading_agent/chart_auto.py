"""Reproducible chart hypotheses, with no future extrapolation or trading signals."""
import statistics

from .analysis import validate_bars
from .common import DomainError

VERSION = 'chart-pattern-v1'
SOURCES = [
    'https://chartschool.stockcharts.com/table-of-contents/chart-analysis/support-and-resistance',
    'https://chartschool.stockcharts.com/table-of-contents/chart-analysis/trend-lines',
]


def quantile(values, q):
    values = sorted(values)
    p = (len(values)-1)*q
    i = int(p)
    return values[i] + (values[min(i+1, len(values)-1)]-values[i])*(p-i)


def analyse_chart(data, lookback=80):
    if lookback not in (40, 80, 160):
        raise DomainError('INVALID_WINDOW', '自动分析支持40、80、160根观察窗口')
    quality = validate_bars(data)
    if not quality['valid']:
        raise DomainError('INVALID_MARKET_DATA', '; '.join(quality['errors']))
    bars = [b for b in data['bars'] if b.get('is_final')][-lookback:]
    n = len(bars)
    modes = [dict(id=k, title=t, available=False, drawings=[], evidence=[], reason='结构依据不足')
             for k, t in [('trend', '趋势通道'), ('range', '横盘区间'), ('levels', '关键价位')]]
    out = dict(algorithm_version=VERSION, lookback=lookback, count=n, recommended=None,
               reason='结构依据不足，暂不自动画线。', modes=modes,
               boundary='自动线条是可复查的历史结构候选，不是买卖信号或缠论笔、中枢；不外推未来价格。',
               parameters={'pivot_sides':2, 'level_tolerance_atr':0.6, 'trend_min_r2':0.5,
                           'trend_min_move_atr':3, 'range_max_r2':0.35, 'range_max_width_atr':8},
               sources=SOURCES)
    if n:
        out.update(start_at=bars[0]['time'], as_of=bars[-1]['time'])
    if n < 24:
        out['reason'] = '完整K线少于24根，暂不推荐画线模式。'
        return out
    trs = [max(bars[i]['high']-bars[i]['low'], abs(bars[i]['high']-bars[i-1]['close']),
               abs(bars[i]['low']-bars[i-1]['close'])) for i in range(1, n)]
    atr = statistics.fmean(trs[-14:])
    closes = [b['close'] for b in bars]
    mean = statistics.fmean(closes)
    variance = sum((c-mean)**2 for c in closes)
    if atr <= 0 or variance < mean**2*1e-12:
        out['reason'] = '价格几乎没有变化，暂不生成结构线。'
        return out
    if any(abs(bars[i]['open']-bars[i-1]['close']) > max(5*atr, bars[i-1]['close']*.18) for i in range(1,n)):
        out['reason'] = '观察窗口存在明显跳空，需先核对除权或事件；暂不跨缺口拟合结构。'
        return out
    xm = (n-1)/2
    slope = sum((i-xm)*(c-mean) for i,c in enumerate(closes))/sum((i-xm)**2 for i in range(n))
    line = [mean + slope*(i-xm) for i in range(n)]
    r2 = max(0, 1-sum((c-y)**2 for c,y in zip(closes,line))/variance)
    move = abs(slope)*(n-1)/atr
    out['metrics'] = dict(atr_14=atr, regression_r2=r2, fitted_move_atr=move)

    def point(i, price):
        return {'time':bars[i]['time'], 'price':round(price, 6)}

    def drawing(key, kind, prices, label):
        return dict(id='auto_'+key, kind=kind, points=[point(i,p) for i,p in prices], label=label)

    pivots = []
    for i in range(2, n-2):
        for kind in ('low','high'):
            p = bars[i][kind]
            neighbors = [bars[j][kind] for j in range(i-2,i+3) if j != i]
            if (p < min(neighbors) if kind == 'low' else p > max(neighbors)):
                pivots.append(dict(index=i, price=p, kind=kind, time=bars[i]['time'], confirmed_at=bars[i+2]['time']))
    trend, range_mode, levels = modes
    lower = quantile([b['low']-y for b,y in zip(bars,line)], .1)
    upper = quantile([b['high']-y for b,y in zip(bars,line)], .9)
    trend['reason'] = f'直线拟合度 R²={r2:.2f}，拟合位移={move:.1f}倍近期波幅；需 R²≥0.50、位移≥3倍。'
    if r2 >= .5 and move >= 3 and min(line[0]+lower,line[-1]+lower) > 0:
        trend.update(available=True, reason=('上行' if slope > 0 else '下行')+'方向较连贯，优先用统计趋势通道观察。',
                     drawings=[drawing('trend_'+key,'trend',[(0,line[0]+shift),(n-1,line[-1]+shift)],label)
                               for key,shift,label in [('mid',0,'拟合中线'),('low',lower,'通道下沿'),('high',upper,'通道上沿')]],
                     evidence=[f'观察{n}根完整K线，R²={r2:.2f}；这不是预测正确率。',
                               '收盘价最小二乘拟合；上下沿采用低/高价残差的10%/90%分位，允许极端K线超出。',
                               '此模式是统计通道，并非连接转折点确认的传统趋势线。'])
    # Range boundaries use preceding bars; the latest close is checked separately.
    low = quantile([b['low'] for b in bars[:-1]], .1)
    high = quantile([b['high'] for b in bars[:-1]], .9)
    touches_low = [p for p in pivots if p['kind']=='low' and abs(p['price']-low) <= atr*.7]
    touches_high = [p for p in pivots if p['kind']=='high' and abs(p['price']-high) <= atr*.7]
    inside = low-atr*.6 <= closes[-1] <= high+atr*.6
    range_mode['reason'] = '需走势拟合度<0.35、区间宽度≤8倍近期波幅，且上下沿各有至少2次已确认转折。'
    if r2 < .35 and (high-low)/atr <= 8 and len(touches_low)>=2 and len(touches_high)>=2 and inside:
        range_mode.update(available=True, reason='价格反复往返，方向不明显，优先观察横盘上下沿。',
                          drawings=[drawing('range','zone',[(0,low),(n-1,high)],'候选横盘区间'),
                                    drawing('range_low','horizontal',[(0,low)],'区间下沿'),
                                    drawing('range_high','horizontal',[(0,high)],'区间上沿')],
                          evidence=[f'上下沿约{low:.2f} / {high:.2f}；附近转折分别{len(touches_low)} / {len(touches_high)}次。',
                                    '边界采用此前K线低价10%及高价90%分位；最新收盘单独检查是否离开区间。'],
                          anchors=touches_low+touches_high)
    elif not inside:
        range_mode['reason'] = '最新收盘已离开历史区间容差，暂不推荐继续按横盘观察。'
    # Cluster only confirmed swing points, not every candle at a nearby price.
    groups = []
    for p in sorted(pivots, key=lambda p:p['price']):
        if not groups or p['price']-groups[-1][0]['price'] > atr*1.2:
            groups.append([p])
        else:
            groups[-1].append(p)
    qualified = []
    for group in groups:
        spaced = []
        for p in sorted(group,key=lambda p:p['index']):
            if not spaced or p['index']-spaced[-1]['index'] >= 4:
                spaced.append(p)
        if len(spaced) >= 2:
            qualified.append((statistics.fmean(p['price'] for p in spaced),spaced))
    qualified.sort(key=lambda g:(abs(g[0]-closes[-1]),-len(g[1])))
    if qualified:
        selected = sorted(qualified[:4],key=lambda g:g[0])
        levels.update(available=True, reason='重复转折形成可参考价位；结构较复杂时，先看这些位置。',
                      drawings=[drawing('level_'+str(i),'horizontal',[(group[-1]['index'],price)],
                                ('参考支撑' if price < closes[-1] else '参考阻力')+f' · {len(group)}次转折')
                                for i,(price,group) in enumerate(selected)],
                      evidence=['仅聚合左右各2根确认的局部高低点；最后2根尚不能确认新转折。',
                                '同一组价差≤1.2倍近期波幅，重复转折至少间隔4根；相近价位应作为区域理解。'],
                      anchors=[p for _,group in selected for p in group])
    for candidate in modes:
        if candidate['available']:
            out.update(recommended=candidate['id'], reason=candidate['reason'])
            break
    return out

import copy
import unittest
from test_chart_auto import chart
from trading_agent.market_study import study


def sample(kind='up'):
    data=chart(kind,80)
    for i,b in enumerate(data['bars']):
        c=20+.12*i if kind=='up' else 40-.12*i
        b.update(open=c,close=c); b['volume_raw']=100
        b.update(high=c+(.05 if kind=='up' else .45),low=c-(.45 if kind=='up' else .05))
    return data


class FundSignalsTests(unittest.TestCase):
    def test_buy_side_and_breakout_not_holder_identity(self):
        data=sample()
        for b in data['bars'][-5:]:b['volume_raw']=200
        out=study(data)['participation']
        self.assertEqual(out['degree'],'较高')
        self.assertEqual(out['metrics']['relative_volume_5'],2)
        self.assertEqual(out['participation'],'有持续放量迹象')
        self.assertEqual(out['bias'],'偏买方的量价配合')
        self.assertEqual(out['recommended'],'breakout')
        self.assertIn('无法确认',out['identity'])
        self.assertNotIn('holder_position',out)
        other=copy.deepcopy(data)
        for b in other['bars']:b['volume_raw']*=100
        self.assertEqual(study(other)['participation'],out)

    def test_sell_pressure_is_not_buying_or_accumulation(self):
        data=sample('down')
        for b in data['bars'][-5:]:b['volume_raw']=200
        out=study(data)['participation']
        self.assertEqual(out['degree'],'较高')
        self.assertEqual(out['bias'],'偏卖方的量价配合')
        self.assertEqual(out['recommended'],'selling')

    def test_single_spike_is_not_sustained_participation(self):
        data=sample();data['bars'][-1]['volume_raw']=200
        out=study(data)['participation']
        self.assertEqual(out['degree'],'有所增强')
        self.assertIn('单根',out['participation'])
        self.assertFalse(next(m for m in out['modes'] if m['id']=='persistent')['matched'])

    def test_low_volume_decline_and_conflicting_cmf(self):
        data=sample('down')
        for b in data['bars'][-5:]:b['volume_raw']=50
        out=study(data)['participation']
        self.assertEqual(out['degree'],'偏低')
        self.assertEqual(out['recommended'],'pullback')
        data=sample('down')
        for b in data['bars']:
            c=b['close'];b.update(high=c+.05,low=c-.45)
        self.assertEqual(study(data)['participation']['recommended'],'divergence')

    def test_unavailable_volume_short_sample_and_gap(self):
        data=sample();data['bars'][0]['volume_raw']=None
        self.assertFalse(study(data)['participation']['available'])
        data=sample();data['bars']=data['bars'][:25]
        self.assertFalse(study(data)['participation']['available'])
        data=sample()
        for b in data['bars'][-3:]:
            for k in ['open','high','low','close']:b[k]+=10
        out=study(data)['participation']
        self.assertFalse(out['available'])
        self.assertIn('跳空',out['reason'])
        self.assertEqual(out['degree'],'暂无法判断')

    def test_zero_baseline_and_unfinished_exclusion(self):
        data=sample()
        for b in data['bars'][-25:-5]:b['volume_raw']=0
        self.assertFalse(study(data)['participation']['available'])
        data=sample();before=study(data)['participation']
        extra=copy.deepcopy(data['bars'][-1])
        extra.update(time='2026-10-07T10:00:00+08:00',is_final=False,volume_raw=100000)
        data['bars'].append(extra)
        self.assertEqual(study(data)['participation'],before)

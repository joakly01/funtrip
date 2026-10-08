import copy
import math
import unittest
from datetime import datetime, timedelta, timezone
from trading_agent.chart_auto import analyse_chart
from trading_agent.common import DomainError


def chart(kind='range', count=80):
    start = datetime(2026,1,1,15,tzinfo=timezone(timedelta(hours=8)))
    rows=[]
    for i in range(count):
        c = (20 + .12*i + .3*math.sin(i*math.pi/6) if kind=='up' else
             40 - .12*i + .3*math.sin(i*math.pi/6) if kind=='down' else
             20 + 1.1*math.sin(i*math.pi/6) if kind=='range' else
             10 + (i%32 if i%32 <= 16 else 32-i%32)*.625 if kind=='levels' else 20)
        rows.append(dict(time=(start+timedelta(days=i)).isoformat(),open=c,close=c,high=c+.15,low=c-.15,is_final=True))
    return dict(bars=rows, fetched_at='2026-10-07T12:00:00+08:00',warnings=[],interval='1d',adjustment='none')


class AutoChartTests(unittest.TestCase):
    def test_direction_and_exact_regression_endpoints(self):
        for kind in ['up','down']:
            with self.subTest(kind=kind):
                data=chart(kind);out=analyse_chart(data)
                self.assertEqual(out['recommended'],'trend')
                candidate=out['modes'][0]
                self.assertEqual(len(candidate['drawings']),3)
                mid=candidate['drawings'][0]['points']
                self.assertEqual(mid[0]['time'],data['bars'][0]['time'])
                self.assertEqual(mid[-1]['time'],data['bars'][-1]['time'])
                self.assertEqual(mid[1]['price'] > mid[0]['price'],kind=='up')
                self.assertGreaterEqual(out['metrics']['regression_r2'],.5)

    def test_repeated_range_and_manual_alternative(self):
        out=analyse_chart(chart())
        self.assertEqual(out['recommended'],'range')
        self.assertTrue(out['modes'][2]['available'])
        zone=out['modes'][1]['drawings'][0]
        self.assertEqual(zone['kind'],'zone')
        self.assertLess(zone['points'][0]['price'],zone['points'][1]['price'])
        self.assertTrue(all(a['confirmed_at'] > a['time'] for a in out['modes'][1]['anchors']))

    def test_wide_swings_fall_back_to_repeated_levels(self):
        out=analyse_chart(chart('levels',160),160)
        self.assertEqual(out['recommended'],'levels')
        self.assertFalse(out['modes'][1]['available'])
        self.assertTrue(1 <= len(out['modes'][2]['drawings']) <= 4)
        self.assertTrue(all(d['kind']=='horizontal' for d in out['modes'][2]['drawings']))

    def test_not_forced_for_flat_short_or_abrupt_gap(self):
        for data in [chart('flat'),chart(count=20)]:
            self.assertIsNone(analyse_chart(data)['recommended'])
            self.assertFalse(any(m['drawings'] for m in analyse_chart(data)['modes']))
        data=chart('up')
        for b in data['bars'][40:]:
            for k in ['open','high','low','close']:b[k] += 20
        self.assertIsNone(analyse_chart(data)['recommended'])
        self.assertIn('跳空',analyse_chart(data)['reason'])

    def test_window_unfinished_and_invalid_inputs(self):
        data=chart('up',160);before=analyse_chart(data,40)
        extra=copy.deepcopy(data['bars'][-1]);extra.update(time='2026-10-07T10:00:00+08:00',is_final=False)
        data['bars'].append(extra)
        self.assertEqual(analyse_chart(data,40),before)
        self.assertEqual(before['count'],40)
        self.assertNotIn('win_rate',before)
        with self.assertRaises(DomainError):analyse_chart(data,100)
        data['bars'][0]['high']=1
        with self.assertRaises(DomainError):analyse_chart(data)

    def test_latest_range_break_is_not_recommended_as_sideways(self):
        data=chart();b=data['bars'][-1]
        b.update(open=23,high=23.1,low=22.8,close=23)
        out=analyse_chart(data)
        self.assertFalse(out['modes'][1]['available'])
        self.assertIn('离开',out['modes'][1]['reason'])

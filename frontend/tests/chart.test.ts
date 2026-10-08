import test from "node:test";
import assert from "node:assert/strict";
import {
  chartScale,
  pointAt,
  drawingCoordinates,
  plot,
  sameDrawing,
  type Bar,
} from "../src/domain/chart";
const bars: Bar[] = Array.from({ length: 40 }, (_, i) => ({
  time: `2026-08-${String(i + 1).padStart(2, "0")}`,
  open: 10 + i,
  high: 12 + i,
  low: 9 + i,
  close: 11 + i,
}));
test("屏幕坐标转换保留时间与价格，缩放后标注仍对应原K线", () => {
  const scale = chartScale(bars);
  const p = pointAt(bars, scale.x(20), scale.y(30))!;
  assert.equal(p.time, bars[20].time);
  assert.equal(p.price, 30);
  assert.equal(pointAt(bars, plot.right + 1, 100), null);
  const d = {
    id: "x",
    kind: "trend" as const,
    points: [p, { time: bars[30].time, price: 40 }],
    label: "",
  };
  const coords = drawingCoordinates(d, bars.slice(10))!;
  assert.equal(coords[0].x, chartScale(bars.slice(10)).x(10));
  assert.equal(drawingCoordinates(d, bars.slice(25)), null);
  assert.ok(
    drawingCoordinates(
      { ...d, kind: "horizontal", points: [p] },
      bars.slice(25),
    ),
  );
});

test('保存后的同一条自动线只显示一次，价格或时点变化仍显示新候选', () => {
  const a = {id:'auto',kind:'trend' as const,points:[{time:'a',price:10},{time:'b',price:12}],label:'拟合线'};
  assert.ok(sameDrawing(a,{...a,id:'saved',label:'我的备注'}));
  assert.ok(!sameDrawing(a,{...a,points:[a.points[0],{time:'b',price:12.1}]}));
  assert.ok(!sameDrawing(a,{...a,points:[a.points[0],{time:'c',price:12}]}));
});

test('自动通道超出K线高低点时仍完整显示，点击与线条使用同一个价格轴', () => {
  const prices = [5, 70];
  const scale = chartScale(bars, prices);
  assert.ok(scale.y(5) < plot.bottom);
  assert.ok(scale.y(70) > plot.top);
  const p = pointAt(bars, scale.x(10), scale.y(5), prices)!;
  assert.equal(p.price,5);
  const d = {id:'auto',kind:'trend' as const,points:[p,{time:bars[20].time,price:70}],label:''};
  const coords = drawingCoordinates(d,bars,prices)!;
  assert.equal(coords[0].y,scale.y(5));
  assert.equal(coords[1].y,scale.y(70));
});

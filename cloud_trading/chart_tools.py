"""Owner-created drawings, separate from trading facts and knowledge objects."""
import json
import sqlite3
from pathlib import Path
from typing import Literal

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field

from trading_agent.common import now, stamp
from trading_agent.market import security_code


class Point(BaseModel):
    time: str = Field(max_length=40)
    price: float = Field(gt=0, allow_inf_nan=False)


class AutoOrigin(BaseModel):
    algorithm_version: str = Field(max_length=40)
    mode: Literal['trend', 'range', 'levels']
    lookback: Literal[40, 80, 160]
    as_of: str = Field(max_length=40)


class Drawing(BaseModel):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    kind: Literal['horizontal', 'trend', 'zone']
    points: list[Point] = Field(min_length=1, max_length=2)
    label: str = Field(default='', max_length=60)
    origin: AutoOrigin | None = None


class DrawingInput(BaseModel):
    security_id: str = Field(pattern=r'^(?:SSE|SZSE|BSE):\d{6}$')
    interval: Literal['1d', '5m', '15m', '30m', '60m']
    expected_version: int = Field(ge=0)
    drawings: list[Drawing] = Field(max_length=50)


def install_chart_routes(app, owner, call, directory):
    path = Path(directory)/'chart-annotations.sqlite'

    def connect():
        con = sqlite3.connect(path, timeout=10)
        con.execute('CREATE TABLE IF NOT EXISTS charts(key TEXT PRIMARY KEY, version INTEGER NOT NULL, body TEXT NOT NULL, updated_at TEXT NOT NULL)')
        return con

    def key(security_id, interval):
        security_code(security_id)
        return security_id + ':' + interval + ':none'

    @app.get('/api/tools/study', dependencies=[Depends(owner)])
    def market_study(query: str = Query(default='', max_length=40),
                     security_id: str = Query(default='', pattern=r'^(?:(?:SSE|SZSE|BSE):\d{6})?$'),
                     interval: Literal['1d', '5m', '15m', '30m', '60m'] = '1d'):
        identity = None
        if not security_id:
            if not query.strip():
                raise HTTPException(400, '请输入股票名称或代码')
            resolved = call('security_resolve', {'query': query})
            identity = resolved.get('resolved')
            if not identity:
                return {'data': None, **resolved}
            security_id = identity['security_id']
        return {**call('market_study', {'security_id': security_id, 'interval': interval, 'count': 240}),
                'identity': identity}

    @app.get('/api/tools/trend', dependencies=[Depends(owner)])
    def trend(query: str = Query(default='', max_length=40),
              security_id: str = Query(default='', pattern=r'^(?:(?:SSE|SZSE|BSE):\d{6})?$')):
        identity = None
        if not security_id:
            if not query.strip():
                raise HTTPException(400, '请输入股票名称或代码')
            resolved = call('security_resolve', {'query': query})
            identity = resolved.get('resolved')
            if not identity:
                return {'analysis': None, **resolved}
            security_id = identity['security_id']
        return {'analysis': call('market_trend', {'security_id': security_id}), 'identity': identity}

    @app.get('/api/tools/selection', dependencies=[Depends(owner)])
    def selection(query: str = Query(default='', max_length=40),
                  security_id: str = Query(default='', pattern=r'^(?:(?:SSE|SZSE|BSE):\d{6})?$'),
                  lookback: Literal['20', '40', '60'] = '40',
                  volume_ratio: float = Query(default=1.5, ge=1.2, le=5),
                  start_date: str | None = Query(default=None, max_length=10),
                  as_of: str | None = Query(default=None, max_length=10)):
        identity = None
        if not security_id:
            if not query.strip():
                raise HTTPException(400, '请输入股票名称或代码')
            resolved = call('security_resolve', {'query': query})
            identity = resolved.get('resolved')
            if not identity:
                return {'analysis': None, **resolved}
            security_id = identity['security_id']
        args = {'security_id': security_id, 'lookback': int(lookback), 'volume_ratio': volume_ratio}
        if start_date: args['start_date'] = start_date
        if as_of: args['as_of'] = as_of
        return {**call('market_selection', args), 'identity': identity}

    @app.get('/api/tools/pattern', dependencies=[Depends(owner)])
    def pattern(query: str = Query(default='', max_length=40),
                security_id: str = Query(default='', pattern=r'^(?:(?:SSE|SZSE|BSE):\d{6})?$'),
                volume_ratio: float = Query(default=2, ge=1.2, le=5),
                max_wait: int = Query(default=20, ge=5, le=40),
                as_of: str | None = Query(default=None, max_length=40)):
        identity = None
        if not security_id:
            if not query.strip():
                raise HTTPException(400, '请输入股票名称或代码')
            resolved = call('security_resolve', {'query': query})
            identity = resolved.get('resolved')
            if not identity:
                return {'analysis': None, **resolved}
            security_id = identity['security_id']
        args = {'security_id': security_id, 'volume_ratio': volume_ratio, 'max_wait': max_wait}
        if as_of:
            args['as_of'] = as_of
        return {**call('market_pattern', args), 'identity': identity}

    @app.get('/api/tools/drawings', dependencies=[Depends(owner)])
    def drawings(security_id: str = Query(pattern=r'^(?:SSE|SZSE|BSE):\d{6}$'),
                 interval: Literal['1d', '5m', '15m', '30m', '60m'] = '1d'):
        chart_key = key(security_id, interval)
        if not path.exists():
            return {'version': 0, 'drawings': [], 'updated_at': None}
        with connect() as con:
            row = con.execute('SELECT version,body,updated_at FROM charts WHERE key=?', (chart_key,)).fetchone()
        return {'version': row[0], 'drawings': json.loads(row[1]), 'updated_at': row[2]} if row else {'version': 0, 'drawings': [], 'updated_at': None}

    @app.put('/api/tools/drawings', dependencies=[Depends(owner)])
    def save_drawings(body: DrawingInput):
        chart_key = key(body.security_id, body.interval)
        ids = set()
        for drawing in body.drawings:
            if len(drawing.points) != (1 if drawing.kind == 'horizontal' else 2) or drawing.id in ids:
                raise HTTPException(400, '标注端点数量或编号无效')
            ids.add(drawing.id)
            if drawing.origin:
                try:
                    stamp(drawing.origin.as_of)
                except Exception:
                    raise HTTPException(400, '自动标注行情时间无效')
            for point in drawing.points:
                try:
                    stamp(point.time)
                except Exception:
                    raise HTTPException(400, '标注时间无效')
        with connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT version FROM charts WHERE key=?', (chart_key,)).fetchone()
            version = row[0] if row else 0
            if version != body.expected_version:
                raise HTTPException(409, '标注已在另一端更新，请重新载入后再编辑；当前未保存标注仍保留。')
            updated = now()
            encoded = json.dumps([d.model_dump(exclude_none=True) for d in body.drawings], ensure_ascii=False)
            con.execute('INSERT INTO charts VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET version=excluded.version,body=excluded.body,updated_at=excluded.updated_at',
                        (chart_key, version+1, encoded, updated))
        return {'version': version+1, 'updated_at': updated}

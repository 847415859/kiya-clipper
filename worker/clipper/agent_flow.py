"""Isolated, resumable candidate production. Role adapters return data, never commands."""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar
from uuid import uuid4

from .operations import DailyLedger, beijing_day

T = TypeVar('T')


def now():
    return datetime.now(timezone.utc).isoformat()


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f'.{path.name}.{uuid4().hex}.tmp')
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def identity(path):
    stat = path.stat()
    return [str(path), stat.st_size, stat.st_mtime_ns]


class Pause(RuntimeError):
    pass


class Review(RuntimeError):
    pass


class RunState:
    def __init__(self, path, ledger):
        self.path = path
        self.ledger = ledger
        self.data = load(path) if path.exists() else {'schema': 1, 'steps': {}, 'events': [], 'attempts': []}

    def commit(self, **values):
        self.data.update(values)
        save(self.path, self.data)

    def event(self, stage, state, **details):
        self.data['events'].append(dict(details, at=now(), stage=stage, status=state))
        self.commit(stage=stage, status=state)

    def step(self, name: str, inputs: Any, perform: Callable[[], T], *,
             valid: Callable[[T], bool] = lambda result: True) -> T:
        key = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
        old = self.data['steps'].get(name)
        if old and old['key'] == key and valid(old['result']):
            return old['result']
        self.event(name, 'running', step_id=key)
        result = perform()
        self.data['steps'][name] = {'key': key, 'result': result}
        self.event(name, 'done', step_id=key)
        return result

    def call(self, purpose, maximum_fen, perform, *, request_key=None):
        if request_key is not None:
            for earlier in reversed(self.data['attempts']):
                if earlier.get('request_key') == request_key and earlier['status'] == 'completed' and 'response' in earlier:
                    return earlier['response']
        if type(maximum_fen) is not int or not 0 < maximum_fen <= 5000:
            raise Pause(f'{purpose}: verified attempt ceiling unavailable')
        if self.ledger is None:
            raise Pause(f'{purpose}: shared daily ledger unavailable')
        day = beijing_day(datetime.now(timezone.utc))
        attempt = {'id': uuid4().hex, 'purpose': purpose, 'day': day, 'maximum_fen': maximum_fen,
                   'status': 'reserving', 'started_at': now(), 'request_key': request_key}
        self.data['attempts'].append(attempt)
        self.commit()
        receipt = self.ledger.reserve_cost('kiya', day, attempt['id'], maximum_fen)
        attempt['reservation'] = receipt
        if receipt != 'reserved':
            attempt['status'] = 'blocked'
            self.commit()
            raise Pause(f'{purpose}: daily budget exhausted ({day})')
        attempt['status'] = 'in_flight'
        self.commit()
        try:
            response, usage, actual = perform(attempt['id'])
            attempt['usage'] = usage
            if actual is not None:
                self.ledger.settle_cost('kiya', attempt['id'], actual)
            attempt.update(status='completed', actual_fen=actual)
            if actual is not None and actual > maximum_fen:
                raise Review(f'{purpose}: settled cost exceeded verified ceiling')
            if request_key is not None:
                attempt['response'] = response
        except Exception as exc:
            if attempt['status'] != 'completed':
                attempt.update(status='unknown', error=str(exc))
            raise
        finally:
            attempt['finished_at'] = now()
            self.commit()
        return response

    def recover(self):
        for attempt in self.data['attempts']:
            if attempt['status'] in {'reserving', 'in_flight'}:
                attempt.update(status='unknown', error='interrupted before outcome persisted')
        self.commit()


def session_lock(path):
    """SQLite releases the writer lock on process exit on Windows and Linux."""
    db = sqlite3.connect(path, timeout=0, isolation_level=None)
    try:
        db.execute('BEGIN EXCLUSIVE')
        return db
    except BaseException:
        db.close()
        raise


def disk_guard(paths, minimum):
    for path in paths:
        location = path if path.is_dir() else path.parent
        free = shutil.disk_usage(location).free
        if free < minimum:
            raise Pause(f'low disk: {location} has {free}, requires {minimum} bytes')


def interval(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise Review('expected two source seconds')
    a, b = value
    if not all(type(v) in (int, float) and math.isfinite(v) for v in (a, b)) or not 0 <= a < b <= 86400:
        raise Review('invalid seconds or units')
    return a, b


def probe(path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
                             '-of', 'json', str(path)], check=True, capture_output=True, timeout=30)
    data = json.loads(result.stdout)
    video = next((row for row in data['streams'] if row['codec_type'] == 'video'), {})
    return {'height': video.get('height', 0), 'width': video.get('width', 0),
            'audio': any(row['codec_type'] == 'audio' for row in data['streams']),
            'duration': float(data['format']['duration'])}


def decode(path):
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-i', str(path),
                    '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'],
                   check=True, capture_output=True, timeout=300)

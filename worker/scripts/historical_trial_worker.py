#!/usr/bin/env python3
"""Consume the isolated historical queue. Fixture mode is explicit; live calls do not exist."""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import time

WORKER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKER))
from clipper.agent_flow import digest, disk_guard, load, now, save, session_lock
from clipper.agent_evidence import normalize_segments, source_spans, subtract
from clipper.agent_production import Production
from scripts.agent_produce import RecordedRoles

TERMINAL = {'waiting_for_budget', 'needs_review', 'failed', 'has_clips'}
TASK_ID = re.compile(r'history-[0-9a-fA-F-]{36}\Z')


def contained(path: Path, root: Path, *, exists=True) -> Path:
    """Reject symlinks/junctions, including ancestors, before following the path."""
    path = path.absolute()
    root = root.absolute()
    if not path.is_relative_to(root):
        raise ValueError('source path outside configured session root')
    for item in [path, *path.parents]:
        if item.is_symlink() or (hasattr(item, 'is_junction') and item.is_junction()):
            raise ValueError('symlink or junction path rejected')
    resolved = path.resolve(strict=exists)
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError('resolved path outside configured session root')
    return resolved


def safe_reason(error):
    text = str(error)
    text = re.sub(r'(?i)(token|password|secret|api[_-]?key|authorization)\s*[:=]\s*\S+', r'\1=[REDACTED]', text)
    text = re.sub(r'https?://\S+', '[URL REDACTED]', text)
    return text[:1000]


class HistoricalWorker:
    def __init__(self, root, *, fixture=False, config=None, minimum=1024**3):
        self.root = Path(root).absolute()
        self.fixture = fixture
        self.config = Path(config or WORKER.parent / 'config/kiya.yaml').resolve()
        self.minimum = minimum
        for part in ('queue', 'status', 'production', 'locks'):
            contained(self.root / part, self.root, exists=False).mkdir(parents=True, exist_ok=True)

    def status(self, task, state, phase, **fields):
        path = self.root / 'status' / f'{task["task_id"]}.json'
        try:
            previous = load(path) if path.exists() else {}
        except (ValueError, OSError):
            previous = {}
        save(path, {**previous, 'schema': 1, 'source': 'historical_trial',
                    'task_id': task['task_id'], 'session': task.get('session', task.get('session_id')),
                    'session_id': task.get('session_id', task.get('session')),
                    'rank': task.get('rank'), 'input_revision': task.get('input_revision'),
                    'status': state, 'phase': phase, 'stage': phase, 'updated_at': now(),
                    'simulation': True, 'publish': False, **fields})

    def validate(self, task, queue):
        if not isinstance(task, dict):
            raise ValueError('queue task must be an object')
        if (type(task.get('schema')) is not int or task.get('schema') != 1 or task.get('source') != 'historical_trial'
                or not TASK_ID.fullmatch(str(task.get('task_id', '')))
                or queue.stem != task['task_id'] or type(task.get('rank')) is not int or task['rank'] < 1
                or task.get('publish') is not False):
            raise ValueError('invalid historical trial contract')
        if not task.get('session_id', task.get('session')):
            raise ValueError('missing session binding')

    def snapshot(self, task, directory):
        inputs = directory / 'input'
        manifest_path = directory / 'snapshot.json'
        if manifest_path.exists():
            manifest = load(manifest_path)
            if (manifest['input_revision'] != task['input_revision']
                    or manifest.get('session') != task.get('session', task.get('session_id'))
                    or manifest.get('rank') != task['rank']):
                raise ValueError('checkpoint input or session binding changed')
            for name, checksum in manifest['files'].items():
                path = contained(directory / name, directory)
                if digest(path) != checksum:
                    raise ValueError('snapshot checkpoint hash mismatch')
            return inputs / 'candidates.json'
        raw = task.get('candidates', task.get('candidates_path', task.get('source_path')))
        if not raw or not Path(raw).is_absolute():
            raise ValueError('server absolute candidates path required')
        source_root = Path(task.get('source_root', Path(raw).parent))
        rec_root = Path(task.get('rec_root', source_root))
        if not source_root.is_absolute() or not rec_root.is_absolute():
            raise ValueError('server source roots must be absolute')
        source = contained(Path(raw), source_root)
        for name, expected_hash in task.get('input_files', {}).items():
            input_path = Path(name)
            allowed_root = source_root if input_path.is_relative_to(source_root) else rec_root
            if not input_path.is_absolute() or digest(contained(input_path, allowed_root)) != expected_hash:
                raise ValueError('input file hash mismatch')
        original_hash = digest(source)
        if original_hash != task.get('input_revision'):
            raise ValueError('candidate input revision mismatch')
        if task.get('input_files', {}).get(str(source), original_hash) != original_hash:
            raise ValueError('candidate input hash mismatch')
        data = load(source)
        session = task.get('session_id', task.get('session'))
        if data.get('session', session) != session:
            raise ValueError('candidate session binding mismatch')
        selected = [c for c in data['candidates'] if c.get('rank') == task['rank']]
        if len(selected) != 1:
            raise ValueError('candidate rank is missing or ambiguous')
        candidate = dict(selected[0])
        start, end = float(candidate['clip_start']), float(candidate['clip_end'])
        if not 0 <= start < end:
            raise ValueError('invalid candidate source window')
        inputs.mkdir(parents=True, exist_ok=True)
        files = {}
        segments = []
        originals = {str(source): original_hash}
        ordered = sorted(data['segments'], key=lambda row: float(row['offset']))
        for index, segment in enumerate(ordered):
            offset = float(segment['offset'])
            next_offset = float(ordered[index + 1]['offset']) if index + 1 < len(ordered) else float('inf')
            declared_end = offset + float(segment['duration']) if segment.get('duration') else next_offset
            if offset >= end or min(next_offset, declared_end) <= start:
                continue
            video_path = (source.parent / segment['video']).absolute()
            allowed_root = source_root if video_path.is_relative_to(source_root) else rec_root
            video = contained(video_path, allowed_root)
            checksum = digest(video)
            expected = task.get('input_files', {}).get(str(video))
            if expected and checksum != expected:
                raise ValueError('source media input hash mismatch')
            target = inputs / 'segments' / f'{index:05d}{video.suffix}'
            target.parent.mkdir(exist_ok=True)
            disk_guard([target.parent], self.minimum + video.stat().st_size)
            shutil.copyfile(video, target)
            if digest(target) != checksum or digest(video) != checksum:
                raise ValueError('source media changed during snapshot')
            originals[str(video)] = checksum
            files[str(target.relative_to(directory))] = checksum
            segments.append({**segment, 'video': str(target), 'offset': offset})
        segments = normalize_segments(segments, inputs)
        gaps = subtract([start, end], source_spans(segments))
        if gaps:
            raise ValueError(f'missing source ranges: {gaps}')
        candidate['file_index'] = next(i for i, s in enumerate(segments) if s['offset'] <= start < s['available_end'])
        candidate['local_start'] = start - segments[candidate['file_index']]['offset']
        dest = inputs / 'candidates.json'
        save(dest, {**data, 'session': session, 'segments': segments, 'candidates': [candidate]})
        transcript = source.parent / 'transcript.json'
        if transcript.exists():
            transcript = contained(transcript, source_root)
            checksum = digest(transcript)
            text = load(transcript)
            text['sentences'] = [s for s in text.get('sentences', []) if s['t0'] < end and s['t1'] > start]
            save(inputs / 'transcript.json', text)
            originals[str(transcript)] = checksum
            expected = task.get('input_files', {}).get(str(transcript))
            if expected and checksum != expected:
                raise ValueError('transcript input hash mismatch')
        for name, checksum in originals.items():
            allowed = source_root if Path(name).is_relative_to(source_root) else Path(task.get('rec_root', source_root))
            if digest(contained(Path(name), allowed)) != checksum:
                raise ValueError('source changed during snapshot')
        for path in inputs.glob('*.json'):
            files[str(path.relative_to(directory))] = digest(path)
        save(manifest_path, {'schema': 1, 'session': session, 'rank': task['rank'],
                             'input_revision': original_hash, 'files': files, 'sources': originals})
        return dest

    def roles(self, source, target, rank):
        candidate = load(source)['candidates'][0]
        start, end = candidate['clip_start'], candidate['clip_end']
        evidence = {'id': 'candidate-window', 'start': start, 'end': end, 'modality': 'av',
                    'content': 'SIMULATION: candidate source window; no model inference performed'}
        save(target, {'simulation': True, 'ranks': {str(rank): {
            'topic': {'verdict': 'worth', 'reason': 'SIMULATION: deterministic fixture only',
                      'topic': '有趣', 'evidence_ids': [evidence['id']]}, 'evidence': [evidence],
            'draft': {'window': {'start': start, 'end': end}, 'trim': {'start': 0, 'end': end-start},
                      'items': [], 'subtitles': {'enabled': False},
                      'required_evidence': [{'id': evidence['id'], 'start': start, 'end': end, 'modality': '音画'}]},
            'reviews': [{'verdict': 'pass', 'reason': 'SIMULATION: technical fixture; human review required'}],
            'pack': {'title': '历史隔离试跑（模拟）', 'eligibility': 'rehearsal_only', 'publish': False}}}})
        return RecordedRoles(target)

    def process(self, queue):
        task = load(queue)
        self.validate(task, queue)
        directory = contained(self.root / 'production' / task['task_id'], self.root, exists=False)
        status_path = contained(self.root / 'status' / queue.name, self.root, exists=False)
        if status_path.exists() and load(status_path).get('status') in TERMINAL:
            return False
        try:
            lock = session_lock(self.root / 'locks' / f'{task["task_id"]}.sqlite3')
        except sqlite3.OperationalError as exc:
            if 'locked' in str(exc):
                return False
            raise
        with closing(lock):
            if status_path.exists() and load(status_path).get('status') in TERMINAL:
                return False
            directory.mkdir(parents=True, exist_ok=True)
            for existing in directory.rglob('*'):
                contained(existing, directory)
            phase = 'configuration'
            try:
                if (not self.fixture or task.get('mode') != 'fixture'
                        or type(task.get('max_cost_fen')) is not int or task['max_cost_fen'] != 0):
                    self.status(task, 'waiting_for_budget', phase,
                                reason='Paid/live execution disabled: explicit server and worker fixture mode with zero cost required')
                    return True
                self.status(task, 'running', 'snapshot', reason=None)
                phase = 'snapshot'
                source = self.snapshot(task, directory)
                phase = 'production'
                self.status(task, 'running', phase)
                roles = self.roles(source, directory / 'fixture-roles.json', task['rank'])
                save(directory / 'simulation-ledger.json', {'schema': 1, 'simulation': True,
                     'actual_cost_fen': 0, 'paid_requests': 0, 'publish': False,
                     'source_hashes': load(directory / 'snapshot.json')['sources']})
                result = Production(source, directory, roles, self.config, minimum=self.minimum).run([task['rank']])[0]
                state = {'inventoried': 'has_clips', 'needs_review': 'needs_review', 'paused': 'failed'}.get(result.get('status'), 'failed')
                reason = (result.get('events') or [{}])[-1].get('reason')
                self.status(task, state, result.get('stage', phase), reason=safe_reason(reason or '') or None,
                            production_dir=str(directory), inventory_path=str(directory / f'{task["rank"]:02d}' / 'inventory.json'),
                            current_version=result.get('current_version'))
            except Exception as exc:
                self.status(task, 'failed', phase, reason=safe_reason(exc), error_type=type(exc).__name__)
        return True

    def once(self):
        handled = 0
        for queue in sorted((self.root / 'queue').glob('history-*.json')):
            try:
                handled += int(self.process(contained(queue, self.root)))
            except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
                # Invalid identifiers must never influence status/lock paths.
                if TASK_ID.fullmatch(queue.stem):
                    self.status({'task_id': queue.stem}, 'failed', 'contract', reason=safe_reason(exc))
        return handled


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--fixture', action='store_true')
    parser.add_argument('--config', type=Path, default=WORKER.parent / 'config/kiya.yaml')
    parser.add_argument('--poll-seconds', type=float, default=3)
    args = parser.parse_args()
    if args.poll_seconds < 0.2:
        parser.error('--poll-seconds must be at least 0.2')
    os.environ['KIYA_RENDER_COPY_ASSETS'] = '1'
    worker = HistoricalWorker(args.root, fixture=args.fixture, config=args.config)
    while True:
        worker.once()
        if args.once:
            break
        time.sleep(args.poll_seconds)


if __name__ == '__main__':
    main()

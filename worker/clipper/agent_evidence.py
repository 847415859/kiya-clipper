"""Source-time evidence contracts and targeted local extraction."""
from pathlib import Path
import subprocess
from typing import Any

from .agent_flow import Review, digest, interval, identity

MODES = {'setup': 'av', 'speaker': 'av', 'picture': 'visual', 'response': 'audio', 'sync': 'av'}
PRIORITY = {'反差': 3, '出丑': 3, '抽象': 3, '灵机一动': 3, '黏人': 2, '温馨': 2, '有趣': 2}


def subtract(window, spans):
    start, end = interval(window)
    remaining = []
    for a, b in sorted(spans):
        if b <= start or a >= end:
            continue
        if a > start:
            remaining.append([start, a])
        start = max(start, b)
    if start < end:
        remaining.append([start, end])
    return remaining


def evidence_rows(rows, segments):
    ids = set()
    for row in rows:
        a, b = interval([row['start'], row['end']])
        if (not row.get('id') or row['id'] in ids or not row.get('content')
                or row.get('modality') not in {'av', 'visual', 'audio', 'screenshot'}):
            raise Review('invalid evidence contract')
        spans = [(s['offset'], s['offset'] + s['duration']) for s in segments]
        if subtract([a, b], spans):
            raise Review(f"evidence {row['id']} crosses unavailable source")
        ids.add(row['id'])
    return rows


def topic_contract(decision, rows):
    verdict = decision['verdict']
    refs = decision.get('evidence_ids', [])
    if verdict not in {'worth', 'drop', 'review'} or not decision.get('reason'):
        raise Review('topic verdict or reason missing')
    if verdict == 'worth' and (not refs or set(refs) - {row['id'] for row in rows}):
        raise Review('recommendation has no verifiable evidence references')
    questions = decision.get('questions', [])
    if len({q['id'] for q in questions}) != len(questions):
        raise Review('duplicate evidence question id')
    for q in questions:
        if q.get('kind') not in MODES or not q.get('question'):
            raise Review('unsupported evidence question')
        interval(q['range'])
    return dict(decision, priority=PRIORITY.get(decision.get('topic'), 1))


def collect(question: dict[str, Any], rows: list[dict[str, Any]],
            segments: list[dict[str, Any]], directory: Path) -> dict[str, Any]:
    mode = MODES[question['kind']]
    window = interval(question['range'])
    modes = {'av', mode} | ({'screenshot'} if mode == 'visual' else set())
    # Only observations that answer this question can close its semantic gap.
    known = [(r['start'], r['end']) for r in rows if r['modality'] in modes
             and question['id'] in r.get('answers', [])]
    gaps = subtract(window, known)
    coverage = []
    failures = []
    for a, b in gaps:
        for seg in segments:
            offset = seg['offset']
            left, right = max(a, offset), min(b, offset + seg['duration'])
            if right <= left:
                continue
            source = Path(seg['video'])
            if not source.is_file():
                failures.append({'range': [left, right], 'source': str(source), 'reason': 'missing source'})
                continue
            directory.mkdir(parents=True, exist_ok=True)
            output = directory / f'{len(coverage):02d}.mp4'
            stream = ['-map', '0:a:0'] if mode == 'audio' else ['-map', '0:v:0', '-map', '0:a:0']
            cmd = ['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-y', '-ss', str(left-offset),
                   '-i', str(source), '-t', str(right-left), *stream, str(output)]
            result = subprocess.run(cmd, capture_output=True, timeout=120)
            actual_mode = mode
            if result.returncode and mode != 'audio':
                output = output.with_suffix('.jpg')
                result = subprocess.run(cmd[:cmd.index('-t')] + ['-map', '0:v:0', '-frames:v', '1', str(output)],
                                        capture_output=True, timeout=120)
                actual_mode = 'screenshot'
            if result.returncode or not output.is_file():
                failures.append({'range': [left, right], 'source': str(source),
                                 'reason': result.stderr.decode('utf-8', errors='replace')[-600:] or 'no media emitted'})
                continue
            actual = [left, left] if actual_mode == 'screenshot' else [left, right]
            if actual_mode != 'screenshot':
                from .agent_flow import probe
                actual[1] = min(right, left + probe(output)['duration'])
            coverage.append({'range': actual, 'requested': [left, right], 'file_seconds': [left-offset, right-offset],
                             'source': identity(source), 'modality': actual_mode, 'file': str(output), 'sha256': digest(output)})
    usable = known + [c['range'] for c in coverage if c['modality'] in modes and c['range'][1] > c['range'][0]]
    return {'question': question, 'coverage': coverage, 'reused': known,
            'media_gaps': subtract(window, usable), 'unresolved': gaps, 'failures': failures}

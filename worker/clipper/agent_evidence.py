"""Source-time evidence contracts and targeted local extraction."""
from math import isfinite
from pathlib import Path
import subprocess
from typing import Any

from .agent_flow import Review, digest, interval, identity, probe

MODES = {'setup': 'av', 'speaker': 'av', 'picture': 'visual', 'response': 'audio', 'sync': 'av'}
PRIORITY = {'反差': 3, '出丑': 3, '抽象': 3, '灵机一动': 3, '黏人': 2, '温馨': 2, '有趣': 2}


def normalize_segments(segments, base):
    """Resolve screen.py segments without inferring coverage from adjacent recordings."""
    ordered = sorted(segments, key=lambda segment: float(segment['offset']))
    offsets = [float(segment['offset']) for segment in ordered]
    normalized = []
    for index, segment in enumerate(ordered):
        source = (base / segment['video']).resolve()
        offset = offsets[index]
        duration = None
        status = 'missing_source'
        if source.is_file():
            status = 'probe_failed'
            try:
                info = probe(source)
                measured = info['duration']
                if info['height'] > 0 and isfinite(measured) and measured > 0:
                    duration, status = measured, 'readable'
            except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
                pass
        next_offset = offsets[index + 1] if index + 1 < len(offsets) else None
        end = min(offset + duration, next_offset) if duration is not None and next_offset is not None else (
            offset + duration if duration is not None else None)
        normalized.append(dict(segment, video=str(source), offset=offset, duration=duration,
                               available_end=end, next_offset=next_offset, source_status=status))
    return normalized


def available_end(segment):
    if 'available_end' in segment:
        return segment['available_end']
    duration = segment.get('duration')
    return segment['offset'] + duration if duration is not None else None


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


def source_spans(segments):
    return [(segment['offset'], end) for segment in segments
            if (end := available_end(segment)) is not None and end > segment['offset']]


def evidence_rows(rows, segments):
    spans = source_spans(segments)
    ids = set()
    for row in rows:
        a, b = interval([row['start'], row['end']])
        if (not row.get('id') or row['id'] in ids or not row.get('content')
                or row.get('modality') not in {'av', 'visual', 'audio', 'screenshot'}):
            raise Review('invalid evidence contract')
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
            end = available_end(seg)
            source = Path(seg['video'])
            left = max(a, offset)
            if end is None:
                boundary = seg.get('next_offset')
                right = min(b, boundary) if boundary is not None else b
                if right > left:
                    failures.append({'range': [left, right], 'source': str(source),
                                     'reason': seg.get('source_status', 'unknown_duration')})
                continue
            right = min(b, end)
            if right <= left:
                continue
            if not source.is_file():
                failures.append({'range': [left, right], 'source': str(source), 'reason': 'missing source'})
                continue
            directory.mkdir(parents=True, exist_ok=True)
            output = directory / f'{len(coverage):02d}.mp4'
            stream = ['-map', '0:v:0', '-map', '0:a:0']
            prefix = ['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-y']
            # FFmpeg duration options need decimal seconds even near a segment boundary.
            local_start = left - offset
            seek = ['-ss', f'{local_start:.6f}'] if local_start > 0 else []
            media_args = ['-t', f'{right - left:.6f}', *stream, '-vf', 'scale=-2:360', str(output)]
            actual = None
            reason = 'no usable audio/video emitted'
            input_args = ['-i', str(source)]
            attempts = [seek + input_args]
            if seek:
                attempts.append(input_args + seek)
                # Decode from the first keyframe for tiny FLV offsets; input seek can skip it.
                if local_start < 0.001:
                    attempts.reverse()
            for args in attempts:
                output.unlink(missing_ok=True)
                result = subprocess.run(prefix + args + media_args, capture_output=True, timeout=120)
                if result.returncode:
                    reason = result.stderr.decode('utf-8', errors='replace')[-600:] or reason
                    continue
                try:
                    info = probe(output)
                    duration = info['duration']
                    if info['audio'] and info['height'] and isfinite(duration) and duration > 0:
                        actual = [left, min(right, left + duration)]
                        break
                except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
                    pass
            actual_mode = mode
            if actual is None and mode != 'audio':
                output = output.with_suffix('.jpg')
                output.unlink(missing_ok=True)
                result = subprocess.run(prefix + ['-i', str(source), *seek, '-map', '0:v:0',
                                        '-frames:v', '1', str(output)], capture_output=True, timeout=120)
                if result.returncode == 0 and output.is_file():
                    actual, actual_mode = [left, left], 'screenshot'
            if actual is None:
                failures.append({'range': [left, right], 'source': str(source), 'reason': reason})
                continue
            coverage.append({'range': actual, 'requested': [left, right], 'file_seconds': [left-offset, right-offset],
                             'source': identity(source), 'modality': actual_mode, 'file': str(output), 'sha256': digest(output)})
    usable = known + [c['range'] for c in coverage if c['modality'] in modes and c['range'][1] > c['range'][0]]
    return {'question': question, 'coverage': coverage, 'reused': known,
            'media_gaps': subtract(window, usable), 'unresolved': gaps, 'failures': failures}

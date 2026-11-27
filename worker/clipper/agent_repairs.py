"""Conservative, source-bound local repairs. Input edits are never mutated."""
from __future__ import annotations

import copy
import math

from .agent_flow import Review, interval
from .agent_evidence import subtract
from .protection import required, revision


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def repair(edit, decision, evidence, mapping, transcript=None):
    reason = decision.get('reason') or 'missing reason'
    location = decision.get('output_range')

    def fail(message):
        raise Review(f'{message}; output_range={location}; {reason}')

    # Batches are atomic and expressed against one original draft. Timeline changes
    # cannot be combined with other actions because they invalidate output locations.
    actions = decision.get('actions')
    if actions is not None:
        if not isinstance(actions, list) or not 1 <= len(actions) <= 8:
            fail('invalid repair actions')
        targets = set()
        revised = copy.deepcopy(edit)
        for action in actions:
            if not isinstance(action, dict) or action.get('actions') is not None:
                fail('invalid nested repair action')
            kind = action.get('action')
            if not isinstance(kind, str):
                fail('invalid action type')
            if len(actions) > 1 and kind in {'adjust_boundary', 'adjust_rhythm', 'extend_tail'}:
                fail('conflicting timeline actions require separate review')
            target = ('item', action.get('item_id')) if action.get('item_id') else ('subtitle', (action.get('subtitle') or {}).get('t0'), (action.get('subtitle') or {}).get('t1'))
            if target in targets:
                fail('conflicting actions target the same object')
            targets.add(target)
            revised = repair(revised, {**decision, 'actions': None, **action}, evidence, mapping, transcript)
        return revised
    action = decision.get('action')
    fields = {'extend_tail': ('source_end', 'evidence_ids'), 'remove_overlay': ('item_id',),
              'remove_zoom': ('item_id',), 'adjust_overlay': ('item_id', 'changes'),
              'move_overlay': ('item_id', 'changes'), 'shorten_overlay': ('item_id', 'changes'),
              'adjust_zoom': ('item_id', 'changes'), 'shorten_subtitle': ('subtitle',),
              'correct_subtitle': ('subtitle',), 'adjust_subtitle_timing': ('subtitle',),
              'adjust_subtitle_layout': ('subtitle',), 'split_subtitle': ('subtitle',),
              'adjust_boundary': ('trim',), 'adjust_rhythm': ('cuts',)}
    if not isinstance(action, str) or action not in fields:
        fail('missing valid repair action')
    if not decision.get('reason') or not isinstance(reason, str) or not reason.strip():
        fail('repair needs reason')
    missing = [key for key in ('output_range', *fields[action]) if decision.get(key) is None]
    if missing:
        fail('missing ' + ', '.join(missing))
    if not isinstance(location, (list, tuple)) or len(location) != 2 or not all(number(v) for v in location):
        fail('invalid output_range')
    try:
        start, end = interval(location)
    except (Review, ValueError, TypeError):
        fail('invalid output_range')
    if not all(number(v) for v in (start, end)):
        fail('invalid output_range')
    touched = []
    for row in mapping:
        values = [row.get(k) for k in ('output_start', 'output_end', 'source_start', 'source_end')]
        if not all(number(v) for v in values):
            fail('invalid source mapping')
        oa, ob, sa, sb = values
        if ob <= oa or abs((ob - oa) - (sb - sa)) > .01:
            fail('nonlinear source mapping')
        a, b = max(start, oa), min(end, ob)
        if a < b:
            touched.append((sa + a - oa, sa + b - oa))
    if not touched or subtract([start, end], [(r['output_start'], r['output_end']) for r in mapping]):
        fail('repair needs a mapped output location')
    revised = copy.deepcopy(edit)
    origin = edit['window']['start']
    trim = edit.get('trim', {'start': 0, 'end': edit['window']['end'] - origin})

    def overlaps(a, b):
        return any(a < y and b > x for x, y in touched)

    def contained(a, b):
        return not subtract([a, b], touched)

    def sentence_guard(a, b, removing=False):
        if transcript is None or not isinstance(transcript.get('sentences'), list):
            fail('sentence boundaries unavailable for timeline repair')
        for sentence in transcript['sentences']:
            x, y = sentence.get('t0'), sentence.get('t1')
            if not all(number(v) for v in (x, y)) or y <= x:
                fail('invalid original transcript sentence')
            if removing and a < y and b > x:
                fail('rhythm cut overlaps original speech')
            if not removing and (x < a < y or x < b < y):
                fail('boundary splits original sentence')

    if action in {'remove_overlay', 'remove_zoom', 'adjust_overlay', 'move_overlay', 'shorten_overlay', 'adjust_zoom'}:
        matches = [x for x in revised.get('items', []) if x.get('id') == decision['item_id']]
        if len(matches) != 1:
            fail('effect target not unique')
        item = matches[0]
        zoom = action in {'remove_zoom', 'adjust_zoom'}
        # Untyped legacy text records remain supported by the old public repair API.
        if item.get('type', 'text') != ('zoom' if zoom else 'text'):
            fail('effect target type mismatch')
        old_a, old_b = origin + item['start'], origin + item['start'] + item['duration']
        if not overlaps(old_a, old_b):
            fail('effect does not match repair location')
        if action.startswith('remove_'):
            revised['items'].remove(item)
        else:
            changes = decision['changes']
            allowed = {'start', 'duration', 'x', 'y', 'size'} if not zoom else {'start', 'duration', 'scale', 'x', 'y', 'ease_in', 'ease_out'}
            if action == 'move_overlay':
                allowed = {'x', 'y'}
            elif action == 'shorten_overlay':
                allowed = {'duration'}
            if not isinstance(changes, dict) or not changes or set(changes) - allowed or not all(number(v) for v in changes.values()):
                fail('invalid effect changes')
            if all(item.get(k) == v for k, v in changes.items()):
                fail('repair has no expected change')
            item.update(changes)
            if not (trim['start'] <= item['start'] < item['start'] + item['duration'] <= trim['end']):
                fail('effect changes exceed current trim')
            if action == 'shorten_overlay' and item['duration'] >= old_b - old_a:
                fail('overlay shortening must reduce duration')
            bounds = {'duration': (.2, 30), 'x': (0, 1), 'y': (0, 1), 'size': (24, 300), 'scale': (1.05, 3), 'ease_in': (.05, 2), 'ease_out': (.05, 2)}
            if any(k in bounds and not bounds[k][0] <= v <= bounds[k][1] for k, v in changes.items()):
                fail('effect parameter outside supported bounds')
            if not overlaps(origin + item['start'], origin + item['start'] + item['duration']):
                fail('effect moved away from reviewed location')
    elif action in {'shorten_subtitle', 'correct_subtitle', 'adjust_subtitle_timing', 'adjust_subtitle_layout', 'split_subtitle'}:
        from .speech_edit import sentence_text, validate_overrides
        try:
            override, = validate_overrides([decision['subtitle']])
        except (ValueError, TypeError):
            fail('invalid subtitle revision')
        a, b = override['t0'], override['t1']
        matches = [r for r in (transcript or {}).get('sentences', []) if abs(r['t0'] - a) < 1e-6 and abs(r['t1'] - b) < 1e-6]
        if len(matches) != 1 or sentence_text(matches[0]) != override['original']:
            fail('subtitle revision does not match original transcript')
        if not overlaps(a, b):
            fail('subtitle does not match repair location')
        subs = revised.setdefault('subtitles', {})
        overrides = subs.get('sentence_overrides', [])
        previous = next((r for r in overrides if r['t0'] == a and r['t1'] == b), {'text': override['original']})
        if action == 'shorten_subtitle':
            remaining = iter(previous['text'])
            if len(override['text']) >= len(previous['text']) or not all(c in remaining for c in override['text']):
                fail('subtitle shortening must only remove characters')
        elif action == 'correct_subtitle':
            if override['text'] == previous['text']:
                fail('subtitle correction has no expected change')
        elif action in {'adjust_subtitle_layout', 'split_subtitle'}:
            if override['text'] != previous['text']:
                fail('subtitle layout/split must preserve current wording')
            if action == 'adjust_subtitle_layout':
                keys = {'display_x', 'display_y'} & set(override)
                if not keys or all(override[k] == previous.get(k) for k in keys):
                    fail('subtitle layout needs a changed position')
                if any(not number(override[k]) or not .05 <= override[k] <= .95 for k in keys):
                    fail('subtitle position outside supported bounds')
                if 'parts' in override and override['parts'] != previous.get('parts'):
                    fail('layout action may not change subtitle splitting')
            else:
                parts = override.get('parts')
                if not isinstance(parts, list) or len(parts) < 2 or ''.join(p.get('text', '') for p in parts) != previous['text']:
                    fail('subtitle split must preserve exact current text')
                cursor = override.get('display_t0', a)
                display_end = override.get('display_t1', b)
                for part in parts:
                    pa, pb = part.get('t0'), part.get('t1')
                    if not all(number(v) for v in (pa, pb)) or pa < cursor or pb - pa < .3 or pb > display_end:
                        fail('subtitle split timing overlaps or exceeds display bounds')
                    cursor = pb
                if parts == previous.get('parts'):
                    fail('subtitle split has no expected change')
            if any(override.get(k, previous.get(k)) != previous.get(k) for k in ('display_t0', 'display_t1')):
                fail('layout/split may not silently change subtitle timing')
        else:
            if override['text'] != previous['text'] or 'display_t0' not in override:
                fail('subtitle timing must preserve current wording and give display timing')
            da, db = override['display_t0'], override['display_t1']
            if abs(da - a) > 1 or abs(db - b) > 1 or not origin + trim['start'] <= da < db <= origin + trim['end']:
                fail('subtitle timing exceeds local bounds')
            ordered = sorted((transcript or {})['sentences'], key=lambda r: r['t0'])
            for other in ordered:
                if other is matches[0]:
                    continue
                if da < other['t1'] and db > other['t0']:
                    fail('subtitle timing overlaps another original sentence')
        allowed_extra = ({'display_t0', 'display_t1'} if action == 'adjust_subtitle_timing' else
                         {'display_x', 'display_y'} if action == 'adjust_subtitle_layout' else
                         {'parts'} if action == 'split_subtitle' else set())
        for key in {'display_t0', 'display_t1', 'display_x', 'display_y', 'parts'} - allowed_extra:
            if key in override and override[key] != previous.get(key):
                fail('subtitle action changes unrelated fields')
        merged_override = {**previous, **override}
        if action in {'shorten_subtitle', 'correct_subtitle', 'adjust_subtitle_timing'}:
            merged_override.pop('parts', None)
        try:
            merged_override, = validate_overrides([merged_override])
        except (ValueError, TypeError):
            fail('combined subtitle revision is invalid')
        subs['sentence_overrides'] = [r for r in overrides if (r['t0'], r['t1']) != (a, b)] + [merged_override]
    elif action == 'extend_tail':
        source_end = decision['source_end']
        if not isinstance(decision['evidence_ids'], list) or not decision['evidence_ids'] or not all(isinstance(v, str) for v in decision['evidence_ids']):
            fail('invalid tail evidence ids')
        old_end = origin + trim['end']
        if not number(source_end) or not old_end < source_end <= min(old_end + 5, edit['window']['end']):
            fail('tail extension exceeds local repair bounds')
        proven = [(r['start'], r['end']) for r in evidence if r['modality'] in {'av', 'audio'} and r['id'] in decision['evidence_ids']]
        if subtract([old_end, source_end], proven) or not any(abs(b - old_end) < .05 for a, b in touched):
            fail('tail extension lacks original audio or tail location')
        if transcript is not None:
            sentence_guard(origin + trim['start'], source_end)
        revised['trim']['end'] = source_end - origin
        revised.setdefault('required_evidence', []).append({'id': f'tail-repair-{old_end:g}', 'start': old_end, 'end': source_end,
                                                          'modality': 'audio', 'role': 'effective_response', 'reason': reason})
    elif action == 'adjust_boundary':
        target = decision['trim']
        if not isinstance(target, dict) or set(target) != {'start', 'end'} or not all(number(v) for v in target.values()):
            fail('invalid boundary trim')
        a, b = target['start'], target['end']
        if not trim['start'] <= a < b <= trim['end'] or b - a < 1 or target == trim:
            fail('boundary may only tighten current trim')
        removed = [(origin + trim['start'], origin + a), (origin + b, origin + trim['end'])]
        if any(x < y and not contained(x, y) for x, y in removed):
            fail('boundary removal outside reviewed location')
        if any(not origin + a <= r['start'] < r['end'] <= origin + b for r in required(edit)):
            fail('boundary would remove required evidence')
        sentence_guard(origin + a, origin + b)
        revised['trim'] = target.copy()
    elif action == 'adjust_rhythm':
        from .timeline import merge_cuts
        cuts = decision['cuts']
        if not isinstance(cuts, list) or not cuts or len(cuts) > 10:
            fail('rhythm requires explicit local cuts')
        for q in cuts:
            if not isinstance(q, dict) or set(q) != {'start', 'end'} or not all(number(v) for v in q.values()):
                fail('invalid rhythm cut')
            a, b = q['start'], q['end']
            if not trim['start'] <= a < b <= trim['end'] or b - a > 3 or not contained(origin + a, origin + b):
                fail('rhythm cut exceeds reviewed location or bounds')
            if any(origin + a < r['end'] and origin + b > r['start'] for r in required(edit)):
                fail('rhythm would remove required evidence')
            sentence_guard(origin + a, origin + b, removing=True)
            if any(a < old['end'] and b > old['start'] for old in edit.get('cuts', [])):
                fail('rhythm cut conflicts with existing cut')
        merged = merge_cuts(cuts, trim['start'], trim['end'])
        if len(merged) != len(cuts):
            fail('rhythm cuts overlap')
        revised['cuts'] = merge_cuts([*edit.get('cuts', []), *merged], trim['start'], trim['end'])
        if trim['end'] - trim['start'] - sum(q['end'] - q['start'] for q in revised['cuts']) < 1:
            fail('rhythm leaves less than one second')
    # Verify every required interval against the actual surviving source schedule,
    # including pre-existing manual and automatic cuts, not merely the new action.
    if required(revised):
        from .timeline import edit_cuts
        from .boiler import keep_ranges
        from .protection import coverage
        current_trim = revised.get('trim', trim)
        a, b = origin + current_trim['start'], origin + current_trim['end']
        removed = edit_cuts(revised, (transcript or {}).get('sentences', []), origin, a, b)
        if coverage(required(revised), keep_ranges(a, b, removed))['status'] != 'time_covered':
            fail('repair schedule does not preserve ALL required evidence')
    if revision(revised) == revision(edit):
        fail('repair has no expected change')
    return revised

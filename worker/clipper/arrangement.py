"""Editorial handoff and checks in source and rendered-main time."""
import math
import re

from .protection import coverage, required


def narrative_handoff(llm, candidate_start):
    story = llm.get('story') or {}
    narrative = llm.get('narrative') or {}
    punch = llm.get('punch_time')
    beats = [
        {'role': 'setup', 'information': story.get('setup'), 'medium': 'source_or_caption'},
        {'role': 'change', 'information': story.get('punch'), 'medium': 'source_first'},
        {'role': 'response', 'information': narrative.get('effective_response') or story.get('reaction'), 'medium': 'source'},
    ]
    if isinstance(punch, (int, float)) and math.isfinite(punch) and punch >= 0:
        beats[1]['source_time'] = round(candidate_start + punch, 3)
    return {'basis': 'rerank_review_pending', 'focus': narrative.get('payoff') or llm.get('what_happened') or '',
            'beats': [beat for beat in beats if beat['information'] or 'source_time' in beat]}


def speech_events(edit, sentences, kept):
    rows = [row for row in required(edit) if row.get('modality') in
            {'audio', 'audio_visual', 'both', '原声', '音画', '音频'}]
    rows += [{'start': row['t0'], 'end': row['t1']} for row in sentences
             if row.get('text') and row['t1'] > row['t0']]
    ranges = sorted((span['output_start'], span['output_end'])
                    for row in coverage(rows, kept)['checks'] for span in row['output_ranges'])
    merged = []
    for start, end in ranges:
        if merged and start <= merged[-1]['t1'] + 1e-6:
            merged[-1]['t1'] = max(end, merged[-1]['t1'])
        else:
            merged.append({'t0': start, 't1': end})
    return merged


def inspect_plan(edit, items, subtitles, kept, fps):
    """Return review leads. Temporal/anchor overlap cannot establish occlusion."""
    mapping = coverage([], kept)['source_to_output']
    layout = subtitles.get('style') or {}
    anchor = {'top': 0.12, 'middle': 0.5, 'center': 0.5, 'bottom': 0.88}.get(layout.get('position', 'bottom'))
    beats = (edit.get('narrative_plan') or {}).get('beats') or []
    trigger = next((beat.get('source_time') for beat in beats if beat.get('role') == 'change'), None)
    findings = []

    def finding(code, start, end, **details):
        spans = [[max(start, row['output_start']) - row['output_start'] + row['source_start'],
                  min(end, row['output_end']) - row['output_start'] + row['source_start']]
                 for row in mapping if start < row['output_end'] and end > row['output_start']]
        findings.append(dict(details, code=code, main_seconds=[start, end], source_ranges=spans, status='review_hint'))

    for item in items:
        if item['type'] not in {'text', 'narration', 'fakeui', 'sticker'}:
            continue
        start = round(item['start'] * fps) / fps
        end = start + max(1, round(item['duration'] * fps)) / fps
        if isinstance(trigger, (int, float)) and item.get('purpose') == 'reveal':
            hit = next((row for row in mapping if row['output_start'] <= start < row['output_end']), None)
            if hit and hit['source_start'] + start - hit['output_start'] < trigger - 1 / fps:
                finding('early_reveal', start, end, effect=item['type'], trigger_source=trigger, text=item.get('text'))
        if anchor is not None and abs(item.get('y', -1) - anchor) <= 0.24:
            for caption in subtitles.get('events', []) if subtitles.get('enabled') else []:
                a, b = max(start, caption['t0']), min(end, caption['t1'])
                if b > a:
                    finding('subtitle_overlay_review', a, b, effect=item['type'], text=caption.get('text'))
    for caption in subtitles.get('events', []) if subtitles.get('enabled') else []:
        text = caption.get('text') or ''
        chars = len(re.sub(r'[\s\W]', '', text))
        start, end = caption['t0'], caption['t1']
        if chars >= 4 and end > start and (end - start < 0.7 or chars / (end - start) > 10):
            finding('caption_reading_review', start, end, text=text, chars_per_second=round(chars / (end - start), 1))
    return findings

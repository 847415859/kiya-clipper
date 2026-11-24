"""Compare human-confirmed event IDs, not temporal overlap, across recall routes."""
from __future__ import annotations
import argparse
import json
from pathlib import Path


def evaluate(data):
    sessions = data.get('sessions', [])
    counts = {'reference_events': 0, 'heat_found': 0, 'exploration_extra': 0, 'reviewed_candidates': 0, 'redundant_candidates': 0}
    result = []
    for session in sessions:
        references = {r['event_id'] for r in session.get('human_events', []) if r.get('confirmed') is True}
        heat, extra = set(), set()
        seen = set()
        for row in session.get('reviewed_candidates', []):
            counts['reviewed_candidates'] += 1
            event = row.get('confirmed_event_id')
            if event not in references:
                continue
            if event in seen:
                counts['redundant_candidates'] += 1
            seen.add(event)
            if row.get('route') == 'heat':
                heat.add(event)
            elif row.get('route') == 'exploration':
                extra.add(event)
        extra -= heat
        counts['reference_events'] += len(references)
        counts['heat_found'] += len(heat)
        counts['exploration_extra'] += len(extra)
        result.append({'session':session['session'], 'reference_events':len(references), 'heat_found':len(heat), 'exploration_extra':len(extra), 'missed':sorted(references-heat-extra)})
    n = counts['reference_events']
    return {'status':'measured' if n else 'blocked_missing_independent_human_labels', 'sessions':result, 'counts':counts,
            'heat_recall':counts['heat_found']/n if n else None,
            'joint_recall':(counts['heat_found']+counts['exploration_extra'])/n if n else None,
            'independent_sessions':len({s['session'] for s in sessions if any(e.get('confirmed') for e in s.get('human_events', []))}),
            'review_seconds':sum(s.get('review_seconds',0) for s in sessions),
            'limits':'Only human-confirmed event associations count. No IoU-based inferred matches. Labels must be collected before comparing route output.'}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('labels')
    ap.add_argument('--output',required=True)
    args=ap.parse_args()
    result=evaluate(json.loads(Path(args.labels).read_text(encoding='utf-8')))
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    main()

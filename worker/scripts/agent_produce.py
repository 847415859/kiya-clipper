#!/usr/bin/env python3
"""Run isolated production from recorded role results, or generate a local rehearsal."""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import sys
from pathlib import Path

WORKER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKER))
from clipper.agent_flow import load, save, session_lock
from clipper.operations import beijing_day
from datetime import datetime, timezone
from clipper.agent_production import Production
from clipper.operations import DailyLedger


class RecordedRoles:
    def __init__(self, path):
        data = load(path)
        self.name = 'fixture' if data.get('simulation') else 'recorded'
        self.rows = data['ranks']

    def for_rank(self, rank):
        return self.rows.get(str(rank), {'topic': {'verdict': 'review', 'reason': 'No role result for candidate'}})

    def answer(self, row, question, result, state):
        answer = row.get('answers', {}).get(question['id'])
        if self.name == 'fixture' and row.get('simulate_paid_answer'):
            from clipper.protection import revision
            request_key = revision({'adapter': 'fixture-v1', 'question': question, 'result': result, 'answer': answer})
            return state.call('fixture-evidence', 10, lambda _: (answer, {'simulated': True}, 2),
                              request_key=request_key)
        return answer

    def review(self, row, index, rendered):
        if self.name == 'fixture':
            expected = row.get('fixture_expectations', {}).get(str(index))
            if expected is not None and abs(rendered['probe']['duration'] - expected) > 0.2:
                return {'verdict': 'review', 'reason': 'Fixture actual duration differs from expected'}
            return row['reviews'][index]
        decision = next((r for r in row.get('reviews', []) if r.get('media_sha256') ==
                         rendered['files'][rendered['media']]), None)
        return decision or {'verdict': 'review', 'reason': 'Review of this exact MP4 is pending'}


def fixture(root, video):
    root.mkdir(parents=True, exist_ok=True)
    source = root / 'input'
    source.mkdir(exist_ok=True)
    candidates, roles = source / 'candidates.json', root / 'fixture-roles.json'
    if not candidates.exists():
        save(candidates, {'streamer': 'kiya', 'session': 'fixed-kiya-signoff', 'segments': [
            {'video': str(video.resolve()), 'offset': 0, 'duration': 75}], 'candidates': [
            {'rank': rank, 't': start + 2, 'clip_start': start, 'clip_end': start + 10,
             'file_index': 0, 'local_start': start} for rank, start in [(1, 44), (2, 60), (3, 5)]]})
        save(source / 'edits' / '01.json', {'human': True, 'fixture': 'leave intact'})
    if not roles.exists():
        trigger = {'id': 'trigger', 'start': 44, 'end': 47, 'modality': 'av', 'content': 'Fixture: sign-off setup'}
        response = {'id': 'tail', 'start': 50, 'end': 52, 'modality': 'audio',
                    'content': 'Fixture: proposed response tail', 'answers': ['tail']}
        topic = {'verdict': 'worth', 'reason': 'Fixture recommendation for the sign-off', 'topic': '温馨',
                 'evidence_ids': ['trigger']}
        question = {'id': 'tail', 'kind': 'response', 'range': [50, 52], 'question': 'Check ending of response'}
        save(roles, {'simulation': True, 'ranks': {
            '1': {'topic': dict(topic, questions=[question]), 'evidence': [trigger], 'answers': {'tail': response},
                  'simulate_paid_answer': True,
                  'draft': {'window': {'start': 44, 'end': 54}, 'trim': {'start': 0, 'end': 6},
                            'items': [], 'subtitles': {'enabled': False}, 'required_evidence': [
                                {'id': 'trigger', 'start': 44, 'end': 47, 'modality': '音画'}]},
                  'fixture_expectations': {'0': 6, '1': 8},
                  'reviews': [{'verdict': 'repair', 'action': 'extend_tail', 'output_range': [5, 6],
                               'source_end': 52, 'evidence_ids': ['tail'], 'reason': 'Fixture: premature ending'},
                              {'verdict': 'pass', 'reason': 'Fixture: duration and technical gates verified'}],
                  'pack': {'title': '下播告别工程样本', 'eligibility': 'rehearsal_only'}},
            '2': {'topic': dict(topic, questions=[dict(question, range=[74, 76])]), 'evidence': [trigger]},
            '3': {'topic': {'verdict': 'drop', 'reason': 'Fixture: low value'}}}})
    return candidates, roles


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--candidates', type=Path)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument('--roles', type=Path)
    mode.add_argument('--live', action='store_true')
    ap.add_argument('--fixture-video', type=Path)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--config', type=Path, default=WORKER.parent / 'config/kiya.yaml')
    ap.add_argument('--ledger', type=Path)
    ap.add_argument('--min-free-mib', type=int, default=1024)
    ap.add_argument('--ranks', type=int, nargs='+')
    ap.add_argument('--rank5-gap1-context', type=Path, nargs=2, metavar=('BEFORE', 'TARGET'))
    ap.add_argument('--rank5-gap1-manual-reviewer')
    args = ap.parse_args()
    if args.rank5_gap1_context and (not args.live or args.ranks != [5] or not args.ledger
                                   or not args.ledger.is_file() or not (args.out / '05/state.json').is_file()
                                   or not (args.out / 'session.json').is_file()):
        ap.error('--rank5-gap1-context requires --live --ranks 5, existing output and existing ledger')
    if args.rank5_gap1_manual_reviewer and (not args.rank5_gap1_context or not args.rank5_gap1_manual_reviewer.strip()):
        ap.error('--rank5-gap1-manual-reviewer requires --rank5-gap1-context and a non-empty reviewer identity')
    if args.min_free_mib < 1:
        ap.error('--min-free-mib must be positive')
    if args.fixture_video and (args.candidates or args.roles or args.live):
        ap.error('--fixture-video cannot be combined with --candidates, --roles or --live')
    if args.fixture_video:
        args.candidates, args.roles = fixture(args.out, args.fixture_video)
        args.out = args.out / 'run'
    if not args.candidates or not (args.roles or args.live) or (args.live and not args.ledger):
        ap.error('supply --fixture-video, or --candidates with --roles or --live --ledger')
    ledger = DailyLedger(args.ledger) if args.ledger else None
    if args.live:
        import os
        from clipper.agent_live import LiveRoles
        os.environ['KIYA_OPS_LEDGER'] = str(args.ledger.resolve())
        roles = LiveRoles(args.candidates, args.out, args.config, args.ledger)
        if args.rank5_gap1_context:
            if not all(path.is_file() for path in args.rank5_gap1_context):
                ap.error('both context clips must exist')
            roles.contextual_gap = 'gap-1'
            roles.context_media = [path.resolve() for path in args.rank5_gap1_context]
            roles.manual_reviews = {}
            if args.rank5_gap1_manual_reviewer:
                import os
                if os.environ.get('KIYA_OPS_LLM_MAX_FEN') != '368':
                    ap.error('manual continuation requires the preconfigured 368 fen model ceiling')
                from clipper.agent_live import RANK5_TARGET
                from clipper.agent_flow import digest, probe
                from clipper.protection import revision
                assets = [{'path': str(path), 'sha256': digest(path), 'bytes': path.stat().st_size}
                          for path in roles.context_media]
                mapping = []
                for index, path in enumerate(roles.context_media):
                    info = probe(path)
                    mapping.append({'clip': index, 'local_range': [0, info['duration']], 'role': 'context_only'})
                duration = round(RANK5_TARGET[1] - RANK5_TARGET[0], 3)
                mapping[1].update(role='target_then_context', target_local_range=[0, duration],
                                  target_source_range=RANK5_TARGET, context_only_after=duration)
                record_dir = args.out / '05' / 'manual_reviews'
                record_dir.mkdir(parents=True, exist_ok=True)
                record_path = record_dir / 'gap-1-human.json'
                manual_record = {'schema': 1, 'session': '20261124-2151', 'streamer': 'kiya', 'rank': 5,
                     'question_id': 'gap-1', 'quote': '我有点嫌弃他', 'source_range': RANK5_TARGET,
                     'vocal_range': [2058.223, 2059.423],
                     'media': {'assets': assets, 'mapping': mapping}, 'reviewer': args.rank5_gap1_manual_reviewer,
                     'note': '人工复听确认原话；Omni 原结论“只有背景音乐”保留为独立模型观察，不覆盖或删除。'}
                with closing(session_lock(args.out / 'session.lock.sqlite3')):
                    if record_path.exists():
                        existing = load(record_path)
                        if {key: value for key, value in existing.items() if key != 'reviewed_at'} != manual_record:
                            ap.error(f'manual review record already exists and differs: {record_path}')
                    else:
                        save(record_path, {**manual_record, 'reviewed_at': datetime.now(timezone.utc).isoformat()})
                roles.manual_reviews['gap-1'] = record_path
                roles.manual_review_revision = revision(load(record_path))
    else:
        roles = RecordedRoles(args.roles)
    runner = Production(args.candidates, args.out, roles, args.config, ledger,
                        minimum=args.min_free_mib * 1024**2)
    result = runner.run(args.ranks)
    attempts = [attempt for row in result for attempt in row.get('attempts', [])]
    day = beijing_day(datetime.now(timezone.utc))
    save(args.out / ('summary.json' if not args.ranks else 'summary-selected.json'), {
        'simulation': runner.roles.name == 'fixture',
        'candidates': [{key: row.get(key) for key in ('rank', 'status', 'stage', 'topic', 'current_version')}
                       | {'last_event': row.get('events', [None])[-1]} for row in result],
        'attempts': attempts,
        'ledger': ledger.snapshot('kiya', day) if ledger else None,
        'ledger_path': str(ledger.path.resolve()) if ledger else None,
    })
    print(json.dumps([{k: row.get(k) for k in ('rank', 'stage', 'status', 'current_version')}
                      for row in result], ensure_ascii=False))


if __name__ == '__main__':
    main()

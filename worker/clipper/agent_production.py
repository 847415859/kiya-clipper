"""Opt-in candidate production using versioned role decisions and real render tools."""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import tempfile
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

from .agent_evidence import MODES, collect, evidence_rows, subtract, topic_contract
from .agent_flow import (Pause, Review, RunState, decode, digest, disk_guard, identity,
                         interval, load, now, probe, save, session_lock)
from .edit import validate_edit
from .protection import required, revision

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'


def fingerprint(path):
    return digest(path) if path.is_file() else 'missing'


def version_files(directory):
    media, = (directory / 'final').glob('*.mp4')
    return [media, media.with_suffix('.edit.json'), media.with_suffix('.evidence.json'),
            directory / 'quality' / '01.json']


def artifacts_valid(records):
    return all(fingerprint(Path(path)) == checksum for path, checksum in records.items())


def verify_render(directory: Path, edit: dict[str, Any]) -> dict[str, Any]:
    media, normalized, receipt_file, quality_file = version_files(directory)
    receipt, quality = load(receipt_file), load(quality_file)
    info = probe(media)
    if info['height'] != 720 or not info['audio'] or not info['duration'] > 0:
        raise Review('actual media requires 720p and audio')
    stat = media.stat()
    if (receipt['partial'] or receipt['media_size'] != stat.st_size
            or receipt['media_mtime_ns'] != stat.st_mtime_ns
            or receipt['edit_revision'] != revision(load(normalized))
            or revision(load(normalized)) != revision(edit)):
        raise Review('media receipt or draft revision mismatch')
    if not required(edit) or receipt['status'] != 'time_covered' or not quality['ok']:
        raise Review('required evidence or technical quality did not pass')
    if Path(quality['media']).resolve() != media.resolve():
        raise Review('quality report refers to another file')
    decode(media)
    return {'media': str(media), 'probe': info, 'receipt': str(receipt_file),
            'quality': str(quality_file), 'files': {str(p): digest(p) for p in version_files(directory)}}


def render(directory, data, candidate, edit, transcript, config, minimum):
    directory.mkdir(parents=True, exist_ok=True)
    disk_guard([directory, Path(tempfile.gettempdir()), *[Path(s['video']) for s in data['segments']]], minimum)
    selected = dict(candidate, rank=1)
    save(directory / 'candidates.json', dict(data, candidates=[selected]))
    snapshot = directory / 'draft.edit.json'
    save(snapshot, edit)
    if transcript is not None:
        save(directory / 'transcript.json', transcript)
    command = [sys.executable, str(SCRIPTS / 'render.py'), str(directory / 'candidates.json'),
               '--rank', '1', '--height', '720', '--edit', str(snapshot), '--config', str(config)]
    with (directory / 'render.log').open('a', encoding='utf-8') as log:
        proc = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=900)
    if proc.returncode:
        raise Pause(f'render exit {proc.returncode}: {directory / "render.log"}')
    return verify_render(directory, edit)


def repair(edit, decision, evidence, mapping, transcript=None):
    revised = copy.deepcopy(edit)
    start, end = interval(decision['output_range'])
    touched = []
    for row in mapping:
        left, right = max(start, row['output_start']), min(end, row['output_end'])
        if left < right:
            offset = row['source_start'] - row['output_start']
            touched.append({'source_start': left + offset, 'source_end': right + offset})
    if not touched or not decision.get('reason'):
        raise Review('repair needs a mapped output interval and reason')
    action = decision['action']
    if action == 'extend_tail':
        source_end = decision['source_end']
        origin = edit['window']['start']
        old_end = origin + edit['trim']['end']
        if not old_end < source_end <= min(old_end + 5, edit['window']['end']):
            raise Review('tail extension exceeds local repair bounds')
        proven = [(r['start'], r['end']) for r in evidence if r['modality'] in {'av', 'audio'}
                  and r['id'] in decision.get('evidence_ids', [])]
        if subtract([old_end, source_end], proven) or abs(touched[-1]['source_end'] - old_end) > 0.05:
            raise Review('tail extension lacks continuous original audio or tail location')
        revised['trim']['end'] = round(source_end - origin, 3)
        revised.setdefault('required_evidence', []).append({
            'id': f'tail-repair-{old_end:g}', 'start': old_end, 'end': source_end,
            'modality': '原声', 'role': 'effective_response', 'reason': decision['reason'],
        })
    elif action == 'remove_overlay':
        item = next((x for x in edit['items'] if x['id'] == decision.get('item_id')), None)
        if item is None or not any(edit['window']['start'] + item['start'] < row['source_end']
                                   and edit['window']['start'] + item['start'] + item['duration'] > row['source_start']
                                   for row in touched):
            raise Review('overlay does not match repair location')
        revised['items'] = [x for x in edit['items'] if x['id'] != item['id']]
    elif action == 'shorten_subtitle':
        from .speech_edit import sentence_text, validate_overrides

        override, = validate_overrides([decision['subtitle']])
        a, b = override['t0'], override['t1']
        matching = [row for row in (transcript or {}).get('sentences', [])
                    if round(row['t0'], 2) == round(a, 2) and round(row['t1'], 2) == round(b, 2)]
        if len(matching) != 1 or sentence_text(matching[0]) != override['original']:
            raise Review('subtitle revision does not match the original transcript')
        if not any(a < row['source_end'] and b > row['source_start'] for row in touched):
            raise Review('subtitle does not match repair location')
        subs = revised.setdefault('subtitles', {})
        overrides = subs.get('sentence_overrides', [])
        key = (round(a, 2), round(b, 2))
        previous = next((row['text'] for row in overrides if (round(row['t0'], 2), round(row['t1'], 2)) == key),
                        override['original'])
        remaining = iter(previous)
        text = override['text']
        if len(text) >= len(previous) or not all(char in remaining for char in text):
            raise Review('subtitle shortening must only remove characters from the current wording')
        subs['sentence_overrides'] = [row for row in overrides
                                      if (round(row['t0'], 2), round(row['t1'], 2)) != key] + [override]
    else:
        raise Review(f'unsupported semantic repair: {action}')
    return revised


class Production:
    def __init__(self, source, output, roles, config, ledger=None, minimum=1024**3, max_repairs=2):
        self.source, self.output, self.config = source.resolve(), output.resolve(), config.resolve()
        if self.output == self.source.parent or self.source.parent in self.output.parents:
            raise ValueError('output must be separate from the original session')
        self.roles = roles
        self.ledger, self.minimum, self.max_repairs = ledger, minimum, max_repairs

    def run(self, ranks=None):
        self.output.mkdir(parents=True, exist_ok=True)
        binding = self.output / 'session.json'
        with closing(session_lock(self.output / 'session.lock.sqlite3')):
            if binding.exists() and load(binding)['source'] != str(self.source):
                raise ValueError('output is already assigned to another session')
            if not binding.exists():
                save(binding, {'source': str(self.source), 'schema': 1})
        candidate_bytes = self.source.read_bytes()
        input_revision = hashlib.sha256(candidate_bytes).hexdigest()
        data = json.loads(candidate_bytes)
        data['segments'] = [dict(s, video=str((self.source.parent / s['video']).resolve())) for s in data['segments']]
        results = []
        for candidate in data['candidates']:
            rank = candidate['rank']
            if type(rank) is not int or rank < 1 or (ranks and rank not in ranks):
                continue
            directory = self.output / f'{rank:02d}'
            directory.mkdir(exist_ok=True)
            try:
                with closing(session_lock(directory / 'run.lock.sqlite3')):
                    results.append(self.candidate(data, candidate, directory, input_revision))
            except sqlite3.OperationalError as exc:
                if 'locked' not in str(exc):
                    raise
                results.append({'rank': rank, 'status': 'busy'})
        return results

    def candidate(self, data, candidate, directory, candidate_file_revision):
        state = RunState(directory / 'state.json', self.ledger)
        state.recover()
        rank = candidate['rank']
        inventory = directory / 'inventory.json'
        state.commit(rank=rank, session=data.get('session', self.source.parent.name),
                     room=data.get('room_id', '1724773744'), streamer=data.get('streamer', 'kiya'),
                     role_adapter=self.roles.name)
        try:
            role_data = self.roles.for_rank(rank)
            def source_versions():
                return [identity(path) if path.is_file() else [str(path), 'missing']
                        for path in (Path(s['video']) for s in data['segments'])]

            sources = source_versions()
            formal = self.source.parent / 'edits' / f'{rank:02d}.json'
            transcript_path = self.source.parent / 'transcript.json'
            if digest(self.source) != candidate_file_revision:
                raise Pause('candidate input changed before execution')
            base = [candidate, data['segments'], sources, fingerprint(self.config)]
            formal_revision = fingerprint(formal)
            transcript_revision = fingerprint(transcript_path)
            current_inventory = load(inventory) if inventory.exists() else None
            if current_inventory and (current_inventory['base'] != base or current_inventory['formal_revision'] != formal_revision
                                      or current_inventory['transcript_revision'] != transcript_revision
                                      or current_inventory['role_revision'] != role_data
                                      or not artifacts_valid(current_inventory['render']['files'])):
                inventory.unlink()
            rows = evidence_rows(role_data.get('evidence', []), data['segments'])
            topic = state.step('topic', [base, rows, role_data['topic']],
                               lambda: topic_contract(role_data['topic'], rows))
            state.commit(topic=topic, evidence=rows, event_id=topic.get('event_id', f'{state.data["session"]}:{rank}'))
            if topic['verdict'] == 'drop':
                state.event('topic', 'dropped', reason=topic['reason'])
                return state.data
            if topic['verdict'] == 'review':
                raise Review(topic['reason'])
            questions = topic.get('questions', [])
            if not isinstance(questions, list):
                raise Review('invalid evidence questions')
            for index, question in enumerate(questions):
                name = f'evidence-{index}'
                result = state.step(name, [base, question, rows],
                                    lambda: collect(question, rows, data['segments'], directory / name),
                                    valid=lambda r: not r.get('failures') and all(
                                        Path(c['file']).is_file() and digest(Path(c['file'])) == c['sha256']
                                        for c in r['coverage']))
                if result['unresolved']:
                    if result['media_gaps']:
                        raise Review(f"unresolved {question['id']}: {question['question']}; media gaps {result['media_gaps']}")
                    answer = state.step(f'{name}-answer', [result, role_data.get('answers'), role_data.get('simulate_paid_answer'), self.roles.name],
                                        lambda: self.roles.answer(role_data, question, result, state))
                    if not answer:
                        raise Review(f"unresolved {question['id']}: {question['question']}")
                    answer = evidence_rows([answer], data['segments'])[0]
                    eligible = {'av', MODES[question['kind']]}
                    if (answer['modality'] not in eligible or question['id'] not in answer.get('answers', [])
                            or subtract([answer['start'], answer['end']], [c['range'] for c in result['coverage'] if c['modality'] in eligible])
                            or subtract(question['range'], [(answer['start'], answer['end']), *result['reused']])):
                        raise Review(f"answer does not cover {question['id']}")
                    rows = [*rows, answer]
            state.commit(evidence=rows, boundary_status='confirmed_by_adapter')
            length = candidate['clip_end'] - candidate['clip_start']
            raw_draft = copy.deepcopy(role_data['draft'])
            for index, effect in enumerate(raw_draft.get('items', [])):
                if not effect.get('id'):
                    effect['id'] = f'effect-{index}-{revision(effect)[:12]}'
            draft = validate_edit(raw_draft, length)
            if 'window' not in draft:
                raise Review('draft must specify source window')
            for item in required(draft):
                if subtract([item['start'], item['end']], [(r['start'], r['end']) for r in rows]):
                    raise Review('required draft time is unsupported by evidence')
            transcript = load(transcript_path) if transcript_path.exists() else None
            parent = None
            for index in range(self.max_repairs + 1):
                record = state.step(f'draft-{index}', [base, draft, rows, parent, formal_revision, transcript_revision],
                                    lambda: {'id': revision({'edit': draft, 'parent': parent, 'input': base,
                                                            'evidence': rows, 'transcript': transcript_revision}),
                                             'parent': parent, 'edit': draft, 'evidence': rows,
                                             'base_revision': formal_revision, 'created_at': now()})
                version = directory / 'versions' / record['id']
                manifest = version / 'draft.json'
                if manifest.exists() and load(manifest)['edit'] != draft:
                    raise Review('draft snapshot changed')
                if not manifest.exists():
                    save(manifest, record)
                def render_version():
                    target = version
                    if (version / 'final').exists():
                        from uuid import uuid4
                        target = version / 'renders' / uuid4().hex
                    return render(target, data, candidate, draft, transcript, self.config, self.minimum)

                result = state.step(f'render-{index}', [record['id'], base], render_version,
                                    valid=lambda r: artifacts_valid(r['files']))
                receipt = load(Path(result['receipt']))
                mapping = [dict(row, output_start=row['output_start'] + receipt['callback_offset'],
                                output_end=row['output_end'] + receipt['callback_offset']) for row in receipt['source_to_output']]
                decision = state.step(f'review-{index}', [record['id'], result['files'], role_data.get('reviews')],
                                      lambda: self.roles.review(role_data, index, result))
                state.commit(current_version=record['id'], render=result)
                if decision['verdict'] == 'pass':
                    if (digest(self.source) != candidate_file_revision or fingerprint(self.config) != base[-1]
                            or fingerprint(formal) != formal_revision or fingerprint(transcript_path) != transcript_revision
                            or sources != source_versions()):
                        raise Pause('candidate, configuration, source, transcript or human edit changed during production')
                    if not artifacts_valid(result['files']):
                        raise Review('render changed after review')
                    pack = role_data.get('pack')
                    if not isinstance(pack, dict) or not pack.get('title'):
                        raise Review('inventory requires a reviewed publish pack')
                    pack_file = version / f'publish_pack-{revision(pack)[:16]}.json'
                    if not pack_file.exists() or load(pack_file) != pack:
                        save(pack_file, pack)
                    item = {'rank': rank, 'event_id': state.data['event_id'], 'version': record['id'],
                            'draft': str(manifest), 'draft_sha256': digest(manifest),
                            'base': base, 'role_revision': role_data, 'formal_revision': formal_revision,
                            'transcript_revision': transcript_revision, 'render': result,
                            'review': decision, 'pack': str(pack_file), 'pack_sha256': digest(pack_file),
                            'simulation': self.roles.name == 'fixture'}
                    if not inventory.exists() or load(inventory) != item:
                        save(inventory, item)
                    if state.data['status'] != 'inventoried':
                        state.event('inventory', 'inventoried', file=str(inventory))
                    return state.data
                if decision['verdict'] != 'repair' or index == self.max_repairs:
                    raise Review(decision.get('reason', 'review or repair limit reached'))
                parent = record['id']
                draft = validate_edit(repair(draft, decision, rows, mapping, transcript), length)
        except (Pause, OSError, subprocess.SubprocessError) as exc:
            state.event(state.data.get('stage', 'inputs'), 'paused', reason=str(exc))
        except (Review, ValueError, KeyError, TypeError) as exc:
            state.event(state.data.get('stage', 'inputs'), 'needs_review', reason=str(exc))
        if inventory.exists():
            inventory.unlink()
        return state.data

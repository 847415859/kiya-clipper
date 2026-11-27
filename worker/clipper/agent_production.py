"""Opt-in candidate production using versioned role decisions and real render tools."""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
import sqlite3
import tempfile
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from typing import Any
from uuid import uuid4

from .agent_evidence import MODES, collect, evidence_rows, normalize_segments, source_spans, subtract, topic_contract
from .agent_flow import (Pause, Review, RunState, decode, digest, disk_guard, identity,
                         interval, load, now, probe, save, session_lock)
from .edit import validate_edit
from .protection import required, revision
from .agent_repairs import repair

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'


def fingerprint(path):
    return digest(path) if path.is_file() else 'missing'


def version_files(directory):
    media, = (directory / 'final').glob('*.mp4')
    return [media, media.with_suffix('.edit.json'), media.with_suffix('.evidence.json'),
            directory / 'quality' / '01.json']


def artifacts_valid(records):
    return all(fingerprint(Path(path)) == checksum for path, checksum in records.items())


def verify_render(directory: Path, edit: dict[str, Any], source_segments: list[dict] | None = None) -> dict[str, Any]:
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
    return verify_render(directory, edit, data.get('segments'))


def adopt_rank5_repaired_audio(version, previous, preview, edit, candidate, minimum):
    from .quality import quality_report, write_report

    old = Path(previous['media'])
    old_root = old.parent.parent
    if not old.resolve().is_relative_to(version.resolve()) or not artifacts_valid(previous['files']):
        raise Review('原返修成片或其记录已变化')

    def video_checksum(path):
        return subprocess.check_output(
            ['ffmpeg', '-nostdin', '-v', 'error', '-i', str(path), '-map', '0:v:0',
             '-c', 'copy', '-f', 'hash', '-'], timeout=120,
        )

    if video_checksum(old) != video_checksum(preview):
        raise Review('音轨修复版的画面与已验收返修版不同')
    target = version / 'renders' / f'audio-{uuid4().hex}'
    target.mkdir(parents=True)
    disk_guard([target], minimum)
    media = target / 'final' / old.name
    media.parent.mkdir()
    shutil.copyfile(preview, media)
    checksum = digest(media)
    if checksum != '63746e257730f660087d98d2fbefd633a2cb40361f38b844cb45ae4fab64246b':
        raise Review('修复版不匹配已通过本地与服务器对照检查的文件')
    for relative in ('candidates.json', 'transcript.json', 'render/job_01/props.json'):
        source = old_root / relative
        if source.is_file():
            dest = target / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
    shutil.copyfile(old.with_suffix('.edit.json'), media.with_suffix('.edit.json'))
    bindings = {'media_size': media.stat().st_size, 'media_mtime_ns': media.stat().st_mtime_ns}
    receipt = load(Path(previous['receipt']))
    save(media.with_suffix('.evidence.json'), {**receipt, **bindings,
         'audio_repair': {'previous_media_sha256': previous['files'][str(old)], 'media_sha256': checksum,
                          'output_range': [37.033, 38.9], 'basis': 'verified_original_audio_splice'}})
    schedule = load(old.with_suffix('.schedule.json'))
    save(media.with_suffix('.schedule.json'), {**schedule, 'video_size': bindings['media_size'],
                                             'video_mtime_ns': bindings['media_mtime_ns']})
    report = quality_report(target, 1, final=media, candidate={**candidate, 'rank': 1}, edit=edit)
    write_report(report, target / 'quality/01.json')
    if report.checks.get('audio_continuity', {}).get('status') != 'pass':
        raise Review('修复版音频连续性仍未通过')
    return verify_render(target, edit)


def rank5_opening_overlay_decision(edit, decision, rendered):
    """Record the one approved correction without changing the model review."""
    reason = decision.get('reason') or ''
    prefix = '刚做完奶粉雪花酥'
    if (decision.get('verdict') != 'repair' or decision.get('action') or decision.get('output_range')
            or prefix not in reason or '重叠遮挡' not in reason
            or decision.get('media_sha256') != rendered['files'].get(rendered['media'])):
        raise Review(f'已保存的审片结论与本次定点返修不匹配。{reason}')
    target_id = '4334c3f5'
    candidates = [item for item in edit['items'] if item['type'] == 'text' and item.get('id') == target_id]
    if len(candidates) != 1:
        raise Review('无法唯一定位需要移除的开头花字')
    return {**decision, 'action': 'remove_overlay', 'output_range': [0.2, 3.2],
            'item_id': target_id, 'repair_basis': 'operator_approved_opening_overlay'}


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
        data['segments'] = normalize_segments(data['segments'], self.source.parent)
        save(self.output / 'source_segments.json', {'segments': data['segments']})
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
                                      or not artifacts_valid(current_inventory['render']['files'])
                                      or fingerprint(Path(current_inventory['draft'])) != current_inventory.get('draft_sha256')
                                      or fingerprint(Path(current_inventory['pack'])) != current_inventory.get('pack_sha256')):
                inventory.unlink()
                state.commit(qualified_version=None)
            elif current_inventory:
                state.commit(qualified_version=current_inventory['version'])
            role_data = self.roles.for_rank(rank)
            rows = evidence_rows(role_data.get('evidence', []), data['segments'])
            contextual = getattr(self.roles, 'contextual_gap', None)
            topic = state.step('topic', [base, rows, role_data['topic']],
                               lambda: topic_contract(role_data['topic'], rows), reuse_only=contextual is not None)
            state.commit(topic=topic, evidence=rows, event_id=topic.get('event_id', f'{state.data["session"]}:{rank}'))
            if topic['verdict'] == 'drop':
                inventory.unlink(missing_ok=True)
                state.commit(qualified_version=None)
                state.event('topic', 'dropped', reason=topic['reason'])
                return state.data
            if topic['verdict'] == 'review':
                raise Review(topic['reason'])
            questions = topic.get('questions', [])
            if not isinstance(questions, list):
                raise Review('invalid evidence questions')
            if contextual is not None:
                self.roles.check_context_target(state, questions)
            manual_review_audit = None
            for index, question in enumerate(questions):
                name = f'evidence-{index}'
                target = contextual is not None and question['id'] == contextual
                context = self.roles.context_inputs(question) if target else None
                result = state.step(name, [base, question, rows],
                                    lambda: collect(question, rows, data['segments'], directory / name),
                                    valid=lambda r: not r.get('failures') and all(
                                        Path(c['file']).is_file() and digest(Path(c['file'])) == c['sha256']
                                        for c in r['coverage']), reuse_only=contextual is not None)
                if result['unresolved'] or target:
                    if result['media_gaps']:
                        raise Review(f"unresolved {question['id']}: {question['question']}; media gaps {result['media_gaps']}")
                    eligible = {'av', MODES[question['kind']]}
                    original_coverage = [c['range'] for c in result['coverage'] if c['modality'] in eligible]
                    answer_key = [result, role_data.get('answers'), role_data.get('simulate_paid_answer'), self.roles.name]
                    if target:
                        if subtract(question['range'], original_coverage):
                            raise Review(f"original evidence does not cover {contextual}")
                        manual_review = getattr(self.roles, 'manual_reviews', {}).get(contextual)
                        response = state.step(f'{name}-answer-context', [answer_key, context,
                                              getattr(self.roles, 'manual_review_revision', None)],
                                              lambda: self.roles.answer_context(question, context, manual_review))
                        if not response['answer']:
                            raise Review(f"{contextual}: {response['reason']}")
                        answer = response['answer']
                        manual_review_audit = response.get('manual_review')
                    else:
                        answer = state.step(f'{name}-answer', answer_key,
                                            lambda: self.roles.answer(role_data, question, result, state),
                                            reuse_only=contextual is not None)
                    if not answer:
                        raise Review(f"unresolved {question['id']}: {question['question']}")
                    answer = evidence_rows([answer], data['segments'])[0]
                    if (answer['modality'] not in eligible or question['id'] not in answer.get('answers', [])
                            or subtract([answer['start'], answer['end']], original_coverage)
                            or subtract(question['range'], [(answer['start'], answer['end']), *result['reused']])):
                        raise Review(f"answer does not cover {question['id']}")
                    rows = [*rows, answer]
            state.commit(evidence=rows, boundary_status='manual_review_confirmed' if manual_review_audit
                         else 'confirmed_by_adapter', manual_review=manual_review_audit)
            length = candidate['clip_end'] - candidate['clip_start']
            transcript = load(transcript_path) if transcript_path.exists() else None
            draft_inputs = [base, role_data, rows, transcript_revision, self.roles.name]
            if contextual is not None:
                draft_inputs.append(state.data['steps']['evidence-1-answer-context']['key'])
                draft_inputs.append(state.data.get('manual_review'))
            raw_draft = copy.deepcopy(state.step('initial-edit', draft_inputs,
                lambda: self.roles.draft(role_data, state, data, candidate, rows, transcript))
                if hasattr(self.roles, 'draft') else role_data['draft'])
            for index, effect in enumerate(raw_draft.get('items', [])):
                if not effect.get('id'):
                    effect['id'] = f'effect-{index}-{revision(effect)[:12]}'
            draft = validate_edit(raw_draft, length)
            if 'window' not in draft:
                raise Review('draft must specify source window')
            for item in required(draft):
                if subtract([item['start'], item['end']], [(r['start'], r['end']) for r in rows]):
                    raise Review('required draft time is unsupported by evidence')
            parent = None
            for index in range(self.max_repairs + 1):
                window = draft['window']
                gaps = subtract([window['start'], window['end']], source_spans(data['segments']))
                if gaps:
                    raise Review(f'draft crosses unavailable source: {gaps}')
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
                state.commit(working_version=record['id'])
                def render_version():
                    target = version
                    if (version / 'final').exists():
                        target = version / 'renders' / uuid4().hex
                    rendered = render(target, data, candidate, draft, transcript, self.config, self.minimum)
                    sidecar = Path(rendered['media']).with_suffix('.audio.json')
                    if not sidecar.is_file():
                        raise Review('音频诊断 sidecar 缺失，需人工复核')
                    audio = load(sidecar)
                    diagnosis = audio.get('after_recovery', {})
                    final_stage = diagnosis.get('stages', {}).get('final', {})
                    cut_stage = diagnosis.get('stages', {}).get('cut', {})
                    if (diagnosis.get('status') != 'diagnosed' or diagnosis.get('earliest_stage') != 'none'
                            or final_stage.get('sha256') != rendered['files'].get(rendered['media'])
                            or final_stage.get('status') != 'available' or final_stage.get('gaps')
                            or cut_stage.get('status') != 'available' or cut_stage.get('gaps')
                            or any(s.get('status') != 'available' or s.get('gaps') for s in diagnosis.get('sources', []))):
                        raise Review('音频分阶段诊断存在缺口、不匹配或不确定结果，需人工复核')
                    rendered['audio_diagnostics'] = audio
                    rendered['files'][str(sidecar)] = digest(sidecar)
                    return rendered

                result = state.step(f'render-{index}', [record['id'], base, data['segments']], render_version,
                                    valid=lambda r: artifacts_valid(r['files']))
                receipt = load(Path(result['receipt']))
                mapping = [dict(row, output_start=row['output_start'] + receipt['callback_offset'],
                                output_end=row['output_end'] + receipt['callback_offset']) for row in receipt['source_to_output']]
                review_input = {**role_data, 'evidence': rows,
                                'version_id': record['id'], 'parent_version': parent,
                                'transcript_revision': transcript_revision,
                                'transcript': {'sentences': [s for s in (transcript or {}).get('sentences', [])
                                    if s['t0'] < window['end'] and s['t1'] > window['start']]}}
                review_inputs = [record['id'], result['files'], review_input, self.roles.name]
                if contextual is not None and state.data.get('manual_review'):
                    review_inputs.append(state.data['manual_review'])
                state.commit(current_version=record['id'], working_version=record['id'], render=result)
                def review_version():
                    decision = self.roles.review(review_input, index, result)
                    if decision.get('verdict') in {'pass', 'repair'}:
                        if decision.get('media_sha256') != result['files'].get(result['media']):
                            raise Review(f"审片建议媒体哈希与当前版本不匹配。{decision.get('reason', '')}")
                        current_edit = load(Path(result['media']).with_suffix('.edit.json'))
                        if decision.get('edit_revision') != revision(current_edit):
                            raise Review(f"审片建议编辑版本已过期。{decision.get('reason', '')}")
                        if decision.get('version_id', record['id']) != record['id']:
                            raise Review('review version_id differs from current draft')
                        if decision.get('verdict') == 'repair' and not str(decision.get('expected_change') or '').strip():
                            raise Review('repair requires expected_change and exact location')
                    return {**decision, 'version_id': record['id'], 'simulation': self.roles.name == 'fixture'}
                decision = state.step(f'review-{index}', review_inputs, review_version,
                                      valid=lambda d: d.get('version_id') == record['id'])
                if decision['verdict'] == 'pass':
                    if manual_review_audit:
                        self.roles.verify_manual_review_render(result, manual_review_audit)
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
                    state.commit(qualified_version=record['id'])
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
            accepted = load(inventory)
            unchanged = ('formal' in locals() and 'transcript_path' in locals() and 'sources' in locals()
                         and accepted.get('base') == locals().get('base')
                         and accepted.get('formal_revision') == fingerprint(formal)
                         and accepted.get('transcript_revision') == fingerprint(transcript_path)
                         and artifacts_valid(accepted['render']['files'])
                         and fingerprint(Path(accepted['draft'])) == accepted.get('draft_sha256')
                         and fingerprint(Path(accepted['pack'])) == accepted.get('pack_sha256')
                         and digest(self.source) == candidate_file_revision
                         and sources == source_versions())
            if unchanged:
                state.commit(qualified_version=accepted['version'])
            else:
                inventory.unlink()
                state.commit(qualified_version=None)
        return state.data

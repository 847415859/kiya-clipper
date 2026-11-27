"""Production roles backed by rerank, autoedit, and continuous AV review."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from .agent_flow import Pause, Review, digest, load, now, probe, save
from .agent_production import SCRIPTS
from .llm import DEFAULT_BASE_URL, load_env, text_part
from .video_evidence import make_video_client


class EvidenceAnswer(BaseModel):
    supported: bool
    content: str


class ContextAnswer(BaseModel):
    supported: bool
    quote: str = ''
    explanation: str = ''
    reason: str = ''


class ManualEvidenceReview(BaseModel):
    schema_version: int = Field(alias='schema')
    session: str
    streamer: str
    rank: int
    question_id: str
    quote: str
    source_range: list[float]
    vocal_range: list[float]
    media: dict
    reviewer: str
    reviewed_at: str
    note: str


RANK5_TARGET = [2058.223, 2060.080]
CONTEXT_QUESTION = 'Kiya 在目标区间说了什么？请引用能听清的原话，并说明前文如何帮助理解。无法确认时说明原因。'
CONTEXT_VERSION = 'rank5-gap1-context-v1'


class TargetedRepair(BaseModel):
    output_range: list[float] | None = None
    action: Literal['extend_tail', 'remove_overlay', 'remove_zoom', 'adjust_overlay', 'move_overlay',
                    'shorten_overlay', 'adjust_zoom', 'shorten_subtitle', 'correct_subtitle',
                    'adjust_subtitle_timing', 'adjust_subtitle_layout', 'split_subtitle',
                    'adjust_boundary', 'adjust_rhythm'] | None = None
    expected_change: str | None = None
    item_id: str | None = None
    subtitle: dict | None = None
    source_end: float | None = None
    evidence_ids: list[str] | None = None
    changes: dict | None = None
    trim: dict | None = None
    cuts: list[dict] | None = None


class MediaDecision(TargetedRepair):
    verdict: Literal['pass', 'repair', 'review']
    reason: str = Field(min_length=1)
    actions: list[TargetedRepair] | None = None
    version_id: str | None = None
    media_sha256: str | None = None
    edit_revision: str | None = None


class LiveRoles:
    name = 'live-v1'

    def __init__(self, source, output, config, ledger):
        self.source, self.output, self.config = source.resolve(), output.resolve(), config.resolve()
        self.ledger = ledger.resolve()
        self.rerank = self.source.with_name('rerank.json')

    def for_rank(self, rank):
        rows = load(self.rerank)['candidates'] if self.rerank.is_file() else []
        row = next((r for r in rows if r['rank'] == rank and r.get('llm')), None)
        if not row:
            raise Pause('rerank result pending')
        llm, event = row['llm'], row.get('event') or {}
        report = row.get('video_evidence') or {}
        modes = {'原声': 'audio', '音画': 'av', '画面': 'visual', '字幕': 'visual'}
        observations = [dict(id=o['id'], start=o['start'], end=o['end'], content=o['description'],
                             modality=modes[o['modality']]) for o in report.get('observations', [])
                        if o.get('modality') in modes and o.get('id') and o.get('description')]
        reason = llm.get('reason') or llm.get('what_happened') or '精排未给出理由'
        if row.get('taboo') or llm.get('score', 0) < 4:
            return {'topic': {'verdict': 'drop', 'reason': reason}, 'evidence': observations}
        questions = []
        for i, gap in enumerate(report.get('selected_gaps', report.get('gaps', [])) or []):
            if isinstance(gap, (tuple, list)) and len(gap) == 2:
                questions.append({'id': f'gap-{i}', 'kind': 'setup' if gap[1] <= row.get('t', 0) else 'response',
                                  'range': list(gap), 'question': '核实此区间的前因或回应，记录原话与可见动作'})
        if len(questions) > 2 or report.get('unknown_ids') or (report.get('needs_review') and not questions):
            return {'topic': {'verdict': 'review', 'reason': f'{reason}。精排仍有待复核证据'},
                    'evidence': observations}
        title = llm.get('what_happened') or event.get('summary')
        topic = {'verdict': 'worth' if observations else 'review', 'reason': reason, 'topic': llm.get('type'),
                 'event_id': event.get('event_id') or f'{self.source.parent.name}:{rank}',
                 'evidence_ids': [o['id'] for o in observations], 'questions': questions, 'score': row.get('final')}
        return {'topic': topic, 'evidence': observations, 'rerank': row,
                'pack': {'title': str(title or reason)[:80], 'eligibility': 'proposal_only'}}

    def _request(self, media, prompt, schema, *, context=None):
        load_env()
        if Path(os.environ.get('KIYA_OPS_LEDGER', '')).resolve() != self.ledger:
            raise Pause('isolated model ledger missing')
        client = make_video_client({}, self.output / '_llm_cache')
        if getattr(self, 'contextual_gap', None) is not None:
            if (client.model != 'qwen3.8-omni-flash' or client.base_url != DEFAULT_BASE_URL
                    or os.environ.get('KIYA_OPS_LLM_MAX_FEN') != '368'):
                raise Pause('rank5 isolation requires Beijing Omni and the verified 368 fen reservation')
            if not client.api_key:
                raise Pause('rank5 isolation requires an existing Omni credential')
        content = []
        if sum(path.stat().st_size for path in media) > 18_000_000:
            raise Review('media exceeds bounded model request size')
        assets = []
        for path in media:
            payload = path.read_bytes()
            assets.append({'path': str(path), 'sha256': hashlib.sha256(payload).hexdigest(), 'bytes': len(payload)})
            content.append({'type': 'video_url', 'video_url': {
                'url': 'data:;base64,' + base64.b64encode(payload).decode('ascii')}})
        content.append(text_part(prompt))
        request_id = uuid4().hex
        if context is not None:
            if assets != context['assets']:
                raise Review('context media changed before request')
            client.max_retries = 1
            from .protection import revision
            request_id = revision(context)
        audit_path = self.output / 'model_requests' / f'{request_id}.json'
        if context is not None and audit_path.exists():
            prior = load(audit_path)
            if prior.get('status') == 'completed' and prior.get('response') is not None:
                return prior['response']
            if prior.get('status') != 'failed' or prior.get('request_attempts') != 0:
                raise Review(prior.get('error') or 'previous contextual request may have reached the model; not resending')
        audit = {'started_at': now(), 'model': client.model, 'purpose': schema.__name__, 'media': assets,
                 'ledger': str(self.ledger), 'configured_max_fen': os.environ.get('KIYA_OPS_LLM_MAX_FEN'),
                 'status': 'in_flight'}
        if context is not None:
            audit['context'] = context
        save(audit_path, audit)
        result = client.chat_json([{'role': 'user', 'content': content}], schema, tag=self.name,
                                  retry_invalid=context is None)
        response = result.data.model_dump(exclude_none=True) if result.data is not None else None
        save(audit_path, {**audit, 'finished_at': now(), 'status': 'completed' if response is not None else 'failed',
                          'usage': result.usage, 'cached': result.cached, 'request_attempts': client.request_attempts,
                          'requests_without_usage': client.requests_without_usage, 'error': result.error,
                          **({'response': response} if context is not None else {})})
        if response is None:
            error = result.error or 'model observation unavailable'
            if context is not None and client.request_attempts:
                raise Review(error)
            raise Pause(error)
        return response

    def answer(self, row, question, result, state):
        from .agent_evidence import MODES

        usable = [c for c in result['coverage'] if c['modality'] in {'av', MODES[question['kind']]}]
        if not usable:
            return None
        answer = self._request([Path(c['file']) for c in usable],
            '根据这些原片音画回答问题。无法确认时 supported=false。只返回 JSON supported, content。\n'
            + json.dumps({'question': question, 'ranges': [c['range'] for c in usable]}, ensure_ascii=False), EvidenceAnswer)
        if answer['supported'] and answer['content'].strip():
            return {'id': 'live-' + question['id'], 'start': min(c['range'][0] for c in usable),
                    'end': max(c['range'][1] for c in usable), 'modality': MODES[question['kind']],
                    'content': answer['content'], 'answers': [question['id']]}
        return None

    def check_context_target(self, state, questions):
        if (state.data['rank'] != 5 or state.data['streamer'] != 'kiya'
                or state.data['session'] != '20261124-2151'
                or [q['id'] for q in questions] != ['gap-0', 'gap-1']
                or questions[1]['range'] != RANK5_TARGET or questions[1]['kind'] != 'response'):
            raise Review('context is only configured for rank 5, session 20261124-2151, gap-1')

    def context_inputs(self, question):
        assets, mapping = [], []
        for index, path in enumerate(self.context_media):
            path = Path(path)
            info = probe(path)
            if not info['audio'] or info['height'] <= 0 or not info['duration'] > 0:
                raise Review(f'context requires playable audio and video: {path}')
            assets.append({'path': str(path), 'sha256': digest(path), 'bytes': path.stat().st_size})
            mapping.append({'clip': index, 'local_range': [0, info['duration']], 'role': 'context_only'})
        start, end = question['range']
        duration = round(end - start, 3)
        if mapping[1]['local_range'][1] < duration:
            raise Review('second context clip is shorter than the target')
        mapping[1].update(role='target_then_context', target_local_range=[0, duration],
                          target_source_range=[start, end], context_only_after=duration)
        return {'assets': assets, 'mapping': mapping, 'question': CONTEXT_QUESTION,
                'version': CONTEXT_VERSION, 'model': 'qwen3.8-omni-flash'}

    def answer_context(self, question, context, manual_record=None):
        if manual_record is not None:
            return self.manual_context_review(question, context, manual_record)
        if self.output.joinpath('05', 'manual_reviews', 'gap-1-human.json').is_file():
            raise Review('manual review is present; skip gap-1 model re-question')
        prompt = (CONTEXT_QUESTION + '\n按发送顺序看两段原声音画。仅第二段开头 0–1.857 秒是目标；'
                  '第一段及第二段其余内容仅供理解，不得引用为目标原话。请独立判断，不猜测。'
                  '只返回 JSON：supported（布尔值），quote（目标内原话），explanation（前文如何帮助理解），'
                  'reason（无法确认的原因）。\n' + json.dumps(context['mapping'], ensure_ascii=False))
        response = self._request(self.context_media, prompt, ContextAnswer, context=context)
        quote = response['quote'].strip()
        if not response['supported'] or not quote:
            return {'answer': None, 'reason': response['reason'].strip() or '模型未确认目标区间的可引用原话',
                    'response': response, 'context': context}
        start, end = question['range']
        answer = {'id': 'live-' + question['id'], 'start': start, 'end': end, 'modality': 'audio',
                  'content': quote, 'answers': [question['id']]}
        return {'answer': answer, 'response': response, 'context': context}

    def manual_context_review(self, question, context, record_path):
        if (question['id'] != 'gap-1' or question['range'] != RANK5_TARGET
                or context.get('question') != CONTEXT_QUESTION):
            raise Review('manual review does not match the configured target')
        verified = []
        for asset in context['assets']:
            path = Path(asset['path'])
            if not path.is_file() or path.stat().st_size != asset['bytes'] or digest(path) != asset['sha256']:
                raise Review('context media changed since evidence collection')
            verified.append({'path': str(path), 'sha256': asset['sha256'], 'bytes': asset['bytes']})
        if len(verified) != 2:
            raise Review('manual review requires both ordered context clips')
        record = load(record_path)
        try:
            review = ManualEvidenceReview.model_validate(record)
        except Exception as exc:
            raise Review(f'invalid manual review record: {exc}') from exc
        if (review.schema_version != 1 or review.session != '20261124-2151' or review.streamer != 'kiya'
                or review.rank != 5 or review.question_id != 'gap-1' or review.source_range != RANK5_TARGET
                or review.vocal_range != [2058.223, 2059.423] or review.quote != '我有点嫌弃他'
                or not review.reviewer.strip() or not review.note.strip()
                or review.media != {'assets': verified, 'mapping': context['mapping']}):
            raise Review('manual review identity, quote, target interval or media hashes do not match')
        answer = {'id': 'manual-gap-1', 'start': RANK5_TARGET[0], 'end': RANK5_TARGET[1],
                  'modality': 'audio', 'content': review.quote, 'answers': ['gap-1'],
                  'basis': 'human_review', 'reviewer': review.reviewer, 'reviewed_at': review.reviewed_at}
        record_path = Path(record_path)
        return {'answer': answer, 'reason': review.note, 'context': context,
                'manual_review': {'path': str(record_path), 'sha256': digest(record_path),
                                  'reviewer': review.reviewer, 'reviewed_at': review.reviewed_at}}

    def draft(self, row, state, data, candidate, evidence, transcript):
        if Path(os.environ.get('KIYA_OPS_LEDGER', '')).resolve() != self.ledger:
            raise Pause('isolated model ledger missing')
        folder = state.path.parent / 'autoedit' / uuid4().hex
        source = folder / 'candidates.json'
        rank = candidate['rank']
        save(source, {**data, 'candidates': [candidate]})
        rerank = row['rerank']
        supplemental = [item for item in evidence if item.get('answers')]
        if supplemental:
            report = rerank.get('video_evidence') or {}
            observations = [dict(id=item['id'], start=item['start'], end=item['end'],
                                 description=item['content'], kind='前因或回应',
                                 modality={'av': '音画', 'audio': '原声', 'visual': '画面'}[item['modality']])
                            for item in supplemental]
            rerank = {**rerank, 'video_evidence': {**report,
                'observations': [*report.get('observations', []), *observations],
                'must_keep_ids': [*report.get('must_keep_ids', []), *(o['id'] for o in observations)]}}
        save(folder / 'rerank.json', {'candidates': [rerank]})
        if transcript is not None:
            save(folder / 'transcript.json', transcript)
        command = [sys.executable, str(SCRIPTS / 'autoedit.py'), str(source), '--rank', str(rank),
                   '--config', str(self.config), '--draft-only', '--styles', 'standard', '--template', 'default']
        answer_audit = state.data.get('manual_review')
        if getattr(self, 'contextual_gap', None) is not None:
            if answer_audit is None:
                raise Review('missing auditable manual confirmation for contextual draft')
            command += ['--isolated-reaction-ending', '--keep-reaction', *map(str, RANK5_TARGET),
                        '--isolated-audit', str(self.output / 'model_requests' / f'{uuid4().hex}.json'),
                        '--manual-review-record', answer_audit['path']]
            if digest(Path(answer_audit['path'])) != answer_audit['sha256']:
                raise Review('manual review record changed before draft generation')
        with (folder / 'autoedit.log').open('w', encoding='utf-8') as log:
            process = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=600)
        drafts = list((folder / 'edits' / 'ai_drafts' / f'{rank:02d}').glob('*.json'))
        if process.returncode or len(drafts) != 1:
            raise Pause(f'autoedit pending: {folder / "autoedit.log"}')
        edit = load(drafts[0])['variants'][0]['edit']
        for item in evidence:
            if not item.get('answers'):
                continue
            if not edit['window']['start'] <= item['start'] < item['end'] <= edit['window']['end']:
                raise Review('confirmed context lies outside the draft window')
            protected = edit.setdefault('required_evidence', [])
            if not any(entry['id'] == item['id'] for entry in protected):
                protected.append({**item, 'role': 'context'})
        return edit

    def verify_manual_review_render(self, rendered, manual_review):
        if not manual_review:
            raise Review('render pass requires the persisted human review record')
        record_path = Path(manual_review['path'])
        if not record_path.is_file() or digest(record_path) != manual_review['sha256']:
            raise Review('manual review record changed after drafting')
        edit_path = Path(rendered['media']).with_suffix('.edit.json')
        edit = load(edit_path)
        record = load(record_path)
        expected_hashes = [asset['sha256'] for asset in record['media']['assets']]
        # Older snapshots can contain a model projection before the human record.
        human_rows = [row for row in edit.get('required_evidence', [])
                      if row.get('id') == 'manual-gap-1' and row.get('basis') == 'human_review']
        if len(human_rows) != 1:
            raise Review('rendered edit does not contain exactly one human evidence record for gap-1')
        item = human_rows[0]
        if (item.get('content') != record['quote'] or [item.get('start'), item.get('end')] != RANK5_TARGET
                or item.get('media_sha256') != expected_hashes):
            raise Review('rendered edit lost the hash-bound human evidence')
        receipt = load(Path(rendered['receipt']))
        spans = [(row['source_start'], row['source_end']) for row in receipt.get('source_to_output', [])]
        from .protection import coverage as source_coverage
        report = source_coverage([item], spans)
        if report['status'] != 'time_covered':
            raise Review('rendered edit no longer covers the confirmed source evidence')

    def review(self, row, index, rendered):
        media = Path(rendered['media'])
        inputs = {'receipt': load(Path(rendered['receipt'])), 'edit': load(media.with_suffix('.edit.json')),
                  'evidence': row['evidence'], 'pack': row['pack'], 'quality': load(Path(rendered['quality'])),
                  'transcript': row.get('transcript'), 'transcript_revision': row.get('transcript_revision'),
                  'version_id': row.get('version_id'), 'parent_version': row.get('parent_version')}
        from .protection import revision
        binding = {'media_sha256': rendered['files'][str(media)], 'edit_revision': revision(inputs['edit'])}
        inputs['binding'] = binding
        decision = self._request([media], '看实际成片，检查前因、反应末句、字幕阅读和画面遮挡及标题真实性。'
            '返回 JSON verdict=pass/repair/review, reason。返修给成片秒 output_range 和 action。'
            '动作：remove_overlay（item_id）、shorten_subtitle（subtitle）、extend_tail（source_end,evidence_ids）、'
            'adjust_overlay/adjust_zoom（item_id,changes）、adjust_boundary（trim）、adjust_rhythm（cuts）。'
            '还支持 move_overlay/shorten_overlay、remove_zoom、correct_subtitle、adjust_subtitle_timing、'
            'adjust_subtitle_layout（subtitle.display_x/display_y .05-.95）、'
            'split_subtitle（subtitle.parts=[{text,t0,t1}]，整场秒，拼接等于当前字幕文字）。'
            '所有通过或返修结论原样返回 binding 的 media_sha256/edit_revision。'
            '返修必须含 expected_change、output_range；缺失或定位不确定则 review。'
            'trim/cuts/item 时间相对 window；subtitle t0/t1/original 必须精确绑定原始转写；'
            '字幕显示时间另用整场秒 display_t0/display_t1，不更改原始绑定。'
            '节奏只删除无语音且非必保证据的区间；边界保留所有必保证据和完整句子。\n'
            + json.dumps(inputs, ensure_ascii=False), MediaDecision)
        if decision.get('verdict') in {'pass', 'repair'} and any(decision.get(k) != v for k, v in binding.items()):
            raise Review('model review media hash or edit revision differs from supplied binding')
        return {**decision, 'basis': 'actual_mp4_model'}

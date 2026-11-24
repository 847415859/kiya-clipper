"""Production roles backed by rerank, autoedit, and continuous AV review."""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from .agent_flow import Pause, Review, load, save
from .agent_production import SCRIPTS
from .llm import load_env, text_part
from .video_evidence import make_video_client


class EvidenceAnswer(BaseModel):
    supported: bool
    content: str


class MediaDecision(BaseModel):
    verdict: Literal['pass', 'repair', 'review']
    reason: str = Field(min_length=1)
    output_range: list[float] | None = None
    action: str | None = None
    item_id: str | None = None
    subtitle: dict | None = None
    source_end: float | None = None
    evidence_ids: list[str] | None = None


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

    def _request(self, media, prompt, schema):
        load_env()
        if Path(os.environ.get('KIYA_OPS_LEDGER', '')).resolve() != self.ledger:
            raise Pause('isolated model ledger missing')
        client = make_video_client({}, self.output / '_llm_cache')
        content = []
        if sum(path.stat().st_size for path in media) > 18_000_000:
            raise Review('media exceeds bounded model request size')
        for path in media:
            content.append({'type': 'video_url', 'video_url': {
                'url': 'data:;base64,' + base64.b64encode(path.read_bytes()).decode('ascii')}})
        content.append(text_part(prompt))
        result = client.chat_json([{'role': 'user', 'content': content}], schema, tag=self.name)
        if result.data is None:
            raise Pause(result.error or 'model observation unavailable')
        return result.data.model_dump(exclude_none=True)

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
        with (folder / 'autoedit.log').open('w', encoding='utf-8') as log:
            process = subprocess.run([sys.executable, str(SCRIPTS / 'autoedit.py'), str(source), '--rank', str(rank),
                                      '--config', str(self.config), '--draft-only', '--styles', 'standard',
                                      '--template', 'default'], stdout=log, stderr=subprocess.STDOUT, timeout=600)
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

    def review(self, row, index, rendered):
        media = Path(rendered['media'])
        inputs = {'receipt': load(Path(rendered['receipt'])), 'edit': load(media.with_suffix('.edit.json')),
                  'evidence': row['evidence'], 'pack': row['pack'], 'quality': load(Path(rendered['quality']))}
        decision = self._request([media], '看实际成片，检查前因、反应末句、字幕阅读和画面遮挡及标题真实性。'
            '返回 JSON verdict=pass/repair/review, reason。返修给成片秒 output_range 和 action。'
            '只允许 remove_overlay（item_id）、shorten_subtitle（subtitle: 原片 t0,t1,original,text）、'
            'extend_tail（原片 source_end 和 evidence_ids）。事实或位置不确定则 review。\n'
            + json.dumps(inputs, ensure_ascii=False), MediaDecision)
        return {**decision, 'media_sha256': rendered['files'][str(media)], 'basis': 'actual_mp4_model'}

"""Load candidate-scoped evidence without silently dropping saved constraints."""
import json
from .protection import resolve


def attach(edit, candidate, folder):
    path = folder / 'rerank.json'
    if not path.exists():
        return edit
    data = json.loads(path.read_text(encoding='utf-8'))
    record = next((r for r in data.get('candidates', []) if r['rank'] == candidate['rank']), {})
    rows, trace = resolve(record, candidate, folder.name)
    result = dict(edit)
    saved = list(edit.get('required_evidence') or [])
    identities = {(r['id'], r.get('evidence_revision')) for r in saved}
    result['required_evidence'] = saved + [r for r in rows if (r['id'],r.get('evidence_revision')) not in identities]
    result['evidence_source_check'] = trace
    return result

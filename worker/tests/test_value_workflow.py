"""Regression cases crossing policy, real input evidence, queues and observations."""
import copy
import json
from types import SimpleNamespace

import pytest

from clipper.boundary import refine_bounds
from clipper.llm import load_prompt, fill
from clipper.memory import VALUE_WEIGHTS, value_final_score
from clipper.publish import normalize_title_options
from clipper.recall import explore
from clipper.value_metrics import observe, summarize, attributed_followers
from clipper.value_review import review_batch, calibrate, publication_quota
from clipper.value_strategy import annotate_shadow, constrain_cute, evidence_errors, route, verify_evidence
from scripts.rerank import RerankOut, is_pick, legacy_pick, listwise_pool


def value(**kwargs):
    return dict(value_schema_version=1, standalone=True, complete=True, confidence=.9,
                funny=False, score=1, **VALUE_WEIGHTS,
                value_evidence=[dict(dimension=k, src="原话", t=1, quote="真的喜欢", why="具体表达") for k in VALUE_WEIGHTS],
                **kwargs)


def high(**kwargs):
    v = value()
    v.update({k: .9 for k in VALUE_WEIGHTS})
    v.update(kwargs)
    return v


@pytest.mark.parametrize("bad", [None, True, "0.9", float('nan'), float('inf'), -1, 2])
def test_invalid_dimension_never_falls_back_to_legacy(bad):
    v = high(cute=bad, funny=True, score=10)
    assert not is_pick(v, False)
    assert listwise_pool([dict(rank=1, llm=v)], 7, 10) == []
    parsed = RerankOut.model_validate(v).model_dump()
    assert parsed['cute'] is None


def test_raw_threshold_zero_and_legacy_dispatch():
    v = high(**{k: .57 for k in VALUE_WEIGHTS})
    assert value_final_score(v, 1, 1) > .65  # rank bonus must not manufacture eligibility
    assert not is_pick(v, False, value_pick_score=.65)
    assert not is_pick(high(**{k: 0 for k in VALUE_WEIGHTS}, funny=True, score=10), False)
    assert is_pick(high(), False)
    assert not legacy_pick(high(), False)
    assert legacy_pick(high(funny=True, score=9), False)


@pytest.mark.parametrize('change, expected', [({'risk_level':'high'},'quarantine'),
    ({'asr_doubt':True},'quarantine'), ({'evidence_errors':['引用不存在']},'quarantine'),
    ({'needs_context':True},'repair'), ({'complete':False},'repair'), ({'hook_ok':False},'repair'),
    ({'funny':False, 'fail_reason':'not_funny'},'normal')])
def test_routing_preserves_fixable_and_nonhumor(change, expected):
    assert route(high(**change))[0] == expected


def test_input_evidence_verification():
    v = high()
    c = dict(clip_start=10, clip_end=20)
    speech = [dict(t0=10, t1=12, text='真的喜欢')]
    assert verify_evidence(v, c, speech, [], []) == []
    assert verify_evidence(v, c, [], [], [])
    v['value_evidence'][0].update(src='画面', t=2)
    assert verify_evidence(v, c, speech, [], [2]) == []
    assert verify_evidence(v, c, speech, [], [5])
    v['value_evidence'][0].update(src='弹幕', t=1)
    assert verify_evidence(v, c, speech, [SimpleNamespace(t=14, text='真的喜欢')], [], delay=3) == []


def test_shadow_never_mutates_official_pick_and_deduplicates():
    recs = [dict(rank=i, new_rank=i, pick=False, final=.1, value_llm=high(), clip_start=10, clip_end=30) for i in range(1,4)]
    before = copy.deepcopy(recs)
    annotate_shadow(recs, 3)
    assert all(r['pick'] == old['pick'] and r['final'] == old['final'] and r['new_rank'] == old['new_rank'] for r,old in zip(recs,before))
    assert sum(r['shadow_pick'] for r in recs) == 1
    assert recs[1]['shadow_comparison']['dup_of'] == 1
    with pytest.raises(ValueError):
        value_final_score(high(),1,1,screen_weight=True)


def test_nonpeak_recall_is_bounded_reproducible_and_separate():
    data = dict(session='s', duration_seconds=600, segments=[dict(offset=0,video='x')],
                candidates=[dict(rank=1,clip_start=0,clip_end=30)])
    original = copy.deepcopy(data)
    sents = [dict(t0=100,t1=103,text='其实我觉得可以试试')]
    q = explore(data, sents, 5, [dict(start=300,end=310,note='人工')])
    assert q == explore(data,sents,5,[dict(start=300,end=310,note='人工')])
    assert data == original and len(q['candidates']) == 6 and q['llm_calls'] == 0
    assert q['review_only'] and all(r['review_only'] for r in q['candidates'])
    assert {r['recall_source'] for r in q['candidates']} == {'manual','transcript_semantic','non_peak_sample'}
    assert explore(data, [], 0)['candidates'] == []
    assert len(explore(data, [], 999)['candidates']) <= 10


def test_bounds_reject_nonfinite_and_trace_clamp():
    r = refine_bounds([],peak=10,start=100,end=12,material_end=20)
    assert 0 <= r['start'] < r['end'] <= 20 and r['before']['start'] == 100
    assert r['after']['end'] == r['end']
    with pytest.raises(ValueError):
        refine_bounds([],peak=float('nan'))
    r = refine_bounds([dict(t0=8,t1=12,text='一整句')], peak=10,start=9,end=11,min_len=30,material_end=12)
    assert r['start'] == 7.7 and r['end'] == 12


def test_cute_constraints_apply_after_other_effects():
    edit = constrain_cute(dict(items=[dict(type='zoom',scale=3)]*4 + [dict(type='fakeui')], music={'track':'x'},callback={}))
    assert len(edit['items']) == 3 and all(i['scale'] == 1.15 for i in edit['items'])
    assert 'music' not in edit and 'callback' not in edit and edit['original_audio']
    version, _, _ = load_prompt('edit_cute_v1')
    assert version == 'edit-cute-v1'


def test_value_prompt_has_independent_rubric_and_fills():
    version, system, user = load_prompt('rerank_value_v1')
    assert version == 'rerank-value-v1' and '未知用 null' in system and '不包含可听音频' in system
    args = dict(profile='',memory='',window='30',context_before='120',context_after='45',before_speech='',speech='',after_speech='',danmaku='',gifts='',frames_note='')
    assert '{{' not in fill(user, **args)


def test_titles_reject_invented_refs_and_numeric_titles():
    v = high(title_candidates=['旧标题'],title_options=[dict(title=123),dict(title='越界',evidence_refs=[99])])
    assert normalize_title_options(v) == (['旧标题'],[])
    v['title_options'] = [dict(title='真实语录',style='语录',why='引用',evidence_refs=[0])]*2
    titles, options = normalize_title_options(v)
    assert titles == ['真实语录'] and options[0]['evidence_refs'] == [0]


def observation(**kwargs):
    row = dict(bvid='BVx',session='s',rank=1,pubdate=1000,stats_fetched_at=87400,stats_stale=False,view=1000,
               type='互动',main_value='情绪回报')
    row.update(kwargs)
    return row


def test_fixed_age_cache_missing_and_attribution():
    row = observation()
    history = observe([row],now=87400)
    assert history['BVx']['24h']['clip_follows_per_1000'] is None
    assert not observe([observation(stats_stale=True)],now=87400)
    assert not observe([observation(stats_fetched_at=90000)],now=90000+86400)
    assert not observe([observation(stats_fetched_at=200000)],now=200000) # cannot fabricate a missed 24h snapshot
    updated = observe([observation(view=2000,stats_fetched_at=87500)],history,now=87500)
    assert updated['BVx']['24h']['view'] == 1000
    assert attributed_followers(observation(followers=10),'clip_account') is None
    row['snapshot_label'] = '24h'
    row['attribution'] = {'clip_account':dict(count=10,verified=True,source='creator_export',window='24h',view=1000)}
    assert attributed_followers(row,'clip_account') == 10
    assert attributed_followers({**row,'view':0},'clip_account') is None
    history['BVdup'] = {'24h':{**history['BVx']['24h'],'bvid':'BVdup'}}
    summary = summarize(history)
    assert summary['24h']['情绪回报']['n'] == 1 and not summary['24h']['情绪回报']['sufficient']


def test_blind_review_hides_predictions_and_never_auto_applies():
    recs=[dict(rank=i,new_rank=i,shadow_rank=4-i,clip_start=i*10,clip_end=i*10+5,value_llm=high()) for i in range(1,4)]
    rows, keys = review_batch(recs,'s')
    assert len(keys) == 3 and all('value_llm' not in row and 'shadow_rank' not in row for row in rows)
    assert calibrate(rows,keys)['status'] == 'insufficient'
    assert not calibrate(rows,keys)['applied']
    assert publication_quota([dict(newcomer=True,pubdate=1),dict(pubdate=2)])['unknown'] == 1


def test_rerank_main_keeps_legacy_and_shadow_independent(tmp_path, monkeypatch):
    import scripts.rerank as script
    path = tmp_path/'candidates.json'
    path.write_text(json.dumps(dict(session='s',segments=[],duration_seconds=60,candidates=[dict(rank=1,t=15,score=1,clip_start=10,clip_end=30)])),encoding='utf-8')
    monkeypatch.setattr(script,'load_cfg',lambda _: {'rerank':{'frames':0,'listwise':False}})
    monkeypatch.setattr(script,'resolve_config',lambda *a,**k:'unused')
    monkeypatch.setattr(script,'load_events',lambda _:[])
    monkeypatch.setattr(script,'load_transcript',lambda _:dict(sentences=[dict(t0=10,t1=12,text='真的喜欢')]))
    monkeypatch.setattr(script,'build_messages',lambda *a,**k:([],a[6]['prompt'],0))
    monkeypatch.setattr(script,'extract_frames',lambda *a,**k:[])
    class Client:
        model='fake'
        total=dict(calls=0,cached=0,prompt_tokens=0,completion_tokens=0)
        def __init__(self,**k): pass
        def cost(self,*a): return None
        def chat_json(self,msgs,schema,tag):
            v = high() if tag == 'rerank_value_v1' else dict(funny=False,score=2,standalone=True,complete=True)
            return SimpleNamespace(data=RerankOut.model_validate(v),error=None,cached=False,usage={})
    monkeypatch.setattr(script,'LLMClient',Client)
    monkeypatch.setattr('sys.argv',['rerank.py',str(path)])
    assert script.main() == 0
    rec=json.loads((tmp_path/'rerank.json').read_text(encoding='utf-8'))['candidates'][0]
    assert not rec['pick'] and rec['shadow_pick'] and rec['value_llm']['funny'] is False


def test_snapshot_sqlite_is_immutable_and_due_collector_is_bounded(tmp_path):
    from clipper.value_metrics import snapshot_store, collect_due, upload_cache_store
    path = tmp_path/'metrics.sqlite3'
    one = observe([observation()], now=87400)
    snapshot_store(path,one)
    changed = observe([observation(view=9999)],now=87400)
    assert snapshot_store(path,changed)['BVx']['24h']['view'] == 1000
    rows=[observation(bvid=f'BV{i}') for i in range(40)]
    calls=[]
    def fetch(bv):
        calls.append(bv)
        return observation(bvid=bv)
    saved,errors,n=collect_due(rows,{},fetch,87400,budget=3)
    assert n == 3 and len(calls) == 3 and len(saved) == 3 and not errors
    _,_,n=collect_due(rows,saved,fetch,200000,budget=3)
    assert n == 0  # missed windows are never retroactively inferred
    cache=upload_cache_store(path,{'BVx':{'pubdate':1000}})
    assert cache['BVx']['pubdate'] == 1000


def test_semantic_model_recall_is_bounded_and_grounded():
    from scripts.recall import semantic_recall, SemanticOut
    class Client:
        calls=0
        def chat_json(self,*args,**kwargs):
            self.calls+=1
            return SimpleNamespace(data=SemanticOut(candidates=[dict(start=100,end=120,quote='真的喜欢',reason='具体表达')]),error=None)
    client=Client()
    sents=[dict(t0=i*20,t1=i*20+10,text='真的喜欢'+'甲'*990) for i in range(50)]
    proposals, errors = semantic_recall(sents,client,5)
    assert client.calls == 3 and not errors
    data=dict(session='s',duration_seconds=1200,candidates=[],segments=[])
    result=explore(data,sents,5,proposals=proposals+[dict(start=500,end=530,quote='编造引用')])
    assert any(r['recall_source']=='semantic_model' for r in result['candidates'])
    assert any(r['reason']=='语义候选引用无法核对' for r in result['recall_skipped'])


def test_quiet_session_signals_do_not_grow_longer_than_time_axis():
    import yaml
    from pathlib import Path
    from clipper.peaks import compute_signals
    cfg=yaml.safe_load((Path(__file__).resolve().parents[2]/'config'/'kiya.yaml').read_text(encoding='utf-8'))
    result=compute_signals([],cfg)
    assert len(result['score']) == len(result['times']) == 1


def test_value_calibration_uses_session_holdout_and_versioned_blind_labels():
    reviews, keys = [], {}
    for session in ('a','b','c'):
        for i in range(4):
            identity=f'{session}:{i}'
            reviews.append(dict(review_id=identity,session=session,reviewer='human',valuable='Y' if i%2 else 'N',mode='blind',rubric_version='value-v1'))
            keys[identity]=dict(value_llm=high(cute=.9 if i%2 else .3))
    result=calibrate(reviews,keys)
    assert result['held_out_session'] == 'c' and result['held_out_pairs'] == 4
    assert not result['applied']
    assert calibrate([{**r,'mode':'fast'} for r in reviews],keys)['status'] == 'insufficient'

import hashlib,json,sys
from pathlib import Path
import pytest
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from clipper import schedule_poster as poster
from clipper.schedule import validate_plan

def make_case(tmp_path,monkeypatch):
    week='2026-11-16';folder=tmp_path/'_schedule';folder.mkdir()
    image=tmp_path/'reference.jpg';Image.new('RGB',(900,1200),'pink').save(image)
    cfg={'streamer':{'name':'测试主播'},'schedule':{'portrait_file':str(image)}}
    plan=validate_plan({'entries':[{'date':'2026-11-18','start_time':'20:00','title':'正确日程，不让模型改字'}]},week)
    state=folder/f'{week}.json';state.write_text(json.dumps({'published':plan,'draft':plan,'active':True}),encoding='utf-8')
    def rev(p):return hashlib.sha256(p.read_bytes()).hexdigest()
    request={'expected_revision':rev(state),'source':'published','supplement':'粉色蝴蝶结','mode':'seedream'}
    monkeypatch.setattr(poster,'font_file',lambda cfg:('C:/Windows/Fonts/msyh.ttc',0) if Path('C:/Windows/Fonts/msyh.ttc').exists() else __import__('clipper.publish',fromlist=['find_font']).find_font('Noto Sans CJK SC'))
    def fake(prompt,ref,out,**kw):
        assert '完整中文主播周计划图片' in prompt and '正确日程，不让模型改字' in prompt and ref.exists()
        Image.new('RGB',(1920,1200),'lavender').save(out)
        return {'model':'fake-seedream','source_url':'secret-not-persisted'}
    monkeypatch.setattr(poster,'generate',fake)
    return week,cfg,request,rev,state

def test_versions_are_immutable_and_keep_snapshot(tmp_path,monkeypatch):
    week,cfg,request,rev,state=make_case(tmp_path,monkeypatch)
    original=state.read_bytes()
    a=poster.create(tmp_path,week,cfg,request,rev);first=poster.poster_dir(tmp_path,week)/a['id']/'poster.png';data=first.read_bytes()
    request['supplement']='改成紫色'
    b=poster.create(tmp_path,week,cfg,request,rev)
    assert a['id']!=b['id'] and first.read_bytes()==data and state.read_bytes()==original
    versions=poster.history(tmp_path,week)['versions'];assert len(versions)==2
    assert versions[0]['plan']['entries'][0]['title']=='正确日程，不让模型改字'
    assert 'source_url' not in versions[0]
    with Image.open(first) as im:assert im.size==(1920,1200)
    assert poster.history(tmp_path/'other',week)['versions']==[]

def test_failure_preserves_history_and_no_paid_call_on_conflict(tmp_path,monkeypatch):
    week,cfg,request,rev,state=make_case(tmp_path,monkeypatch)
    poster.create(tmp_path,week,cfg,request,rev)
    def fail(*args,**kw):raise RuntimeError('provider failed')
    monkeypatch.setattr(poster,'generate',fail)
    request['expected_revision']='stale'
    assert poster.create(tmp_path,week,cfg,request,rev)['conflict']
    request['expected_revision']=rev(state)
    with pytest.raises(RuntimeError):poster.create(tmp_path,week,cfg,request,rev)
    assert len(poster.history(tmp_path,week)['versions'])==1
    assert len(list(poster.poster_dir(tmp_path,week).iterdir()))==1

def test_input_validation_and_draft_snapshot(tmp_path,monkeypatch):
    week,cfg,request,rev,state=make_case(tmp_path,monkeypatch)
    request['source']='draft'
    result=poster.create(tmp_path,week,cfg,request,rev);assert result['source']=='draft'
    request['supplement']='x'*1501
    with pytest.raises(ValueError):poster.create(tmp_path,week,cfg,request,rev)
    with pytest.raises(ValueError):poster.history(tmp_path,'../secret')


def test_default_template_never_calls_seedream(tmp_path,monkeypatch):
    week,cfg,request,rev,state=make_case(tmp_path,monkeypatch)
    request['mode']='template'
    def forbidden(*args,**kw):raise AssertionError('默认模板不应调用付费生图')
    monkeypatch.setattr(poster,'generate',forbidden)
    result=poster.create(tmp_path,week,cfg,request,rev)
    assert result['mode']=='template' and result['model']=='journal-template-v1'
    with Image.open(poster.poster_dir(tmp_path,week)/result['id']/'poster.png') as im:assert im.size==(1920,1080)

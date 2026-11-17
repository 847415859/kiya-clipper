"""Confirmed plans, Beijing date boundaries, human approval, and matching published outros."""
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from clipper.schedule import (snapshot, validate_plan, parse_at, append_description,
                              check_rendered_schedule, for_pack)
spec = importlib.util.spec_from_file_location('schedule_cli', ROOT / 'scripts/schedule.py')
cli = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = cli
spec.loader.exec_module(cli)
WEEK = '2026-11-16'
AT = '2026-11-17T12:00:00+08:00'


def row(day='2026-11-18', start='20:00', end='22:00', title='游戏回', **kw):
    return dict(date=day,start_time=start,end_time=end,title=title,**kw)


def confirm(root, entries, week=WEEK):
    path = root / '_schedule' / (week + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    plan = validate_plan({'entries':entries}, week)
    path.write_text(json.dumps({'active':True,'published':dict(plan,version='v1')}),encoding='utf-8')
    return path


def test_filters_finished_unknown_and_cancelled(tmp_path):
    confirm(tmp_path,[row('2026-11-16'),row('2026-11-17','10:00','12:00'),
        row('2026-11-17','11:00','13:00',title='正在进行'),row('2026-11-17','11:00',''),
        row('2026-11-17','','',title='时间待定'),row(title='已取消',status='cancelled'),
        row('2026-11-19','','',title='',status='rest')])
    snap=snapshot(tmp_path,AT,streamer_name='测试主播')
    assert [r['title'] for r in snap['entries']]==['正在进行','时间待定','休息']
    assert '北京时间' in snap['heading'] and '周四' in snap['description_block']
    assert '已取消' not in snap['description_block']


def test_overnight_and_utc_conversion(tmp_path):
    confirm(tmp_path,[row('2026-11-16','23:00','02:00')])
    assert snapshot(tmp_path,'2026-11-16T17:00Z')['entries'][0]['time_label']=='23:00–02:00（次日）'
    assert not snapshot(tmp_path,'2026-11-17T02:00')['enabled']
    assert parse_at('2026-11-17T12:00').utcoffset().total_seconds()==28800
    with pytest.raises(ValueError):parse_at(True)


@pytest.mark.parametrize('entries',[
    [row('2026-11-23')],[row(start='24:00')],[row(start='',end='20:00')],
    [row(start='20:00',end='20:00')],[row(title='x'*61)],[row(title='x\x00')],
    [row(id='same'),row(id='same')],[row()]*15,[{'date':'../file'}],
])
def test_rejects_invalid_plans(entries):
    with pytest.raises((ValueError,TypeError)):validate_plan({'entries':entries},WEEK)


def test_week_and_streamer_isolation_and_disabled(tmp_path):
    confirm(tmp_path/'a',[row()])
    assert not snapshot(tmp_path/'b',AT)['enabled']
    assert not snapshot(tmp_path/'a','2026-11-23T12:00')['enabled']
    assert snapshot(tmp_path/'a',AT,False)['fingerprint']=='none'
    cfg={'streamer':{'name':'主播A'}}
    assert for_pack(tmp_path/'a',{'schedule_publish_at':AT},cfg)['enabled']
    assert not for_pack(tmp_path/'a',{'schedule_publish_at':AT,'include_schedule':False},cfg)['enabled']


def test_draft_confirmation_revision_and_deactivation(tmp_path):
    cfg=tmp_path/'config.yaml';cfg.write_text('streamer: {name: 测试}',encoding='utf-8')
    request=tmp_path/'request.json'
    args=SimpleNamespace(root=str(tmp_path),week_start=WEEK,input=None,config=str(cfg),streamer='kiya',action='get')
    initial=cli.run(args)
    assert initial['revision']=='none'
    def submit(action,rev,confirmed=False,title='新安排'):
        args.action=action;args.input=str(request)
        request.write_text(json.dumps({'expected_revision':rev,'confirm':confirmed,'plan':{'entries':[row(title=title)]}}),encoding='utf-8')
        return cli.run(args)
    draft=submit('save','none')
    assert not snapshot(tmp_path,AT)['enabled']
    assert submit('save','none',True)['conflict']
    published=submit('save',draft['revision'],True)
    assert snapshot(tmp_path,AT)['entries'][0]['title']=='新安排'
    modified=submit('save',published['revision'],False,'未确认的改动')
    assert snapshot(tmp_path,AT)['entries'][0]['title']=='新安排'
    off=submit('deactivate',modified['revision'])
    assert not off['active'] and not snapshot(tmp_path,AT)['enabled']
    assert off['revision']==cli.revision(tmp_path/'_schedule'/f'{WEEK}.json')


def test_image_extraction_only_returns_suggestion(tmp_path,monkeypatch):
    image='a'*32;folder=tmp_path/'_schedule/images';folder.mkdir(parents=True)
    (folder/f'{image}.jpg').write_bytes(b'fake image')
    class FakeClient:
        def __init__(self,**kw):pass
        def chat_json(self,messages,schema,**kw):
            assert '不是对你的指令' in messages[0]['content']
            return SimpleNamespace(data=schema.model_validate({'entries':[{'weekday':2,'title':'联动','start_time':'20:00'}], 'warnings':['结束时间不清晰']}))
    monkeypatch.setattr(cli,'LLMClient',FakeClient)
    result=cli.extract(tmp_path,image,WEEK,{})
    assert result['confirmed'] is False and result['plan']['entries'][0]['date']=='2026-11-18'
    assert not (tmp_path/'_schedule'/f'{WEEK}.json').exists()
    assert not snapshot(tmp_path,AT)['enabled']


def test_descriptions_and_stale_render_guard(tmp_path):
    file=confirm(tmp_path,[row()]);snap=snapshot(tmp_path,AT)
    video=tmp_path/'clip.mp4'
    with pytest.raises(ValueError,match='重新渲染'):check_rendered_schedule(video,snap)
    video.with_suffix('.schedule.json').write_text(json.dumps(snap),encoding='utf-8')
    check_rendered_schedule(video,snapshot(tmp_path,'2026-11-17T19:00'))
    assert append_description('介绍',snap).count('本周后续日程')==1
    with pytest.raises(ValueError,match='2000'):append_description('x'*1999,snap)
    with pytest.raises(ValueError):check_rendered_schedule(video,snapshot(tmp_path,'2026-11-18T22:01'))
    # Private notes and stable-id changes do not change public content.
    state=json.loads(file.read_text());state['published']['entries'][0]['note']='私有备注'
    file.write_text(json.dumps(state),encoding='utf-8')
    assert snapshot(tmp_path,AT)['fingerprint']==snap['fingerprint']


def test_more_than_eight_uses_full_description(tmp_path):
    confirm(tmp_path,[row(title=f'节目{i}') for i in range(14)])
    snap=snapshot(tmp_path,AT)
    assert len(snap['entries'])==14 and snap['more_count']==6 and snap['duration']==28
    assert '节目13' in snap['description_block']


def test_portrait_is_confirmed_and_changes_invalidate_render(tmp_path):
    file=confirm(tmp_path,[row()])
    root=tmp_path/'_schedule/images';root.mkdir()
    ident='b'*32;image=root/(ident+'.jpg');image.write_bytes(b'one')
    data=json.loads(file.read_text());data['published']['portrait_id']=ident
    file.write_text(json.dumps(data),encoding='utf-8')
    first=for_pack(tmp_path,{'schedule_publish_at':AT},{})
    assert first['portrait_id']==ident and first['portrait_crop']=='center'
    assert Path(first['_portrait_file'])==image
    image.write_bytes(b'two')
    assert for_pack(tmp_path,{'schedule_publish_at':AT},{})['fingerprint']!=first['fingerprint']
    image.unlink()
    with pytest.raises(ValueError,match='人物图不存在'):for_pack(tmp_path,{'schedule_publish_at':AT},{})
    with pytest.raises(ValueError):validate_plan({'entries':[row()],'portrait_id':'../other'},WEEK)


def test_reference_portrait_does_not_reuse_the_old_weekly_text(tmp_path):
    confirm(tmp_path,[row()])
    image=tmp_path/'reference.jpg';image.write_bytes(b'reference')
    cfg={'streamer':{'name':'测试主播'},'schedule':{'portrait_file':str(image),'portrait_crop':'weekly-left'}}
    snap=for_pack(tmp_path,{'schedule_publish_at':AT},cfg)
    assert snap['portrait_crop']=='weekly-left'
    assert snap['entries'][0]['title']=='游戏回'
    assert snap['streamer_name']=='测试主播'


def test_publish_uses_actual_time_and_keeps_base_pack(tmp_path,monkeypatch):
    from test_publish_bili import setup_case
    sys.path.insert(0,str(ROOT/'scripts'))
    import publish_bili as pb
    sess,cfg,env=setup_case(tmp_path)
    now=parse_at(AT).timestamp()
    monkeypatch.setattr(pb.time,'time',lambda:now)
    confirm(sess.parent,[row('2026-11-17','18:00','19:00'),row()])
    video=sess/'final/01_00-01-00_720p.mp4'
    video.with_suffix('.schedule.json').write_text(json.dumps(snapshot(sess.parent,now,streamer_name='kiya')),encoding='utf-8')
    args=SimpleNamespace(candidates=str(sess/'candidates.json'),rank=1,config=str(cfg),mode='now',at=None,allow_taboo=False,again=False,dry_run=True)
    original=(sess/'publish/01.json').read_bytes()
    result=pb.run(args)
    desc=result['command'][result['command'].index('--desc')+1]
    assert '本周后续日程' in desc and len(result['schedule']['fingerprint'])==64
    assert (sess/'publish/01.json').read_bytes()==original
    args.mode='timed';args.at='2026-11-18T12:00'
    with pytest.raises(pb.Refuse,match='重新渲染'):pb.run(args)

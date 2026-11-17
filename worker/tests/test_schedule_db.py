import json,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from clipper import schedule_db as db
from clipper.schedule import calendar_history,read_state,validate_plan,for_pack
from test_schedule import cli
from types import SimpleNamespace

@pytest.fixture
def storage(tmp_path,monkeypatch):
    monkeypatch.setenv('KIYA_SCHEDULE_STORAGE','sqlite')
    monkeypatch.setenv('KIYA_SCHEDULE_SQLITE',str(tmp_path/'calendar.db'))
    monkeypatch.delenv('KIYA_STREAMER',raising=False)
    return tmp_path

def test_database_authority_import_cas_and_streamer_isolation(storage):
    root=storage/'a';folder=root/'_schedule';folder.mkdir(parents=True)
    file=folder/'2026-11-16.json';original={'active':True,'published':validate_plan({'entries':[{'date':'2026-11-18','title':'原安排'}]},'2026-11-16')}
    file.write_text(json.dumps(original),encoding='utf-8')
    state,rev=db.import_week(root,'2026-11-16')
    file.write_text('{corrupt',encoding='utf-8')
    assert read_state(root,'2026-11-16')==original
    new={**state,'active':False}
    assert db.save(root,'week','2026-11-16',new,rev)
    assert not db.save(root,'week','2026-11-16',original,rev)
    assert read_state(root,'2026-11-16')['active'] is False
    assert read_state(storage/'b','2026-11-16')=={}
    assert len(calendar_history(root)['weeks'])==1

def test_save_without_json_and_publication_reads_database(storage,monkeypatch):
    root=storage/'a';root.mkdir()
    from PIL import Image
    from clipper import schedule_poster
    reference=storage/'character.jpg';Image.new('RGB',(100,100),'pink').save(reference)
    cfg=storage/'cfg.yaml';cfg.write_text('streamer: {name: 测试}\nschedule:\n  portrait_file: '+str(reference).replace('\\','/'),encoding='utf-8')
    def fake(prompt,reference,out,**kw):Image.new('RGB',(1920,1080),'pink').save(out);return {'model':'mock-seedream'}
    monkeypatch.setattr(schedule_poster,'generate',fake)
    request=storage/'request.json';request.write_text(json.dumps({'expected_revision':'none','confirm':True,'plan':{'entries':[{'date':'2026-11-18','title':'数据库安排','start_time':'20:00'}]}}),encoding='utf-8')
    args=SimpleNamespace(root=str(root),week_start='2026-11-16',input=str(request),streamer=None,config=str(cfg),action='save')
    saved=cli.run(args)
    assert saved['active'] and saved['revision']!='none'
    assert saved['poster']['mode']=='seedream' and saved['poster_error'] is None
    assert len(db.list_records(root,'poster'))==1
    assert not (root/'_schedule/2026-11-16.json').exists()
    repeated=json.loads(request.read_text());repeated['expected_revision']=saved['revision']
    request.write_text(json.dumps(repeated),encoding='utf-8')
    again=cli.run(args)
    assert again['poster']['id']==saved['poster']['id']
    assert len(db.list_records(root,'poster'))==1
    snap=for_pack(root,{'schedule_publish_at':'2026-11-17T12:00'},{})
    assert snap['entries'][0]['title']=='数据库安排'
    assert cli.run(args)['conflict']

def test_media_and_poster_migration_is_idempotent(storage):
    root=storage/'a';poster=root/'_schedule/posters/2026-11-16'/('a'*32);poster.mkdir(parents=True)
    metadata={'id':'a'*32,'created_at':1,'plan':{'entries':[]}}
    (poster/'meta.json').write_text(json.dumps(metadata))
    image=root/'_schedule/images';image.mkdir();(image/('b'*32+'.jpg')).write_bytes(b'image')
    db.migrate(root);db.migrate(root)
    assert len(db.list_records(root,'poster'))==1 and len(db.list_records(root,'image'))==1
    (poster/'meta.json').unlink()
    assert db.get(root,'poster','2026-11-16/'+'a'*32)[0]==metadata

def test_database_failure_never_falls_back_to_json(storage,monkeypatch):
    monkeypatch.setenv('KIYA_SCHEDULE_SQLITE',str(storage/'missing/db.sqlite'))
    with pytest.raises(RuntimeError,match='数据库连接失败'):read_state(storage/'a','2026-11-16')


def test_generated_poster_history_is_database_backed(storage,monkeypatch):
    from test_schedule_poster import make_case
    from clipper import schedule_poster as poster
    week,cfg,request,rev,state=make_case(storage,monkeypatch)
    request['expected_revision']=db.import_week(storage,week)[1]
    generated=poster.create(storage,week,cfg,request,rev)
    folder=poster.poster_dir(storage,week)/generated['id']
    (folder/'meta.json').unlink()
    assert poster.history(storage,week)['versions'][0]['id']==generated['id']
    assert db.get(storage,'poster',week+'/'+generated['id'])[0]['plan']['entries']

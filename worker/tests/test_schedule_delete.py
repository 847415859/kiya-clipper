import json,sys
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_schedule import cli
from clipper.schedule import validate_plan,calendar_history,snapshot
from clipper import schedule_db as db

@pytest.mark.parametrize('database',[False,True])
def test_delete_updates_both_versions_and_empty_weeks_do_not_return(tmp_path,monkeypatch,database):
    monkeypatch.delenv('KIYA_STREAMER',raising=False)
    monkeypatch.setenv('KIYA_SCHEDULE_STORAGE','sqlite' if database else 'file')
    monkeypatch.setenv('KIYA_SCHEDULE_SQLITE',str(tmp_path/'calendar.db'))
    root=tmp_path/'a';folder=root/'_schedule';folder.mkdir(parents=True)
    week='2026-11-16'
    plan=validate_plan({'entries':[{'id':'a','date':'2026-11-18','title':'删除这条'},{'id':'b','date':'2026-11-19','title':'保留这条'}]},week)
    file=folder/(week+'.json');file.write_text(json.dumps({'active':True,'draft':plan,'published':plan}),encoding='utf-8')
    cfg=tmp_path/'cfg.yaml';cfg.write_text('streamer: {name: 测试}',encoding='utf-8')
    request=tmp_path/'request.json'
    args=SimpleNamespace(root=str(root),week_start=week,streamer=None,config=str(cfg),input=str(request),action='delete')
    token=cli.revision(file)
    request.write_text(json.dumps({'expected_revision':token,'entry':{'id':'a','date':'2026-11-18'}}),encoding='utf-8')
    result=cli.run(args)
    assert [r['id'] for r in result['draft']['entries']]==['b']
    assert [r['id'] for r in result['published']['entries']]==['b']
    assert result['poster'] is None
    assert cli.run(args)['conflict']
    request.write_text(json.dumps({'expected_revision':result['revision']}),encoding='utf-8')
    deleted=cli.run(args)
    assert not deleted['active'] and deleted['published'] is None
    assert calendar_history(root)['weeks']==[]
    assert not snapshot(root,'2026-11-17T12:00')['enabled']
    if database:
        # Old JSON still exists, but the database tombstone prevents re-import.
        assert file.exists()
        db.migrate(root)
        assert calendar_history(root)['weeks']==[]
        assert db.get(root,'week',week)[0]['deleted']
    assert calendar_history(tmp_path/'b')['weeks']==[]

import argparse
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import publish_bili as publish


def test_actual_publish_entry_refuses_missing_before_any_upload(tmp_path, monkeypatch):
    candidates = tmp_path/'candidates.json'
    candidates.write_text(json.dumps({'candidates':[{'rank':1,'clip_start':0,'clip_end':10}]}),encoding='utf-8')
    folder = tmp_path/'publish'
    folder.mkdir()
    (folder/'01.json').write_text(json.dumps({'title':'engineering fixture','source':'fixture'}),encoding='utf-8')
    media=tmp_path/'fixture.mp4'
    media.write_bytes(b'not uploaded')
    media.with_suffix('.evidence.json').write_text(json.dumps({'status':'missing','media_size':media.stat().st_size,'media_mtime_ns':media.stat().st_mtime_ns}),encoding='utf-8')
    monkeypatch.setattr(publish,'load_cfg',lambda *_:{})
    monkeypatch.setattr(publish,'resolve_config',lambda *_:None)
    monkeypatch.setattr(publish,'bili_cfg',lambda *_:{'height':480})
    monkeypatch.setattr(publish,'find_video',lambda *_:media)
    def no_upload(*a,**k):
        pytest.fail('Confirmed evidence loss must stop before upload')
    monkeypatch.setattr(publish,'run_biliup',no_upload)
    monkeypatch.setattr(publish,'biliup_bin',no_upload)
    args=argparse.Namespace(candidates=str(candidates),config=None,rank=1,mode='now',allow_taboo=False,again=False,confirm_no_existing=False)
    with pytest.raises(publish.Refuse,match='必保证据缺失'):
        publish.run(args)

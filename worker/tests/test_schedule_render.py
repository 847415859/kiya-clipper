"""Exercise the render worker contract, exact outro preview range, and saved render metadata."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from clipper.schedule import snapshot, check_rendered_schedule, validate_plan

pytestmark=pytest.mark.skipif(not shutil.which('ffmpeg'),reason='需要 ffmpeg')


def test_render_and_outro_preview_share_plan_and_correct_frames(tmp_path):
    source=tmp_path/'source.mp4'
    subprocess.run(['ffmpeg','-nostdin','-loglevel','error','-y','-f','lavfi','-i','testsrc2=s=320x180:r=30:d=6','-f','lavfi','-i','sine=d=6','-shortest','-c:v','libx264','-preset','ultrafast','-c:a','aac',str(source)],check=True)
    sess=tmp_path/'output/20261117-1600'
    for name in ('edits','publish'):(sess/name).mkdir(parents=True,exist_ok=True)
    candidates=sess/'candidates.json'
    candidates.write_text(json.dumps({'segments':[{'video':str(source),'offset':0}],
        'candidates':[{'rank':1,'t':1,'clip_start':0,'clip_end':6}]}),encoding='utf-8')
    (sess/'edits/01.json').write_text(json.dumps({'trim':{'start':0,'end':6},'subtitles':{'enabled':False},'items':[]}),encoding='utf-8')
    pack={'title':'测试','description':'基础简介','include_schedule':True,'schedule_publish_at':'2026-11-17T12:00'}
    (sess/'publish/01.json').write_text(json.dumps(pack),encoding='utf-8')
    schedule=sess.parent/'_schedule/2026-11-16.json';schedule.parent.mkdir()
    plan=validate_plan({'entries':[{'date':'2026-11-18','start_time':'20:00','title':'联动'}]},'2026-11-16')
    schedule.write_text(json.dumps({'active':True,'published':plan}),encoding='utf-8')
    cfg=tmp_path/'config.yaml';cfg.write_text('streamer: {name: 测试主播}',encoding='utf-8')
    fake=tmp_path/'fake-render.py'
    fake.write_text("import sys,json,shutil\nfrom pathlib import Path\njob,props,out=sys.argv[1:4]\np=json.loads(Path(props).read_text(encoding='utf-8'))\nassert p['schedule']['entries'][0]['title']=='联动'\nassert p['duration']==13\nif '--frames' in sys.argv: assert sys.argv[-1]=='180:389'\nshutil.copyfile(Path(job)/'source.mp4',out)\n",encoding='utf-8')
    command=[sys.executable,str(fake)]
    import shlex
    env=dict(os.environ,KIYA_RENDER_CMD=subprocess.list2cmdline(command) if os.name=='nt' else shlex.join(command),KIYA_OVERRIDES=str(tmp_path/'none.json'))
    args=[sys.executable,str(ROOT/'scripts/render.py'),str(candidates),'--rank','1','--height','360','--config',str(cfg)]
    rendered=subprocess.run(args,capture_output=True,text=True,env=env)
    assert rendered.returncode==0,rendered.stdout+rendered.stderr
    video=next((sess/'final').glob('*.mp4'))
    record=json.loads(video.with_suffix('.schedule.json').read_text(encoding='utf-8'))
    assert record['outro_duration']==7 and record['callback_duration']==0
    assert record['video_size']==video.stat().st_size
    check_rendered_schedule(video,snapshot(sess.parent,pack['schedule_publish_at'],streamer_name='测试主播'))
    preview=subprocess.run(args+['--schedule-tail'],capture_output=True,text=True,env=env)
    assert preview.returncode==0,preview.stdout+preview.stderr
    range_file=next((sess/'render_previews').glob('*.range.json'))
    assert json.loads(range_file.read_text())=={'start':6,'end':13,'duration':7}
    assert len(list((sess/'final').glob('*.mp4')))==1
    # A changed video cannot reuse an earlier schedule receipt.
    video.write_bytes(b'changed')
    with pytest.raises(ValueError,match='不匹配'):check_rendered_schedule(video,record)

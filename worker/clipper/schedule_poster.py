"""Seedream artwork with deterministic weekly text and immutable, self-contained history."""
import hashlib
import json
import os
import re
import time
import uuid
from datetime import timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps
from .schedule import REPO, WEEKDAYS, parse_day, read_state, validate_plan
from .seedream import generate


def poster_dir(root, week):
    day=parse_day(week)
    if day.weekday()!=0: raise ValueError('周起始日须为周一')
    return root/'_schedule/posters'/week


def history(root, week):
    from . import schedule_db
    folder=poster_dir(root,week)
    versions=[];warnings=[]
    for f in folder.glob('*/meta.json'):
        if not re.fullmatch('[a-f0-9]{32}',f.parent.name): continue
        try:
            data=json.loads(f.read_text(encoding='utf-8'))
            if (f.parent/'poster.png').is_file():
                versions.append(data)
                if schedule_db.enabled():schedule_db.save(root,'poster',week+'/'+f.parent.name,data)
        except (ValueError,OSError): warnings.append(f'{f.parent.name} 历史记录无法读取')
    if schedule_db.enabled():versions=[v for k,v in schedule_db.list_records(root,'poster') if k.startswith(week+'/')]
    return {'versions':sorted(versions,key=lambda x:x['created_at'],reverse=True),'warnings':warnings}


def font_file(cfg):
    override=(cfg.get('schedule') or {}).get('poster_font') or os.environ.get('KIYA_SCHEDULE_FONT')
    if override: return str(override),0
    windows=Path('C:/Windows/Fonts/msyh.ttc')
    if windows.exists(): return str(windows),0
    from .publish import find_font
    return find_font('Noto Sans CJK SC')


def compose(raw, out, plan, name, font):
    file,index=font
    def ft(size): return ImageFont.truetype(file,size,index=index)
    titlefont,bodyfont,small=ft(64),ft(36),ft(28)
    probe=ImageDraw.Draw(Image.new('RGB',(1,1)))
    def wrap(text):
        lines=[];line=''
        for c in text:
            if line and probe.textlength(line+c,font=bodyfont)>940: lines.append(line);line=''
            line+=c
        if line: lines.append(line)
        return lines
    days=[]
    for i in range(7):
        day=(parse_day(plan['week_start'])+timedelta(days=i)).isoformat()
        rows=[(r,wrap(r['title'])) for r in plan['entries'] if r['date']==day]
        height=max(144,70+sum(58+len(lines)*47 for _,lines in rows))
        days.append((day,rows,height))
    height=290+sum(h+12 for _,_,h in days)
    image=Image.new('RGB',(1920,height),'#ded0ea')
    with Image.open(raw) as source:
        art=source.convert('RGB');art=art.crop((0,0,int(art.width*.38),art.height))
        art=ImageOps.contain(art,(660,height-160));image.paste(art,(30,70))
    draw=ImageDraw.Draw(image)
    draw.rounded_rectangle((720,28,1892,height-28),radius=26,fill='#faf7ff')
    draw.text((770,60),name+' · 周计划',font=titlefont,fill='#695181')
    draw.text((773,149),plan['week_start']+' — '+days[-1][0]+'  北京时间',font=small,fill='#9178a6')
    y=215
    for i,(day,rows,h) in enumerate(days):
        draw.rounded_rectangle((754,y,1858,y+h),radius=15,fill='#eee6f5')
        draw.text((780,y+15),f'{day[5:]}  周{WEEKDAYS[i]}',font=small,fill='#8a6da0')
        yy=y+61
        if not rows: draw.text((790,yy),'未填写安排',font=bodyfont,fill='#a79aac')
        for r,lines in rows:
            label={'rest':'休息','cancelled':'已取消','tbd':'待定'}.get(r['status'],'直播 / 活动')
            times=r['start_time'] or ('全天' if r['status']=='rest' else '时间待定')
            if r['end_time']: times+='–'+r['end_time']+('（次日）' if r['end_time']<r['start_time'] else '')
            draw.text((790,yy),times+' · '+label,font=small,fill='#a083b5');yy+=43
            for line in lines: draw.text((790,yy),line,font=bodyfont,fill='#665075');yy+=47
            yy+=15
        y+=h+12
    draw.text((780,height-65),'安排如有调整，以主播最新公告为准',font=small,fill='#9178a6')
    image.save(out,'PNG')
    return image.size


def create(root,week,cfg,request,revision):
    from . import schedule_db
    state_file=root/'_schedule'/f'{week}.json'
    content=state_file.read_bytes() if state_file.exists() else b'{}'
    token=hashlib.sha256(content).hexdigest() if state_file.exists() else 'none'
    if schedule_db.enabled():
        saved,token=schedule_db.import_week(root,week)
        content=json.dumps(saved).encode()
    if request.get('expected_revision')!=token:
        return {'conflict':True,'error':'日程已更新，请重新读取并核对后生成'}
    source=request.get('source','published')
    if source not in ('draft','published'): raise ValueError('请选择已确认版本或已保存草稿')
    state=json.loads(content)
    if not state.get(source): raise ValueError('所选版本尚未保存，请先保存日程')
    plan=validate_plan(state[source],week)
    supplement=request.get('supplement','')
    if not isinstance(supplement,str) or len(supplement)>1500 or '\0' in supplement: raise ValueError('补充要求最多1500字，不能包含空字符')
    name=(cfg.get('streamer') or {}).get('name') or '主播'
    settings=cfg.get('schedule') or {};pc=(cfg.get('publish') or {})
    reference=root/'_schedule/images'/f"{plan['portrait_id']}.jpg" if plan['portrait_id'] else REPO/str(settings.get('portrait_file') or '')
    if not reference.is_file(): raise ValueError('请先上传片尾人物图并保存，作为主播风格参考')
    font=font_file(cfg)
    # Check font availability before issuing a paid generation request.
    ImageFont.truetype(font[0],36,index=font[1])
    ident=uuid.uuid4().hex;folder=poster_dir(root,week)/ident;folder.mkdir(parents=True)
    prompt=('制作主播个人周计划海报底图，参考人物发色、服装、眼睛和气质，保持人物身份特征。'
            '紫色手账、纸张拼贴、柔和精致插画，人物在左侧约35%，右侧约60%留浅色干净区域供程序排版。'
            '不要生成任何文字、数字、日期、Logo或水印；参考图已有的文字和日程不要沿用。用户补充风格要求：'+supplement)
    try:
        with Image.open(reference) as image:
            if not plan['portrait_id'] and settings.get('portrait_crop')=='weekly-left': image=image.crop((110,175,700,725))
            image.convert('RGB').save(folder/'reference.jpg',quality=95)
        meta=generate(prompt,folder/'reference.jpg',folder/'art.jpg',model=pc.get('seedream_model'),endpoint=pc.get('seedream_endpoint'),size=pc.get('seedream_size','1920x1200'))
        width,height=compose(folder/'art.jpg',folder/'poster.png',plan,name,font)
        record={'id':ident,'week_start':week,'created_at':time.time(),'source':source,'supplement':supplement,
                'file':str((folder/'poster.png').relative_to(root)),'art_file':str((folder/'art.jpg').relative_to(root)),
                'prompt':prompt,'model':meta.get('model'),'plan':plan,'streamer_name':name,'width':width,'height':height,
                'plan_hash':hashlib.sha256(json.dumps(plan,ensure_ascii=False,sort_keys=True).encode()).hexdigest()}
        tmp=folder/'meta.tmp';tmp.write_text(json.dumps(record,ensure_ascii=False,indent=1),encoding='utf-8');tmp.replace(folder/'meta.json')
        if schedule_db.enabled():schedule_db.save(root,'poster',week+'/'+ident,record)
        return record
    finally:
        (folder/'reference.jpg').unlink(missing_ok=True)
        # Failed generations never appear as a successful history version.
        if not (folder/'meta.json').exists():
            for file in folder.iterdir(): file.unlink()
            folder.rmdir()

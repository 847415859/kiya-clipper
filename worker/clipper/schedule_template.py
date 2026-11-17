"""Default weekly journal layout; all visible dates and schedule text are data-driven."""
from datetime import timedelta
from PIL import Image,ImageDraw,ImageFont,ImageOps
from .schedule import REPO,parse_day

def compose_template(out,plan,name,font,cfg,root):
    ft=lambda size:ImageFont.truetype(font[0],size,index=font[1])
    body,small,title=ft(34),ft(24),ft(58)
    probe=ImageDraw.Draw(Image.new('RGB',(1,1)))
    def wrap(text):
        lines=[];line=''
        for c in text:
            if line and probe.textlength(line+c,font=body)>825:lines.append(line);line=''
            line+=c
        if line:lines.append(line)
        return lines
    days=[]
    for i in range(7):
        date=(parse_day(plan['week_start'])+timedelta(days=i)).isoformat()
        rows=[]
        for r in plan['entries']:
            if r['date']!=date:continue
            time=r['start_time'] or ('休息' if r['status']=='rest' else '时间待定')
            if r['end_time']:time+='–'+r['end_time']+('（次日）' if r['end_time']<r['start_time'] else '')
            if r['status']=='cancelled':time+=' · 已取消'
            elif r['status']=='tbd' and r['start_time']:time+=' · 待定'
            rows.append((time,wrap(r['title'])))
        h=max(112,16+sum(32+len(lines)*42+10 for _,lines in rows))
        days.append((date,rows,h))
    height=max(1080,190+sum(h for _,_,h in days))
    image=Image.new('RGB',(1920,height),'#ddd5e5');draw=ImageDraw.Draw(image)
    draw.rounded_rectangle((25,24,1895,height-25),18,fill='#74638d')
    draw.rectangle((64,67,1854,height-65),fill='#fbf9fe',outline='#b4a2c9',width=5)
    for x in range(70,1850,55):draw.line((x,72,x,height-72),fill='#eee8f4',width=2)
    for y in range(75,height-70,55):draw.line((70,y,1848,y),fill='#eee8f4',width=2)
    settings=cfg.get('schedule') or {}
    template=REPO/str(settings.get('poster_template') or '')
    if template.is_file() and not plan['portrait_id']:
        with Image.open(template) as im:
            original=im.convert('RGB').resize((1920,1080))
            image.paste(original.crop((0,0,760,1080)),(0,0))
            image.paste(original.crop((0,0,1920,70)),(0,0))
            image.paste(original.crop((0,1015,1920,1080)),(0,height-65))
            image.paste(original.crop((1895,0,1920,1080)).resize((25,height)),(1895,0))
    else:
        draw.rounded_rectangle((65,125,714,960),20,fill='#d2bedf',outline='#a083b5',width=10)
        reference=root/'_schedule/images'/f"{plan['portrait_id']}.jpg" if plan['portrait_id'] else REPO/str(settings.get('portrait_file') or '')
        if reference.is_file():
            with Image.open(reference) as im:image.paste(ImageOps.fit(im.convert('RGB'),(595,645)),(92,155))
        else:draw.text((175,400),'下次直播见',font=title,fill='#735c86')
        draw.rectangle((80,815,704,932),fill='#e8c9f4')
        size=58
        while size>22 and draw.textlength(name,font=ft(size))>590:size-=2
        draw.text((101,840),name,font=ft(size),fill='#735c86')
    # Only the character-side strip is copied. The old schedule and date note are never pasted.
    draw.text((810,82),'本 周 计 划',font=title,fill='#816496')
    draw.text((1390,101),plan['week_start'][5:].replace('-','.')+' — '+days[-1][0][5:].replace('-','.'),font=small,fill='#8d77a1')
    y=175
    for i,(date,rows,h) in enumerate(days):
        draw.polygon([(811,y+17),(949,y+13),(964,y+23),(951,y+31),(963,y+40),(951,y+48),(961,y+62),(808,y+67),(813,y+55),(802,y+45),(815,y+35),(805,y+26)],fill='#d4c6e3')
        draw.text((825,y+18),['MON','TUE','WED','THU','FRI','SAT','SUN'][i],font=ft(30),fill='#705984')
        draw.text((824,y+73),date[5:],font=small,fill='#9a84aa')
        yy=y+10
        if not rows:draw.text((1000,yy+20),'未填写安排',font=body,fill='#a395ad')
        for time,lines in rows:
            draw.text((1000,yy),time,font=small,fill='#a18bb1');yy+=32
            for line in lines:draw.text((1000,yy),line,font=body,fill='#725b83');yy+=42
            yy+=10
        draw.line((1000,y+h-8,1810,y+h-8),fill='#ded2e8',width=2)
        y+=h
    draw.text((810,height-110),'北京时间 · 安排调整以主播最新公告为准',font=ft(21),fill='#a48db4')
    image.save(out,'PNG')
    return image.size

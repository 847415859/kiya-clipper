"""Database authority for calendar records; JSON is imported once as legacy data."""
import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from .env import load_env

DDL='''CREATE TABLE IF NOT EXISTS schedule_record (
 streamer_id VARCHAR(64) NOT NULL, kind VARCHAR(16) NOT NULL,
 record_key VARCHAR(100) NOT NULL, payload LONGTEXT NOT NULL,
 revision VARCHAR(64) NOT NULL, PRIMARY KEY(streamer_id,kind,record_key))'''

def enabled():
    load_env()
    mode=os.environ.get('KIYA_SCHEDULE_STORAGE','').lower()
    if mode not in ('','file','mysql','sqlite'):raise ValueError('KIYA_SCHEDULE_STORAGE 配置无效')
    return mode in ('mysql','sqlite')

def namespace(root):
    name=os.environ.get('KIYA_STREAMER') or ('kiya' if root.name=='output' else root.name)
    return name if re.fullmatch('[a-z][a-z0-9_-]{0,31}',name) else hashlib.sha256(str(root.resolve()).encode()).hexdigest()

def encode(data):return json.dumps(data,ensure_ascii=False,indent=1)
def token(data):return hashlib.sha256(encode(data).encode()).hexdigest()

@contextmanager
def connect():
    driver=os.environ.get('KIYA_SCHEDULE_STORAGE','').lower()
    try:
        if driver=='sqlite':
            db=sqlite3.connect(os.environ['KIYA_SCHEDULE_SQLITE'],timeout=30)
        else:
            import pymysql
            db=pymysql.connect(host=os.environ.get('DB_HOST','127.0.0.1'),port=int(os.environ.get('DB_PORT','3306')),
                user=os.environ.get('DB_USER','kiya'),password=os.environ.get('DB_PASSWORD',''),database=os.environ.get('DB_NAME','kiya_admin'),
                charset='utf8mb4',connect_timeout=10,read_timeout=30,write_timeout=30)
    except Exception as e:
        raise RuntimeError('日历数据库连接失败，请检查连接配置、PyMySQL依赖与权限') from e
    try:
        cursor=db.cursor();cursor.execute(DDL);db.commit()
        yield db,cursor,('?' if driver=='sqlite' else '%s')
        db.commit()
    except Exception as e:
        db.rollback()
        raise RuntimeError('日历数据库操作失败，请检查数据库权限与表结构') from e
    finally:db.close()

def get(root,kind,key):
    with connect() as (_,c,p):
        c.execute(f'SELECT payload,revision FROM schedule_record WHERE streamer_id={p} AND kind={p} AND record_key={p}',(namespace(root),kind,key))
        row=c.fetchone()
        return (json.loads(row[0]),row[1]) if row else (None,'none')

def save(root,kind,key,data,expected='none'):
    value=encode(data);revision=token(data)
    with connect() as (_,c,p):
        if expected=='none':
            sql='INSERT OR IGNORE' if p=='?' else 'INSERT IGNORE'
            c.execute(f'{sql} INTO schedule_record(streamer_id,kind,record_key,payload,revision) VALUES({",".join([p]*5)})',(namespace(root),kind,key,value,revision))
        else:
            c.execute(f'UPDATE schedule_record SET payload={p},revision={p} WHERE streamer_id={p} AND kind={p} AND record_key={p} AND revision={p}',(value,revision,namespace(root),kind,key,expected))
        return c.rowcount==1

def list_records(root,kind):
    with connect() as (_,c,p):
        c.execute(f'SELECT record_key,payload FROM schedule_record WHERE streamer_id={p} AND kind={p} ORDER BY record_key DESC',(namespace(root),kind))
        return [(key,json.loads(value)) for key,value in c.fetchall()]

def import_week(root,week):
    data,revision=get(root,'week',week)
    if data is None:
        file=root/'_schedule'/f'{week}.json'
        if file.exists():
            data=json.loads(file.read_text(encoding='utf-8'))
            if not isinstance(data,dict):raise ValueError('日程文件格式错误')
            save(root,'week',week,data)
            return get(root,'week',week)
    return data or {},revision

def migrate(root):
    warnings=[]
    known={kind:{k for k,_ in list_records(root,kind)} for kind in ('week','poster','image')}
    records=[('week',f.stem,f) for f in (root/'_schedule').glob('????-??-??.json')]
    records += [('poster',f.parent.parent.name+'/'+f.parent.name,f) for f in (root/'_schedule/posters').glob('????-??-??/*/meta.json') if re.fullmatch('[a-f0-9]{32}',f.parent.name)]
    for kind,key,file in records:
        if key in known[kind]:continue
        try:
            data=json.loads(file.read_text(encoding='utf-8'))
            if not isinstance(data,dict):raise ValueError('格式错误')
            save(root,kind,key,data)
        except (OSError,ValueError):warnings.append(f'{key} 旧记录无法导入，请核对原文件')
    for image in (root/'_schedule/images').glob('*.jpg'):
        if re.fullmatch('[a-f0-9]{32}',image.stem) and image.stem not in known['image']:
            save(root,'image',image.stem,{'image_id':image.stem,'file':str(image.relative_to(root))})
    return warnings

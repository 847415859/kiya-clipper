#!/usr/bin/env python3
"""标注工具：在浏览器里逐个播放候选片段，标“有趣 / 不有趣”，自动保存到 eval/reviews/<场次>.csv。

在服务器上运行（只监听本机，用 SSH 隧道访问，不对公网开放）：
  cd /opt/kiya-clipper/worker && source .venv/bin/activate
  python scripts/review.py
然后在你的电脑上：
  ssh -N -o ServerAliveInterval=30 -L 8765:127.0.0.1:8765 root@<服务器IP>
浏览器打开 http://localhost:8765

快捷键：Y 有趣 · N 不有趣 · ←/→ 上一个/下一个 · 空格 播放/暂停 · J/L 后退/前进 5 秒 · E 编辑备注
标注即时保存；片段还没剪的，页面上点“剪切这个片段”即可（服务器上调用 cut.py）。
3M 带宽下一个 1 分钟 720p 片段约需 10–20 秒加载；想更快可以先用 cut.py --height 480 剪。
"""
from __future__ import annotations

import argparse
import csv
import json
import mimetypes
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))

from clipper.clipwin import read_windows  # noqa: E402
MARK_COL, NOTE_COL = "是否有趣(Y/N)", "备注"
SESSION_RE = re.compile(r"^[\w\-]+$")
LOCK = threading.Lock()


class Store:
    def __init__(self, out_dir: Path, reviews_dir: Path):
        self.out = out_dir
        self.reviews = reviews_dir

    def sessions(self) -> list[dict]:
        out = []
        for d in sorted(self.out.iterdir(), reverse=True) if self.out.exists() else []:
            if (d / "candidates.json").exists() and SESSION_RE.match(d.name):
                rows = self.rows(d.name)
                done = sum(1 for r in rows if r.get(MARK_COL, "").strip().upper() in ("Y", "N"))
                yes = sum(1 for r in rows if r.get(MARK_COL, "").strip().upper() == "Y")
                out.append({"name": d.name, "total": len(rows), "done": done, "yes": yes})
        return out

    def _csv_path(self, session: str) -> Path:
        return self.reviews / f"{session}.csv"

    def rows(self, session: str) -> list[dict]:
        src = self._csv_path(session)
        if not src.exists():
            src = self.out / session / "peaks.csv"
        if not src.exists():
            return []
        with open(src, encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))

    def candidates(self, session: str) -> dict:
        data = json.loads((self.out / session / "candidates.json").read_text(encoding="utf-8"))
        marks = {r["序号"]: r for r in self.rows(session)}
        clips_dir = self.out / session / "clips"
        wins = read_windows(self.out / session)
        for c in data["candidates"]:
            w = wins.get(f"{c['rank']:02d}") or {}
            c["cut_start"] = w.get("start", c["clip_start"])         # 审核片段第 0 秒对应的整场时间（按精排剪时和 clip_start 不同）
            c["cut_end"] = w.get("end", c["clip_end"])
            r = marks.get(str(c["rank"]), {})
            c["mark"] = r.get(MARK_COL, "").strip().upper()
            c["note"] = r.get(NOTE_COL, "")
            mp4 = sorted(clips_dir.glob(f"{c['rank']:02d}_*.mp4")) if clips_dir.exists() else []
            flv = sorted(clips_dir.glob(f"{c['rank']:02d}_*.flv")) if clips_dir.exists() else []
            c["clip"] = mp4[0].name if mp4 else None
            c["flv_only"] = bool(flv) and not mp4
        return {"session": session, "delay": data.get("delay_seconds"), "signals": data.get("signals_used"),
                "transcript": data.get("transcript"), "candidates": data["candidates"]}

    def save_mark(self, session: str, rank: int, mark: str | None, note: str | None) -> None:
        with LOCK:
            rows = self.rows(session)
            if not rows:
                raise ValueError("没有 peaks.csv")
            fields = list(rows[0].keys())
            for col in (MARK_COL, NOTE_COL):
                if col not in fields:
                    fields.append(col)
            for r in rows:
                if r.get("序号") == str(rank):
                    if mark is not None:
                        r[MARK_COL] = mark
                    if note is not None:
                        r[NOTE_COL] = note
            self.reviews.mkdir(parents=True, exist_ok=True)
            dst = self._csv_path(session)
            tmp = dst.with_suffix(".tmp")
            with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=fields)
                w.writeheader()
                w.writerows(rows)
            tmp.replace(dst)


def make_handler(store: Store):
    class H(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _json(self, obj, code=200):
            b = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def _session(self, s: str) -> str:
            if not SESSION_RE.match(s) or not (store.out / s / "candidates.json").exists():
                raise ValueError("场次不存在")
            return s

        def do_GET(self):
            u = urlparse(self.path)
            try:
                if u.path == "/":
                    b = PAGE.encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(b)))
                    self.end_headers()
                    self.wfile.write(b)
                elif u.path == "/api/sessions":
                    self._json(store.sessions())
                elif u.path == "/api/candidates":
                    self._json(store.candidates(self._session(parse_qs(u.query)["session"][0])))
                elif u.path.startswith("/clip/"):
                    _, _, sess, name = u.path.split("/", 3)
                    sess, name = self._session(unquote(sess)), unquote(name)
                    if "/" in name or "\\" in name or not name.endswith(".mp4"):
                        raise ValueError("文件名不合法")
                    self._file(store.out / sess / "clips" / name)
                elif u.path.startswith("/curve/"):
                    sess = self._session(unquote(u.path.split("/", 2)[2]))
                    self._file(store.out / sess / "curve.png")
                else:
                    self._json({"error": "not found"}, 404)
            except (ValueError, KeyError, IndexError) as e:
                self._json({"error": str(e)}, 400)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _file(self, path: Path):
            if not path.exists():
                self._json({"error": "文件不存在"}, 404)
                return
            size = path.stat().st_size
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            rng = self.headers.get("Range")
            start, end = 0, size - 1
            if rng:
                m = re.match(r"bytes=(\d*)-(\d*)", rng)
                if m:
                    if m.group(1):
                        start = int(m.group(1))
                        end = int(m.group(2)) if m.group(2) else size - 1
                    elif m.group(2):
                        start, end = max(0, size - int(m.group(2))), size - 1
                end = min(end, size - 1)
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            else:
                self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            self.end_headers()
            with open(path, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(256 * 1024, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)

        def do_POST(self):
            u = urlparse(self.path)
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                sess = self._session(body["session"])
                rank = int(body["rank"])
                if u.path == "/api/mark":
                    mark = body.get("mark")
                    if mark not in (None, "", "Y", "N"):
                        raise ValueError("mark 只能是 Y / N / 空")
                    note = body.get("note")
                    if note is not None:
                        note = str(note).replace("\n", " ")[:300]
                    store.save_mark(sess, rank, mark, note)
                    self._json({"ok": True})
                elif u.path == "/api/cut":
                    cmd = [sys.executable, str(ROOT / "scripts" / "cut.py"),
                           str(store.out / sess / "candidates.json"), "--ranks", str(rank),
                           "--height", str(int(body.get("height", 720)))]
                    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
                    self._json({"ok": r.returncode == 0, "log": (r.stdout + r.stderr)[-500:]})
                else:
                    self._json({"error": "not found"}, 404)
            except (ValueError, KeyError, json.JSONDecodeError) as e:
                self._json({"error": str(e)}, 400)

    return H


PAGE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>kiya 切片标注</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#1f2328;--muted:#6a737d;--line:#e3e6ea;--accent:#3b6ea5;--yes:#2f8f4e;--no:#b54747;--warn:#b7791f}
@media (prefers-color-scheme:dark){:root{--bg:#16181c;--card:#1e2126;--ink:#e6e8eb;--muted:#9aa3ad;--line:#2d3138;--accent:#7aa7d9;--yes:#5cbf7a;--no:#e07a7a;--warn:#e0b45c}}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--ink)}
header{display:flex;gap:12px;align-items:center;padding:10px 16px;border-bottom:1px solid var(--line);background:var(--card);flex-wrap:wrap}
header h1{font-size:16px;margin:0}select,button,input{font:inherit;color:inherit}
select,input[type=text]{background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:5px 8px}
button{background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:6px 12px;cursor:pointer}
button:hover{border-color:var(--accent)}button.yes{border-color:var(--yes);color:var(--yes)}button.no{border-color:var(--no);color:var(--no)}
button.on.yes{background:var(--yes);color:#fff}button.on.no{background:var(--no);color:#fff}
.stat{color:var(--muted)}main{display:grid;grid-template-columns:260px 1fr;gap:0;height:calc(100vh - 53px)}
@media (max-width:800px){main{grid-template-columns:1fr;height:auto}aside{max-height:220px}}
aside{border-right:1px solid var(--line);overflow:auto;background:var(--card)}
.item{display:flex;gap:8px;align-items:center;padding:8px 12px;border-bottom:1px solid var(--line);cursor:pointer}
.item.cur{background:color-mix(in srgb,var(--accent) 14%,transparent)}.item .r{width:26px;color:var(--muted)}
.badge{margin-left:auto;font-size:12px;padding:1px 7px;border-radius:10px;border:1px solid var(--line);color:var(--muted)}
.badge.Y{border-color:var(--yes);color:var(--yes)}.badge.N{border-color:var(--no);color:var(--no)}
section{padding:16px;overflow:auto}video{width:100%;max-height:58vh;background:#000;border-radius:8px}
.meta{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;margin:12px 0;color:var(--muted)}.meta b{color:var(--ink);font-weight:500}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:10px 0}.row input[type=text]{flex:1;min-width:200px}
.empty{padding:40px;border:1px dashed var(--line);border-radius:8px;text-align:center;color:var(--muted)}
.keys{color:var(--muted);font-size:12px}.saved{color:var(--yes);font-size:12px;min-width:48px}
.speech{margin:10px 0;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card);max-height:220px;overflow:auto}
.sl{padding:2px 4px;border-radius:4px;cursor:pointer}.sl:hover{background:color-mix(in srgb,var(--accent) 12%,transparent)}.st{color:var(--muted);font-size:12px;margin-right:8px;font-variant-numeric:tabular-nums}
details img{max-width:100%;border-radius:6px;margin-top:8px;background:#fff}
</style></head><body>
<header><h1>kiya 切片标注</h1><select id="sess"></select><span class="stat" id="stat"></span>
<span class="keys">Y 有趣 · N 不有趣 · ←/→ 切换 · 空格 播放 · J/L ±5秒 · E 备注</span></header>
<main><aside id="list"></aside><section id="view"><div class="empty">加载中…</div></section></main>
<script>
let S=null,data=null,cur=0;const $=s=>document.querySelector(s);
const fmt=t=>{t=Math.max(0,Math.round(t));return [t/3600|0,(t%3600)/60|0,t%60].map(x=>String(x).padStart(2,'0')).join(':')};
async function api(p,b){const r=await fetch(p,b?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)}:{});return r.json()}
async function loadSessions(){const ss=await api('/api/sessions');const sel=$('#sess');sel.innerHTML='';
 if(!ss.length){$('#view').innerHTML='<div class="empty">还没有粗筛结果：先运行 python scripts/screen.py /data/rec --latest</div>';return}
 for(const s of ss){const o=document.createElement('option');o.value=s.name;o.textContent=`${s.name}（${s.done}/${s.total}）`;sel.appendChild(o)}
 sel.onchange=()=>openSession(sel.value);openSession(ss[0].name)}
async function openSession(name){S=name;data=await api('/api/candidates?session='+encodeURIComponent(name));
 const first=data.candidates.findIndex(c=>!c.mark);cur=first<0?0:first;renderList();show()}
function stats(){const c=data.candidates,d=c.filter(x=>x.mark).length,y=c.filter(x=>x.mark==='Y').length;
 $('#stat').textContent=`已标 ${d}/${c.length}，有趣 ${y}${d?`（${Math.round(y/d*100)}%）`:''}`;
 const o=[...$('#sess').options].find(o=>o.value===S);if(o)o.textContent=`${S}（${d}/${c.length}）`}
function renderList(){const L=$('#list');L.innerHTML='';data.candidates.forEach((c,i)=>{const d=document.createElement('div');
 d.className='item'+(i===cur?' cur':'');d.innerHTML=`<span class="r">#${c.rank}</span><span>${fmt(c.t)}</span><span class="badge ${c.mark}">${c.mark==='Y'?'有趣':c.mark==='N'?'不有趣':(c.clip?'未标':'未剪')}</span>`;
 d.onclick=()=>{cur=i;renderList();show()};L.appendChild(d)});stats();const el=L.children[cur];if(el)el.scrollIntoView({block:'nearest'})}
function esc(x){return String(x).replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]))}
function speechHtml(c){if(!c.speech)return '';if(!c.speech.length)return '<div class="speech"><div class="keys">主播原话：这段没有识别到说话</div></div>';
 return '<div class="speech"><div class="keys">主播原话（点一句跳到那里；语音识别可能有错字）</div>'+c.speech.map(x=>{const r=Math.max(0,x.t0-c.cut_start);
 return `<div class="sl" data-t="${r}"><span class="st">${Math.floor(r/60)}:${String(Math.floor(r%60)).padStart(2,'0')}</span>${esc(x.text)}</div>`}).join('')+'</div>'}
function show(){const c=data.candidates[cur];if(!c){$('#view').innerHTML='<div class="empty">这场没有达到分数线的片段，不用审核。</div>';return}const top=Object.entries(c.contrib||{}).sort((a,b)=>b[1]-a[1]).slice(0,3).map(([k,v])=>`${k} ${v>0?'+':''}${v}`).join('，');
 const video=c.clip?`<video id="v" controls autoplay preload="auto" src="/clip/${encodeURIComponent(S)}/${encodeURIComponent(c.clip)}"></video>`
 :`<div class="empty">${c.flv_only?'这个片段是用 --fast 剪的 FLV，浏览器播不了，需要重新剪成 MP4。<br><br>':'这个片段还没剪。<br><br>'}<button id="cutbtn">剪切这个片段（720p）</button> <button id="cutbtn480">剪成 480p（加载更快）</button><div id="cutlog" class="keys"></div></div>`;
 $('#view').innerHTML=`${video}
 <div class="row"><button class="yes ${c.mark==='Y'?'on':''}" id="by">Y 有趣</button><button class="no ${c.mark==='N'?'on':''}" id="bn">N 不有趣</button>
 <button id="bc">清除</button><button id="bp">← 上一个</button><button id="bx">下一个 →</button><span class="saved" id="saved"></span></div>
 <div class="row"><input type="text" id="note" placeholder="备注：发生了什么 / 为什么不行（例如：舰长互动，笑点在 0:35；边界太早）" value="${esc(c.note||'')}"></div>
 <div class="meta"><span>序号</span><b>#${c.rank} / ${data.candidates.length}</b><span>高峰时间</span><b>${fmt(c.t)}（片段 ${fmt(c.cut_start)}–${fmt(c.cut_end)}）</b>
 <span>分数</span><b>${c.score.toFixed(2)}</b><span>主要信号</span><b>${top||'—'}</b><span>弹幕</span><b>${Math.round(c.danmaku_per_min)} 条/分，情绪词 ${c.emotion_hits}，礼物 ${Math.round(c.gift_value)}</b>
 <span>高频弹幕</span><b>${esc((c.top_danmaku||[]).join(' | '))||'—'}</b><span>本场</span><b>信号 ${data.signals}；弹幕延迟 ${data.delay} 秒</b></div>
 ${speechHtml(c)}<details><summary>本场信号曲线</summary><img src="/curve/${encodeURIComponent(S)}" alt="信号曲线"></details>`;
 $('#by').onclick=()=>mark('Y');$('#bn').onclick=()=>mark('N');$('#bc').onclick=()=>mark('');$('#bp').onclick=()=>go(-1);$('#bx').onclick=()=>go(1);
 document.querySelectorAll('.sl').forEach(el=>el.onclick=()=>{const v=$('#v');if(v){v.currentTime=+el.dataset.t;v.play()}});
 const n=$('#note');n.onchange=()=>saveNote(n.value);n.onkeydown=e=>{if(e.key==='Enter'){n.blur()}e.stopPropagation()};
 if($('#cutbtn')){$('#cutbtn').onclick=()=>cut(720);$('#cutbtn480').onclick=()=>cut(480)}}
async function mark(m){const c=data.candidates[cur];const r=await api('/api/mark',{session:S,rank:c.rank,mark:m});if(!r.ok){alertMsg(r.error);return}
 c.mark=m;flash();renderList();if(m)setTimeout(()=>go(1),250);else show()}
async function saveNote(v){const c=data.candidates[cur];const r=await api('/api/mark',{session:S,rank:c.rank,note:v});if(r.ok){c.note=v;flash()}}
async function cut(h){$('#cutlog').textContent=`正在剪切（${h}p），1 分钟片段在 2 核服务器上约需 30–60 秒…`;
 const c=data.candidates[cur];const r=await api('/api/cut',{session:S,rank:c.rank,height:h});
 if(r.ok){data=await api('/api/candidates?session='+encodeURIComponent(S));renderList();show()}else{$('#cutlog').textContent='剪切失败：'+(r.log||r.error)}}
function flash(){const s=$('#saved');if(s){s.textContent='已保存';setTimeout(()=>{if(s)s.textContent=''},1200)}}
function alertMsg(t){const s=$('#saved');if(s){s.style.color='var(--no)';s.textContent=t}}
function go(d){const n=cur+d;if(n<0||n>=data.candidates.length)return;cur=n;renderList();show()}
document.addEventListener('keydown',e=>{if(!data||e.target.tagName==='INPUT'||e.target.tagName==='SELECT')return;const v=$('#v');
 const k=e.key.toLowerCase();if(k==='y')mark('Y');else if(k==='n')mark('N');else if(e.key==='ArrowRight')go(1);else if(e.key==='ArrowLeft')go(-1);
 else if(k===' '&&v){e.preventDefault();v.paused?v.play():v.pause()}else if(k==='j'&&v)v.currentTime-=5;else if(k==='l'&&v)v.currentTime+=5;
 else if(k==='e'){e.preventDefault();$('#note')&&$('#note').focus()}});
loadSessions();
</script></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "output"), help="screen.py 的输出目录")
    ap.add_argument("--reviews", default=str(REPO / "eval" / "reviews"), help="标注保存目录")
    ap.add_argument("--host", default="127.0.0.1", help="默认只监听本机，用 SSH 隧道访问")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    store = Store(Path(args.out), Path(args.reviews))
    srv = ThreadingHTTPServer((args.host, args.port), make_handler(store))
    print(f"标注工具已启动：http://{args.host}:{args.port}  （Ctrl+C 退出）")
    print(f"在你的电脑上执行：ssh -N -o ServerAliveInterval=30 -L {args.port}:127.0.0.1:{args.port} root@<服务器IP>")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

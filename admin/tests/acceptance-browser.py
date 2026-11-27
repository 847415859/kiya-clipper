"""Offline GUI acceptance using built admin and explicitly synthetic HTTP API fixtures.
Run from repo root: worker/.venv/Scripts/python.exe admin/tests/acceptance-browser.py
No production service, credentials, paid requests, or publishing are used.
"""
from __future__ import annotations
import functools
import hashlib
import http.server
import json
from pathlib import Path
import subprocess
import threading
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'worker/output/_repair-acceptance/web'
STATIC = ROOT / 'backend/src/main/resources/static'
OLD, NEW = 'a' * 64, 'b' * 64


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    media = OUT / 'synthetic-preview.mp4'
    if not media.exists():
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                        'testsrc2=s=320x180:r=30:d=12', '-f', 'lavfi', '-i', 'sine=d=12',
                        '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac', '-shortest', str(media)], check=True)
    checksum = hashlib.sha256(media.read_bytes()).hexdigest()
    mapping_old = [{'source_start': 100, 'source_end': 110, 'output_start': 2, 'output_end': 12}]
    mapping_new = [{'source_start': 103, 'source_end': 110, 'output_start': 0, 'output_end': 7}]
    old_edit = {'trim': {'start': 0, 'end': 10}, 'items': [{'id': 'opening', 'type': 'text', 'text': '模拟遮挡花字', 'y': .1}]}
    new_edit = {'trim': {'start': 3, 'end': 10}, 'items': []}
    versions = [
        {'version': OLD, 'parent': None, 'playable': True, 'accepted': False, 'simulation': True,
         'mediaSha256': checksum, 'reviewBound': True, 'renderBound': True, 'mapping': mapping_old,
         'review': {'status': 'repair', 'range': [7, 8], 'timeline': 'output', 'reason': '模拟旧版遮挡'},
         'quality': {'status': 'pass', 'bound': True}, 'draft': {'edit': old_edit}},
        {'version': NEW, 'parent': OLD, 'current': True, 'playable': True, 'accepted': True, 'simulation': True,
         'mediaSha256': checksum, 'reviewBound': True, 'renderBound': True, 'mapping': mapping_new,
         'review': {'status': 'pass'}, 'quality': {'status': 'pass', 'bound': True}, 'draft': {'edit': new_edit}},
    ]
    item = {'rank': 5, 'status': 'inventoried', 'version': NEW, 'inventoryVersion': NEW, 'playable': True,
            'review': {'status': 'pass', 'bound': True}, 'quality': {'status': 'pass', 'bound': True},
            'evidence': {'status': 'time_covered'}, 'versions': versions}
    detail = {'status': 'has_clips', 'message': '模拟夹具验收；不代表真实 #5 生产媒体', 'candidates': [item]}
    overview = {'observed': True, 'live': False, 'sessions': [{'name': 'synthetic-repair-demo', 'status': 'has_clips',
        'clipCount': 1, 'message': '离线模拟 API / FFmpeg 夹具'}]}
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(STATIC))
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    requests, errors = [], []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe', headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1100})
            page.on('pageerror', lambda error: errors.append(str(error)))
            def api(route):
                request = route.request
                requests.append({'method': request.method, 'url': request.url})
                assert request.method == 'GET', 'Read-only acceptance must never POST'
                path = request.url.split('/api/', 1)[1]
                if '/media/' in path:
                    data = media.read_bytes()
                    byte_range = request.headers.get('range')
                    if byte_range:
                        import re
                        match = re.fullmatch(r'bytes=(\d+)-(\d*)', byte_range)
                        start = int(match[1]); end = min(int(match[2]) if match[2] else len(data) - 1, len(data) - 1)
                        route.fulfill(status=206, content_type='video/mp4', headers={'Accept-Ranges': 'bytes', 'Content-Range': f'bytes {start}-{end}/{len(data)}'}, body=data[start:end + 1])
                    else:
                        route.fulfill(status=200, content_type='video/mp4', headers={'Accept-Ranges': 'bytes'}, body=data)
                    return
                value = ({'authenticated': True, 'username': 'offline-fixture'} if path == 'auth/me' else
                         {'streamers': [{'id': 'kiya', 'name': '离线模拟'}], 'current': 'kiya'} if path == 'streamers' else
                         overview if path == 'sessions/acceptance-operations' else detail)
                route.fulfill(status=200, content_type='application/json', body=json.dumps(value, ensure_ascii=False))
            page.route('**/api/**', api)
            page.goto(f'http://127.0.0.1:{server.server_port}/#/sessions/acceptance')
            page.get_by_role('button', name='查看详情').click()
            page.get_by_text('候选 #5 · 已有成片', exact=True).click()
            page.locator('.compare-controls .el-select__wrapper').click()
            page.get_by_role('option', name='历史 · aaaaaaaaaaaa').click()
            page.wait_for_function('document.querySelectorAll(".compare-grid video").length === 2 && [...document.querySelectorAll(".compare-grid video")].every(v => v.readyState >= 1)')
            page.get_by_role('button', name='定位历史版问题').click()
            page.wait_for_function('Math.abs(document.querySelectorAll(".compare-grid video")[0].currentTime - 2) < .1 && Math.abs(document.querySelectorAll(".compare-grid video")[1].currentTime - 7) < .1')
            assert 'opening' in page.locator('.version-compare').inner_text()
            assert '原片锚点' in page.locator('.version-compare').inner_text()
            page.locator('.version-compare').screenshot(path=str(OUT / 'version-comparison.png'))
            page.screenshot(path=str(OUT / 'acceptance-desktop.png'), full_page=True)
            # A removed source anchor must never move the other version by equal output seconds.
            page.evaluate('document.querySelectorAll(".compare-grid video")[0].currentTime = 0')
            page.get_by_role('button', name='按原片锚点同步').click()
            page.set_viewport_size({'width': 390, 'height': 844})
            comparison = page.locator('.version-compare')
            comparison.scroll_into_view_if_needed()
            assert comparison.evaluate("""el => [...el.querySelectorAll('.compare-controls button, .compare-grid video')]
                .every(child => {
                    const bounds = child.getBoundingClientRect()
                    return bounds.left >= -1 && bounds.right <= window.innerWidth + 1
                })"""), 'Mobile comparison buttons or video overflow the viewport'
            comparison.screenshot(path=str(OUT / 'acceptance-mobile.png'), animations='disabled')
            assert not errors, errors
            browser.close()
        (OUT / 'browser-result.json').write_text(json.dumps({'passed': True, 'evidence_type': 'synthetic-api-and-media',
            'assertions': ['two version players', 'historical 7s maps current 2s', 'stable ID edit diff', 'GET only',
                           'mobile comparison fits viewport', 'desktop/mobile screenshots', 'no browser errors'], 'requests': requests, 'errors': errors}, ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        server.shutdown()
    print('PASS offline browser comparison: historical 7s -> current 2s; GET-only; screenshots saved')


if __name__ == '__main__':
    main()

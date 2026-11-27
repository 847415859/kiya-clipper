#!/usr/bin/env python3
"""Linux ext4 acceptance: real root Spring HTTP -> kiya Worker -> root media playback.
Requires --runtime with backend/{classes,test-classes,lib}, worker,renderer,config.
Run as root on a disposable test host; never point this at a production directory.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import grp
import shutil
import socket
import stat
import subprocess
import time
from playwright.sync_api import sync_playwright


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding='utf-8')


def snapshot(root):
    return {str(p.relative_to(root)): [hashlib.sha256(p.read_bytes()).hexdigest(),
        stat.S_IMODE(p.stat().st_mode), p.stat().st_uid, p.stat().st_gid]
        for p in root.rglob('*') if p.is_file()}


def metadata(path):
    s = path.stat()
    return {'path': str(path), 'owner': pwd.getpwuid(s.st_uid).pw_name,
            'group': grp.getgrgid(s.st_gid).gr_name, 'mode': oct(stat.S_IMODE(s.st_mode))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--prepare-script', type=Path, required=True)
    args = parser.parse_args()
    assert os.geteuid() == 0, 'backend process must really run as root'
    kiya = pwd.getpwnam('kiya')
    runtime, out = args.runtime.resolve(), args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    out.chmod(0o755)
    repo, rec, acceptance, production = [out / p for p in ('repo', 'recordings', 'acceptance', 'production')]
    rec.mkdir(); acceptance.mkdir(); production.mkdir()
    # Existing production remains deliberately root-only and is never repaired.
    production.chmod(0o700)
    save(production / 'sentinel.json', {'production': 'read-only acceptance sentinel'})
    (production / 'sentinel.json').chmod(0o600)
    video = rec / 'source.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i',
        'testsrc2=s=1280x720:r=30:d=7', '-f', 'lavfi', '-i', 'sine=d=7', '-c:v',
        'libx264', '-preset', 'ultrafast', '-c:a', 'aac', '-shortest', str(video)], check=True)
    source = repo / 'worker/output/kiya/history-linux'
    save(source / 'candidates.json', {'streamer': 'kiya', 'session': 'history-linux',
        'segments': [{'video': str(video), 'offset': 0, 'duration': 7}],
        'candidates': [{'rank': 1, 't': 1.5, 'clip_start': 0, 'clip_end': 6, 'file_index': 0, 'local_start': 0}]})
    history = acceptance / 'history'
    # Reproduce legacy root-owned 0700 and 0600 leftovers, then exercise migration.
    (history / 'status').mkdir(parents=True)
    history.chmod(0o700)
    legacy = history / 'status/legacy.json'
    save(legacy, {'legacy': True}); legacy.chmod(0o600)
    # Do not change parent traversal/production/source permissions with the repair.
    original = {'source': snapshot(repo), 'rec': snapshot(rec), 'production': snapshot(production),
                'acceptance': metadata(acceptance), 'productionDir': metadata(production)}
    subprocess.run(['python3', str(args.prepare_script), '--root', str(history)], check=True)
    assert metadata(history)['owner'] == 'kiya' and metadata(legacy)['mode'] == '0o640'
    migrated = [metadata(history), metadata(legacy)]
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    cp = ':'.join(str(runtime / 'backend' / part) for part in ('test-classes', 'classes', 'lib/*'))
    command = ['java', '-cp', cp, 'com.kiya.admin.HistoricalTrialFixtureServer',
        '--server.address=127.0.0.1', f'--server.port={port}',
        '--server.servlet.session.cookie.secure=false',
        '--spring.datasource.url=jdbc:h2:mem:history;MODE=MySQL;DATABASE_TO_LOWER=TRUE;DB_CLOSE_DELAY=-1',
        '--spring.datasource.driver-class-name=org.h2.Driver', '--spring.datasource.username=sa',
        '--spring.datasource.password=', f'--kiya.repo-dir={repo}', f'--kiya.rec-dir={rec}',
        f'--kiya.ops-dir={production}', f'--kiya.acceptance-dir={acceptance}',
        '--kiya.admin-password=local-linux-fixture-123', '--kiya.historical-trial-fixture-enabled=true',
        '--kiya.historical-trial-group=kiya']
    log = (out / 'spring.log').open('w')
    # Strong umask must not break explicit queue permissions before atomic rename.
    server = subprocess.Popen(command, cwd=runtime, stdout=log, stderr=subprocess.STDOUT, umask=0o077)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
            context = browser.new_context()
            deadline = time.monotonic() + 120
            while True:
                try:
                    if context.request.get(url + '/api/auth/csrf', timeout=2000).ok: break
                except Exception:
                    pass
                if server.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('Spring startup failed: ' + (out / 'spring.log').read_text()[-5000:])
                time.sleep(.3)
            assert Path(f'/proc/{server.pid}/status').read_text().split('Uid:')[1].splitlines()[0].split()[0] == '0'
            csrf = context.request.get(url + '/api/auth/csrf').json()
            login = context.request.post(url + '/api/auth/login', form={'username': 'admin',
                'password': 'local-linux-fixture-123'}, headers={csrf['headerName']: csrf['token']})
            assert login.ok, login.text()
            csrf = context.request.get(url + '/api/auth/csrf').json()
            endpoint = url + '/api/sessions/history-linux/isolated-trials'
            response = context.request.post(endpoint, data={'rank': 1, 'confirmation': True},
                headers={csrf['headerName']: csrf['token']})
            assert response.ok, response.text()
            task = response.json()['taskId']
            queue = history / 'queue' / f'{task}.json'
            status = history / 'status' / f'{task}.json'
            submitted = [metadata(queue), metadata(status)]
            for item in submitted:
                assert item['owner'] == 'root' and item['group'] == 'kiya' and item['mode'] == '0o640', item
            directories = [metadata(history / name) for name in ('queue', 'status', 'production', 'locks', 'ledger')]
            assert all(d['group'] == 'kiya' and d['mode'] == '0o2770' for d in directories), directories
            subprocess.run(['runuser', '-u', 'kiya', '--', 'test', '-r', str(queue)], check=True)
            # Worker service has UMask=0077. Match that rather than a permissive test shell.
            worker_log = (out / 'worker.log').open('w')
            worker_command = ['runuser', '-u', 'kiya', '--', 'env', 'PYTHONDONTWRITEBYTECODE=1',
                'KIYA_ENV_FILE=/dev/null', 'KIYA_RENDER_CMD=', 'PATH=/usr/local/bin:/usr/bin:/bin',
                'REMOTION_BROWSER=/usr/bin/chromium', 'REMOTION_CONCURRENCY=4',
                'HOME=/var/lib/kiya-trial-test', str(runtime / 'venv/bin/python'),
                str(runtime / 'worker/scripts/historical_trial_worker.py'), '--root', str(history), '--once', '--fixture']
            try:
                subprocess.run(worker_command, cwd=runtime / 'worker', stdout=worker_log,
                    stderr=subprocess.STDOUT, check=True, timeout=900, umask=0o077)
            finally:
                worker_log.close()
            result = json.loads(status.read_text())
            assert result['status'] == 'has_clips', result
            assert status.stat().st_uid == kiya.pw_uid, metadata(status)
            detail_response = context.request.get(endpoint + '/' + task)
            assert detail_response.ok, detail_response.text()
            detail = detail_response.json(); save(out / 'detail.json', detail)
            candidate = detail['candidates'][0]
            assert not candidate['playable'] and candidate['previewPlayable']
            media_path = next((history / 'production' / task).rglob('render.final.mp4'), None)
            if media_path is None:
                inventory = json.loads(next((history / 'production' / task).rglob('inventory.json')).read_text())
                media_path = Path(inventory['render']['media'])
            assert media_path.stat().st_uid == kiya.pw_uid
            media_url = endpoint + '/' + task + '/media/1'
            ranged = context.request.get(media_url, headers={'Range': 'bytes=0-1023'})
            assert ranged.status == 206 and len(ranged.body()) == 1024
            page = context.new_page()
            page.goto(url + '/#/sessions/acceptance?taskId=' + task + '&session=history-linux')
            page.get_by_text('候选 #1 · 已有成片', exact=True).click()
            page.locator('video').first.wait_for(state='visible')
            page.wait_for_function('() => document.querySelector("video").readyState >= 1')
            page.locator('video').first.evaluate('(v) => { v.currentTime=1; return v.play(); }')
            page.wait_for_function('() => document.querySelector("video").currentTime > 1.05')
            page.screenshot(path=str(out / 'playback.png'), full_page=True)
            browser.close()
        after = {'source': snapshot(repo), 'rec': snapshot(rec), 'production': snapshot(production),
                 'acceptance': metadata(acceptance), 'productionDir': metadata(production)}
        assert after == original, 'repair escaped history tree'
        assert not list(history.rglob('.history-*.tmp'))
        save(out / 'result.json', {'status': 'pass', 'backendUid': 0, 'workerUid': kiya.pw_uid,
             'workerUser': 'kiya', 'taskId': task, 'migrated': migrated, 'submitted': submitted,
             'runtimeDirectories': directories, 'workerStatus': metadata(status), 'media': metadata(media_path),
             'rangeStatus': 206, 'playbackAndSeek': True, 'outsideHistoryUnchanged': True,
             'backendUmask': '0077', 'workerUmask': '0077', 'paidRequests': 0, 'publish': False})
        print(json.dumps({'status': 'pass', 'out': str(out), 'taskId': task}))
    finally:
        server.terminate()
        try: server.wait(timeout=20)
        except subprocess.TimeoutExpired: server.kill(); server.wait()
        log.close()


if __name__ == '__main__':
    main()

"""Real Spring HTTP + Vue + Worker local acceptance; only model decisions are simulated.
Run from root after admin build and backend test-compile/dependency:build-classpath:
 worker/.venv/Scripts/python.exe admin/tests/historical-trial-e2e.py
No deployment, external credentials, paid model calls or publishing.
"""
from __future__ import annotations
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.error
import urllib.request
import http.cookiejar
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'worker/output/_historical-trial-acceptance'
JAVA = Path('D:/Develop/Java/JDK/jdk-17.0.14+7/bin/java.exe')
PYTHON = ROOT / 'worker/.venv/Scripts/python.exe'


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def digest_tree(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def main():
    # A fresh run preserves every previous acceptance run.
    run = OUT / time.strftime('%Y%m%d-%H%M%S')
    repo, rec, acceptance, production = (run / name for name in ('repo', 'recordings', 'acceptance', 'production'))
    rec.mkdir(parents=True, exist_ok=True)
    production.mkdir(exist_ok=True)
    sentinel = production / 'same-session-sentinel.json'
    save(sentinel, {'session': 'history-demo', 'production': 'must remain byte-identical'})
    video = rec / 'source.mp4'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                    'testsrc2=s=1280x720:r=30:d=7', '-f', 'lavfi', '-i', 'sine=d=7',
                    '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac', '-shortest', str(video)], check=True)
    source = repo / 'worker/output/kiya/history-demo'
    candidates = {'streamer': 'kiya', 'session': 'history-demo', 'segments': [
        {'video': str(video.resolve()), 'offset': 0, 'duration': 7}], 'candidates': [
        {'rank': 1, 't': 1.5, 'clip_start': 0, 'clip_end': 6, 'file_index': 0, 'local_start': 0}]}
    save(source / 'candidates.json', candidates)
    missing = repo / 'worker/output/kiya/history-missing'
    save(missing / 'candidates.json', {**candidates, 'session': 'history-missing', 'segments': [
        {'video': str(rec / 'missing.mp4'), 'offset': 0, 'duration': 7}]})
    failed = repo / 'worker/output/kiya/history-failure'
    bad_video = rec / 'corrupt.mp4'
    bad_video.write_bytes(b'Intentionally corrupt local fixture media')
    save(failed / 'candidates.json', {**candidates, 'session': 'history-failure', 'segments': [
        {'video': str(bad_video), 'offset': 0, 'duration': 7}]})
    source_before, production_before = digest_tree(repo), digest_tree(production)
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    cpfile = ROOT / 'backend/target/historical-classpath.txt'
    cp = os.pathsep.join([str(ROOT / 'backend/target/test-classes'), str(ROOT / 'backend/target/classes'),
                          cpfile.read_text(encoding='utf-8').strip()])
    args = [str(JAVA), '-cp', cp, 'com.kiya.admin.HistoricalTrialFixtureServer',
            '--server.address=127.0.0.1', f'--server.port={port}',
            '--server.servlet.session.cookie.secure=false', '--spring.datasource.url=jdbc:h2:mem:history;MODE=MySQL;DATABASE_TO_LOWER=TRUE;DB_CLOSE_DELAY=-1',
            '--spring.datasource.driver-class-name=org.h2.Driver', '--spring.datasource.username=sa',
            '--spring.datasource.password=', f'--kiya.repo-dir={repo}', f'--kiya.rec-dir={rec}',
            f'--kiya.ops-dir={production}', f'--kiya.acceptance-dir={acceptance}',
            '--kiya.admin-password=local-fixture-pass-123', '--kiya.historical-trial-fixture-enabled=true']
    env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8', 'REMOTION_CONCURRENCY': '4'}
    logs = (run / 'spring.log').open('w', encoding='utf-8')
    server = subprocess.Popen(args, cwd=ROOT / 'backend', env=env, stdout=logs, stderr=subprocess.STDOUT)
    errors, network = [], []
    try:
        deadline = time.monotonic() + 90
        while True:
            try:
                urllib.request.urlopen(url + '/api/auth/csrf', timeout=2).close(); break
            except (urllib.error.URLError, TimeoutError):
                if server.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('Spring fixture failed to start; inspect spring.log')
                time.sleep(.25)
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe', headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 1000})
            csrf = context.request.get(url + '/api/auth/csrf').json()
            login = context.request.post(url + '/api/auth/login', form={'username': 'admin', 'password': 'local-fixture-pass-123'},
                headers={csrf['headerName']: csrf['token']})
            assert login.ok, login.text()
            csrf = context.request.get(url + '/api/auth/csrf').json()
            headers = {csrf['headerName']: csrf['token']}
            anonymous = p.request.new_context()
            unauth = anonymous.get(url + '/api/sessions/history-demo/isolated-trials/options')
            assert unauth.status == 401
            anonymous.dispose()
            page = context.new_page()
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.on('request', lambda req: network.append({'method': req.method, 'url': req.url}))
            page.goto(url + '/#/sessions')
            row = page.locator('.el-table__row').filter(has_text='history-demo')
            row.get_by_role('button', name='隔离试跑', exact=True).click()
            page.get_by_role('dialog').last.wait_for()
            # A single candidate may be preselected; use role label for stable user interaction.
            dialog = page.get_by_role('dialog').last
            dialog.locator('.el-radio').first.click()
            dialog.locator('.el-checkbox').last.click()
            dialog.get_by_role('button', name='提交隔离试跑', exact=True).click()
            page.wait_for_timeout(400)
            tasks_result = context.request.get(url + '/api/sessions/isolated-trials').json()
            tasks = tasks_result['tasks'] if isinstance(tasks_result, dict) else tasks_result
            task = next(t for t in tasks if t['session'] == 'history-demo')
            task_id = task['taskId']
            # Concurrent HTTP retries use independent clients with the authenticated cookie/token.
            cookies = context.cookies()
            cookie_header = '; '.join(f"{c['name']}={c['value']}" for c in cookies)
            def repeat(_):
                request = urllib.request.Request(url + '/api/sessions/history-demo/isolated-trials',
                    data=json.dumps({'rank': 1, 'confirmation': True}).encode(),
                    headers={'Content-Type': 'application/json', 'Cookie': cookie_header, **headers}, method='POST')
                with urllib.request.urlopen(request) as response:
                    return json.load(response)['taskId']
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                assert set(pool.map(repeat, range(12))) == {task_id}
            # Missing media is stopped before queue persistence.
            missing_options = context.request.get(url + '/api/sessions/history-missing/isolated-trials/options').json()
            rejected = context.request.post(url + '/api/sessions/history-missing/isolated-trials',
                data={'rank': 1, 'confirmation': True}, headers=headers)
            assert rejected.status in (400, 409, 422), rejected.text()
            save(run / 'missing-result.json', {'options': missing_options, 'status': rejected.status, 'body': rejected.json()})
            worker_command = [str(PYTHON), str(ROOT / 'worker/scripts/historical_trial_worker.py'),
                              '--root', str(acceptance / 'history'), '--once', '--fixture']
            with (run / 'worker-success.log').open('w', encoding='utf-8') as log:
                subprocess.run(worker_command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
            detail_url = url + f'/api/sessions/history-demo/isolated-trials/{task_id}'
            detail = context.request.get(detail_url).json()
            save(run / 'completed-detail.json', detail)
            assert detail['status'] == 'has_clips', detail
            assert any(v['playable'] and v.get('simulation') is True for v in detail['candidates'][0]['versions']), detail
            assert detail['candidates'][0]['playable'] is False, 'Simulated preview must not become qualified production inventory'
            media_url = detail_url + '/media/1'
            media = context.request.get(media_url, headers={'Range': 'bytes=0-1023'})
            assert media.status == 206 and len(media.body()) == 1024, (media.status, media.headers)
            page.goto(url + '/#/sessions/acceptance?taskId=' + task_id + '&session=history-demo')
            # Explicit task deep link must open the existing read-only detail.
            expect(page.locator('.el-drawer')).to_contain_text('history-demo')
            page.get_by_text('候选 #1 · 已有成片', exact=True).click()
            page.locator('video').last.wait_for()
            page.wait_for_function('() => document.querySelector("video").readyState >= 1')
            page.locator('video').last.evaluate('(v) => {v.currentTime = 1; return v.play()}')
            page.wait_for_function('() => document.querySelector("video").currentTime > 1.05')
            page.locator('video').last.evaluate('(v) => v.pause()')
            page.screenshot(path=str(run / 'completed-desktop.png'), full_page=True)
            read_index = len(network)
            page.get_by_role('button', name='刷新详情', exact=True).click()
            page.wait_for_timeout(350)
            assert all(r['method'] == 'GET' for r in network[read_index:] if '/api/' in r['url'])
            # An explicit rerun keeps the first immutable record. A worker without the
            # zero-cost fixture capability pauses before any model or rendering step.
            budget_submission = context.request.post(url + '/api/sessions/history-demo/isolated-trials',
                data={'rank': 1, 'confirmation': True, 'rerunTaskId': task_id}, headers=headers)
            assert budget_submission.ok, budget_submission.text()
            budget_task = budget_submission.json()['taskId']
            assert budget_task != task_id
            with (run / 'worker-budget.log').open('w', encoding='utf-8') as log:
                subprocess.run(worker_command[:-1], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=60)
            budget_detail = context.request.get(url + f'/api/sessions/history-demo/isolated-trials/{budget_task}').json()
            assert budget_detail['status'] == 'waiting_for_budget', budget_detail
            page.goto(url + '/#/sessions/acceptance?taskId=' + budget_task + '&session=history-demo')
            expect(page.locator('.el-drawer')).to_contain_text('预算')
            page.screenshot(path=str(run / 'budget-desktop.png'), full_page=True)
            failed_submission = context.request.post(url + '/api/sessions/history-failure/isolated-trials',
                data={'rank': 1, 'confirmation': True}, headers=headers)
            assert failed_submission.ok, failed_submission.text()
            fail_task = failed_submission.json()['taskId']
            with (run / 'worker-failure.log').open('w', encoding='utf-8') as log:
                subprocess.run(worker_command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=180)
            failed_detail = context.request.get(url + f'/api/sessions/history-failure/isolated-trials/{fail_task}').json()
            assert failed_detail['status'] == 'failed' and (failed_detail.get('message') or failed_detail.get('blocker')), failed_detail
            page.goto(url + '/#/sessions/acceptance?taskId=' + fail_task + '&session=history-failure')
            expect(page.locator('.el-drawer')).to_contain_text('失败')
            page.screenshot(path=str(run / 'failure-desktop.png'), full_page=True)
            assert digest_tree(production) == production_before, 'Production queue changed'
            assert digest_tree(repo) == source_before, 'Original candidates/session files changed'
            assert not errors, errors
            result = {'status': 'pass', 'source': 'real Spring HTTP + Vue + Worker + FFmpeg/Remotion; simulated model',
                'taskId': task_id, 'failureTaskId': fail_task, 'concurrentRetries': 12, 'deduplicated': True,
                'mediaRangeStatus': media.status, 'playbackAndSeek': True, 'readonlyAcceptance': True,
                'productionUnchanged': True, 'sourceUnchanged': True, 'unauthenticatedStatus': unauth.status,
                'missingSubmissionStatus': rejected.status, 'detail': detail, 'failedDetail': failed_detail,
                'pageErrors': errors, 'paidModelRequests': 0, 'published': False}
            save(run / 'result.json', result)
            save(OUT / 'latest.json', {'run': str(run), 'result': str(run / 'result.json')})
            print(json.dumps({'status': 'pass', 'run': str(run), 'taskId': task_id}, ensure_ascii=False))
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=20)
        except subprocess.TimeoutExpired:
            server.kill(); server.wait()
        logs.close()


if __name__ == '__main__':
    main()

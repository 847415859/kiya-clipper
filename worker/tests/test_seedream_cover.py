import json
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import publish_pack as packer
from clipper.cover_prompt import seedream_options, seedream_prompt
from clipper.seedream import SeedreamError
from clipper import publish


def test_supplement_extends_automatic_plan_and_clearing_is_explicit():
    concept = {"label": "反差现场", "headline": "承诺翻车", "subline": "她把自己说服了"}
    saved = {"supplement": "人物靠右", "reference": "seedream"}
    prompt = seedream_prompt(concept, saved, "封面大字")
    assert "反差现场" in prompt and "人物靠右" in prompt and "上次生成" in prompt
    assert "封面大字" in prompt and "完整、准确地绘制" in prompt
    assert "后期将叠加" not in prompt and "黄字" not in prompt and "粉色描边" not in prompt
    assert "按本次封面文案替换" in prompt
    blank = seedream_prompt(concept, saved, "")
    assert "生成纯画面封面" in blank and "去除参考图中的旧文案" in blank
    assert seedream_options(saved) == saved
    assert seedream_options(saved, "", "frame") == {"supplement": "", "reference": "frame"}
    for bad in ("x" * 1501, "a\0b", 123):
        with pytest.raises(ValueError): seedream_options(supplement=bad)
    with pytest.raises(ValueError): seedream_options(reference="../elsewhere")


def test_event_turn_and_emotion_reach_model_beyond_legacy_short_subtitle():
    event = '主播认真解释自己的计划，' * 8 + '最后发现观众一直把她说的话理解成了吃夜宵。'
    concept = publish.cover_concept({'type':'温柔日常', 'what_happened':event,
                                     'emotion':'轻松、意外', 'punch_line':'原来你们想的是夜宵！'})
    assert len(concept['subline']) == 26
    prompt = seedream_prompt(concept, seedream_options(), '你们想的是夜宵？')
    assert event in prompt and '核心情绪：轻松、意外' in prompt
    assert '关键转折或原话：原来你们想的是夜宵！' in prompt
    assert '不要将所有内容统一处理成夸张漫画' in prompt
    assert '字号层级' in prompt and '聊天记录、人物台词、游戏战绩或关系结论' in prompt


def test_reference_redesign_and_unknown_emotion_have_explicit_boundaries():
    concept = publish.cover_concept({}, speech='主播在聊明天的安排')
    prompt = seedream_prompt(concept, {'reference':'seedream','supplement':'重新设计为柔和海报'}, '明天见')
    assert '事件：主播在聊明天的安排' in prompt and '核心情绪：' not in prompt
    assert '允许重构对应部分' in prompt and '用户本次设计要求：\n重新设计为柔和海报' in prompt
    assert '人物身份与指定文案准确性优先' in prompt
    assert '自动分类与风格建议仅作参考' in prompt
    assert publish.cover_concept({'what_happened':'a'*800,'emotion':42})['event'] == 'a'*600


@pytest.fixture
def cover_session(tmp_path, monkeypatch):
    folder = tmp_path / "output/20261117-1200"
    folder.mkdir(parents=True)
    (folder / "candidates.json").write_text(json.dumps({"segments": [], "candidates": [
        {"rank": 1, "t": 6, "clip_start": 0, "clip_end": 12}]}), encoding="utf-8")
    (folder / "rerank.json").write_text(json.dumps({"candidates": [{"rank": 1, "llm": {
        "type": "反差", "what_happened": "认真保证后翻车", "title_candidates": ["Test cover"]}}]}), encoding="utf-8")
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"streamer": {"name": "Test", "live_url": "https://live.example/1"},
                               "publish": {"cover_font": "test"}}), encoding="utf-8")
    monkeypatch.setenv("KIYA_MEMORY_DECISIONS", str(tmp_path / "none.json"))
    font = next((p for p in (Path('C:/Windows/Fonts/arial.ttf'), Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')) if p.is_file()), None)
    if font:
        monkeypatch.setattr(publish, "find_font", lambda spec: (str(font), 0))
    calls = []
    def grab(*args):
        Image.new('RGB', (640, 400), 'navy').save(args[-1], 'JPEG')
        return '测试直播画面'
    def generate(prompt, reference, out, **kwargs):
        calls.append({"prompt": prompt, "reference": reference, "reference_bytes": reference.read_bytes()})
        # Model responses can be PNG despite a .jpg destination.
        Image.new('RGB', (640, 400), 'blue' if len(calls) == 1 else 'green').save(out, 'PNG')
        return {"model": "fake-seedream", "prompt": prompt, "file": out.name}
    monkeypatch.setattr(packer, 'grab_frame', grab)
    monkeypatch.setattr(packer, 'generate_seedream', generate)
    def run(*args):
        monkeypatch.setattr(sys, 'argv', ['publish_pack.py', str(folder / 'candidates.json'), '--rank', '1', '--config', str(cfg), *args])
        return packer.main()
    return folder, run, calls, font


def test_prompt_preview_is_read_only_and_uses_current_additions(cover_session, capsys, monkeypatch):
    folder, run, calls, _ = cover_session
    monkeypatch.setattr(packer, 'grab_frame', lambda *a: pytest.fail('preview must not extract a frame'))
    assert run('--cover-prompt-only', '--cover-supplement=主体靠右\n背景粉紫', '--cover-text=HELLO') == 0
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert '主体靠右\n背景粉紫' in result['prompt'] and 'HELLO' in result['prompt']
    assert result['options']['reference'] == 'frame'
    assert not calls and not (folder / 'publish').exists()


def test_preview_and_generation_use_identical_context_and_prompt(cover_session, capsys):
    _, run, calls, _ = cover_session
    args = ['--cover-supplement=柔和海报，文字靠右', '--cover-text=明天见']
    assert run('--cover-prompt-only', *args) == 0
    preview = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert run('--cover-mode=seedream', *args) == 0
    capsys.readouterr()
    assert calls[0]['prompt'] == preview['prompt']


def test_seedream_supplement_caption_and_raw_reference_roundtrip(cover_session, capsys, monkeypatch):
    folder, run, calls, _ = cover_session
    monkeypatch.setattr(packer, 'render_theme_cover', lambda *a: pytest.fail('Seedream must supply the complete cover'))
    monkeypatch.setattr(publish, 'render_cover', lambda *a: pytest.fail('must not overlay fixed typography'))
    assert run('--cover-mode=seedream', '--cover-supplement=人物靠右', '--cover-text=MY COVER') == 0
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result['cover_seedream_options'] == {'supplement': '人物靠右', 'reference': 'frame'}
    assert '人物靠右' in calls[0]['prompt']
    main = folder / 'publish/01_cover.jpg'
    raw = folder / 'publish/01_cover_seedream_raw.jpg'
    with Image.open(main) as img:
        assert img.size == (640, 400)
        yellow = sum(1 for r, g, b in img.convert('RGB').getdata() if r > 180 and g > 150 and b < 140)
        assert yellow == 0
    with Image.open(raw) as img:
        assert img.format == 'JPEG' and img.size == (640, 400)
    old_raw = raw.read_bytes()
    assert main.read_bytes() == old_raw
    assert result['cover_history'][0]['meta']['composition'] == 'seedream-integrated-v1'
    monkeypatch.setattr(packer, 'grab_frame', lambda *a: pytest.fail('iterating the raw image must not need source video'))
    assert run('--cover-mode=seedream', '--cover-reference=seedream', '--cover-supplement=保留构图，只改背景', '--cover-text=') == 0
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert calls[-1]['reference'] == raw and calls[-1]['reference_bytes'] == old_raw
    assert result['cover_text'] == '' and result['cover_source'] == '上次 Seedream 生图'
    assert result['cover_seedream_options']['supplement'] == '保留构图，只改背景'
    meta = result['cover_variants'][0]['meta']
    assert meta['file'] == '01_cover_seedream.jpg' and meta['reference'] == 'seedream'
    assert not list((folder / 'publish').glob('.seedream-*'))


def test_model_failure_preserves_previous_cover_and_settings(cover_session, monkeypatch):
    folder, run, _, _ = cover_session
    out = folder / 'publish'
    out.mkdir()
    old = {'title': 'Manually edited title', 'cover': '01_cover.jpg', 'cover_seedream_options': {'supplement': '原设置', 'reference': 'frame'}}
    receipt = out / '01.json'
    receipt.write_text(json.dumps(old), encoding='utf-8')
    before = receipt.read_bytes()
    Image.new('RGB', (64, 40), 'red').save(out / '01_cover.jpg')
    image_before = (out / '01_cover.jpg').read_bytes()
    def fail(*a, **k): raise SeedreamError('fake service unavailable')
    monkeypatch.setattr(packer, 'generate_seedream', fail)
    assert run('--cover-mode=seedream', '--cover-supplement=新想法') == 1
    assert receipt.read_bytes() == before and (out / '01_cover.jpg').read_bytes() == image_before
    assert not list(out.glob('.seedream-*'))


def test_invalid_or_missing_reference_does_not_call_model(cover_session):
    folder, run, calls, _ = cover_session
    assert run('--cover-mode=seedream', '--cover-reference=seedream') == 1
    assert not calls
    assert run('--cover-mode=seedream', '--cover-supplement=' + 'x' * 1501) == 1
    assert not calls


def test_saved_options_survive_programmatic_reads_and_preview_can_clear_them(cover_session, capsys):
    folder, run, calls, _ = cover_session
    out = folder / 'publish'
    out.mkdir()
    saved = {'title': 'Manual title', 'cover_seedream_options': {'supplement': '旧补充', 'reference': 'frame'}}
    file = out / '01.json'
    file.write_text(json.dumps(saved), encoding='utf-8')
    before = file.read_bytes()
    assert run('--cover-prompt-only', '--cover-supplement=') == 0
    preview = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert preview['options']['supplement'] == '' and '旧补充' not in preview['prompt']
    assert file.read_bytes() == before
    assert run('--no-cover') == 0
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result['title'] == 'Manual title' and result['cover_seedream_options'] == saved['cover_seedream_options']
    assert not calls


def test_multiple_versions_remain_selectable_without_regenerating(cover_session, capsys, monkeypatch):
    folder, run, calls, _ = cover_session
    assert run('--cover-mode=seedream', '--cover-text=', '--cover-supplement=第一版') == 0
    first = json.loads(capsys.readouterr().out.splitlines()[-1])
    first_id = first['cover_history'][0]['id']
    history_dir = folder / 'publish/cover_history/01'
    image_before = (history_dir / (first_id + '.jpg')).read_bytes()
    raw_before = (history_dir / (first_id + '.raw.jpg')).read_bytes()
    assert run('--cover-mode=seedream', '--cover-text=', '--cover-supplement=第二版') == 0
    second = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert len(second['cover_history']) == 2
    assert second['cover_history'][0]['id'] != first_id
    assert (history_dir / (first_id + '.jpg')).read_bytes() == image_before
    monkeypatch.setattr(packer, 'generate_seedream', lambda *a, **k: pytest.fail('selecting history must not call model'))
    monkeypatch.setattr(packer, 'grab_frame', lambda *a: pytest.fail('selecting history must not need source video'))
    assert run('--cover-select-version=' + first_id) == 0
    selected = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert selected['cover_history_selected'] == first_id and len(selected['cover_history']) == 2
    assert (folder / 'publish/01_cover.jpg').read_bytes() == image_before
    assert (folder / 'publish/01_cover_seedream_raw.jpg').read_bytes() == raw_before
    assert len(calls) == 2
    assert run('--cover-select-version=../../outside') == 1
    assert run('--cover-select-version=' + 'f' * 32) == 1


def test_first_new_generation_preserves_existing_legacy_cover(cover_session, capsys):
    folder, run, _, _ = cover_session
    out = folder / 'publish'
    out.mkdir()
    Image.new('RGB', (64, 40), 'red').save(out / '01_cover_seedream.jpg')
    old = (out / '01_cover_seedream.jpg').read_bytes()
    (out / '01.json').write_text(json.dumps({'cover': '01_cover.jpg', 'cover_variant': 'seedream', 'cover_text': 'OLD'}), encoding='utf-8')
    assert run('--cover-mode=seedream', '--cover-text=') == 0
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert len(result['cover_history']) == 2
    legacy = next(v for v in result['cover_history'] if v['legacy'])
    assert not legacy['has_raw']
    assert (out / 'cover_history/01' / (legacy['id'] + '.jpg')).read_bytes() == old
    assert run('--cover-select-version=' + legacy['id']) == 0
    assert not (out / '01_cover_seedream_raw.jpg').exists()


def test_adapter_normalizes_reference_bytes_without_changing_request_contract(tmp_path, monkeypatch):
    import base64
    from clipper import seedream
    reference = tmp_path / 'legacy.jpg'
    Image.new('RGBA', (32, 20), (255, 0, 0, 255)).save(reference, 'PNG')
    requests = []
    monkeypatch.setattr(seedream, 'load_env', lambda: None)
    def request(url, key, body, *args):
        requests.append(body)
        return {'data': [{'url': 'https://example.test/generated.jpg'}]}
    monkeypatch.setattr(seedream, '_json_request', request)
    monkeypatch.setattr(seedream, '_download', lambda url, out, key: Image.new('RGB', (32, 20), 'blue').save(out))
    meta = seedream.generate('custom request', reference, tmp_path / 'out.jpg', api_key='fake-api-key', model='test-model')
    encoded = requests[0]['image'].split(',', 1)[1]
    assert base64.b64decode(encoded).startswith(b'\xff\xd8')
    assert requests[0]['prompt'] == 'custom request'
    assert 'fake-api-key' not in json.dumps(meta)


def test_programmatic_cover_and_all_mode_fallback_keep_seedream_history(cover_session, capsys, monkeypatch):
    folder, run, _, font = cover_session
    if not font: pytest.skip('requires a local font for actual themed cover rendering')
    assert run('--cover-mode=seedream', '--cover-text=') == 0
    original = json.loads(capsys.readouterr().out.splitlines()[-1])
    old_version = original['cover_history_selected']
    assert run('--cover-text=PROGRAM') == 0
    programmatic = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert programmatic['cover_variant'] == 'hero' and 'cover_history_selected' not in programmatic
    assert len(programmatic['cover_history']) == 1
    assert run('--cover-select=seedream') == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1])['cover_history_selected'] == old_version
    def failed(*a, **k): raise SeedreamError('fake generation failed')
    monkeypatch.setattr(packer, 'generate_seedream', failed)
    assert run('--cover-mode=all', '--cover-text=FALLBACK') == 0
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result['cover_variant'] == 'hero' and 'cover_history_selected' not in result
    assert len(result['cover_history']) == 1 and result['cover_seedream_error'] == 'fake generation failed'


def test_thirty_character_seedream_caption_is_not_silently_truncated(cover_session, monkeypatch):
    folder, run, calls, _ = cover_session
    monkeypatch.setattr(publish, 'wrap', lambda *a: pytest.fail('Seedream owns typography and wrapping'))
    caption = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ1234'
    assert len(caption) == 30
    assert run('--cover-mode=seedream', '--cover-text=' + caption) == 0
    assert '「'+caption+'」' in calls[0]['prompt']

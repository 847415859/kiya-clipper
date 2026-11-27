from pathlib import Path
import json
import shutil
import subprocess

import pytest


@pytest.fixture
def media(tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('ffmpeg and ffprobe required for real media regression')
    source = tmp_path / 'continuous.mp4'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                    'color=s=160x90:r=30:d=5', '-f', 'lavfi', '-i',
                    'anullsrc=r=48000:cl=mono', '-t', '5', '-c:v', 'libx264',
                    '-preset', 'ultrafast', '-c:a', 'aac', str(source)], check=True)
    damaged = tmp_path / 'internal-gap.mp4'
    # Drop precisely 70 AAC packets = 71680/48000 = 1.493333 seconds.
    # Stream copy leaves PTS and the final duration intact, unlike asetpts.
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(source),
                    '-map', '0', '-c', 'copy', '-bsf:a',
                    "noise=drop='between(n,48,117)'", str(damaged)], check=True)
    return source, damaged

from clipper import audio_diagnostics


def test_pipeline_audio_gap_localizes_first_bad_stage(monkeypatch, tmp_path):
    source = tmp_path / 'source.mp4'
    cut = tmp_path / 'cut.mp4'
    rendered = tmp_path / 'rendered.mp4'
    for path in (source, cut, rendered):
        path.write_bytes(b'fixture')
    gap = {'start': 37.035, 'end': 38.528, 'duration': 1.493}

    def gaps(path, threshold=0.10):
        return [gap] if path == cut else []

    monkeypatch.setattr(audio_diagnostics, 'decoded_audio_gaps', gaps)
    result = audio_diagnostics.diagnose_pipeline(
        [{'video': str(source), 'offset': 0, 'duration': 60}], cut, rendered)
    assert result['earliest_stage'] == 'cut'
    assert result['cut_gaps'] == [gap]
    assert result['output_gaps'] == []
    assert result['sources'][0]['sha256']


def test_normal_silence_is_not_a_timestamp_gap(monkeypatch, tmp_path):
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'fixture')
    monkeypatch.setattr(audio_diagnostics, 'decoded_audio_gaps', lambda *args, **kwargs: [])
    result = audio_diagnostics.diagnose_pipeline(
        [{'video': str(source), 'offset': 0, 'duration': 60}], None, source)
    assert result['earliest_stage'] == 'none'
    assert result['status'] == 'review'
    assert result['stages']['cut']['reason'] == 'stage_not_supplied'


def test_real_internal_gap_and_continuous_silence(media):
    source, damaged = media
    assert audio_diagnostics.decoded_audio_gaps(source) == []
    gaps = audio_diagnostics.decoded_audio_gaps(damaged)
    assert len(gaps) == 1
    assert gaps[0]['duration'] == pytest.approx(1.493333, abs=0.001)
    info = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries',
                     'format=duration', '-of', 'json', str(damaged)]))
    assert float(info['format']['duration']) == pytest.approx(5, abs=0.04)


def test_selected_ranges_exclude_unselected_source_gap(media):
    source, damaged = media
    report = audio_diagnostics.diagnose_pipeline(
        [{'video': str(damaged), 'offset': 100, 'duration': 5,
          'source_ranges': [[3.5, 5]]}], source, source)
    assert report['earliest_stage'] == 'none'
    assert report['status'] == 'diagnosed'
    report = audio_diagnostics.diagnose_pipeline(
        [{'video': str(damaged), 'source_ranges': [[0, 2]]}], source, source)
    assert report['earliest_stage'] == 'source'
    assert report['sources'][0]['gaps'][0]['end'] == 2


def test_real_internal_gap_recovery_and_receipt(media, tmp_path):
    from scripts.render import audio_receipt
    source, damaged = media
    before = audio_diagnostics.video_sha256(damaged)
    receipt = tmp_path / 'final.audio.json'
    report = audio_receipt([{'video': str(source), 'offset': 0, 'duration': 5}],
                           source, damaged, receipt, 5, True)
    assert report['recovery']['applied']
    assert report['before_recovery']['earliest_stage'] == 'render'
    assert report['after_recovery']['earliest_stage'] == 'none'
    assert report['before_recovery']['stages']['final']['sha256'] != report['after_recovery']['stages']['final']['sha256']
    assert audio_diagnostics.video_sha256(damaged) == before
    assert json.loads(receipt.read_text()) == report


def test_receipt_retains_original_evidence_on_recovery_error(media, tmp_path, monkeypatch):
    import scripts.render as render
    source, damaged = media
    receipt = tmp_path / 'failure.audio.json'

    def fail(*args):
        saved = json.loads(receipt.read_text())
        assert saved['before_recovery']['output_gaps']
        assert saved['after_recovery'] is None
        raise ValueError('fixture recovery failed')

    monkeypatch.setattr(render, 'restore_source_audio', fail)
    with pytest.raises(ValueError, match='fixture recovery failed'):
        render.audio_receipt([{'video': str(source)}], source, damaged, receipt, 5, True)
    saved = json.loads(receipt.read_text())
    assert saved['after_recovery']['status'] == 'review'
    assert 'fixture recovery failed' in saved['recovery']['error']


def test_recovery_rejects_bad_source_without_mutating_render(media):
    from scripts.render import restore_source_audio
    source, damaged = media
    before = damaged.read_bytes()
    assert not restore_source_audio(damaged, damaged, 5)
    assert damaged.read_bytes() == before


def test_short_tail_recovery(media, tmp_path):
    from scripts.render import restore_source_audio
    source, _ = media
    short = tmp_path / 'short-tail.mp4'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(source),
                    '-filter_complex', '[0:a]atrim=end=4.85[a]', '-map', '0:v',
                    '-map', '[a]', '-c:v', 'copy', '-c:a', 'aac', str(short)], check=True)
    before = audio_diagnostics.video_sha256(short)
    assert restore_source_audio(source, short, 5)
    assert audio_diagnostics.video_sha256(short) == before
    assert audio_diagnostics.decoded_audio_gaps(short) == []
    assert not restore_source_audio(source, short, 5)


def test_receipt_never_repairs_from_concealed_source_gap(media, tmp_path):
    from scripts.render import audio_receipt
    source, damaged = media
    # Continuous cut alone is not sufficient evidence: selected original is bad.
    before = damaged.read_bytes()
    report = audio_receipt([{'video': str(damaged), 'source_ranges': [[0, 5]]}],
                           source, damaged, tmp_path / 'guard.audio.json', 5, True)
    assert not report['recovery']['applied']
    assert report['recovery']['skipped_reason'] == 'selected_source_unavailable_or_gapped'
    assert damaged.read_bytes() == before


def test_unavailable_audio_is_review(tmp_path):
    missing = tmp_path / 'missing.mp4'
    with pytest.raises(FileNotFoundError):
        audio_diagnostics.decoded_audio_gaps(missing)
    report = audio_diagnostics.diagnose_pipeline(
        [{'video': str(missing), 'offset': 0, 'duration': 5}], None, missing)
    assert report['status'] == 'review'
    assert all(row['status'] == 'unavailable' for row in report['stages'].values())


def test_decode_errors_and_stream_sample_rate(monkeypatch, tmp_path):
    path = tmp_path / 'fixture.mp4'
    path.write_bytes(b'fixture')
    payload = {'streams': [{'sample_rate': '48000'}], 'frames': [
        {'best_effort_timestamp_time': '0', 'nb_samples': 1024},
        {'best_effort_timestamp_time': '1.514666', 'nb_samples': 1024}]}
    monkeypatch.setattr(audio_diagnostics.shutil, 'which', lambda name: name)
    monkeypatch.setattr(audio_diagnostics.subprocess, 'run', lambda *a, **k:
                        subprocess.CompletedProcess(a, 0, json.dumps(payload), ''))
    assert audio_diagnostics.decoded_audio_gaps(path)[0]['duration'] == pytest.approx(1.493333, abs=0.001)
    monkeypatch.setattr(audio_diagnostics.subprocess, 'run', lambda *a, **k:
                        subprocess.CompletedProcess(a, 0, json.dumps(payload), 'decode failure'))
    assert audio_diagnostics.diagnose_pipeline([{'video': str(path)}], path, path)['status'] == 'review'


def test_missing_cut_does_not_attribute_final_gap(monkeypatch, tmp_path):
    source = tmp_path / 'source.mp4'
    final = tmp_path / 'final.mp4'
    source.write_bytes(b'fixture')
    final.write_bytes(b'fixture')
    gap = {'start': 1, 'end': 2, 'duration': 1}
    monkeypatch.setattr(audio_diagnostics, 'decoded_audio_gaps',
                        lambda p: [gap] if p == final else [])
    report = audio_diagnostics.diagnose_pipeline([{'video': str(source)}], None, final)
    assert report['earliest_stage'] == 'unknown'
    assert report['earliest_observed_stage'] == 'render'
    assert report['status'] == 'review'


def test_offset_duration_source_capture_gap(monkeypatch, tmp_path):
    path = tmp_path / 'source.mp4'
    path.write_bytes(b'fixture')
    monkeypatch.setattr(audio_diagnostics, 'decoded_audio_gaps', lambda *a: [])
    report = audio_diagnostics.diagnose_pipeline([
        {'video': str(path), 'offset': 10, 'duration': 5},
        {'video': str(path), 'offset': 17, 'duration': 5}], path, path)
    assert report['earliest_stage'] == 'source_stitch'
    assert report['segment_boundary_gaps'][0]['source_range'] == [15, 17]

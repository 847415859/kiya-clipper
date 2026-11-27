"""Reproducible synthetic packet-gap evidence; never refers to real candidate #5."""
from pathlib import Path
import json
import subprocess
import sys

WORKER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKER))
from clipper.audio_diagnostics import video_sha256
from scripts.render import audio_receipt


def main():
    out = WORKER / 'output/_repair-acceptance/audio'
    out.mkdir(parents=True, exist_ok=True)
    source, damaged = out / 'continuous.mp4', out / 'damaged.mp4'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'lavfi', '-i',
        'testsrc2=s=320x180:r=30:d=5', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=5',
        '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac', str(source)], check=True)
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(source), '-map', '0', '-c', 'copy',
        '-bsf:a', "noise=drop='between(n,48,117)'", str(damaged)], check=True)
    repaired = out / 'repaired.mp4'
    repaired.write_bytes(damaged.read_bytes())
    before_video = video_sha256(repaired)
    result = audio_receipt([{'video': str(source), 'offset': 0, 'duration': 5}], source, repaired,
                           out / 'recovery.audio.json', 5, True)
    assert result['recovery']['applied']
    assert result['after_recovery']['earliest_stage'] == 'none'
    assert before_video == video_sha256(repaired)
    result = {'evidence_type': 'synthetic-ffmpeg-not-real-rank5', 'video_stream_preserved': True,
        'gap_before': result['before_recovery']['output_gaps'], 'gap_after': result['after_recovery']['output_gaps'],
        'receipt': str(out / 'recovery.audio.json'), 'paid_model_requests': 0, 'model_cost_fen': 0}
    (out / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()

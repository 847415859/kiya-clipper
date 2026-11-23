"""Real HTTP/SSE transport tests against a local fake service, no paid API calls."""
import io
import json
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from clipper.qwen_multimodal import (  # noqa: E402
    MAX_BASE64_BYTES, QwenResearchClient, media_part, read_completion, validate_block,
)
from scripts.qwen_research import main, prepare  # noqa: E402


def evidence(kind="visual", end=1200):
    return {"summary": "测试区间", "observations": [
        {"start_ms": 100, "end_ms": end, "kind": kind, "description": "模型观察草稿"}
    ], "uncertainties": ["尚未独立复核"]}


def stream(body, finish="stop", done=True):
    text = json.dumps(body, ensure_ascii=False)
    events = [{"choices": [{"index": 0, "delta": {"reasoning_content": "不保存的思考"}}]}]
    for i in range(0, len(text), 13):
        events.append({"choices": [{"index": 0, "delta": {"content": text[i:i + 13]}}]})
    events += [{"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
               {"choices": [], "usage": {"prompt_tokens": 20, "completion_tokens": 40}}]
    raw = b":keepalive\n\n" + b"".join(
        ("data: " + json.dumps(e, ensure_ascii=False) + "\n\n").encode() for e in events)
    return raw + (b"data: [DONE]\n\n" if done else b"")


class Response(io.BytesIO):
    headers = {"Content-Type": "text/event-stream; charset=utf-8"}


@pytest.fixture
def service():
    received = []
    setting = {"status": 200, "finish": "stop", "done": True}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append(body)
            if setting["status"] != 200:
                self.send_response(setting["status"])
                self.end_headers()
                self.wfile.write(b'{"error":"DO_NOT_LOG_SECRET_RESPONSE"}')
                return
            content = body["messages"][1]["content"]
            kind = "speech" if any(x["type"] == "input_audio" for x in content) else "visual"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(stream(evidence(kind), finish=setting["finish"], done=setting["done"]))

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/v1", received, setting
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def test_stream_collects_content_and_usage_only():
    text, usage = read_completion(Response(stream(evidence())))
    assert json.loads(text) == evidence()
    assert "不保存的思考" not in text
    assert usage["prompt_tokens"] == 20


@pytest.mark.parametrize("finish,done", [("stop", False), ("length", True), ("content_filter", True)])
def test_incomplete_or_filtered_stream_rejected(finish, done):
    with pytest.raises(RuntimeError):
        read_completion(Response(stream(evidence(), finish=finish, done=done)))


def test_media_types_and_size_guard(tmp_path):
    wav = tmp_path / "input.wav"
    wav.write_bytes(b"test-audio")
    part = media_part(wav, "audio")
    assert part["type"] == "input_audio"
    assert part["input_audio"]["format"] == "wav"
    assert part["input_audio"]["data"].startswith("data:;base64,")
    with pytest.raises(ValueError):
        media_part(wav, "visual")
    wav.write_bytes(b"")
    with pytest.raises(ValueError):
        media_part(wav, "audio")
    with wav.open("wb") as f:
        f.truncate(MAX_BASE64_BYTES)
    with pytest.raises(ValueError):
        media_part(wav, "audio")


def test_modality_and_time_hallucination_rejected():
    with pytest.raises(ValueError):
        validate_block(evidence("sound"), "visual", 2000)
    with pytest.raises(ValueError):
        validate_block(evidence(end=3000), "visual", 2000)
    value = evidence()
    value["observations"][0]["start_ms"] = 1300
    with pytest.raises(ValueError):
        validate_block(value, "visual", 2000)


def test_client_real_sse_media_cache_and_time_mapping(service, tmp_path):
    url, received, _ = service
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"bounded-video")
    client = QwenResearchClient("visual", base_url=url, api_key="test-only")
    kwargs = dict(start_ms=30_000, duration_ms=2000, cache_dir=tmp_path / "cache")
    result = client.analyze(media, **kwargs)
    assert len(received) == 1
    assert received[0]["stream"] is True
    assert received[0]["messages"][1]["content"][0]["type"] == "video_url"
    assert result["absolute_observations"][0]["absolute_start_ms"] == 30_100
    assert result["production_approved"] is False
    assert client.analyze(media, **kwargs)["cached"] is True
    assert len(received) == 1
    # Changing the absolute input offset must not reuse incorrectly mapped evidence.
    client.analyze(media, **dict(kwargs, start_ms=40_000))
    assert len(received) == 2


def test_http_error_not_retried_or_leaked(service, tmp_path):
    url, received, settings = service
    settings["status"] = 429
    wav = tmp_path / "clip.wav"
    wav.write_bytes(b"audio")
    client = QwenResearchClient("audio", base_url=url, api_key="SECRET_KEY")
    with pytest.raises(RuntimeError) as error:
        client.analyze(wav, start_ms=0, duration_ms=2000, cache_dir=tmp_path / "cache")
    assert "429" in str(error.value)
    assert "SECRET" not in str(error.value)
    assert len(received) == 1
    assert received[0]["modalities"] == ["text"]
    assert not list((tmp_path / "cache").glob("*.json"))


@pytest.mark.parametrize("url", ["http://example.com/v1", "https://user:secret@example.com/v1",
                                "https://example.com/v1?key=secret"])
def test_unsafe_endpoint_rejected(url):
    with pytest.raises(ValueError):
        QwenResearchClient("audio", base_url=url, api_key="test")


def test_config_check_has_no_network_or_secret(monkeypatch, capsys):
    monkeypatch.setenv("QWEN_API_KEY", "TOP_SECRET")
    assert main(["--check"]) == 0
    output = capsys.readouterr().out
    assert "TOP_SECRET" not in output
    assert json.loads(output)["external_calls"] == 0


@pytest.fixture
def video(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg required")
    path = tmp_path / "test.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=25",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000", "-t", "2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
                   check=True, capture_output=True)
    return path


def test_cli_both_real_ffmpeg_and_mock_sse(video, service, monkeypatch, tmp_path):
    url, received, _ = service
    monkeypatch.setenv("QWEN_API_KEY", "test-only")
    monkeypatch.setenv("QWEN_BASE_URL", url)
    out = tmp_path / "results"
    assert main(["--video", str(video), "--start", "0.2", "--duration", "1.5",
                 "--output", str(out)]) == 0
    report = json.loads(next(out.glob("*/run.json")).read_text(encoding="utf-8"))
    assert set(report["results"]) == {"visual", "audio"}
    assert report["status"] == "api_schema_validated_needs_review"
    assert len(received) == 2
    assert report["results"]["audio"]["absolute_observations"][0]["absolute_start_ms"] == 300


def test_dry_run_does_not_call_model(video, service, monkeypatch, tmp_path):
    url, received, _ = service
    monkeypatch.setenv("QWEN_BASE_URL", url)
    assert main(["--video", str(video), "--duration", "1.5", "--dry-run",
                 "--output", str(tmp_path / "dry")]) == 0
    assert received == []


def test_out_of_bounds_interval_rejected(video, tmp_path):
    with pytest.raises(ValueError):
        prepare(video, 1.5, 1, tmp_path / "clip", "both")
    with pytest.raises(ValueError):
        prepare(video, float("nan"), 1, tmp_path / "clip", "both")

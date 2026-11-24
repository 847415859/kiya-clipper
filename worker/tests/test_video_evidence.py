"""Regression coverage for video windows, provider transport and editor handoff."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

WORKER = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(WORKER), str(WORKER / "scripts")]

import autoedit
import rerank
from clipper.llm import LLMClient, LLMResult
from clipper.video_evidence import WindowOut, analyze_candidate, session_sources


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="需要 ffmpeg 和 ffprobe")
def test_video_analysis_keeps_recording_gap_and_extends_response(tmp_path):
    path = tmp_path / "source.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=96x54:r=10:d=8",
                    "-f", "lavfi", "-i", "sine=d=8", "-c:v", "libx264", "-c:a", "aac", str(path)], check=True)
    sources = session_sources([{"video": str(path), "offset": 0}, {"video": str(path), "offset": 10}])
    assert [(s.start, s.end) for s in sources] == [(0, 8), (10, 18)]
    calls = []

    def observe(messages, schema, tag):
        video, text = messages[1]["content"]
        assert video["video_url"]["url"].startswith("data:;base64,")
        assert tag == "rerank-video-observe-v1"
        prompt = text["text"]
        calls.append(prompt)
        duration = float(re.search(r"窗口时长 ([\d.]+)", prompt)[1])
        return LLMResult(data=schema.model_validate({
            "observations": [{"start": 0.2, "end": min(1.0, duration), "kind": "回应",
                              "modality": "原声", "description": "核对回应"}],
            "continues_before": False, "continues_after": "结尾回应" not in prompt,
        }))

    result = analyze_candidate({"clip_start": 0, "clip_end": 14}, sources,
        [{"t0": 15.5, "t1": 16.1, "text": "结尾回应"}], [], 0, {"context_after": 4},
        SimpleNamespace(model="offline", chat_json=observe), 18)
    assert result["gaps"] == [[8.0, 10.0]]
    assert result["planned"] == [0.0, 18]
    assert any(row["phase"] == "context" and row["continues_after"] is False for row in result["windows"])
    assert any(row.get("source_frame_pts") for row in result["windows"] if row["phase"] == "detail")
    assert any("结尾回应" in prompt for prompt in calls)


def test_video_stream_cache_keys_include_video_content(tmp_path, monkeypatch):
    payloads = []

    class Stream:
        def __enter__(self):
            return iter([
                b'data: {"choices":[{"delta":{"content":"{\\"observations\\":[],\\"continues_before\\":false,\\"continues_after\\":null}"}}]}\n',
                b'data: {"choices":[{"finish_reason":"stop"}]}\n',
                b'data: {"choices":[],"usage":{"prompt_tokens":12,"completion_tokens":8}}\n',
                b'data: [DONE]\n',
            ])
        def __exit__(self, *args):
            pass

    def transport(request, timeout):
        payloads.append(json.loads(request.data))
        return Stream()

    monkeypatch.setattr("urllib.request.urlopen", transport)
    client = LLMClient(model="test", base_url="https://example.invalid", api_key="test", cache_dir=tmp_path,
                       request_options={"stream": True, "modalities": ["text"]})
    def messages(video):
        return [{"role": "user", "content": [{"type": "video_url", "video_url": {"url": video}}]}]
    a = client.chat_json(messages("data:;base64,AAA"), WindowOut)
    assert a.data is not None and a.usage["prompt_tokens"] == 12
    assert client.chat_json(messages("data:;base64,AAA"), WindowOut).cached
    assert not client.chat_json(messages("data:;base64,BBB"), WindowOut).cached
    assert client.request_attempts == len(payloads) == 2
    assert client.requests_without_usage == 0


def test_audio_visual_evidence_reaches_normal_rerank_and_editor(tmp_path, monkeypatch, capsys):
    config = WORKER.parent / "config" / "kiya.yaml"
    session = tmp_path / "20261124-2033"
    session.mkdir()
    path = session / "candidates.json"
    path.write_text(json.dumps({"segments": [], "duration_seconds": 50, "candidates": [
        {"rank": 1, "t": 8, "clip_start": 0, "clip_end": 20, "score": 5}]}), encoding="utf-8")
    report = {"model": "video-test", "observations": [
        {"id": "AV001", "start": 19.2, "end": 21.4, "kind": "结果", "modality": "字幕", "description": "画面显示扫码失败"}],
        "covered": [[0, 19], [19.5, 26]], "gaps": [[19, 19.5]], "open_context": [], "planned": [0, 26], "windows": []}
    monkeypatch.setattr(rerank, "analyze_candidate", lambda *args: report)
    monkeypatch.setattr(rerank, "make_video_client", lambda *args: SimpleNamespace(
        model="video-test", total={}, request_attempts=0, requests_without_usage=0))

    class Model:
        model, total = "test", {"calls": 0, "cached": 0, "prompt_tokens": 0, "completion_tokens": 0}

        def __init__(self, *args, **kwargs):
            pass

        def cost(self, *_):
            return None

        def chat_json(self, messages, schema, tag):
            if tag == "edit-v9":
                assert "AV001 19.20–21.40s 结果/字幕，必保" in messages[1]["content"]
                assert "音画证据缺口" in messages[1]["content"]
                return LLMResult(data=schema(trim={"start": 2, "end": 16}, items=[], note="初稿"))
            if tag == "rerank-v6":
                assert "AV001" in messages[1]["content"][-1]["text"]
                assert all(x["type"] != "image_url" for x in messages[1]["content"])
            return LLMResult(data=schema(funny=True, standalone=True, score=9, start_time=2, end_time=19,
                                         must_keep_evidence_ids=["AV001"]))

    monkeypatch.setattr(rerank, "LLMClient", Model)
    monkeypatch.setattr(sys, "argv", ["rerank", str(path), "--config", str(config)])
    assert rerank.main() == 0
    log = capsys.readouterr().out
    assert "连续音画优先，无有效观察时回退抽帧" in log
    assert "正式评分使用连续音画观察 1 条，截图 0 张" in log
    record = json.loads((session / "rerank.json").read_text(encoding="utf-8"))["candidates"][0]
    assert record["refined"]["end"] >= 21.4
    assert record["video_evidence"]["needs_review"] and not record["pick"]
    monkeypatch.setattr(autoedit, "LLMClient", Model)
    monkeypatch.setattr(autoedit.cb_lib, "settings", lambda _: {"enabled": False})
    monkeypatch.setattr(sys, "argv", ["autoedit", str(path), "--config", str(config), "--rank", "1",
                                     "--material-start", "0", "--material-end", "26", "--template", "default"])
    assert autoedit.main() == 0
    edit = json.loads((session / "edits/01.json").read_text(encoding="utf-8"))
    assert edit["trim"]["end"] >= 21.4
    assert edit["boundary_trace"]["video_evidence"]["kept_ids"] == ["AV001"]

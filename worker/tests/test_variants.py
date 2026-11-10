"""剪辑初稿多版本：档位参数、前 N 个才出多版本、三版各自的限制、选中那一版成为当前脚本和 AI 原稿、重跑单版本清掉旧的多版本。"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from clipper import variants as V  # noqa: E402
from test_memes import world  # noqa: E402,F401  (复用假服务 + 示例场次)


def test_cfg_resolve_and_gate():
    assert V.variants_cfg({})["styles"] == ["restrained", "standard", "lively"] and V.variants_cfg({})["top_n"] == 5
    assert V.variants_cfg({"variants": {"styles": ["bogus"], "top_n": 2}}) == {"top_n": 2, "styles": ["standard"]}
    r, s, l = (V.resolve(k, 8, 2) for k in ("restrained", "standard", "lively"))
    assert (r["max_items"], r["max_memes"]) == (3, 1) and (s["max_items"], s["max_memes"]) == (8, 2) and (l["max_items"], l["max_memes"]) == (11, 2)
    assert V.resolve("restrained", 2, 0)["max_items"] == 2 and V.resolve("restrained", 8, 0)["max_memes"] == 0     # 配置更严时不放宽
    assert V.wants_variants("auto", 3, 9, 5) == (True, "") and V.wants_variants("auto", None, 2, 5) == (True, "")
    ok, why = V.wants_variants("auto", 7, 1, 5)
    assert not ok and "排名第 7" in why and "前 5 个" in why
    assert V.wants_variants("off", 1, 1, 5) == (False, "")
    assert V.summary(None) == []


def test_autoedit_three_variants(world):
    r = world.run("autoedit.py", world.sess / "candidates.json", "--rank", 1, "--variants", "auto")
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(world.srv.chat) == 3                                         # 三版 = 三次调用
    prompts = [json.dumps(c, ensure_ascii=False) for c in world.srv.chat]
    assert "克制版：" in prompts[0] and "标准版：" in prompts[1] and "热闹版：" in prompts[2]
    ed = world.sess / "edits"
    v = json.loads((ed / "01.variants.json").read_text(encoding="utf-8"))
    by = {x["key"]: x for x in v["variants"]}
    assert v["chosen"] == "standard" and v["chosen_by"] == "default" and list(by) == ["restrained", "standard", "lively"]
    assert len(by["restrained"]["edit"]["items"]) == 3                      # 想加 4 个，被限制到 3 个
    assert len(by["lively"]["edit"]["items"]) == 9                          # 热闹版放宽到 11 个
    assert by["lively"]["edit"]["generated_by"].endswith("/ 热闹")
    cur = json.loads((ed / "01.json").read_text(encoding="utf-8"))
    ai = json.loads((ed / "01.ai.json").read_text(encoding="utf-8"))
    assert cur["items"] == by["standard"]["edit"]["items"] and ai["variant"] == "standard" and ai["edit"]["items"] == cur["items"]
    assert "已生成 3 个版本" in r.stdout and "【克制】初稿" in r.stdout


def test_only_top_n_get_variants_and_rerun_clears(world):
    world.cfg.write_text(world.cfg.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    r = world.run("autoedit.py", world.sess / "candidates.json", "--rank", 1, "--variants", "auto")
    assert r.returncode == 0 and (world.sess / "edits" / "01.variants.json").exists()
    text = world.cfg.read_text(encoding="utf-8").replace("autoedit: {prompt: edit_v4, max_items: 8}", "autoedit: {prompt: edit_v4, max_items: 8, variants: {top_n: 0}}")
    assert "top_n: 0" in text
    world.cfg.write_text(text, encoding="utf-8")
    n = len(world.srv.chat)
    r = world.run("autoedit.py", world.sess / "candidates.json", "--rank", 1, "--force", "--variants", "auto")
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(world.srv.chat) == n and "不在前 0 个" in r.stdout          # 超出范围只出标准版（和上次标准版同一份输入，走缓存）
    assert not (world.sess / "edits" / "01.variants.json").exists()           # 旧的多版本作废，免得和当前脚本对不上
    ai = json.loads((world.sess / "edits" / "01.ai.json").read_text(encoding="utf-8"))
    assert ai["variant"] is None
    r = world.run("autoedit.py", world.sess / "candidates.json", "--rank", 1, "--force")          # 不带 --variants 也是单版本
    assert r.returncode == 0 and len(world.srv.chat) == n


def test_pick_styles_explicitly(world):
    """人在编辑器里勾选版本：想要几版就几版，不受前 top_n 个的限制；只选一版就只出那一版。"""
    text = world.cfg.read_text(encoding="utf-8").replace("autoedit: {prompt: edit_v4, max_items: 8}", "autoedit: {prompt: edit_v4, max_items: 8, variants: {top_n: 0}}")
    world.cfg.write_text(text, encoding="utf-8")
    ed = world.sess / "edits"
    r = world.run("autoedit.py", world.sess / "candidates.json", "--rank", 1, "--styles", "lively,standard,lively")
    assert r.returncode == 0, r.stdout + r.stderr
    v = json.loads((ed / "01.variants.json").read_text(encoding="utf-8"))
    assert [x["key"] for x in v["variants"]] == ["lively", "standard"] and v["chosen"] == "standard"     # 去重；有标准版默认选标准版
    assert len(world.srv.chat) == 2
    r = world.run("autoedit.py", world.sess / "candidates.json", "--rank", 1, "--force", "--styles", "restrained")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (ed / "01.variants.json").exists()                                      # 只选一版：没有多版本文件
    cur = json.loads((ed / "01.json").read_text(encoding="utf-8"))
    ai = json.loads((ed / "01.ai.json").read_text(encoding="utf-8"))
    assert len(cur["items"]) == 3 and cur["generated_by"].endswith("/ 克制") and ai["variant"] == "restrained"
    r = world.run("autoedit.py", world.sess / "candidates.json", "--rank", 1, "--force", "--styles", "wild")
    assert r.returncode == 1 and "不认识的版本：wild" in r.stderr

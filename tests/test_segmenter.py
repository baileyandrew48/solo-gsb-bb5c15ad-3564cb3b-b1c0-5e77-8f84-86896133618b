"""动态规划切分与 API 的测试。"""

from __future__ import annotations

import itertools
import random

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import SegmentRequest
from app.segmenter import SegmentationError, SENTENCE_ENDINGS, segment_words
from app.srt import to_srt, _format_timestamp


def make_req(words, **kw):
    base = dict(
        max_chars=40,
        min_duration=0.5,
        max_duration=5.0,
        target_duration=2.0,
        long_pause=0.8,
    )
    base.update(kw)
    return SegmentRequest(words=words, **base)


def ws(text, s, e):
    return {"text": text, "start_ms": s, "end_ms": e}


# --------------------------------------------------------------------------
# 基本功能
# --------------------------------------------------------------------------

def test_readme_example():
    req = make_req(
        [
            ws("你好", 0, 600),
            ws("世界", 700, 1500),
            ws("今天", 3000, 3600),
            ws("天气", 3700, 4400),
            ws("不错。", 4500, 5200),
        ],
        max_chars=12,
    )
    segs, total = segment_words(req)
    assert [s["text"] for s in segs] == ["你好世界", "今天天气不错。"]
    assert segs[0]["start_ms"] == 0 and segs[0]["end_ms"] == 1500
    assert segs[1]["start_ms"] == 3000 and segs[1]["end_ms"] == 5200
    assert segs[0]["char_count"] == 4 and segs[1]["char_count"] == 7
    assert segs[0]["cost"] == pytest.approx(0.25)
    assert segs[1]["cost"] == pytest.approx(0.04)
    assert total == pytest.approx(0.29)


def test_covers_all_words_in_order():
    words = [ws(f"词{i}", i * 1000, i * 1000 + 900) for i in range(6)]
    segs, total = segment_words(make_req(words, max_duration=3.0))
    joined = "".join(s["text"] for s in segs)
    assert joined == "词0词1词2词3词4词5"
    assert segs[0]["index"] == 1 and segs[-1]["index"] == len(segs)


def test_ascii_words_get_spaces_cjk_does_not():
    segs, _ = segment_words(make_req([ws("Hello", 0, 900), ws("world", 900, 1800)]))
    assert segs[0]["text"] == "Hello world"
    assert segs[0]["char_count"] == 11

    segs, _ = segment_words(make_req([ws("你好", 0, 900), ws("世界", 900, 1800)]))
    assert segs[0]["text"] == "你好世界"
    assert segs[0]["char_count"] == 4


# --------------------------------------------------------------------------
# 罚分：句末标点与长停顿
# --------------------------------------------------------------------------

def test_sentence_ending_penalty_makes_split_preferred():
    # 两个 1.5s 的词，第一个以句号结尾；合并会跨过句末边界（+4 罚分）。
    words = [ws("结束。", 0, 1500), ws("继续", 1500, 3000)]
    segs, _ = segment_words(make_req(words, max_duration=5.0, target_duration=1.5))
    assert len(segs) == 2  # 两条都恰为目标时长，且无罚分


def test_crossing_boundary_charged_when_forced():
    # min_duration 迫使两词合并，必然跨过句末标点。
    words = [ws("结束。", 0, 900), ws("继续", 900, 1800)]
    segs, total = segment_words(
        make_req(words, min_duration=1.5, max_duration=5.0, target_duration=1.8)
    )
    assert len(segs) == 1
    dur = 1.8
    assert total == pytest.approx((dur - 1.8) ** 2 + 4.0)
    assert segs[0]["cost"] == pytest.approx(4.0)


def test_long_pause_is_a_boundary():
    # 1 秒静音间隔达到 0.8s 阈值。
    words = [ws("第一", 0, 1000), ws("第二", 2000, 3000)]
    segs_split, _ = segment_words(make_req(words, target_duration=1.0, long_pause=0.8))
    assert len(segs_split) == 2

    # 阈值放宽到 1.5s 后不再是边界；两段时长各 1s，目标 2s，合并更优。
    segs_merge, _ = segment_words(make_req(words, target_duration=2.0, long_pause=1.5))
    assert len(segs_merge) == 1


def test_boundary_is_charged_once_even_if_both_reasons():
    # 间隙既是句末标点又是长停顿，也只罚 4 分。
    words = [ws("完。", 0, 500), ws("续", 1500, 2000), ws("尾", 2000, 2500)]
    segs, total = segment_words(
        make_req(words, min_duration=1.0, max_duration=5.0, target_duration=2.5,
                 long_pause=0.8)
    )
    # [0] 0.5s 太短必须并入词 1（跨过坏边界，+4）；词 2 0.5s 太短必须并入词 1。
    assert len(segs) == 1
    assert total == pytest.approx(4.0)


# --------------------------------------------------------------------------
# 平局：选字幕更少
# --------------------------------------------------------------------------

def test_tie_prefers_fewer_segments():
    import math

    # 两个 1s 的词：单段 2s 的偏差 = 两段 1s 偏差之和（目标 sqrt(2) 时代数相等）。
    words = [ws("a", 0, 1000), ws("b", 1000, 2000)]
    target = math.sqrt(2)
    segs, _ = segment_words(
        make_req(words, min_duration=0.5, max_duration=5.0, target_duration=target)
    )
    assert len(segs) == 1


# --------------------------------------------------------------------------
# 不可行情况
# --------------------------------------------------------------------------

def test_single_word_exceeding_max_chars():
    with pytest.raises(SegmentationError, match="超过字幕字数上限"):
        segment_words(make_req([ws("一个很长的词", 0, 1000)], max_chars=3))


def test_single_word_exceeding_max_duration():
    with pytest.raises(SegmentationError, match="超过字幕时长上限"):
        segment_words(
            make_req([ws("长", 0, 10000)], max_duration=5.0)
        )


def test_no_feasible_segmentation():
    # 每词 1s，最多 2 字 -> 不能合并；下限 3s -> 单词又太短。
    words = [ws("甲", 0, 1000), ws("乙", 1000, 2000), ws("丙", 2000, 3000)]
    with pytest.raises(SegmentationError, match="不存在"):
        segment_words(
            make_req(
                words,
                max_chars=1,
                min_duration=3.0,
                max_duration=5.0,
                target_duration=3.0,
            )
        )


def test_overlapping_words_rejected():
    with pytest.raises(SegmentationError, match="互不重叠"):
        segment_words(make_req([ws("甲", 0, 1000), ws("乙", 900, 1500)]))


def test_empty_text_rejected():
    with pytest.raises(SegmentationError, match="text 为空"):
        segment_words(make_req([ws("   ", 0, 1000)]))


def test_bad_param_ranges_rejected_by_schema():
    with pytest.raises(ValueError):
        make_req([ws("甲", 0, 1000)], min_duration=4, max_duration=3)
    with pytest.raises(ValueError):
        make_req([ws("甲", 0, 1000)], min_duration=1, max_duration=3, target_duration=5)


# --------------------------------------------------------------------------
# DP 与暴力枚举对拍
# --------------------------------------------------------------------------

def _brute_force(req):
    texts = [w.text for w in req.words]
    starts = [w.start_ms for w in req.words]
    ends = [w.end_ms for w in req.words]
    n = len(texts)
    pause_ms = req.long_pause * 1000.0

    def join(lo, hi):
        out = texts[lo]
        for i in range(lo + 1, hi):
            l, r = texts[i - 1], texts[i]
            if l[-1].isascii() and l[-1].isalnum() and r[0].isascii() and r[0].isalnum():
                out += " "
            out += r
        return out

    best = None  # (cost, -segments, cuts)
    for cuts_mask in range(1 << (n - 1)):
        cuts = [0]
        for k in range(n - 1):
            if cuts_mask >> k & 1:
                cuts.append(k + 1)
        cuts.append(n)
        ok = True
        total = 0.0
        for lo, hi in zip(cuts, cuts[1:]):
            text = join(lo, hi)
            dur = (ends[hi - 1] - starts[lo]) / 1000.0
            if len(text) > req.max_chars or not (
                req.min_duration - 1e-9 <= dur <= req.max_duration + 1e-9
            ):
                ok = False
                break
            total += (dur - req.target_duration) ** 2
            for k in range(lo + 1, hi):
                bad = texts[k - 1][-1] in SENTENCE_ENDINGS or (
                    starts[k] - ends[k - 1]
                ) >= pause_ms - 1e-9
                if bad:
                    total += 4.0
        if ok:
            nseg = len(cuts) - 1
            key = (round(total, 9), -nseg)
            if best is None or key < best[0]:
                best = (key, cuts)
    return best


def test_dp_matches_brute_force_random():
    rng = random.Random(20260925)
    punctuation = ["", "", "", "。", "！", "?"]
    ascii_words = ["hello", "world", "foo", "bar", "42"]
    cjk_words = ["你", "好", "世", "界", "今", "天"]

    for trial in range(300):
        n = rng.randint(1, 7)
        words = []
        t = 0
        for _ in range(n):
            gap = rng.choice([0, 0, 0, 200, 900, 1500])
            t += gap
            dur = rng.randint(2, 20) * 100
            if rng.random() < 0.5:
                text = rng.choice(ascii_words)
            else:
                text = rng.choice(cjk_words)
            text += rng.choice(punctuation)
            words.append(ws(text, t, t + dur))
            t += dur
        req = make_req(
            words,
            max_chars=rng.choice([1, 3, 6, 10, 30]),
            min_duration=rng.choice([0.3, 1.0, 2.0]),
            max_duration=rng.choice([3.0, 5.0, 8.0]),
            target_duration=2.0,
            long_pause=rng.choice([0.5, 0.8, 1.2]),
        )
        if req.min_duration > req.max_duration:
            continue
        expect = _brute_force(req)
        try:
            segs, total = segment_words(req)
        except SegmentationError:
            assert expect is None, f"trial {trial}: DP 报无解，但暴力枚举存在解"
            continue
        assert expect is not None, f"trial {trial}: DP 给出解，但暴力枚举无解"
        assert len(segs) == -expect[0][1]
        assert total == pytest.approx(expect[0][0], abs=1e-8)


# --------------------------------------------------------------------------
# SRT
# --------------------------------------------------------------------------

def test_srt_format():
    segs = [
        {"index": 1, "text": "你好", "start_ms": 0, "end_ms": 1500,
         "duration": 1.5, "char_count": 2, "cost": 0.0},
        {"index": 2, "text": "世界", "start_ms": 3600_000, "end_ms": 3661_500,
         "duration": 61.5, "char_count": 2, "cost": 0.0},
    ]
    srt = to_srt(segs)
    lines = srt.splitlines()
    assert lines[0] == "1"
    assert lines[1] == "00:00:00,000 --> 00:00:01,500"
    assert lines[2] == "你好"
    assert "01:00:00,000 --> 01:01:01,500" in srt
    assert _format_timestamp(0) == "00:00:00,000"


# --------------------------------------------------------------------------
# HTTP API
# --------------------------------------------------------------------------

client = TestClient(app)

PAYLOAD = {
    "words": [
        {"text": "你好", "start_ms": 0, "end_ms": 600},
        {"text": "世界", "start_ms": 700, "end_ms": 1500},
        {"text": "今天", "start_ms": 3000, "end_ms": 3600},
        {"text": "天气", "start_ms": 3700, "end_ms": 4400},
        {"text": "不错。", "start_ms": 4500, "end_ms": 5200},
    ],
    "max_chars": 12,
    "min_duration": 1.0,
    "max_duration": 3.0,
    "target_duration": 2.0,
    "long_pause": 0.8,
}


def test_http_health():
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_http_segment():
    r = client.post("/segment", json=PAYLOAD)
    assert r.status_code == 200
    data = r.json()
    assert [s["text"] for s in data["segments"]] == ["你好世界", "今天天气不错。"]
    assert "total_cost" in data


def test_http_srt():
    r = client.post("/segment/srt", json=PAYLOAD)
    assert r.status_code == 200
    assert "application/x-subrip" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]
    assert "00:00:00,000 --> 00:00:01,500" in r.text
    assert r.text.strip().endswith("今天天气不错。")


def test_http_422_contains_reason():
    bad = dict(PAYLOAD)
    bad["max_chars"] = 3
    bad["words"] = [{"text": "超长的一个词啊非常长", "start_ms": 0, "end_ms": 500}]
    r = client.post("/segment", json=bad)
    assert r.status_code == 422
    assert "超过字幕字数上限" in r.json()["detail"]


def test_http_422_on_invalid_params():
    bad = dict(PAYLOAD, target_duration=99)
    r = client.post("/segment", json=bad)
    assert r.status_code == 422
    assert r.json()["detail"]

    r = client.post("/segment", json={"words": []})
    assert r.status_code == 422

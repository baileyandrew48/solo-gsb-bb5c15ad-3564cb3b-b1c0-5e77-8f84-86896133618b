"""动态规划字幕切分核心逻辑。

代价定义（总代价取最小值）：
    sum((字幕时长秒数 - target_duration) ** 2)
    + 4 * 被跨过的句末标点 / 长停顿边界数量

“跨过边界”指相邻两个词之间存在句末标点或长停顿，但二者没有在该处切开、
而是落在了同一条字幕中。

平局规则：总代价相同时，选择字幕条数更少的方案。
"""

from __future__ import annotations

import math
from typing import Dict, List, Tuple

from .schemas import SegmentRequest

# 句末标点（中英文）
SENTENCE_ENDINGS = frozenset("。！？!?…．.")

# 跨过一个句末标点 / 长停顿边界的罚分
BOUNDARY_PENALTY = 4.0

# 浮点比较容差
_EPS = 1e-9


class SegmentationError(ValueError):
    """无法完成合法切分，由 API 层转换为 HTTP 422。"""


def _is_ascii_alnum(ch: str) -> bool:
    return ch.isascii() and ch.isalnum()


def _needs_space(left: str, right: str) -> bool:
    """相邻两词是否需要空格分隔：仅两侧均为 ASCII 字母/数字时才加空格。

    这样英文/数字之间保留空格，而中日韩文字之间不会插入多余空格。
    """
    return bool(left) and bool(right) and _is_ascii_alnum(left[-1]) and _is_ascii_alnum(right[0])


def _join_words(texts: List[str], left: int, right: int) -> str:
    """拼接下标区间 [left, right) 内的词。"""
    parts: List[str] = [texts[left]]
    for i in range(left + 1, right):
        if _needs_space(texts[i - 1], texts[i]):
            parts.append(" ")
        parts.append(texts[i])
    return "".join(parts)


def _validate_words(req: SegmentRequest) -> Tuple[List[str], List[int], List[int]]:
    texts: List[str] = []
    starts: List[int] = []
    ends: List[int] = []

    for k, w in enumerate(req.words):
        if not w.text or not w.text.strip():
            raise SegmentationError(f"第 {k + 1} 个词的 text 为空")
        if w.end_ms < w.start_ms:
            raise SegmentationError(f"第 {k + 1} 个词的 end_ms 早于 start_ms")
        texts.append(w.text)
        starts.append(w.start_ms)
        ends.append(w.end_ms)

    for k in range(1, len(texts)):
        if starts[k] < ends[k - 1]:
            raise SegmentationError(
                f"输入词必须按时间排序且互不重叠：第 {k} 个词与第 {k + 1} 个词时间重叠或乱序"
            )

    return texts, starts, ends


def segment_words(req: SegmentRequest) -> Tuple[List[Dict], float]:
    """执行 DP 切分，返回 (字幕列表, 最小总代价)。失败时抛 SegmentationError。"""

    texts, starts, ends = _validate_words(req)
    n = len(texts)

    max_chars = req.max_chars
    min_s = req.min_duration
    max_s = req.max_duration
    target_s = req.target_duration
    pause_ms = req.long_pause * 1000.0

    # 单词层面的硬性不可行检查：合并更多词只会让字数更多、时长更长。
    for k in range(n):
        if len(texts[k]) > max_chars:
            raise SegmentationError(
                f"第 {k + 1} 个词本身有 {len(texts[k])} 字，超过字幕字数上限 {max_chars}，无法容纳"
            )
        word_dur = (ends[k] - starts[k]) / 1000.0
        if word_dur > max_s + _EPS:
            raise SegmentationError(
                f"第 {k + 1} 个词时长 {word_dur:.3f}s 超过字幕时长上限 {max_s:g}s，无法容纳"
            )

    # sep_len[k]：词 k-1 与词 k 之间的分隔符长度（k >= 1）。
    sep_len = [0] * n
    for k in range(1, n):
        sep_len[k] = 1 if _needs_space(texts[k - 1], texts[k]) else 0

    # bad_prefix[t]：词区间 [0, t) 内部（边界位置 1..t-1）的“坏边界”数量。
    # 坏边界 = 前一个词以句末标点结尾，或相邻词间隔达到长停顿阈值。
    bad_prefix = [0] * (n + 1)
    for k in range(1, n):
        bad = texts[k - 1][-1] in SENTENCE_ENDINGS or (starts[k] - ends[k - 1]) >= pause_ms - _EPS
        bad_prefix[k + 1] = bad_prefix[k] + (1 if bad else 0)

    # DP[i]：覆盖前 i 个词的最优方案。
    inf = float("inf")
    best_cost: List[float] = [inf] * (n + 1)
    best_count: List[int] = [n + 1] * (n + 1)
    best_cut: List[int] = [-1] * (n + 1)
    best_cost[0] = 0.0
    best_count[0] = 0

    for i in range(1, n + 1):
        chars = 0
        j = i - 1
        while j >= 0:
            # 将词 j 并入区间 [j, i)。
            chars += len(texts[j]) + (sep_len[j + 1] if j < i - 1 else 0)
            if chars > max_chars:
                break  # 再向左并入只会更长

            dur_ms = ends[i - 1] - starts[j]
            dur = dur_ms / 1000.0
            if dur > max_s + _EPS:
                break  # 起点只会更早，时长只会更长
            if dur < min_s - _EPS:
                j -= 1
                continue  # 继续向左并入以拉长时长

            if best_cost[j] < inf:
                bad_inside = bad_prefix[i] - bad_prefix[j + 1]
                cand = best_cost[j] + (dur - target_s) ** 2 + BOUNDARY_PENALTY * bad_inside
                cand_count = best_count[j] + 1
                if cand < best_cost[i] - _EPS or (
                    abs(cand - best_cost[i]) <= _EPS and cand_count < best_count[i]
                ):
                    best_cost[i] = cand
                    best_count[i] = cand_count
                    best_cut[i] = j
            j -= 1

    if best_cut[n] < 0:
        raise SegmentationError(
            "不存在满足字数上限与时长上下限约束的合法切分，请放宽约束后重试"
        )

    # 回溯重建切分点。
    ranges: List[Tuple[int, int]] = []
    i = n
    while i > 0:
        j = best_cut[i]
        ranges.append((j, i))
        i = j
    ranges.reverse()

    segments: List[Dict] = []
    for idx, (left, right) in enumerate(ranges, start=1):
        text = _join_words(texts, left, right)
        dur = (ends[right - 1] - starts[left]) / 1000.0
        bad_inside = bad_prefix[right] - bad_prefix[left + 1]
        segments.append(
            {
                "index": idx,
                "text": text,
                "start_ms": starts[left],
                "end_ms": ends[right - 1],
                "duration": dur,
                "char_count": len(text),
                "cost": (dur - target_s) ** 2 + BOUNDARY_PENALTY * bad_inside,
            }
        )

    total_cost = math.fsum(seg["cost"] for seg in segments)
    return segments, total_cost

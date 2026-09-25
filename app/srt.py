"""SRT 导出。"""

from __future__ import annotations

from typing import List


def _format_timestamp(ms: int) -> str:
    """毫秒 -> SRT 时间戳 HH:MM:SS,mmm。"""
    if ms < 0:
        ms = 0
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def to_srt(segments: List[dict]) -> str:
    """将切分结果转换为 SRT 文本。"""
    blocks = []
    for seg in segments:
        blocks.append(
            "\n".join(
                [
                    str(seg["index"]),
                    f"{_format_timestamp(seg['start_ms'])} --> {_format_timestamp(seg['end_ms'])}",
                    seg["text"],
                ]
            )
        )
    return "\n\n".join(blocks) + "\n"

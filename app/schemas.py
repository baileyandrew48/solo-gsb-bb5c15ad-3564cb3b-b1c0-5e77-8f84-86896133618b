"""请求与响应的数据模型。

时间约定：
- 词级时间戳 start_ms / end_ms 使用毫秒整数；
- 所有时长配置（时长上下限、目标时长、长停顿阈值）使用秒。
"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field, model_validator


class Word(BaseModel):
    """一个带时间戳的词。"""

    text: str = Field(..., description="词文本，不允许为空")
    start_ms: int = Field(..., ge=0, description="起始时间（毫秒）")
    end_ms: int = Field(..., ge=0, description="结束时间（毫秒）")


class SegmentRequest(BaseModel):
    """字幕切分请求。"""

    words: List[Word] = Field(..., min_length=1, description="按时间排序且互不重叠的词")
    max_chars: int = Field(..., gt=0, description="每条字幕的字数上限")
    min_duration: float = Field(..., gt=0, description="每条字幕时长下限（秒）")
    max_duration: float = Field(..., gt=0, description="每条字幕时长上限（秒）")
    target_duration: float = Field(..., gt=0, description="期望的字幕时长（秒）")
    long_pause: float = Field(..., gt=0, description="长停顿阈值（秒），相邻词间隔达到该值视为停顿边界")

    @model_validator(mode="after")
    def _check_ranges(self) -> "SegmentRequest":
        if self.min_duration > self.max_duration:
            raise ValueError("min_duration 不能大于 max_duration")
        if not (self.min_duration <= self.target_duration <= self.max_duration):
            raise ValueError("target_duration 必须在 min_duration 与 max_duration 之间")
        return self


class Segment(BaseModel):
    """一条切分后的字幕。"""

    index: int = Field(..., description="字幕序号，从 1 开始")
    text: str = Field(..., description="拼接后的字幕文本")
    start_ms: int = Field(..., description="起始时间（毫秒）")
    end_ms: int = Field(..., description="结束时间（毫秒）")
    duration: float = Field(..., description="时长（秒）")
    char_count: int = Field(..., description="字数（含词间空格）")
    cost: float = Field(..., description="该条字幕对总代价的贡献（含其跨过的边界罚分）")


class SegmentResponse(BaseModel):
    """切分结果。"""

    segments: List[Segment]
    total_cost: float = Field(..., description="动态规划求得的最小总代价")

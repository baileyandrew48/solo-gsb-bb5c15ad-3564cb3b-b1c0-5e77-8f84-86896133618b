"""本地字幕切分 API。"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse

from .schemas import SegmentRequest, SegmentResponse
from .segmenter import SegmentationError, segment_words
from .srt import to_srt

app = FastAPI(
    title="字幕切分 API",
    description="基于动态规划的词级转写字幕切分服务，支持 JSON 分段结果与 SRT 导出。",
    version="1.0.0",
)


def _flatten_validation_errors(errors: list) -> str:
    msgs = []
    for err in errors:
        loc = " -> ".join(str(p) for p in err.get("loc", []) if p != "body")
        msg = err.get("msg", "参数不合法")
        msgs.append(f"{loc}: {msg}" if loc else msg)
    return "；".join(msgs) or "请求参数不合法"


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"detail": _flatten_validation_errors(exc.errors())},
    )


@app.exception_handler(SegmentationError)
async def segmentation_exception_handler(_: Request, exc: SegmentationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/health", tags=["其他"])
async def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/segment", response_model=SegmentResponse, tags=["切分"])
async def segment(req: SegmentRequest) -> SegmentResponse:
    """对词级转写进行最优切分，返回分段 JSON。"""
    segments, total_cost = segment_words(req)
    return SegmentResponse(segments=segments, total_cost=total_cost)


@app.post("/segment/srt", response_class=PlainTextResponse, tags=["切分"])
async def segment_srt(req: SegmentRequest) -> PlainTextResponse:
    """对词级转写进行最优切分，直接返回 SRT 字幕文件内容。"""
    segments, _ = segment_words(req)
    return PlainTextResponse(
        to_srt(segments),
        media_type="application/x-subrip; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="subtitles.srt"'},
    )

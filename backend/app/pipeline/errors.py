"""流水线里可以「告诉用户怎么办」的错误。"""
from __future__ import annotations


class PipelineError(Exception):
    def __init__(self, message: str, code: str = "PIPELINE_FAILED", hint: str = ""):
        super().__init__(message)
        self.message = message
        self.code = code
        self.hint = hint

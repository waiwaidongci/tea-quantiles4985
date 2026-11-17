"""Seal checks shared by every read/write entry point.

Once a tea lot's authorization is revoked the lot is sealed first. Every
endpoint that could expose original readings, frozen inputs, results or
downloads must refuse before doing any work.
"""
from fastapi import HTTPException

SEALED_DETAIL='该批次因授权撤销已封存，停止提供原记录、冻结输入、结果与下载'

def ensure_open(study):
    if study is not None and getattr(study,'sealed_at',None):
        raise HTTPException(423,SEALED_DETAIL)
    return study

"""Batch + source-key destruction for revoked tea-sample authorization.

Flow: seal the affected study first (all entry points reject), then erase the
protected source observations, redact frozen inputs, clear results and delete
shared artifact copies. Only a certificate without original values is kept.

Every step is idempotent and scoped by (study_id, source_key), so a resumed
cleanup never touches another batch that happens to reuse the same source key.
Task creation is de-duplicated by a fingerprint, so retrying never adds a task.
"""
import os
import json
import hashlib
from pathlib import Path
from redis import Redis
from sqlalchemy import select,func
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from src.models.destruction import DestructionTask
from src.models.study import Study
from src.models.observation import Observation
from src.models.run import Run
from src.models.review import Review
from src.models.audit import Audit
from src.services.audits import log
from src.utils.serializers import record,now,checksum

ARTIFACT_DIR=Path(os.environ.get('ARTIFACT_DIR','/app/artifacts'))
QUEUE='destruction-queue'
REDACTED='[授权撤销后清除]'


def enqueue(task_id):
    Redis.from_url(os.environ['REDIS_URL']).rpush(QUEUE,task_id)


def _artifact(run_id):
    return ARTIFACT_DIR/f'run-{run_id}.json'


def _normalize(keys):
    if not isinstance(keys,list) or not keys: raise HTTPException(422,'至少提供一个源记录编号')
    out=[]
    for k in keys:
        if not isinstance(k,str): raise HTTPException(422,'源记录编号必须是文本')
        k=k.strip()
        if not k or len(k)>100: raise HTTPException(422,'源记录编号长度必须为1到100')
        if k not in out: out.append(k)
    if len(out)>5000: raise HTTPException(422,'单次最多销毁5000条源记录')
    return sorted(out)


def fingerprint(study_id,keys):
    return checksum({'study_id':study_id,'source_keys':sorted(keys)})


def _transition(task,status):
    task.status=status
    task.updated_at=now()
    task.history=list(task.history or [])+[{'status':status,'at':task.updated_at}]


def get(db,id):
    task=db.get(DestructionTask,id)
    if not task: raise HTTPException(404,'销毁任务不存在')
    return record(task)


def list_tasks(db):
    return [record(x) for x in db.scalars(select(DestructionTask).order_by(DestructionTask.id.desc()))]


def create(db,data,user):
    """Seal the batch and create one destruction task, reusing any duplicate."""
    keys=_normalize(data.source_keys)
    fp=fingerprint(data.study_id,keys)
    existing=db.scalar(select(DestructionTask).where(DestructionTask.fingerprint==fp))
    if existing: return record(existing)
    study=db.scalar(select(Study).where(Study.id==data.study_id).with_for_update())
    if not study: raise HTTPException(404,'业务批次不存在')
    # Re-check after taking the row lock: a concurrent identical submission may
    # have committed while this request waited, in which case reuse its task.
    existing=db.scalar(select(DestructionTask).where(DestructionTask.fingerprint==fp))
    if existing: return record(existing)
    if study.sealed_at: raise HTTPException(423,'该批次已因授权撤销封存，不能发起新的销毁')
    found=db.scalar(select(func.count()).select_from(Observation).where(Observation.study_id==study.id,Observation.source_key.in_(keys)))
    if found!=len(keys): raise HTTPException(422,'源记录编号不存在或不属于该批次')
    stamp=now()
    task=DestructionTask(study_id=study.id,source_keys=keys,target_count=found,fingerprint=fp,status='pending',
                         error='',history=[{'status':'pending','at':stamp}],initiated_by=user.email,
                         created_at=stamp,updated_at=stamp)
    db.add(task)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing=db.scalar(select(DestructionTask).where(DestructionTask.fingerprint==fp))
        if existing: return record(existing)
        raise
    # Seal immediately, before any cleanup: every entrance rejects from now on.
    study.sealed_at=task.created_at;study.sealed_by=user.email;study.destruction_id=task.id
    log(db,user,'destruction.create',f'task={task.id} study={study.id} keys={len(keys)}')
    db.commit()
    try: enqueue(task.id)
    except Exception: pass  # task stays pending; the retry endpoint can resume it
    return record(task)


def _verify(db,study_id,keys,run_ids):
    remaining=db.scalar(select(func.count()).select_from(Observation).where(Observation.study_id==study_id,Observation.source_key.in_(keys)))
    run_rows=list(db.scalars(select(Run).where(Run.study_id==study_id)))
    leaked_inputs=[r.id for r in run_rows if any(row.get('source_key') in keys for row in (r.snapshot or []))]
    leaked_results=[r.id for r in run_rows if r.result is not None]
    leaked_artifacts=[rid for rid in run_ids if _artifact(rid).exists()]
    leaked_reviews=[x.run_id for x in db.scalars(select(Review).where(Review.run_id.in_(run_ids)))] if run_ids else []
    leaked_audits=[x.id for x in db.scalars(select(Audit).where(Audit.action=='review.approve'))
                   if any((x.detail or '').startswith(f'{rid}: ') for rid in run_ids) and REDACTED not in (x.detail or '')]
    preserved_other=db.scalar(select(func.count()).select_from(Observation).where(Observation.study_id!=study_id,Observation.source_key.in_(keys)))
    return {
        'source_observations_remaining':remaining,
        'frozen_inputs_with_original_values':leaked_inputs,
        'results_remaining':leaked_results,
        'artifacts_remaining':leaked_artifacts,
        'reviews_remaining':leaked_reviews,
        'review_audit_entries_remaining':leaked_audits,
        'other_batches_same_source_key_preserved':preserved_other,
        'all_cleared':not any([remaining,leaked_inputs,leaked_results,leaked_artifacts,leaked_reviews,leaked_audits]),
    }


def cleanup(db,task_id,actor=None):
    """Erase every copy; raise (persisting failed state) unless all copies verify empty."""
    task=db.scalar(select(DestructionTask).where(DestructionTask.id==task_id).with_for_update())
    if not task: raise HTTPException(404,'销毁任务不存在')
    if task.status=='completed': return task.certificate
    _transition(task,'cleaning')
    db.commit()
    actor=actor or task.initiated_by
    try:
        study=db.scalar(select(Study).where(Study.id==task.study_id).with_for_update())
        if not study: raise RuntimeError('业务批次不存在，无法核对销毁')
        if not study.sealed_at:
            study.sealed_at=now();study.sealed_by=actor;study.destruction_id=task.id
        keys=list(task.source_keys or [])
        removed=0
        for row in list(db.scalars(select(Observation).where(Observation.study_id==task.study_id,Observation.source_key.in_(keys)))):
            db.delete(row);removed+=1
        run_rows=list(db.scalars(select(Run).where(Run.study_id==task.study_id).with_for_update()))
        run_ids=[r.id for r in run_rows]
        # Capture the pre-destruction states once, before the first attempt mutates them.
        if task.prior_run_states is None:
            task.prior_run_states=[{'id':r.id,'status':r.status} for r in run_rows]
        prior=list(task.prior_run_states)
        for r in run_rows:
            r.snapshot=[row for row in (r.snapshot or []) if row.get('source_key') not in keys]
            r.result=None;r.error=''
            r.status='destroyed';r.destroyed_at=now();r.destruction_id=task.id
        # Review free text can paraphrase readings; it is a shared archive too.
        live_reviews=list(db.scalars(select(Review).where(Review.run_id.in_(run_ids)))) if run_ids else []
        if task.prior_review_ids is None:
            task.prior_review_ids=[x.id for x in live_reviews]
        reviews_removed=0
        for x in live_reviews:
            db.delete(x);reviews_removed+=1
        audits_redacted=0
        for x in db.scalars(select(Audit).where(Audit.action=='review.approve')):
            if REDACTED in (x.detail or ''):
                if any((x.detail or '').startswith(f'{rid}: ') for rid in run_ids): audits_redacted+=1
                continue
            if any((x.detail or '').startswith(f'{rid}: ') for rid in run_ids):
                x.detail=f'{x.detail.split(": ")[0]}: {REDACTED}';audits_redacted+=1
        log(db,actor,'destruction.clean',f'task={task.id} study={task.study_id} observations={removed} runs={len(run_ids)} reviews={reviews_removed}')
        db.commit()
        artifacts=[]
        for rid in run_ids:
            path=_artifact(rid)
            if path.exists():
                path.unlink()
                artifacts.append(str(path))
        checks=_verify(db,task.study_id,keys,run_ids)
        if not checks['all_cleared']:
            raise RuntimeError('副本核对未通过：'+json.dumps(checks,ensure_ascii=False))
        cert={
            'task_id':task.id,'study_id':task.study_id,'study_name':study.name,
            'fingerprint':task.fingerprint,'source_key_count':len(keys),
            # One aggregate digest proves which set was destroyed without
            # publishing enumerable per-key fingerprints.
            'source_key_set_sha256':hashlib.sha256('\n'.join(keys).encode()).hexdigest(),
            'source_observations_removed':task.target_count or len(keys),
            'source_observations_removed_this_attempt':removed,'runs_scrubbed':len(run_ids),
            'run_ids':run_ids,'artifacts_removed':artifacts,
            'reviews_removed':len(task.prior_review_ids or []),'review_audits_redacted':audits_redacted,
            'frozen_inputs_redacted':True,'results_cleared':True,'shared_archives_cleared':True,
            'prior_run_states':prior,'checks':checks,'hash_algorithm':'sha256',
            'sealed':True,'verified_at':now(),
        }
        task=db.scalar(select(DestructionTask).where(DestructionTask.id==task_id).with_for_update())
        task.certificate=cert;task.error='';task.completed_at=now()
        # The certificate is the only retained artifact: drop locating keys.
        task.source_keys=[]
        _transition(task,'completed')
        log(db,actor,'destruction.complete',f'task={task.id} study={task.study_id} verified=true')
        db.commit()
        return cert
    except Exception as e:
        db.rollback()
        task=db.scalar(select(DestructionTask).where(DestructionTask.id==task_id).with_for_update())
        if task and task.status!='completed':
            task.error=str(e)[:1000]
            _transition(task,'failed')
            log(db,actor,'destruction.fail',f'task={task.id} error={str(e)[:200]}')
            db.commit()
        raise


def process(task_id):
    from src.config.database import Session
    with Session() as db:
        try:
            return cleanup(db,task_id)
        except HTTPException:
            raise
        except Exception:
            return None  # failed state is persisted by cleanup; queued retry can resume


def retry(db,id,user):
    task=db.scalar(select(DestructionTask).where(DestructionTask.id==id).with_for_update())
    if not task: raise HTTPException(404,'销毁任务不存在')
    if task.status=='completed': return record(task)
    if task.status not in ('failed','pending','cleaning'): raise HTTPException(409,'当前状态不能恢复清理')
    if not task.source_keys: raise HTTPException(409,'任务缺少定位编号，无法恢复')
    if task.status!='pending': _transition(task,'pending')
    task.error=''
    log(db,user,'destruction.retry',f'task={task.id}')
    db.commit()
    try: enqueue(task.id)
    except Exception: pass
    return record(task)

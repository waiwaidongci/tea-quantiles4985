import os,json,logging
from pathlib import Path
from redis import Redis
from sqlalchemy import select
from src.config.database import Session,engine
from src.config.schema import ensure_schema
from src.models.run import Run
from src.models.study import Study
from src.models import user,study,observation,review,audit,destruction  # noqa: F401
from src.services.algorithm import calculate
from src.services import destruction as destruction_service
from src.utils.serializers import checksum

redis=Redis.from_url(os.environ['REDIS_URL'])
ARTIFACT_DIR=Path(os.environ.get('ARTIFACT_DIR','/app/artifacts'))


def _held_study(db,run):
    """Lock the study row; return None when the batch has already been sealed."""
    study_row=db.scalar(select(Study).where(Study.id==run.study_id).with_for_update())
    if not study_row or study_row.sealed_at: return None
    return study_row


def process_analysis(id):
    with Session() as db:
        run=db.get(Run,id)
        if not run or run.status in ('completed','failed','destroyed'): return
        if _held_study(db,run) is None:
            # Sealed before dequeue: drop the job without writing anything back.
            db.rollback();return
        run.status='running';db.commit()
        try:
            if checksum(run.snapshot)!=run.checksum: raise ValueError('输入快照校验失败')
            result=calculate(run.snapshot,run.parameters)
            result['input_sha256']=run.checksum
        except Exception as e:
            logging.exception('analysis failed')
            # Re-check the seal under lock before persisting any failure state.
            db.rollback();run=db.get(Run,id)
            if not run or _held_study(db,run) is None: db.rollback();return
            run.status='failed';run.error=str(e);db.commit();return
        # Late result: re-read under the study row lock. If the seal landed while
        # we were computing, discard the result and leave no artifact behind.
        db.rollback();run=db.get(Run,id)
        if not run or run.status=='destroyed': db.rollback();return
        if _held_study(db,run) is None:
            db.rollback();return
        run.result=result;run.status='completed'
        ARTIFACT_DIR.joinpath(f'run-{id}.json').write_text(json.dumps(result,ensure_ascii=False,allow_nan=False))
        db.commit()


def recover():
    ensure_schema(engine)
    ARTIFACT_DIR.mkdir(parents=True,exist_ok=True)
    # Recover jobs interrupted after dequeue. A project runs exactly one worker.
    with Session() as db:
        pending=list(db.scalars(select(Run).where(Run.status.in_(['queued','running']))))
        for run in pending: redis.rpush('analysis-queue',run.id)
        pending_tasks=list(db.scalars(select(destruction.DestructionTask).where(destruction.DestructionTask.status.in_(['pending','cleaning','failed']))))
        for task in pending_tasks: redis.rpush(destruction_service.QUEUE,task.id)


def run_forever():
    recover()
    while True:
        Path('/tmp/worker-heartbeat').touch()
        job=redis.blpop([destruction_service.QUEUE,'analysis-queue'],timeout=2)
        if not job: continue
        queue,raw=job
        try:
            id=int(raw)
            if queue.decode()==destruction_service.QUEUE:
                destruction_service.process(id)
            else:
                process_analysis(id)
        except Exception:
            logging.exception('job failed queue=%s id=%s',queue,raw)


if __name__=='__main__':
    run_forever()

"""End-to-end checks for authorization-withdrawal destruction.

Runs the real app, service layer and worker functions against a throwaway
SQLite database and fakeredis in a temp directory. No network, no Docker.

    python3 backend/tests/test_destruction_e2e.py

Test dependencies: fakeredis, httpx (see backend/requirements-dev.txt).
"""
import os,sys,json,tempfile
from pathlib import Path

BACKEND=Path(__file__).resolve().parents[1]
TMP=tempfile.mkdtemp(prefix='destruction-e2e-')
DB=Path(TMP)/'app.db'
ART=Path(TMP)/'artifacts';ART.mkdir()
os.environ['DATABASE_URL']=f'sqlite+pysqlite:///{DB}?check_same_thread=False'
os.environ['REDIS_URL']='redis://unused:6379/0'
os.environ['JWT_SECRET']='test-secret'
os.environ['ARTIFACT_DIR']=str(ART)
os.environ['SEED_DEMO']='false'
sys.path.insert(0,str(BACKEND))

import fakeredis
_server=fakeredis.FakeServer()
import redis
redis.Redis.from_url=lambda url,*a,**k: fakeredis.FakeStrictRedis(server=_server,decode_responses=False)

from fastapi.testclient import TestClient
from src.main import app
from src.config.database import Session,engine
from src.config.schema import ensure_schema
ensure_schema(engine)
from src.models.user import User
from src.models.study import Study
from src.models.observation import Observation
from src.models.run import Run
from src.models.destruction import DestructionTask
from src.utils.security import hash_password
from src.services.algorithm import calculate
from src.utils.serializers import checksum,now
from src.services import destruction as destruction_service
import src.worker  # noqa: F401  (import must not start the loop)
from sqlalchemy import select,func

PASS=[];FAIL=[]
def check(name,cond,extra=''):
    (PASS if cond else FAIL).append(name)
    print(('PASS ' if cond else 'FAIL ')+name+(' '+str(extra) if extra and not cond else ''))

def seed():
    with Session() as db:
        for role in ('admin','analyst','viewer'):
            db.add(User(email=role+'@demo.local',role=role,password_hash=hash_password('Research2026!')))
        db.commit()

def login(client,role):
    r=client.post('/auth/login',json={'email':role+'@demo.local','password':'Research2026!'})
    assert r.status_code==200,r.text
    return {'Authorization':'Bearer '+r.json()['token']}

def add_study(db,name):
    s=Study(name=name,source_filename='f.csv',description='');db.add(s);db.flush();return s

def add_rows(db,study,keys,base=5.0):
    for i,k in enumerate(keys):
        db.add(Observation(study_id=study.id,source_key=k,values={'moisture':base+i*.3,'extract':34+i}))
    db.commit()

def make_run(db,study,status='queued',with_result=True):
    rows=list(db.scalars(select(Observation).where(Observation.study_id==study.id)))
    snap=[{'source_key':r.source_key,'values':r.values} for r in rows]
    run=Run(study_id=study.id,name='run-'+study.name,parameters={'field':'moisture','resamples':200},snapshot=snap,checksum=checksum(snap),created_at=now(),status=status)
    if with_result and status=='completed':
        run.result=calculate(snap,run.parameters);run.result['input_sha256']=run.checksum
    db.add(run);db.commit()
    if run.result is not None:
        (ART/f'run-{run.id}.json').write_text(json.dumps(run.result,ensure_ascii=False))
    return run


seed()
client=TestClient(app)
A=login(client,'admin');AN=login(client,'analyst');V=login(client,'viewer')

with Session() as db:
    s1=add_study(db,'BATCH-A')
    s2=add_study(db,'BATCH-B')
    s3=add_study(db,'BATCH-C')
    add_rows(db,s1,['K1','K2','K3','OTHER'])
    add_rows(db,s2,['K1','K2','Z9'],base=7)
    add_rows(db,s3,['X1','X2','K1'],base=9)
    r1=make_run(db,s1,'completed')
    r2=make_run(db,s1,'running',with_result=False)
    r3=make_run(db,s1,'queued',with_result=False)
    r4=make_run(db,s2,'completed')
    r5=make_run(db,s3,'completed')
    ids=dict(s1=s1.id,s2=s2.id,s3=s3.id,r1=r1.id,r2=r2.id,r3=r3.id,r4=r4.id,r5=r5.id)

# --- 1. create: seals at once, de-dupes, rejects bad keys
r=client.post('/destructions',json={'study_id':ids['s1'],'source_keys':['K1','K2']},headers=AN)
check('analyst cannot initiate destruction',r.status_code==403,r.text)
r=client.post('/destructions',json={'study_id':ids['s1'],'source_keys':['K1','K2']},headers=A)
check('admin creates task',r.status_code==200 and r.json()['status']=='pending',r.text)
task_id=r.json()['id']
r2c=client.post('/destructions',json={'study_id':ids['s1'],'source_keys':['K2','K1','K1']},headers=A)
check('duplicate submission reuses task',r2c.status_code==200 and r2c.json()['id']==task_id,r2c.text)
check('only one task exists',len(client.get('/destructions',headers=A).json())==1)
r=client.post('/destructions',json={'study_id':ids['s3'],'source_keys':['NOPE']},headers=A)
check('unknown key rejected',r.status_code==422,r.text)
r=client.post('/destructions',json={'study_id':ids['s3'],'source_keys':['Z9']},headers=A)
check('key from another batch rejected',r.status_code==422,r.text)

# --- 2. seal: every entrance refuses immediately
check('sealed rows refused',client.get(f"/studies/{ids['s1']}/observations",headers=AN).status_code==423)
check('sealed row write refused',client.post(f"/studies/{ids['s1']}/observations",json={'source_key':'NEW','values':{'moisture':5,'extract':34}},headers=AN).status_code==423)
check('delete on sealed refused',client.delete(f"/studies/{ids['s1']}/observations/1",headers=AN).status_code==423)
check('new run on sealed refused',client.post('/runs',json={'study_id':ids['s1'],'name':'late','parameters':{}},headers=AN).status_code==423)
check('run detail sealed refused',client.get(f"/runs/{ids['r1']}",headers=A).status_code==423)
check('export sealed refused',client.get(f"/runs/{ids['r1']}/export",headers=A).status_code==423)
check('review on sealed refused',client.post(f"/runs/{ids['r1']}/review",json={'note':'late review'},headers=A).status_code==423)
runs=client.get('/runs',headers=A)
check('run list omits sealed values',runs.status_code==200 and all(x['sealed'] and x['result'] is None and x['snapshot']==[] for x in runs.json() if x['study_id']==ids['s1']),runs.text)
st=client.get('/studies',headers=A).json()
sealed=[x for x in st if x['id']==ids['s1']][0]
check('sealed study exposes no row count',sealed['sealed'] and sealed['row_count'] is None,sealed)
check('unaffected studies still readable',client.get(f"/studies/{ids['s3']}/observations",headers=A).status_code==200)
ov=client.get('/overview',headers=A)
check('overview works after seal',ov.status_code==200 and all(x['result'] is None and x['snapshot']==[] for x in ov.json()['runs'] if x['study_id']==ids['s1']),ov.text[:300])

# --- 3. late computation cannot write back
src.worker.process_analysis(ids['r2'])
src.worker.process_analysis(ids['r3'])
with Session() as db:
    late2=db.get(Run,ids['r2']);late3=db.get(Run,ids['r3'])
    check('late running job no writeback',late2.result is None,late2.result)
    check('queued job after seal no writeback',late3.result is None and late3.status!='completed',late3.status)
check('late job left no artifact',not (ART/f"run-{ids['r2']}.json").exists() and not (ART/f"run-{ids['r3']}.json").exists())

# review/audit archives are shared copies too: add a review before the failure case
rid_review=ids['r1']
with Session() as db:
    from src.models.review import Review
    from src.models.audit import Audit
    db.add(Review(run_id=rid_review,note='含水率中位数约5.3，建议复测K1',author='admin@demo.local',created_at=now()))
    db.add(Audit(actor='admin@demo.local',action='review.approve',detail=f'{rid_review}: 含水率中位数约5.3',created_at=now()))
    db.commit()

# --- 4. cleanup failure then resume, without touching same-key other batches
class Boom(Path):
    _flavour=type(Path())._flavour
    def unlink(self,*a,**k): raise OSError('disk busy')
_real_artifactdir=destruction_service.ARTIFACT_DIR
destruction_service.ARTIFACT_DIR=Boom(str(ART))
r=client.post(f'/destructions/{task_id}/retry',headers=A)  # pending -> pending (allowed)
check('retry on pending allowed',r.status_code==200,r.text)
try:
    destruction_service.process(task_id)
    check('failed cleanup recorded',True)
except Exception as e:
    check('failed cleanup recorded',False,e)
with Session() as db:
    t=db.get(DestructionTask,task_id)
    check('task failed on artifact delete',t.status=='failed' and 'disk busy' in (t.error or ''),t.status)
    check('source rows already erased before failure',db.scalar(select(func.count()).select_from(Observation).where(Observation.study_id==ids['s1'],Observation.source_key.in_(['K1','K2'])))==0)
    check('other batch same keys preserved after failure',db.scalar(select(func.count()).select_from(Observation).where(Observation.study_id==ids['s2'],Observation.source_key.in_(['K1','K2'])))==2)
    check('third batch K1 preserved',db.scalar(select(func.count()).select_from(Observation).where(Observation.study_id==ids['s3'],Observation.source_key=='K1'))==1)
destruction_service.ARTIFACT_DIR=_real_artifactdir
tasks_before=len(client.get('/destructions',headers=A).json())
r=client.post(f'/destructions/{task_id}/retry',headers=A)
check('retry resumes failed task',r.status_code==200 and r.json()['status']=='pending',r.text)
check('retry adds no task',len(client.get('/destructions',headers=A).json())==tasks_before)
r=client.post(f'/destructions/{task_id}/retry',headers=AN)
check('analyst cannot retry',r.status_code==403)
destruction_service.process(task_id)

with Session() as db:
    t=db.get(DestructionTask,task_id)
    cert=t.certificate
    check('task completed',t.status=='completed' and cert and cert['checks']['all_cleared'],t.status)
    check('history tracks pending->cleaning->failed->pending->cleaning->completed',
          [h['status'] for h in t.history]==['pending','cleaning','failed','pending','cleaning','completed'],[h['status'] for h in t.history])
    check('no locating keys retained in task',t.source_keys==[])
    blob=json.dumps(cert,ensure_ascii=False)
    check('certificate has no original values',('5.0' not in blob and '5.3' not in blob and 'moisture' not in blob and 'K1' not in blob and 'K2' not in blob),blob[:300])
    check('certificate records counts',cert['source_observations_removed']==2 and cert['source_key_count']==2,cert)
    check('review archive removed',cert['reviews_removed']==1 and cert['checks']['reviews_remaining']==[],cert)
    check('review audit note redacted',cert['review_audits_redacted']==1 and not db.scalar(select(Audit).where(Audit.action=='review.approve',Audit.detail.like('%5.3%'))))
    check('affected artifact deleted, unaffected kept',not (ART/f"run-{ids['r1']}.json").exists() and (ART/f"run-{ids['r4']}.json").exists() and (ART/f"run-{ids['r5']}.json").exists())
    check('unaffected completed run intact',db.get(Run,ids['r4']).result is not None and db.get(Run,ids['r5']).result is not None)

# --- 5. affected study redacted, unaffected usable
client2=TestClient(app)
r=client.get(f'/runs/{ids["r4"]}/export',headers=A)
check('unaffected export still works',r.status_code==200 and 'text/csv' in r.headers['content-type'],r.status_code)
with Session() as db:
    s1=db.get(Study,ids['s1'])
    check('study sealed marker persisted',bool(s1.sealed_at) and s1.destruction_id==task_id)
    remaining=list(db.scalars(select(Observation).where(Observation.study_id==ids['s1'])))
    check('only non-target rows survive', [x.source_key for x in remaining]==['K3','OTHER'],[x.source_key for x in remaining])
    snap=r1v=db.get(Run,ids['r1']).snapshot
    check('frozen snapshot dropped revoked rows and kept rest',[x['source_key'] for x in snap]==['K3','OTHER'],snap)
    check('run marked destroyed',db.get(Run,ids['r1']).status=='destroyed')
r=client.post('/runs',json={'study_id':ids['s3'],'name':'ok run','parameters':{}},headers=AN)
check('unaffected batch can still queue runs',r.status_code==200,r.text)
r=client.post(f'/destructions/{task_id}/retry',headers=A)
check('completed task retry is no-op',r.status_code==200 and r.json()['status']=='completed')
r=client.get(f'/destructions/{task_id}',headers=V)
check('viewer can read certificate',r.status_code==200 and r.json()['certificate']['checks']['all_cleared'],r.text)

print(f'\nRESULT {len(PASS)} passed, {len(FAIL)} failed; workspace {TMP}')
if FAIL:
    print('FAILED:',FAIL);sys.exit(1)

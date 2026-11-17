from sqlalchemy import Column,Integer,String,Text,JSON,ForeignKey
from src.config.database import Base


class DestructionTask(Base):
    """批次授权撤销后的销毁任务。

    源记录编号仅作为定位键保留，任务与凭据中不保存任何观测原值。
    """
    __tablename__='destructions'
    id=Column(Integer,primary_key=True)
    study_id=Column(Integer,ForeignKey('studies.id'),nullable=False,index=True)
    source_keys=Column(JSON,nullable=False)
    target_count=Column(Integer,nullable=False,default=0)
    prior_run_states=Column(JSON,nullable=True)
    prior_review_ids=Column(JSON,nullable=True)
    fingerprint=Column(String(64),unique=True,nullable=False)
    status=Column(String(20),nullable=False,default='pending')
    error=Column(Text,default='')
    certificate=Column(JSON,nullable=True)
    history=Column(JSON,default=list)
    initiated_by=Column(String(120),nullable=False)
    created_at=Column(String(40),nullable=False)
    updated_at=Column(String(40),nullable=False)
    completed_at=Column(String(40),nullable=True)

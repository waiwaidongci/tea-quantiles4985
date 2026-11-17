from fastapi import APIRouter,Depends
from src.config.database import get_db
from src.middlewares.auth import current_user,admin
from src.schemas.destruction import DestructionInput
from src.services import destruction
router=APIRouter()


@router.post('/destructions')
def create(data:DestructionInput,db=Depends(get_db),user=Depends(admin)):
    return destruction.create(db,data,user)


@router.get('/destructions')
def list_tasks(db=Depends(get_db),user=Depends(current_user)):
    return destruction.list_tasks(db)


@router.get('/destructions/{id}')
def get(id:int,db=Depends(get_db),user=Depends(current_user)):
    return destruction.get(db,id)


@router.post('/destructions/{id}/retry')
def retry(id:int,db=Depends(get_db),user=Depends(admin)):
    return destruction.retry(db,id,user)

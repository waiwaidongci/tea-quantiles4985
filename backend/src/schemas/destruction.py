from pydantic import BaseModel,Field
class DestructionInput(BaseModel):
    study_id:int=Field(gt=0)
    source_keys:list[str]=Field(min_length=1,max_length=5000)

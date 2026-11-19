from pydantic import BaseModel, Field
from typing import Optional


class CorruptInput(BaseModel):
    copy_id: int


class RewriteInput(BaseModel):
    page_key: str = Field(min_length=1, max_length=80)


class RepairStartInput(BaseModel):
    copy_id: int
    repair_key: Optional[str] = Field(default=None, max_length=64)


class RepairAdvanceInput(BaseModel):
    simulate_interrupt: bool = False


class ProbeInput(BaseModel):
    page_key: str = Field(min_length=1, max_length=80)
    node_id: int

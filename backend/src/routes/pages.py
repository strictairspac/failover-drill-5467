from fastapi import APIRouter, Depends
from src.config.database import get_db
from src.middlewares.auth import current_user, writable
from src.schemas.page import (CorruptInput, RewriteInput, RepairStartInput,
                              RepairAdvanceInput, ProbeInput)
from src.services import page_service
from src.controllers.actions import audit

router = APIRouter()


@router.get('/pages')
def copies(db=Depends(get_db), user=Depends(current_user)):
    return page_service.list_copies(db)


@router.get('/pages/health')
def health(db=Depends(get_db), user=Depends(current_user)):
    return page_service.node_page_health(db)


@router.post('/pages/corrupt', status_code=201)
def corrupt(data: CorruptInput, db=Depends(get_db), user=Depends(writable)):
    out = page_service.corrupt_page(db, data.copy_id)
    audit(db, user, f'page:corrupt:{data.copy_id}')
    db.commit()
    return out


@router.post('/pages/rewrite')
def rewrite(data: RewriteInput, db=Depends(get_db), user=Depends(writable)):
    out = page_service.rewrite_page(db, data.page_key)
    audit(db, user, f'page:rewrite:{data.page_key}')
    db.commit()
    return out


@router.post('/pages/repair', status_code=201)
def start_repair(data: RepairStartInput, db=Depends(get_db), user=Depends(writable)):
    job, created = page_service.start_repair(db, data.copy_id, data.repair_key)
    audit(db, user, f'page:repair:start:{job["id"]}')
    db.commit()
    return {'job': job, 'created': created}


@router.post('/pages/repair/{job_id}/advance')
def advance(job_id: int, data: RepairAdvanceInput, db=Depends(get_db),
            user=Depends(writable)):
    job, event = page_service.advance_repair(db, job_id, data.simulate_interrupt)
    audit(db, user, f'page:repair:advance:{job_id}:{event}')
    db.commit()
    return {'job': job, 'event': event}


@router.get('/pages/repair')
def repair_jobs(db=Depends(get_db), user=Depends(current_user)):
    return page_service.list_jobs(db)


@router.post('/pages/probe')
def probe(data: ProbeInput, db=Depends(get_db), user=Depends(current_user)):
    return page_service.probe_read(db, data.page_key, data.node_id)

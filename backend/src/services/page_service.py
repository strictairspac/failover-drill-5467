"""坏页/补页沙盘模型。

口径（均为受控演练计算，不触碰真实数据文件）：
- 每个节点保存逻辑页的一个物理副本，带版本号与校验和；校验和不匹配即坏页。
- 坏页停读：读取坏页返回阻断原因；其他页、其他节点照常读写。
- 补页只能采用校验通过、且与坏页登记版本相同的副本；同版本无健康副本则阻断。
- 整页重写产生新版本，补页写盘用版本 CAS：已写成的新版不能被旧页盖掉。
- 同一次补页以 repair_key 幂等：写盘中断可恢复，重试不重复发布。
"""
import hashlib
import uuid
from sqlalchemy import select
from fastapi import HTTPException
from src.models.entities import PageCopy, RepairJob, Node
from src.utils.serialize import record

PAGE_KEYS = ('page-orders', 'page-stock', 'page-ledger')


def checksum_of(page_key, version):
    return hashlib.sha256(f'page|{page_key}|v{version}'.encode()).hexdigest()


def is_healthy(copy):
    return copy.checksum == checksum_of(copy.page_key, copy.version)


def _lock_rows(db, copies):
    """行锁；SQLite 等不支持 SELECT ... FOR UPDATE 的方言降级为普通事务。"""
    try:
        for c in copies:
            db.execute(select(PageCopy).where(PageCopy.id == c.id).with_for_update())
    except Exception:
        pass


def _copy_view(db, copy):
    node = db.get(Node, copy.node_id)
    out = record(copy)
    out['node_name'] = node.name if node else str(copy.node_id)
    out['healthy'] = is_healthy(copy)
    return out


def list_copies(db):
    rows = db.scalars(select(PageCopy).order_by(PageCopy.page_key, PageCopy.node_id)).all()
    return [_copy_view(db, c) for c in rows]


def _get_copy(db, copy_id):
    copy = db.get(PageCopy, copy_id)
    if copy is None:
        raise HTTPException(404, '页面副本不存在')
    return copy


def corrupt_page(db, copy_id):
    """制造坏页：破坏校验和但保留版本号（日志追平也可能遇到的物理坏页）。"""
    copy = _get_copy(db, copy_id)
    if not is_healthy(copy):
        raise HTTPException(409, '该副本已是坏页，无需重复制造')
    copy.checksum = 'corrupt-' + uuid.uuid4().hex[:24]
    db.flush()
    return _copy_view(db, copy)


def rewrite_page(db, page_key):
    """整页重写：所有健康副本原子推进到下一版本；坏页停写、版本原地保留。"""
    copies = db.scalars(select(PageCopy).where(PageCopy.page_key == page_key)
                        .order_by(PageCopy.id)).all()
    if not copies:
        raise HTTPException(404, '逻辑页不存在')
    _lock_rows(db, copies)
    copies = db.scalars(select(PageCopy).where(PageCopy.page_key == page_key)
                        .order_by(PageCopy.id)).all()
    next_version = max(c.version for c in copies) + 1
    advanced, skipped = [], []
    for c in copies:
        if is_healthy(c):
            c.version = next_version
            c.checksum = checksum_of(page_key, next_version)
            advanced.append(c.id)
        else:
            skipped.append(c.id)
    db.flush()
    return {'page_key': page_key, 'next_version': next_version,
            'advanced_copy_ids': advanced, 'skipped_bad_copy_ids': skipped}


def _find_donor(db, target):
    """选源：同 page_key、同版本、校验通过，且不能是目标自己。按节点名确定可复现顺序。"""
    candidates = db.scalars(select(PageCopy).where(
        PageCopy.page_key == target.page_key,
        PageCopy.version == target.version,
        PageCopy.id != target.id)).all()
    healthy = [c for c in candidates if is_healthy(c)]
    healthy.sort(key=lambda c: (db.get(Node, c.node_id).name, c.id))
    return healthy[0] if healthy else None


def start_repair(db, copy_id, repair_key=None):
    """登记一次补页。同 key 重试直接返回原任务，不产生第二条记录。"""
    target = _get_copy(db, copy_id)
    if is_healthy(target):
        raise HTTPException(409, '该副本校验通过，不是坏页，无需补页')
    key = repair_key or f'repair-{uuid.uuid4().hex}'
    existing = db.scalar(select(RepairJob).where(RepairJob.repair_key == key))
    if existing:
        if existing.target_copy_id != target.id:
            raise HTTPException(409, '同一补页键指向了不同的坏页，拒绝复用')
        return record(existing), False
    job = RepairJob(repair_key=key, target_copy_id=target.id, target_version=target.version,
                    status='queued', publish_count=0, attempts=0)
    db.add(job)
    db.flush()
    return record(job), True


def advance_repair(db, job_id, simulate_interrupt=False):
    """推进补页一个状态步。整个过程在一个事务内，中断即回滚，重试不重复发布。"""
    job = db.get(RepairJob, job_id)
    if job is None:
        raise HTTPException(404, '补页任务不存在')
    job.attempts += 1
    target = _get_copy(db, job.target_copy_id)
    # 已发布完成：幂等返回，绝不二次发布
    if job.status == 'healed':
        return record(job), 'already_healed'
    # 目标已被整页重写推进（且健康），坏页事实已消失，任务关闭但不计本次发布
    if is_healthy(target) and target.version != job.target_version:
        job.status = 'healed'
        job.blocked_reason = '整页重写已推进该副本，补页任务关闭'
        db.flush()
        return record(job), 'superseded_by_rewrite'
    # 选源：必须同版本且校验通过
    donor = _find_donor(db, target)
    if donor is None:
        job.status = 'blocked'
        job.donor_copy_id = None
        job.blocked_reason = (f"逻辑页 {target.page_key} 版本 v{target.version} "
                              '不存在校验通过的同版本副本，无法补页')
        db.flush()
        return record(job), 'no_donor'
    job.donor_copy_id = donor.id
    job.blocked_reason = None
    if simulate_interrupt:
        # 模拟写盘中断：停在写盘前，不发布、不计发布次数；页面保持坏页，可幂等重试。
        job.status = 'donor_ready'
        job.blocked_reason = '写盘中断，副本尚未改写；重试同次补页即可继续'
        db.flush()
        return record(job), 'interrupted'
    job.status = 'applying'
    db.flush()
    # 发布前 CAS 校验：目标版本若已被整页重写推进，旧页不得覆盖新版
    fresh = db.get(PageCopy, target.id)
    if fresh.version != job.target_version:
        job.status = 'blocked'
        job.blocked_reason = (f'并发冲突：目标已推进到 v{fresh.version}，'
                              f'拒绝用 v{job.target_version} 旧页覆盖')
        db.flush()
        return record(job), 'version_conflict'
    if not is_healthy(donor) or donor.version != job.target_version:
        # 选源后副本被制造坏页/重写：本次放弃，下次重试重新选源
        job.status = 'donor_ready'
        job.blocked_reason = '选定副本在写盘前失效，请重试重新选源'
        db.flush()
        return record(job), 'donor_invalidated'
    fresh.checksum = donor.checksum
    fresh.version = donor.version
    job.status = 'healed'
    job.publish_count += 1
    job.blocked_reason = None
    db.flush()
    return record(job), 'healed'


def list_jobs(db):
    jobs = db.scalars(select(RepairJob).order_by(RepairJob.id.desc())).all()
    out = []
    for j in jobs:
        item = record(j)
        target = db.get(PageCopy, j.target_copy_id)
        donor = db.get(PageCopy, j.donor_copy_id) if j.donor_copy_id else None
        item['target'] = _copy_view(db, target) if target else None
        item['donor'] = _copy_view(db, donor) if donor else None
        out.append(item)
    return out


def probe_read(db, page_key, node_id):
    """读探测：坏页停读并给出原因；健康页返回读到的版本与校验状态。"""
    node = db.get(Node, node_id)
    if node is None:
        raise HTTPException(404, '节点不存在')
    copy = db.scalar(select(PageCopy).where(PageCopy.node_id == node_id,
                                            PageCopy.page_key == page_key))
    if copy is None:
        raise HTTPException(404, '该节点上不存在此逻辑页副本')
    view = _copy_view(db, copy)
    if not view['healthy']:
        view['readable'] = False
        view['reason'] = f"坏页停读：{node.name} 的 {page_key} 校验和不匹配，需先完成同版本补页"
    else:
        view['readable'] = True
        view['reason'] = f"读取成功：版本 v{copy.version}，校验通过"
    return view


def node_page_health(db):
    """节点级汇总：供切主候选判断坏页是否已修好。"""
    nodes = db.scalars(select(Node).order_by(Node.id)).all()
    copies = list_copies(db)
    summary = []
    for n in nodes:
        owned = [c for c in copies if c['node_id'] == n.id]
        bad = [c for c in owned if not c['healthy']]
        summary.append({'node_id': n.id, 'node_name': n.name,
                        'page_total': len(owned), 'bad_pages': len(bad),
                        'bad_page_keys': [c['page_key'] for c in bad],
                        'page_ready': len(bad) == 0})
    return summary

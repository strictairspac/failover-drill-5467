"""跨会话并发交错测试：模拟两个数据库会话同时操作同一页。

验证：
- 会话 A 发起补页后，会话 B 先完成整页重写；A 发布时 CAS 拒绝旧页覆盖新版
- 同 repair_key 在不同会话登记只产生一条任务、一次发布
"""
import os, sys, tempfile
fd, path = tempfile.mkstemp(suffix='.db'); os.close(fd)
os.environ['DATABASE_URL'] = f'sqlite:///{path}'
os.environ['JWT_SECRET'] = 'x'
sys.path.insert(0, '/workspace/backend'); sys.path.insert(0, '/tmp/pylibs')

from sqlalchemy import select
from src.config.database import Base, engine, Session
from src.models.entities import Node, PageCopy
from src.services import page_service as ps
import scripts.seed_demo as seed_demo

Base.metadata.create_all(engine)
seed_demo.seed()

with Session() as db:
    east = db.scalar(select(Node).where(Node.name == 'hs-east'))
    west = db.scalar(select(Node).where(Node.name == 'hs-west'))
    east_copy = db.scalar(select(PageCopy).where(PageCopy.node_id == east.id,
                                                  PageCopy.page_key == 'page-ledger'))
    west_copy = db.scalar(select(PageCopy).where(PageCopy.node_id == west.id,
                                                  PageCopy.page_key == 'page-ledger'))
    east_id, west_id, key = east_copy.id, west_copy.id, 'page-ledger'

# 会话 A：制造坏页并登记补页（v1）
dbA = Session()
t = dbA.get(PageCopy, east_id)
t.checksum = 'corrupt-concurrency-0001'
job, created = ps.start_repair(dbA, east_id, repair_key='conc-001')
assert created and job['status'] == 'queued'
dbA.commit(); dbA.close()

# 会话 B：整页重写先行，健康副本全部推进 v2，坏页停在 v1
dbB = Session()
out = ps.rewrite_page(dbB, key)
assert out['next_version'] == 2 and east_id in out['skipped_bad_copy_ids']
dbB.commit(); dbB.close()

# 会话 A 重试推进：不得用 v1 donor 盖掉 v2 新版
dbA = Session()
job, event = ps.advance_repair(dbA, job['id'])
assert event == 'no_donor', event  # v1 健康 donor 已随重写消失
assert job['status'] == 'blocked' and job['publish_count'] == 0
dbA.commit(); dbA.close()

with Session() as db:
    t = db.get(PageCopy, east_id)
    w = db.get(PageCopy, west_id)
    assert t.version == 1 and not ps.is_healthy(t)
    assert w.version == 2 and ps.is_healthy(w), 'west 新版必须完好'

# 归档找回 v1 donor 后，同任务继续；期间另起会话用同 key 登记不产生第二条
dbC = Session()
dup, created = ps.start_repair(dbC, east_id, repair_key='conc-001')
assert created is False and dup['id'] == job['id']
dbC.rollback(); dbC.close()

dbB = Session()
w = dbB.get(PageCopy, west_id)
w.version, w.checksum = 1, ps.checksum_of(key, 1)
dbB.commit(); dbB.close()

dbA = Session()
job, event = ps.advance_repair(dbA, job['id'])
assert event == 'healed' and job['publish_count'] == 1, (event, job)
dbA.commit(); dbA.close()

# 再推进幂等：不二次发布
dbA = Session()
job, event = ps.advance_repair(dbA, job['id'])
assert event == 'already_healed' and job['publish_count'] == 1
dbA.rollback(); dbA.close()

with Session() as db:
    jobs = db.scalars(select(ps.RepairJob)).all()
    assert len(jobs) == 1 and jobs[0].repair_key == 'conc-001'
    t = db.get(PageCopy, east_id)
    assert ps.is_healthy(t) and t.version == 1
print('跨会话并发交错测试全部通过：CAS 防覆盖、同键幂等、单次发布')

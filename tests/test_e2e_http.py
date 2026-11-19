"""端到端 HTTP 语义冒烟（TestClient，不依赖 Redis/MySQL）。"""
import os, sys, tempfile
fd, path = tempfile.mkstemp(suffix='.db'); os.close(fd)
os.environ['DATABASE_URL'] = f'sqlite:///{path}'
os.environ['JWT_SECRET'] = 'e2e'
sys.path.insert(0, '/workspace/backend'); sys.path.insert(0, '/tmp/pylibs')

from fastapi.testclient import TestClient
from src.main import app
import scripts.seed_demo as seed_demo
seed_demo.seed()
c = TestClient(app)

def token(role):
    return c.post('/auth/login', json={'email': f'{role}@hengshi.local', 'password': 'Drill2026!'}).json()['access_token']
op = {'Authorization': 'Bearer ' + token('operator')}
vw = {'Authorization': 'Bearer ' + token('viewer')}

# 未认证 401
assert c.get('/pages').status_code == 401
# 观察员只读
assert c.get('/pages', headers=vw).status_code == 200
assert c.post('/pages/rewrite', json={'page_key': 'page-stock'}, headers=vw).status_code == 403
# 初始 9 个健康副本
pages = c.get('/pages', headers=op).json()
assert len(pages) == 9 and all(p['healthy'] for p in pages), pages
# 制造坏页 -> 停读
target = next(p for p in pages if p['node_name'] == 'hs-east' and p['page_key'] == 'page-stock')
r = c.post('/pages/corrupt', json={'copy_id': target['id']}, headers=op)
assert r.status_code == 201 and r.json()['healthy'] is False
probe = c.post('/pages/probe', json={'page_key': 'page-stock', 'node_id': target['node_id']}, headers=op).json()
assert probe['readable'] is False and '坏页停读' in probe['reason']
# 整页重写跳过坏页
rw = c.post('/pages/rewrite', json={'page_key': 'page-stock'}, headers=op).json()
assert rw['next_version'] == 2 and target['id'] in rw['skipped_bad_copy_ids']
# 补页：同版本 donor 被重写带走 -> blocked；中断重试幂等
job = c.post('/pages/repair', json={'copy_id': target['id'], 'repair_key': 'e2e-key'}, headers=op).json()['job']
again = c.post('/pages/repair', json={'copy_id': target['id'], 'repair_key': 'e2e-key'}, headers=op).json()
assert again['created'] is False and again['job']['id'] == job['id']
ev = c.post(f"/pages/repair/{job['id']}/advance", json={}, headers=op).json()
assert ev['event'] == 'no_donor' and ev['job']['status'] == 'blocked'
# 演练入队：坏页阻断切主
d = c.post('/drills', json={'scenario_id': 1, 'label': 'e2e 坏页演练'}, headers=op)
assert d.status_code == 202
snap = d.json()['snapshot']
assert any(not cp['healthy'] for cp in snap['page_copies'])
# 健康摘要
h = c.get('/pages/health', headers=op).json()
east = next(x for x in h if x['node_name'] == 'hs-east')
assert east['bad_pages'] == 1 and east['page_ready'] is False
# 审计落库
audits = c.get('/audits', headers={'Authorization': 'Bearer ' + token('admin')}).json()
actions = {a['action'] for a in audits}
assert {'page:corrupt:' + str(target['id']), 'page:rewrite:page-stock'}.issubset(actions)
print('E2E HTTP 冒烟全部通过：', sorted(actions)[:6], '...')

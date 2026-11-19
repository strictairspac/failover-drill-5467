"""坏页/补页沙盘后端集成测试（SQLite 内存库 + FastAPI TestClient）。

覆盖：
1. 制造坏页后该页停读、其他页/其他节点照常读写
2. 补页只能选校验通过的同版本副本；无同版本健康副本时 blocked
3. 写盘中断可恢复，同 repair_key 重试不重复发布（publish_count 恒为 1）
4. 补页与整页重写并发：CAS 阻止旧页覆盖已写成的新版
5. 坏页未修好的备库不能当选；补好后沿用原切主规则恢复候选资格
6. 入队快照冻结坏页状态，worker 计算可复现
"""
import os
import sys
import tempfile

DB_FD, DB_PATH = tempfile.mkstemp(suffix='.db')
os.close(DB_FD)
os.environ['DATABASE_URL'] = f'sqlite:///{DB_PATH}'
os.environ['JWT_SECRET'] = 'test-secret'
os.environ['SEED_DEMO'] = 'true'
os.environ.setdefault('REDIS_URL', 'redis://localhost:6379/0')

sys.path.insert(0, '/workspace/backend')
sys.path.insert(0, '/tmp/pylibs')

from fastapi.testclient import TestClient
from sqlalchemy import select

from src.main import app
from src.config.database import Session
from src.models.entities import Node, PageCopy, RepairJob
from src.services.page_service import checksum_of, is_healthy
from src.services.election import elect
import scripts.seed_demo as seed_demo

seed_demo.seed()
client = TestClient(app)
PASS, FAILS = 0, []


def check(name, cond, detail=''):
    global PASS
    if cond:
        PASS += 1
        print(f'  PASS  {name}')
    else:
        FAILS.append((name, detail))
        print(f'  FAIL  {name}  {detail}')


def login(role):
    r = client.post('/auth/login', json={'email': f'{role}@hengshi.local', 'password': 'Drill2026!'})
    return r.json()['access_token']


auth = {'Authorization': 'Bearer ' + login('operator')}
viewer_auth = {'Authorization': 'Bearer ' + login('viewer')}

with Session() as db:
    nodes = {n.name: n for n in db.scalars(select(Node)).all()}
    east_id, west_id, primary_id = (nodes['hs-east'].id, nodes['hs-west'].id, nodes['hs-primary'].id)
    def copy(node_id, key):
        return db.scalar(select(PageCopy).where(PageCopy.node_id == node_id,
                                                PageCopy.page_key == key))
    target = copy(east_id, 'page-orders')
    target_id = target.id
    west_orders = copy(west_id, 'page-orders')
    west_orders_id = west_orders.id

print('\n[1] 制造坏页：坏页停读，其他页/其他节点照常读写')
r = client.post('/pages/corrupt', json={'copy_id': target_id}, headers=auth)
check('制造坏页返回 201', r.status_code == 201, r.text)
check('坏页校验和失配', r.json()['healthy'] is False)
r = client.post('/pages/corrupt', json={'copy_id': target_id}, headers=auth)
check('重复制造坏页被 409 拒绝', r.status_code == 409)
r = client.post('/pages/probe', json={'page_key': 'page-orders', 'node_id': east_id}, headers=auth)
check('坏页读探测被停读', r.json()['readable'] is False and '坏页停读' in r.json()['reason'], r.text)
r = client.post('/pages/probe', json={'page_key': 'page-stock', 'node_id': east_id}, headers=auth)
check('同节点其他页照常读', r.json()['readable'] is True and r.json()['version'] == 1)
r = client.post('/pages/probe', json={'page_key': 'page-orders', 'node_id': west_id}, headers=auth)
check('其他节点同页照常读', r.json()['readable'] is True)
r = client.post('/pages/corrupt', json={'copy_id': target_id}, headers=viewer_auth)
check('观察员无写权限（403）', r.status_code == 403)

print('\n[2] 整页重写：健康副本推进，坏页停写且版本原地保留')
r = client.post('/pages/rewrite', json={'page_key': 'page-orders'}, headers=auth)
check('重写返回 200', r.status_code == 200, r.text)
body = r.json()
check('健康副本推进到 v2', body['next_version'] == 2 and target_id not in body['advanced_copy_ids'])
check('坏页被跳过不推进', body['skipped_bad_copy_ids'] == [target_id], str(body))
with Session() as db:
    t = db.get(PageCopy, target_id)
    check('坏页版本仍停在 v1', t.version == 1 and not is_healthy(t))
    w = db.get(PageCopy, west_orders_id)
    check('健康副本已是 v2 且校验通过', w.version == 2 and is_healthy(w))

print('\n[3] 无同版本健康副本：补页阻断并给出原因')
r = client.post('/pages/repair', json={'copy_id': target_id}, headers=auth)
check('登记补页 201', r.status_code == 201, r.text)
job_id = r.json()['job']['id']
check('补页键已生成', bool(r.json()['job']['repair_key']))
r = client.post(f'/pages/repair/{job_id}/advance', json={'simulate_interrupt': False}, headers=auth)
check('无同版本 donor 时 blocked', r.json()['event'] == 'no_donor' and r.json()['job']['status'] == 'blocked', r.text)
check('阻断原因可读', '同版本副本' in r.json()['job']['blocked_reason'])

print('\n[4] 制造同版本健康副本后补页成功，且只能补同版本')
# 把 west 的 page-orders 再做坏，再重写不行——改为：制造一个 v1 健康副本：
# 在 west 上先把它改回 v1 健康（模拟从归档找回同版本副本）
with Session() as db:
    w = db.get(PageCopy, west_orders_id)
    w.version = 1
    w.checksum = checksum_of('page-orders', 1)
    db.commit()
r = client.post(f'/pages/repair/{job_id}/advance', json={}, headers=auth)
check('出现同版本 donor 后补页 healed', r.json()['event'] == 'healed' and r.json()['job']['status'] == 'healed', r.text)
check('发布次数为 1', r.json()['job']['publish_count'] == 1)
with Session() as db:
    t = db.get(PageCopy, target_id)
    check('目标页恢复健康 v1', is_healthy(t) and t.version == 1)
r = client.post('/pages/probe', json={'page_key': 'page-orders', 'node_id': east_id}, headers=auth)
check('补好后恢复可读', r.json()['readable'] is True)

print('\n[5] 写盘中断可恢复：同 repair_key 重试不重复发布')
# 重新制造坏页，走中断 -> 重试流程
r = client.post('/pages/corrupt', json={'copy_id': target_id}, headers=auth)
r = client.post('/pages/repair', json={'copy_id': target_id, 'repair_key': 'drill-key-001'}, headers=auth)
check('首次登记 created=true', r.json()['created'] is True)
job2 = r.json()['job']['id']
r = client.post(f'/pages/repair/{job2}/advance', json={'simulate_interrupt': True}, headers=auth)
check('中断停留 donor_ready', r.json()['event'] == 'interrupted' and r.json()['job']['status'] == 'donor_ready', r.text)
check('中断时发布次数为 0', r.json()['job']['publish_count'] == 0)
with Session() as db:
    check('中断后页面仍是坏页', not is_healthy(db.get(PageCopy, target_id)))
# 同 key 重复登记：幂等返回同一任务
r = client.post('/pages/repair', json={'copy_id': target_id, 'repair_key': 'drill-key-001'}, headers=auth)
check('同键重试不新建任务', r.json()['created'] is False and r.json()['job']['id'] == job2)
r = client.post(f'/pages/repair/{job2}/advance', json={}, headers=auth)
check('重试后 healed', r.json()['event'] == 'healed' and r.json()['job']['status'] == 'healed')
check('只发布一次 publish_count=1', r.json()['job']['publish_count'] == 1)
r = client.post(f'/pages/repair/{job2}/advance', json={}, headers=auth)
check('已完成任务再推进幂等', r.json()['event'] == 'already_healed' and r.json()['job']['publish_count'] == 1)

print('\n[6] 补页与整页重写并发：新版不能被旧页盖掉')
# 干净基线：所有副本 v2 健康
with Session() as db:
    for c in db.scalars(select(PageCopy)).all():
        c.version = 2
        c.checksum = checksum_of(c.page_key, 2)
    db.commit()
# 场景 A：east v2 造坏；整页重写先把健康副本推进 v3，坏页被跳过
r = client.post('/pages/corrupt', json={'copy_id': target_id}, headers=auth)
r = client.post('/pages/rewrite', json={'page_key': 'page-orders'}, headers=auth)
check('并发重写推进到 v3，坏页跳过', r.json()['next_version'] == 3 and target_id in r.json()['skipped_bad_copy_ids'], r.text)
r = client.post('/pages/repair', json={'copy_id': target_id}, headers=auth)
job3 = r.json()['job']['id']
r = client.post(f'/pages/repair/{job3}/advance', json={}, headers=auth)
check('无 v2 donor 时阻断，v3 新版不被旧页盖掉', r.json()['event'] == 'no_donor' and r.json()['job']['status'] == 'blocked', r.text)
with Session() as db:
    t = db.get(PageCopy, target_id)
    check('目标仍是坏页 v2', t.version == 2 and not is_healthy(t))
    w = db.get(PageCopy, west_orders_id)
    check('west 新版 v3 完好未被覆盖', w.version == 3 and is_healthy(w))
# 归档找回同版本 v2 donor 后，同次补页可继续并成功
with Session() as db:
    w = db.get(PageCopy, west_orders_id)
    w.version = 2
    w.checksum = checksum_of('page-orders', 2)
    db.commit()
r = client.post(f'/pages/repair/{job3}/advance', json={}, headers=auth)
check('同版本 donor 恢复后补页成功', r.json()['event'] == 'healed' and r.json()['job']['status'] == 'healed')
with Session() as db:
    check('目标恢复为健康 v2（未伪造版本）', is_healthy(db.get(PageCopy, target_id)) and db.get(PageCopy, target_id).version == 2)

# 场景 B：选源后、写盘前目标被并发推进到 v3 —— CAS 必须拒绝 v2 旧页发布
with Session() as db:
    t = db.get(PageCopy, target_id)
    t.version = 2
    t.checksum = 'corrupt-cas-case-000000000000000001'
    w = db.get(PageCopy, west_orders_id)
    w.version = 2
    w.checksum = checksum_of('page-orders', 2)
    db.commit()
r = client.post('/pages/repair', json={'copy_id': target_id}, headers=auth)
job4 = r.json()['job']['id']
# 模拟并发交错：补页登记后、发布前，目标所在副本被整页重写带到 v3（校验仍坏）
with Session() as db:
    t = db.get(PageCopy, target_id)
    t.version = 3
    db.commit()
r = client.post(f'/pages/repair/{job4}/advance', json={}, headers=auth)
check('CAS 冲突阻断旧页覆盖新版', r.json()['event'] == 'version_conflict' and r.json()['job']['status'] == 'blocked', r.text)
check('冲突原因写明拒绝旧页覆盖', '旧页覆盖' in r.json()['job']['blocked_reason'])
with Session() as db:
    t = db.get(PageCopy, target_id)
    check('目标保持 v3 未被 v2 旧页改写', t.version == 3 and not is_healthy(t))
    check('阻断时不产生发布', db.get(RepairJob, job4).publish_count == 0)

# 场景 C：重写已把目标副本带成健康新版，旧补页任务直接关闭且不计发布
with Session() as db:
    t = db.get(PageCopy, target_id)
    t.version = 3
    t.checksum = checksum_of('page-orders', 3)
    db.commit()
r = client.post(f'/pages/repair/{job4}/advance', json={}, headers=auth)
check('重写已修复目标 -> 任务关闭不重复发布', r.json()['event'] == 'superseded_by_rewrite' and r.json()['job']['publish_count'] == 0, r.text)

print('\n[7] 切主规则：坏页未修好的备库不能当选，修好后恢复')
# 重建干净基线
with Session() as db:
    for c in db.scalars(select(PageCopy)).all():
        c.version = 1
        c.checksum = checksum_of(c.page_key, 1)
    db.commit()
# east 造坏页
r = client.post('/pages/corrupt', json={'copy_id': target_id}, headers=auth)
r = client.get('/pages/health', headers=auth)
health = {h['node_id']: h for h in r.json()}
check('健康汇总标出 east 坏页', health[east_id]['bad_pages'] == 1 and health[east_id]['page_ready'] is False)
with Session() as db:
    nodes_data = [{'id': n.id, 'name': n.name, 'role': n.role, 'term': n.term,
                   'log_position': n.log_position, 'available': n.available,
                   'fenced': n.fenced, 'vote_enabled': n.vote_enabled}
                  for n in db.scalars(select(Node)).all()]
    scenario = {'fail_primary': True, 'fence_primary': True, 'max_data_loss': 0, 'required_term': 7}
result = elect(nodes_data, scenario, health)
east_candidate = next(c for c in result['candidates'] if c['name'] == 'hs-east')
check('坏页备库候选资格被取消', east_candidate['eligible'] is False and '坏页未修好' in east_candidate['block_reason'])
check('整体决策阻断', result['decision'] == 'blocked')
check('坏页清零检查未通过', any(c['rule'] == '坏页清零' and not c['pass'] for c in result['checks']))
# 修好后
r = client.post('/pages/repair', json={'copy_id': target_id}, headers=auth)
jid = r.json()['job']['id']
client.post(f'/pages/repair/{jid}/advance', json={}, headers=auth)
r = client.get('/pages/health', headers=auth)
health2 = {h['node_id']: h for h in r.json()}
result2 = elect(nodes_data, scenario, health2)
east2 = next(c for c in result2['candidates'] if c['name'] == 'hs-east')
check('补好后 east 恢复候选且胜出', east2['eligible'] is True and result2['winner'] == 'hs-east', str(result2['candidates']))
check('补好后决策放行', result2['decision'] == 'approved')

print('\n[8] 入队快照冻结坏页状态，worker 链路可复现')
client.post('/pages/corrupt', json={'copy_id': target_id}, headers=auth)
r = client.post('/drills', json={'scenario_id': 1, 'label': '坏页阻断演练'}, headers=auth)
check('演练入队 202', r.status_code == 202, r.text)
drill_id = r.json()['id']
snapshot = r.json()['snapshot']
check('快照含 page_copies/page_health', 'page_copies' in snapshot and 'page_health' in snapshot)
# 后台补页不影响已冻结快照
client.post('/pages/repair', json={'copy_id': target_id}, headers=auth)
# 直接用 worker 的计算口径模拟处理
from src.services.drill_service import get_drill
with Session() as db:
    job = get_drill(db, drill_id)
    ph = {h['node_id']: h for h in job.snapshot['page_health']}
    job.result = elect(job.snapshot['nodes'], job.snapshot['scenario'], ph)
    job.status = 'completed'
    db.commit()
r = client.get(f'/drills/{drill_id}', headers=auth)
check('冻结快照中的坏页仍阻断切主', r.json()['result']['decision'] == 'blocked')
check('模型版本升级到 badpage-v2', r.json()['result']['model_version'] == 'quorum-fencing-rpo-badpage-v2')

print('\n[9] donor 校验：坏 donor / 跨版本 donor 不能用')
with Session() as db:
    for c in db.scalars(select(PageCopy)).all():
        c.version = 1
        c.checksum = checksum_of(c.page_key, 1)
    db.commit()
primary_orders_id = copy(primary_id, 'page-orders').id
# 同页三个副本全部造坏 -> 无健康 donor
client.post('/pages/corrupt', json={'copy_id': target_id}, headers=auth)
client.post('/pages/corrupt', json={'copy_id': west_orders_id}, headers=auth)
client.post('/pages/corrupt', json={'copy_id': primary_orders_id}, headers=auth)
r = client.post('/pages/repair', json={'copy_id': target_id}, headers=auth)
jid = r.json()['job']['id']
r = client.post(f'/pages/repair/{jid}/advance', json={}, headers=auth)
check('同版副本全部损坏 -> blocked', r.json()['event'] == 'no_donor')
# 跨版本 donor 不算数：把 west 修成 v2（版本不同，不能补 v1 坏页）
with Session() as db:
    w = db.get(PageCopy, west_orders_id)
    w.version = 2
    w.checksum = checksum_of(w.page_key, 2)
    db.commit()
r = client.post(f'/pages/repair/{jid}/advance', json={}, headers=auth)
check('跨版本副本不能做 donor -> 仍 blocked', r.json()['event'] == 'no_donor')
# 同版本健康 donor 出现后，同一任务可继续并成功
with Session() as db:
    w = db.get(PageCopy, west_orders_id)
    w.version = 1
    w.checksum = checksum_of(w.page_key, 1)
    db.commit()
r = client.post(f'/pages/repair/{jid}/advance', json={}, headers=auth)
check('健康同版 donor 出现后补页成功', r.json()['event'] == 'healed')
check('donor 记录为 west 副本', r.json()['job']['donor_copy_id'] == west_orders_id)

print(f'\n==== {PASS} 通过, {len(FAILS)} 失败 ====')
sys.exit(1 if FAILS else 0)

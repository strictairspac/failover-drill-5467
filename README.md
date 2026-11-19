# 衡石 · 值班切主演练沙盘
衡石数据值班组（10人）把节点巡检、故障剧本与切主证明合并为可复核演练记录。

## 业务与模块
原流程用值班表记录节点、聊天传递故障、人工比较日志。痛点：输入版本混乱、隔离证明缺失、决策无法复查。上游是巡检记录；本工具替代人工纸上演练；下游是复核人和运维值班负责人。模块为节点状态、坏页补页沙盘、故障剧本、演练队列、决策证据，配套登录及JSON导出。三个实体 Node / Scenario / Drill 全链路实现，坏页演练由 PageCopy / RepairJob 两张表承载。

**边界：受控教学/运维演练计算模型，不连接真实复制集群、不执行真实生产选主；任期、日志位置和fencing均由用户输入声明。** 本题真实 MySQL 保存业务输入/快照/结果，Python 实际计算规则，后台 worker 异步处理。

## 启动
```bash
cp .env.example .env
docker compose up -d --build
# http://localhost:18249
# API http://localhost:19249/docs
docker compose exec -T backend python -m scripts.seed_demo
docker compose down --remove-orphans
```
初次启动自动seed；脚本自然键查重，不覆盖用户修改。数据库初次连接和DDL失败会使启动失败，避免静默运行坏结构。演示seed异常的降级处理见测试报告。

|账号|密码|权限|典型动作|
|---|---|---|---|
|admin@hengshi.local|Drill2026!|全操作/审计/节点编辑|修改节点日志位置|
|operator@hengshi.local|Drill2026!|剧本新增/运行/导出|提交晚班演练|
|viewer@hengshi.local|Drill2026!|只读/导出|核对规则及结果|

## 一天剧本
09:00操作员登录；09:05核对hs-primary日志8100、hs-east日志8100、hs-west日志8098，任期均6；09:10创建shift-20261117剧本，允许损失0、新任期门槛7、主故障且已隔离；09:20提交晚班演练；09:25查看候选hs-east、新任期7、多数派2/2；09:30导出JSON；09:35运行“缺少隔离证明”案例，应阻断；09:40观察员复核但新增返回403；10:00管理员查看 /api/audits。

## 算法口径
输入为入队时冻结的节点与剧本快照（含 page_copies 与节点级 page_health 坏页快照）。法定票数 floor(配置投票节点数/2)+1；可达票扣除不可达/已隔离/本次故障主节点；候选必须可达、有投票权、非主、未隔离且落后量不超过允许日志损失。新任期=max(节点任期)+1。所有安全条件成立时按日志位置降序、节点名升序确定候选；缺少旧主fencing、票数不足、未触发故障、任期不足、坏页未清零或无合格候选均阻断。空节点、零投票节点、多个主节点计算失败并保存原因。结果含输入SHA256与模型版本，可重复计算。不会伪造真实集群共识证据。

## 坏页 / 补页沙盘口径
- 每个节点对每个逻辑页保存一个物理副本（page_copies），带 version 与 checksum；checksum 不等于 `sha256(page|key|vN)` 即坏页。日志追平只代表日志位置一致，物理页仍可能损坏。
- 坏页停读：读探测命中坏页返回阻断原因；同节点其他页、其他节点同页照常读写。整页重写只推进健康副本，坏页版本原地保留（停写）。
- 补页只能选用**同逻辑页、同版本且校验通过**的副本作 donor；同版本无健康副本则任务 blocked 并写明原因，跨版本或坏 donor 一律不接受。
- 补页写盘带版本 CAS：发布前复核目标版本，整页重写已写成的新版不会被旧页盖掉；冲突时 blocked，publish_count 不增加。
- 同次补页以 repair_key 幂等：写盘中断停在 donor_ready，页面保持坏页、未发布；用同键重试不新建任务、不重复发布，healed 后再推进返回 already_healed，publish_count 只计真正的一次发布。
- 切主沿用原规则，新增「坏页清零」检查与候选级 block_reason：坏页未修好的备库候选资格取消；全部补好后原多数派/fencing/RPO 规则不变。模型版本 `quorum-fencing-rpo-badpage-v2`。
- 页面操作：制造坏页、整页重写、登记/推进补页（含模拟写盘中断）、读探测、节点页面健康摘要；可查看每次读到的版本、停读原因与候选受阻原因。

## 工程
|组件|技术|
|---|---|
|前端|原生HTML/CSS/JS模块，Nginx1.27|
|后端|Python3.11/FastAPI/SQLAlchemy/PyJWT|
|任务|MySQL持久队列 + Redis worker互斥锁 + 心跳|
|存储|MySQL8.0、Redis7|
所有计算和权限检查在后端。JWT有效8小时，密码PBKDF2-SHA256 180000轮随机盐。20条待处理任务配额。审计保存业务写入动作；错误返回request-id。五服务均真实健康检查。

目录：frontend/src/api、stores、types、components/common、hooks、pages、router、utils、constants；backend/src/routes、controllers、services、models、schemas、middlewares、utils、constants、config；backend/scripts；backend/migrations。本地开发：安装backend/requirements.txt并设DATABASE_URL、REDIS_URL、JWT_SECRET，再运行uvicorn；前端以Nginx代理/api/去除前缀。

环境：COMPOSE_PROJECT_NAME控制隔离命名；FRONTEND_PORT/BACKEND_PORT/DB_PORT为端口；DB_PASSWORD为演示数据库密码；JWT_SECRET为签名密钥；SEED_DEMO=false关闭演示数据。命名卷db-data、redis-data、artifacts；reset时显式down -v删除数据。前端字体依赖Google Fonts，离线回退serif/sans-serif。模型是简化安全门禁，非完整Raft实现。MIT许可。

枚举：角色在backend/src/constants/enums.py和frontend/src/constants/enums.js；任务状态与补页状态（queued/donor_ready/applying/healed/blocked）同处定义，使用于models/entities.py、services/page_service.py、worker.py、components/common/status.js；节点角色在schemas/node.py、pages/nodes.js。测试、截图与已知差异见批次test-artifacts/gb-4110/TESTING.md。

## 坏页沙盘测试
`tests/` 下为纯后端回归，不依赖 Docker/MySQL/Redis（SQLite 内存库 + FastAPI TestClient）：`test_integration.py` 覆盖坏页停读、同版本选源、中断幂等、CAS 防覆盖、候选受阻与快照冻结共 53 项断言；`test_concurrency.py` 用独立会话交错模拟补页与整页重写并发；`test_e2e_http.py` 校验认证、403 权限与审计落库。运行：`PYTHONPATH=<依赖目录> python3 tests/test_integration.py`。

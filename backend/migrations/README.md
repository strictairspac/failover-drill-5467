# 数据结构
本小型项目首次启动使用 SQLAlchemy metadata 建立 users/nodes/scenarios/drills/audits/page_copies/repair_jobs 七表。自然键 users.email、nodes.name、scenario.name、repair_jobs.repair_key 唯一；page_copies 以 (node_id, page_key) 唯一。drills.scenario_id 为外键，快照在入队时固定（含 page_copies 与节点级 page_health）。上线结构演进须补充版本化迁移，本项目不支持对既有不同结构数据库自动升级。

## 坏页/补页表
- `page_copies`：节点上逻辑页的物理副本，含 version 与 checksum；checksum 不等于 `sha256(page|key|vN)` 即坏页。坏页停读、停整页重写推进，补页只能采用同 page_key、同 version 且校验通过的 donor。
- `repair_jobs`：补页任务以 repair_key 幂等。状态机 queued → donor_ready（写盘中断停留态）→ applying → healed；无同版本健康副本或版本 CAS 冲突时 blocked。publish_count 仅在真正写盘发布时 +1，中断重试不重复发布。

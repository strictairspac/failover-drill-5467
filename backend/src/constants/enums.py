ROLES = ('admin', 'operator', 'viewer')
DRILL_STATUSES = ('queued', 'running', 'completed', 'failed')
# 补页任务状态机：
# queued      已登记，等待选源/重试（写盘中断后也回到此态，可幂等重试）
# donor_ready 已选定校验通过的同版本副本，等待写入发布
# applying    正在写盘（推进补页的中间态）
# healed      补页完成并已发布一次，后续重试不再重复发布
# blocked     找不到校验通过的同版本副本，等待页面制造新副本或整页重写
REPAIR_STATUSES = ('queued', 'donor_ready', 'applying', 'healed', 'blocked')

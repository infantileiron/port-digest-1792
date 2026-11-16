ROLES=('admin','analyst','viewer')
STATIONS=('北站','南站','东港')
RECEIPT_STATES=('delivered','reviewed')
TIMEZONE='Asia/Shanghai'
# 归档对账批次：pending 待扫描 / scanning 扫描中（崩溃后可续跑）/ held 有来源矛盾留待处理 / completed 已核清
RECON_BATCH_STATES=('pending','scanning','held','completed')
# 差异分类：缺档 / 坏档 / 串档 / 来源自相矛盾 / 孤立文件；None 表示档证一致
RECON_FINDINGS=('missing','corrupt','crossed','contradiction','orphan')
# 处置：pending 处理中（崩溃恢复点）/ ok 无需处理 / repaired 可信回执补档 / held 矛盾留存 / quarantined 孤立隔离
RECON_DISPOSITIONS=('pending','ok','repaired','held','quarantined')

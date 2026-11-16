import logging
from sqlalchemy import select
from src.models.entities import Subscription,Receipt,Audit
from src.services.repository import arrival_rows
from src.services.digest import calculate
from src.services.archive import atomic_write,artifact_path
from src.utils.time import aware,next_due,yesterday

def run_due(s,now):
 sent=[]
 subscriptions=s.scalars(select(Subscription).where(Subscription.enabled.is_(True),Subscription.next_run_at<=now.isoformat()).with_for_update(skip_locked=True)).all()
 for sub in subscriptions:
  if aware(sub.next_run_at)>now:continue
  day=yesterday(now);existing=s.query(Receipt).filter_by(subscription_id=sub.id,report_day=day).first()
  if not existing:
   payload=calculate(arrival_rows(s),day,sub.station)
   receipt=Receipt(subscription_id=sub.id,report_day=day,recipient=sub.recipient,created_at=now.isoformat(),state='delivered',payload=payload)
   s.add(receipt);s.flush();sent.append(receipt.id)
   s.add(Audit(actor='scheduler',action='到点分发订阅 '+str(sub.id)+' / '+day,created=now.isoformat()))
  sub.last_run_at=now.isoformat();sub.next_run_at=next_due(sub.daily_at,now).isoformat()
 s.commit()
 for i in sent:
  try:
   receipt=s.get(Receipt,i)
   # 原子写：临时文件 + rename，对账/下载方不会读到半份档案；写失败不影响 DB 回执
   atomic_write(artifact_path(i),receipt.payload)
  except Exception:logging.exception('DB inbox delivered; optional artifact write failed')
 return sent

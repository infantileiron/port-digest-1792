import os,time,logging
from pathlib import Path
import redis
from redis.exceptions import LockError
from fastapi import HTTPException
from sqlalchemy import select
from src.config.database import Session
from src.services.scheduler import run_due
from src.services.archive import run_batch,recon_lock
from src.models.entities import ReconBatch
from src.utils.time import now_utc
r=redis.Redis.from_url(os.environ['REDIS_URL'])
def tick_scheduler():
 with r.lock('port-digest:scheduler',timeout=20,blocking_timeout=0):
  with Session() as s:run_due(s,now_utc())
def tick_reconcile():
 # 对账与调度用不同锁：扫描期间新回执仍可正常入箱。
 with recon_lock():
  with Session() as s:
   batch=s.scalars(select(ReconBatch).where(ReconBatch.state.in_(('pending','scanning'))).order_by(ReconBatch.id)).first()
   if batch:run_batch(s,batch.id)
def main():
 while True:
  Path('/tmp/heartbeat').touch()
  try:tick_scheduler()
  except LockError:pass
  except Exception:logging.exception('scheduler tick failed')
  try:tick_reconcile()
  except (LockError,HTTPException):pass
  except Exception:logging.exception('reconcile tick failed')
  time.sleep(1)
if __name__=='__main__':main()

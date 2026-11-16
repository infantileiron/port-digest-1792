"""归档对账服务。

口径：
- 共享产物卷 /artifacts 为交接备份；回执（站内收件箱，DB）为送达事实。
- 按回执冻结的来源 rows 重算延误统计，与 payload 摘要逐项比对：不一致即“来源自相矛盾”，留存不补档。
- 共享文件相对回执分四类：缺档 missing、坏档 corrupt、串档 crossed、一致 ok。
- 共享卷里没有任何回执认领的文件（含原子写残留 .tmp）为孤立文件，隔离到 /artifacts/quarantine，绝不冒充已送达。
- 可信回执（冻结来源自洽）的缺/坏/串档可补档；矛盾回执不写不删，留待人工处理。
- 所有文件移动/补档均原子操作；先写对账项差异行并提交，再动文件。崩溃后续跑凭 disposition
  只补未完成动作，不重复补档或隔离，且首次认定的差异与隔离去向始终保留。
"""
import json,os,math,logging,threading
from contextlib import contextmanager
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from src.models.entities import Receipt,ReconBatch,ReconItem,Audit
from src.services.digest import calculate
from src.utils.time import now_utc

ARTIFACT_DIR=Path(os.environ.get('ARTIFACT_DIR','/artifacts'))
QUARANTINE_DIR=ARTIFACT_DIR/'quarantine'
_process_recon_lock=threading.Lock()

@contextmanager
def recon_lock():
 """API 发起与 worker 续跑共用同一把锁，保证同一批次不会被两个执行者并发移动文件。
 Redis 可用时跨进程互斥；本地/测试无 Redis 时退化为进程内锁。"""
 try:
  import redis
  client=redis.Redis.from_url(os.environ['REDIS_URL'],socket_connect_timeout=1,socket_timeout=2)
  lock=client.lock('port-digest:reconcile',timeout=300,blocking_timeout=0)
  if not lock.acquire():
   raise HTTPException(409,'对账批次正在处理中，请稍后在页面查看处理依据')
  try:yield
  finally:
   try:lock.release()
   except Exception:logging.exception('release recon lock failed')
 except HTTPException:raise
 except Exception:
  logging.warning('redis 不可用，对账锁退化为进程内互斥')
  with _process_recon_lock:yield
# 与摘要口径一并核对的统计字段
SUMMARY_FIELDS=('scheduled','completed','pending','late','late_rate','p50','p95','mean_delay','max_delay')

def artifact_path(receipt_id):return ARTIFACT_DIR/(str(receipt_id)+'.json')

def atomic_write(path,obj):
 """临时文件 + os.replace，下载方只会看到完整档案或旧档案，绝不会读到半份。"""
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 tmp=path.with_name(path.name+'.tmp.'+str(os.getpid()))
 tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')
 os.replace(tmp,path)

def atomic_move(src,dst_dir):
 """把可疑文件原子移入隔离区；同名时追加序号，证据不覆盖。返回隔离后的路径字符串。"""
 src=Path(src);dst_dir=Path(dst_dir);dst_dir.mkdir(parents=True,exist_ok=True)
 target=dst_dir/src.name
 n=1
 while target.exists():
  target=dst_dir/(src.name+'.'+str(n));n+=1
 os.replace(src,target)
 return str(target)

def _same_number(a,b):
 if a is None and b is None:return True
 if a is None or b is None:return False
 try:return math.isclose(float(a),float(b),rel_tol=1e-12,abs_tol=1e-9)
 except (TypeError,ValueError):return False

def verify_payload(payload):
 """按回执冻结来源重算，返回 (是否自洽, 差异列表)。差异逐项保留字段级依据。"""
 if not isinstance(payload,dict) or not payload.get('rows'):
  return False,[{'field':'payload','expected':'包含 rows 的冻结晨报','actual':'payload 结构缺失'}]
 report_day=payload.get('report_day');station=payload.get('station','全部')
 if not report_day:
  return False,[{'field':'report_day','expected':'业务日','actual':None}]
 try:
  expected=calculate(payload['rows'],report_day,station)
 except Exception as exc:
  return False,[{'field':'rows','expected':'可按冻结来源重算','actual':'重算失败: '+str(exc)[:200]}]
 diffs=[]
 for field in SUMMARY_FIELDS:
  if not _same_number(payload.get(field),expected.get(field)):
   diffs.append({'field':field,'expected':expected.get(field),'actual':payload.get(field)})
 if payload.get('checksum')!=expected['checksum']:
  diffs.append({'field':'checksum','expected':expected['checksum'],'actual':payload.get('checksum')})
 if payload.get('version')!=expected['version']:
  diffs.append({'field':'version','expected':expected['version'],'actual':payload.get('version')})
 return (not diffs),diffs

def read_artifact(path):
 """读取共享档案，返回 (状态, 数据)。状态：ok / corrupt。"""
 try:
  data=json.loads(Path(path).read_text(encoding='utf-8'))
  if not isinstance(data,dict):return 'corrupt',None
  return 'ok',data
 except Exception:
  return 'corrupt',None

def evaluate_receipt(receipt,on_disk,checksum_index=None):
 """评估单条回执与其共享档案。on_disk 为 path 或 None。
 checksum_index 为冻结 checksum -> 回执 id 的索引，用于认定串档的真实归属。
 返回 (finding, evidence)。finding=None 表示档证一致。"""
 trusted,diffs=verify_payload(receipt.payload)
 evidence={'trusted':trusted,'diffs':diffs,
  'frozen_checksum':(receipt.payload or {}).get('checksum') if isinstance(receipt.payload,dict) else None}
 if not trusted:
  evidence['reason']='冻结来源重算与回执摘要不一致'
  return 'contradiction',evidence
 if on_disk is None:
  evidence['reason']='共享目录缺少该回执的档案'
  return 'missing',evidence
 status,parsed=read_artifact(on_disk)
 evidence['artifact']=str(on_disk)
 if status=='corrupt' or parsed is None:
  evidence['reason']='档案无法解析为完整 JSON（坏档/截断）'
  return 'corrupt',evidence
 if parsed!=receipt.payload:
  evidence['reason']='档案内容与回执冻结 payload 不一致（可能混入别份晨报）'
  evidence['actual_keys']=sorted(parsed.keys())[:20]
  if checksum_index and isinstance(parsed,dict):
   owner=checksum_index.get(parsed.get('checksum'))
   if owner and owner!=receipt.id:evidence['belongs_to_receipt']=owner
  return 'crossed',evidence
 evidence['reason']='档案与回执冻结 payload 完全一致'
 return None,evidence

def _get_item(s,batch_id,receipt_id,artifact_name):
 return s.query(ReconItem).filter_by(batch_id=batch_id,receipt_id=receipt_id,artifact_name=artifact_name).first()

def _touch(item,now,**evidence):
 item.updated_at=now
 if evidence:item.evidence={**(item.evidence or {}),**evidence}

def process_receipt(s,batch,now,receipt,checksum_index=None,crash_after_quarantine=False,crash_after_repair=False):
 """单个回执的对账与处置，天然幂等，可安全续跑。"""
 name=str(receipt.id)+'.json';path=artifact_path(receipt.id)
 item=_get_item(s,batch.id,receipt.id,name)
 if item is not None and item.disposition!='pending':
  return item  # 已处置：崩溃重试不重复补档或隔离
 if item is None:
  finding,evidence=evaluate_receipt(receipt,path if path.is_file() else None,checksum_index)
  item=ReconItem(batch_id=batch.id,receipt_id=receipt.id,artifact_name=name,
   finding=finding,disposition='pending',evidence=evidence,created_at=now,updated_at=now)
  s.add(item);s.commit()  # 差异行先落库，再动文件
 if item.finding is None:
  item.disposition='ok';_touch(item,now);s.commit();return item
 if item.finding=='contradiction':
  # 来源自相矛盾：不补档、不删档，留待人工处理。
  item.disposition='held';_touch(item,now,held_at=now.isoformat());s.commit();return item
 # 可信回执的缺/坏/串档：问题原件（若仍在）先隔离，再原子补档。
 if path.is_file():
  status,parsed=read_artifact(path)
  if status=='ok' and parsed==receipt.payload:
   # 恢复点：上一轮补档已写完整、提交前崩溃。不重复隔离/补档，只补记处置结果。
   _touch(item,now,repaired_from=item.finding,repaired_at=now_utc().isoformat(),
    resume_note='续跑确认补档文件已完整，未重复文件操作')
   item.disposition='repaired';s.commit();return item
  quarantine=atomic_move(path,QUARANTINE_DIR)
  _touch(item,now,quarantined_to=quarantine);s.commit()
  if crash_after_quarantine:raise RuntimeError('crash drill: after quarantine before repair')
 if not path.is_file():
  atomic_write(path,receipt.payload)
  if crash_after_repair:raise RuntimeError('crash drill: after repair write before commit')
 item.disposition='repaired'
 _touch(item,now,repaired_from=item.finding,repaired_at=now_utc().isoformat())
 s.commit()
 return item

def process_orphan(s,batch,now,entry,crash_after_quarantine=False):
 """孤立文件隔离，幂等可续跑；绝不挂到任何回执名下冒充已送达。"""
 name=entry.name
 item=_get_item(s,batch.id,None,name)
 if item is not None and item.disposition!='pending':
  return item
 if item is None:
  item=ReconItem(batch_id=batch.id,receipt_id=None,artifact_name=name,finding='orphan',
   disposition='pending',evidence={'reason':'共享目录存在但无回执认领，隔离且不视为已送达'},
   created_at=now,updated_at=now)
  s.add(item);s.commit()
 if entry.is_file():
  target=atomic_move(entry,QUARANTINE_DIR)
  _touch(item,now,quarantined_to=target,quarantined_at=now_utc().isoformat());s.commit()
  if crash_after_quarantine:raise RuntimeError('crash drill: after orphan quarantine before commit')
 elif not (item.evidence or {}).get('quarantined_to'):
  # 文件已不在（上一轮隔离后崩溃）：隔离区里按原名找回凭据。
  gone=QUARANTINE_DIR/name
  _touch(item,now,quarantined_to=str(gone) if gone.exists() else None,note='续跑时原件已不在共享目录')
 item.disposition='quarantined';item.updated_at=now_utc();s.commit()
 return item

def _finish_batch(s,batch,now,items):
 def count(d):return sum(1 for i in items if i.disposition==d)
 def find(f):return sum(1 for i in items if i.finding==f)
 totals={'receipts':sum(1 for i in items if i.receipt_id is not None),
  'artifacts_scanned':len({i.artifact_name for i in items if i.artifact_name}),
  'missing':find('missing'),'corrupt':find('corrupt'),'crossed':find('crossed'),
  'contradiction':find('contradiction'),'orphan':find('orphan'),
  'ok':count('ok'),'repaired':count('repaired'),'held':count('held'),'quarantined':count('quarantined')}
 batch.totals=totals;batch.finished_at=now
 batch.state='held' if totals['held'] else 'completed'
 return totals

def run_batch(s,batch_id,crash_after_quarantine=False,crash_after_repair=False):
 """执行/续跑一个对账批次。可被 worker 与 API 同步复用。"""
 now=now_utc()
 batch=s.get(ReconBatch,batch_id)
 if not batch or batch.state in ('held','completed'):return batch
 batch.state='scanning';batch.started_at=batch.started_at or now
 s.commit()
 ARTIFACT_DIR.mkdir(parents=True,exist_ok=True)
 # 1) 快照回执与共享目录（扫描期间新到回执由末尾补扫兜住）。
 receipts=s.scalars(select(Receipt).order_by(Receipt.id)).all()
 disk={e.name:e for e in ARTIFACT_DIR.iterdir() if e.is_file()}
 checksum_index={}
 for r in receipts:
  if isinstance(r.payload,dict) and r.payload.get('checksum'):checksum_index.setdefault(r.payload['checksum'],r.id)
 claimed=set()
 for receipt in receipts:
  process_receipt(s,batch,now,receipt,checksum_index,crash_after_quarantine,crash_after_repair)
  claimed.add(str(receipt.id)+'.json')
 # 2) 补扫:扫描期间 worker 新入箱的回执（新回执继续入箱，不阻塞对账）。
 max_id=max([r.id for r in receipts],default=0)
 late=s.scalars(select(Receipt).where(Receipt.id>max_id).order_by(Receipt.id)).all()
 for receipt in late:
  if isinstance(receipt.payload,dict) and receipt.payload.get('checksum'):checksum_index.setdefault(receipt.payload['checksum'],receipt.id)
 for receipt in late:
  process_receipt(s,batch,now_utc(),receipt,checksum_index,crash_after_quarantine,crash_after_repair)
  claimed.add(str(receipt.id)+'.json')
 # 3) 孤立文件：无人认领的常规档案与 .tmp 残留，一律隔离。
 for name,entry in list(disk.items()):
  if name in claimed:continue
  process_orphan(s,batch,now_utc(),entry,crash_after_quarantine)
 # 4) 汇总并结批。
 items=s.query(ReconItem).filter_by(batch_id=batch.id).all()
 totals=_finish_batch(s,batch,now_utc(),items)
 # audits.action 限长 200：只记结论，逐项明细在 recon_items / batch.totals 可查。
 s.add(Audit(actor='reconciler',action='对账批次 %s 结批：%s，补档 %s、留存 %s、隔离 %s'%(
  batch.id,batch.state,totals['repaired'],totals['held'],totals['quarantined']),created=now_utc().isoformat()))
 s.commit()
 logging.info('recon batch %s finished %s',batch.id,totals)
 return batch

def start_batch(s,actor):
 """创建待扫描批次；存在未结批次时拒绝，避免并发批次互相移动文件。"""
 now=now_utc()
 open_batch=s.query(ReconBatch).filter(ReconBatch.state.in_(('pending','scanning','held'))).first()
 if open_batch:
  raise HTTPException(409,'已有未结对账批次 #%s（%s），请先核清'% (open_batch.id,open_batch.state))
 batch=ReconBatch(state='pending',created_at=now,totals={},note='')
 s.add(batch)
 try:s.flush()
 except IntegrityError:
  s.rollback();raise HTTPException(409,'已有未结对账批次，请先核清')
 s.add(Audit(actor=actor,action='发起对账批次 '+str(batch.id),created=now.isoformat()))
 s.commit()
 return batch

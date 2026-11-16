"""归档对账演练（自包含，默认 SQLite + 临时产物目录，不碰真实共享卷，不连 redis）。

演练台账：
1) 正常回执档证一致；
2) 共享目录漏写 -> 缺档，可信回执补档；
3) 档案截断 -> 坏档，原件隔离后补档；
4) 混入别份晨报 -> 串档，隔离并补档，证据保留真实归属；
5) 台账修订后冻结来源与摘要自相矛盾 -> 留存不补档；
6) 无主文件与 .tmp 残留 -> 孤立隔离，不冒充已送达；
7) 两类写盘崩溃恢复：隔离后崩溃、补档写成提交前崩溃，续跑不重复补档/隔离；
8) 再跑一次批次 -> 全部已核清，无重复处置。

用法（容器内）：python -m scripts.drill_reconcile
"""
import os,json,shutil,tempfile,sys
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo

tmp_home=tempfile.mkdtemp(prefix='recon-drill-')
os.environ.setdefault('DATABASE_URL','sqlite:///'+os.path.join(tmp_home,'drill.db'))
os.environ.setdefault('JWT_SECRET','drill-secret')
os.environ.setdefault('ARTIFACT_DIR',os.path.join(tmp_home,'artifacts'))
os.environ.setdefault('REDIS_URL','redis://localhost:6379/0')  # 演练不连 redis，仅保证模块可导入

from src.config.database import Base,engine,Session
from src.models.entities import Subscription,Receipt,ReconBatch,ReconItem
from src.services.digest import calculate
from src.services.archive import run_batch,start_batch,artifact_path,atomic_write,ARTIFACT_DIR,QUARANTINE_DIR
from src.utils.time import now_utc,SH

def build_rows(day,station,n=12):
 rows=[]
 for i in range(n):
  planned=datetime.combine(day,datetime.min.time(),tzinfo=SH)+timedelta(minutes=i*37)
  actual=None if i==11 else planned+timedelta(minutes=(i%4)*5)
  rows.append({'code':'DR-%s-%02d'%(station,i),'station':station,
   'planned_at':planned.astimezone(timezone.utc).isoformat(),
   'actual_at':actual.astimezone(timezone.utc).isoformat() if actual else None,'note':'演练台账'})
 return rows

def expect_crash(fn,label):
 try:fn()
 except RuntimeError as exc:print('  注入崩溃【%s】：%s'%(label,exc));return
 raise AssertionError('崩溃注入点未触发: '+label)

def main():
 Base.metadata.create_all(engine)
 with Session() as s:
  day=(now_utc().astimezone(SH).date()-timedelta(days=1))
  sub_n=Subscription(name='北站演练',station='北站',daily_at='08:30',recipient='北站值班室',enabled=False,next_run_at=now_utc().isoformat())
  sub_s=Subscription(name='南站演练',station='南站',daily_at='08:30',recipient='南站值班室',enabled=False,next_run_at=now_utc().isoformat())
  s.add_all([sub_n,sub_s]);s.flush()
  pn=calculate(build_rows(day,'北站'),day.isoformat(),'北站')
  pn2=calculate(build_rows(day,'北站',n=9),day.isoformat(),'北站')  # 口径自洽但与 pn 不同源
  ps=calculate(build_rows(day,'南站'),day.isoformat(),'南站')
  def receipt(sub,day_offset,payload):
   return Receipt(subscription_id=sub.id,report_day=(day-timedelta(days=day_offset)).isoformat(),
    recipient=sub.recipient,created_at=now_utc().isoformat(),state='delivered',payload=payload)
  r_ok=receipt(sub_n,0,pn);r_missing=receipt(sub_n,1,pn);r_corrupt=receipt(sub_n,2,pn)
  r_crossed=receipt(sub_n,3,pn2);r_conflict=receipt(sub_s,0,json.loads(json.dumps(ps)))
  s.add_all([r_ok,r_missing,r_corrupt,r_crossed,r_conflict]);s.flush()
  # 台账修订后只改回执冻结摘要、不改来源：模拟来源与统计自相矛盾。
  # 注意整体替换字典，SQLAlchemy 的 JSON 列不跟踪就地修改。
  r_conflict.payload={**r_conflict.payload,'p50':999.0,'late':r_conflict.payload['late']+7}
  s.commit()
  ids=dict(ok=r_ok.id,missing=r_missing.id,corrupt=r_corrupt.id,crossed=r_crossed.id,conflict=r_conflict.id)

 # 布置共享目录现场
 with Session() as s:
  atomic_write(artifact_path(ids['ok']),s.get(Receipt,ids['ok']).payload)
  # missing：故意不写
  artifact_path(ids['corrupt']).write_text('{截断的半份档案',encoding='utf-8')  # 坏档
  atomic_write(artifact_path(ids['crossed']),s.get(Receipt,ids['ok']).payload)  # 串档：放成别份晨报
  atomic_write(artifact_path(ids['conflict']),s.get(Receipt,ids['conflict']).payload)
 # 孤立文件：无主常规档案 + 原子写残留
 (ARTIFACT_DIR/'999001.json').write_text(json.dumps({'orphan':True}),encoding='utf-8')
 (ARTIFACT_DIR/'7.json.tmp.4242').write_text('partial',encoding='utf-8')
 print('== 演练现场 ==  产物目录:',sorted(p.name for p in ARTIFACT_DIR.iterdir()))

 # 第一轮批次：先在“问题原件隔离后、补档前”崩溃（首个命中的是坏档项）
 with Session() as s:
  b=start_batch(s,'drill-admin')
  expect_crash(lambda:run_batch(s,b.id,crash_after_quarantine=True),'隔离后补档前')
  mid=s.get(ReconBatch,b.id)
  print('  崩溃后批次状态:',mid.state)
  for it in s.query(ReconItem).filter_by(batch_id=b.id).order_by(ReconItem.id).all():
   print('   ',it.artifact_name,it.finding,it.disposition,(it.evidence or {}).get('quarantined_to'))

 # 续跑同一批次，并在“补档写成、提交前”再崩溃一次（命中缺档项：无隔离直接补写）
 with Session() as s:
  expect_crash(lambda:run_batch(s,b.id,crash_after_repair=True),'补档写成提交前')

 # 再次续跑（worker 恢复后的正常行为，不带注入），应顺利结批
 with Session() as s:
  b2=run_batch(s,b.id)
  print('\n== 续跑结批 == 状态:',b2.state,'\n汇总:',json.dumps(b2.totals,ensure_ascii=False))
  assert b2.totals=={'receipts':5,'artifacts_scanned':7,'missing':1,'corrupt':1,'crossed':1,
   'contradiction':1,'orphan':2,'ok':1,'repaired':3,'held':1,'quarantined':2},b2.totals
  assert b2.state=='held','存在来源矛盾时批次应留存处理'
  for it in s.query(ReconItem).filter_by(batch_id=b.id).order_by(ReconItem.id).all():
   print('   ',it.artifact_name,'| 差异:',it.finding,'| 处置:',it.disposition,
     '| 串档归属:',(it.evidence or {}).get('belongs_to_receipt'),
     '| 隔离至:',(it.evidence or {}).get('quarantined_to'),
     '| 恢复备注:',(it.evidence or {}).get('resume_note'))

 # 校验文件去向：补档完整、矛盾未动、隔离区证据齐全、共享目录无孤立/残留
 with Session() as s:
  for k in ('missing','corrupt','crossed'):
   assert json.loads(artifact_path(ids[k]).read_text())['checksum']==s.get(Receipt,ids[k]).payload['checksum']
  assert json.loads(artifact_path(ids['conflict']).read_text())['p50']==999.0  # 矛盾项未被覆盖
 leftover=sorted(p.name for p in ARTIFACT_DIR.iterdir() if p.is_file())
 assert leftover==sorted(str(ids[k])+'.json' for k in ids),leftover
 quarantined=sorted(p.name for p in QUARANTINE_DIR.iterdir())
 assert {'999001.json','7.json.tmp.4242'}.issubset(set(quarantined))
 print('\n共享目录（仅五份回执档案）:',leftover)
 print('隔离区:',quarantined)

 # 未结批次互斥校验（b 已处于 held 留存状态，新批次必须等矛盾核清）
 with Session() as s:
  assert s.get(ReconBatch,b.id).state=='held'
  try:start_batch(s,'drill-admin');raise AssertionError('未结批次互斥未生效')
  except Exception as exc:
   assert '已有未结对账批次' in str(exc)
   print('未结批次互斥生效：',exc)
  # 人工核清来源矛盾（留存项不动文件，仅结批），模拟台账已更正后放行下一批次。
  s.get(ReconBatch,b.id).state='completed';s.commit()

 # 重试：再发起一个批次，档证一致项全部 ok，矛盾项继续 held，且不产生任何重复文件操作
 with Session() as s:
  b3=start_batch(s,'drill-admin');run_batch(s,b3.id)
  again=s.query(ReconItem).filter_by(batch_id=b3.id).all()
  assert b3.state=='held' and b3.totals['ok']==4 and b3.totals['held']==1
  assert b3.totals['repaired']==0 and b3.totals['quarantined']==0,b3.totals
  assert {i.disposition for i in again}=={'ok','held'}
  print('\n== 重试批次 #%s == 一致项 ok、矛盾项继续留存，无重复补档/隔离:'%b3.id,b3.totals)

 print('\n演练通过，临时目录：',tmp_home)
 shutil.rmtree(tmp_home,ignore_errors=True)

if __name__=='__main__':
 sys.exit(main())

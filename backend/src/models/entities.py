from sqlalchemy import Column,Integer,String,JSON,Text as SqlText,Boolean,ForeignKey,UniqueConstraint,Index,text
from src.config.database import Base
class User(Base):
 __tablename__='users'
 id=Column(Integer,primary_key=True);email=Column(String(100),unique=True,nullable=False);password=Column(String(200));role=Column(String(20))
class Arrival(Base):
 __tablename__='arrivals'
 id=Column(Integer,primary_key=True);code=Column(String(60),unique=True,nullable=False);station=Column(String(30));planned_at=Column(String(40));actual_at=Column(String(40),nullable=True);note=Column(SqlText,default='')
class Subscription(Base):
 __tablename__='subscriptions'
 id=Column(Integer,primary_key=True);name=Column(String(100),unique=True,nullable=False);station=Column(String(30));daily_at=Column(String(5));recipient=Column(String(80));enabled=Column(Boolean,default=False);next_run_at=Column(String(40));last_run_at=Column(String(40),nullable=True)
class Receipt(Base):
 __tablename__='receipts'
 __table_args__=(UniqueConstraint('subscription_id','report_day',name='uq_receipt_subscription_day'),)
 id=Column(Integer,primary_key=True);subscription_id=Column(Integer,ForeignKey('subscriptions.id'));report_day=Column(String(10));recipient=Column(String(80));created_at=Column(String(40));state=Column(String(20),default='delivered');payload=Column(JSON);review_note=Column(SqlText,default='')
class Audit(Base):
 __tablename__='audits'
 id=Column(Integer,primary_key=True);actor=Column(String(100));action=Column(String(200));created=Column(String(40))
class ReconBatch(Base):
 __tablename__='recon_batches'
 id=Column(Integer,primary_key=True);state=Column(String(20),default='pending',nullable=False);created_at=Column(String(40),nullable=False);started_at=Column(String(40),nullable=True);finished_at=Column(String(40),nullable=True)
 # 同一时刻只允许一个未结批次（pending/scanning/held），completed 不受限
 __table_args__=(Index('uq_recon_open_state','state',unique=True,sqlite_where=text("state IN ('pending','scanning','held')"),postgresql_where=text("state IN ('pending','scanning','held')")),)
 totals=Column(JSON,default=dict);note=Column(SqlText,default='')
class ReconItem(Base):
 __tablename__='recon_items'
 __table_args__=(UniqueConstraint('batch_id','receipt_id','artifact_name',name='uq_recon_item'),)
 id=Column(Integer,primary_key=True);batch_id=Column(Integer,ForeignKey('recon_batches.id'),nullable=False)
 receipt_id=Column(Integer,ForeignKey('receipts.id'),nullable=True)
 artifact_name=Column(String(200),nullable=True)
 finding=Column(String(20),nullable=True);disposition=Column(String(20),default='pending',nullable=False)
 evidence=Column(JSON,default=dict);created_at=Column(String(40),nullable=False);updated_at=Column(String(40),nullable=False)

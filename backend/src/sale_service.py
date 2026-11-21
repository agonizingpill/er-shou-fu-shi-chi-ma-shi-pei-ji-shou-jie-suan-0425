from datetime import timedelta
from sqlalchemy import select
from .models import *
from .common import *
from .ledger import active_listing,close_listing,verify_sale

def _latest_ready(s,garment_id,profile_id,now,floor):
 xs=s.scalars(select(FitAssessment).where(FitAssessment.garment_id==garment_id,FitAssessment.profile_id==profile_id,FitAssessment.state=='ready').order_by(FitAssessment.id)).all()
 ok=[x for x in xs if x.id>floor and x.created_at and x.completed_at and time(x.created_at)<=time(now) and time(x.completed_at)<=time(now) and time(x.completed_at)>=time(x.created_at)]
 return ok[-1] if ok else None

def sell(s,d):
 c=lock(s);f=get(s,Fitting,d['fitting_id']);g=garment(s,f.garment_id);a=get(s,Agreement,g.agreement_id)
 if f.state!='reserved' or g.state!='held':fail('预约不是当前有效留衣')
 if not time(f.starts)<=time(c.now)<=time(f.ends):fail('只能在预约试衣时间内成交')
 if d['price_cents']<a.floor_cents:fail('成交价低于寄售底价')
 l=active_listing(s,g.id)
 if not l:fail('衣物缺少有效在架周期，不能成交')
 aid=d.get('assessment_id')
 if aid is None:
  chosen=_latest_ready(s,g.id,f.profile_id,c.now,l.assessment_floor)
 else:
  chosen=get(s,FitAssessment,aid)
  if chosen.garment_id!=g.id:fail('所选尺码结论不属于本件衣物')
  if chosen.profile_id!=f.profile_id:fail('所选尺码结论与预约匿名买家不一致')
 if not chosen:fail('成交必须采用一条已完成的尺码结论')
 if chosen.state!='ready' or not chosen.result:fail('所选尺码结论尚未完成，不能作为成交依据')
 if not chosen.completed_at:fail('所选尺码结论尚未生成，不能作为成交依据')
 if time(chosen.completed_at)>time(c.now):fail('适配结论尚未生效，不能作为成交依据')
 if not chosen.created_at or time(chosen.created_at)>time(c.now):fail('适配记录尚未登记，不能作为成交依据')
 if time(chosen.completed_at)<time(chosen.created_at):fail('适配记录时序异常，不能作为成交依据')
 if chosen.id<=l.assessment_floor or time(chosen.created_at)<time(l.listed_at):fail('适配结论早于本轮在架，不能作为本轮成交依据')
 commission=(d['price_cents']*a.commission_bp+5000)//10000
 x=Sale(fitting_id=f.id,garment_id=g.id,price_cents=d['price_cents'],commission_bp=a.commission_bp,commission_cents=commission,net_cents=d['price_cents']-commission,sold_at=c.now,return_deadline=(time(c.now)+timedelta(days=7)).isoformat(),
   assessment_id=chosen.id,fit_label=chosen.result.get('label'),
   fit_snapshot={'assessment_id':chosen.id,'profile_id':chosen.profile_id,'completed_at':chosen.completed_at,'adopted_at':c.now,'result':chosen.result},
   listing_id=l.id,listed_at=l.listed_at,cycle=l.sequence,agreement_id=a.id,agreement_code=a.code,
   agreement_snapshot={'agreement_id':a.id,'code':a.code,'consignor_id':a.consignor_id,'floor_cents':a.floor_cents,'commission_bp':a.commission_bp,'ends':a.ends,'adopted_at':c.now})
 s.add(x);s.flush();close_listing(l,c.now,'sold',x.id)
 g.state='sold';f.state='completed';s.flush()
 errs=verify_sale(s,x)
 if errs:fail('成交依据链不完整：'+'；'.join(errs))
 return x

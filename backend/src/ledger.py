from datetime import datetime
from sqlalchemy import select
from .models import *
from .common import fail,rows

# 在架周期（Listing）是一条独立的“挂在衣架上”的区间。
# 每轮从 listed_at 开始，成交时结束；退货复检重新上架会开启下一轮序号。
# 成交必须引用其中一轮，尺码结论必须产生于该轮之内，形成可追查的依据链。

def parse(v):
 try:return datetime.fromisoformat(v).replace(tzinfo=None) if v else None
 except (ValueError,TypeError):return None

def all_listings(s,garment_id):
 return s.scalars(select(Listing).where(Listing.garment_id==garment_id).order_by(Listing.sequence)).all()

def active_listing(s,garment_id):
 for x in all_listings(s,garment_id):
  if x.ended_at is None:return x
 return None

def create_listing(s,garment_id,now,source):
 ls=all_listings(s,garment_id)
 if any(x.ended_at is None for x in ls):fail('衣物已有未结束的在架周期')
 sequence=(ls[-1].sequence+1) if ls else 1
 floor=s.scalars(select(FitAssessment.id).where(FitAssessment.garment_id==garment_id).order_by(FitAssessment.id.desc())).first() or 0
 x=Listing(garment_id=garment_id,sequence=sequence,listed_at=now,source=source,assessment_floor=floor);s.add(x);s.flush();return x

def close_listing(x,now,reason,sale_id=None):
 if x.ended_at is not None:fail('在架周期已经结束')
 x.ended_at=now;x.end_reason=reason;x.sale_id=sale_id

def verify_chain(s,garment_id):
 errs=[];ls=all_listings(s,garment_id);g=s.get(Garment,garment_id)
 if not ls:
  return [] if g and g.state=='unmeasured' else ['缺少在架周期记录']
 if g and g.state=='unmeasured':errs.append('衣物未上架却存在在架周期')
 for i,x in enumerate(ls):
  if x.sequence!=i+1:errs.append(f'在架周期序号不连续：第{i+1}条为第{x.sequence}轮')
  if x.garment_id!=garment_id:errs.append('在架周期串件')
  if i>0:
   p=ls[i-1]
   if p.ended_at is None:errs.append(f'第{p.sequence}轮未结束即出现第{x.sequence}轮')
   elif p.end_reason!='sold':errs.append(f'第{p.sequence}轮未成交却重新上架')
   if p.sale_id is None:errs.append(f'第{p.sequence}轮无成交却重新上架')
   else:
    ps=s.get(Sale,p.sale_id)
    if not ps or ps.garment_id!=garment_id:errs.append(f'第{p.sequence}轮成交记录缺失或串件')
    elif ps.state!='returned':errs.append(f'第{p.sequence}轮成交未退货即重新上架')
    else:
     r=s.scalars(select(Return).where(Return.sale_id==ps.id)).first()
     if not r:errs.append(f'第{p.sequence}轮退货记录缺失')
     elif r.state!='inspected' or r.outcome!='relist':errs.append(f'第{p.sequence}轮退货未复检重新上架')
   if x.source!='relist':errs.append(f'第{x.sequence}轮并非退货复检后上架')
  elif x.source not in ('measure','seed'):errs.append('首轮在架来源不明')
  if x.ended_at is not None:
   if x.end_reason=='sold':
    if x.sale_id is None:errs.append(f'第{x.sequence}轮已结束但无成交记录')
    else:
     sl=s.get(Sale,x.sale_id)
     if not sl or sl.garment_id!=garment_id:errs.append(f'第{x.sequence}轮成交记录缺失或串件')
     else:
      if sl.listing_id!=x.id:errs.append(f'第{x.sequence}轮与成交 #{sl.id} 未互相关联')
      if parse(sl.sold_at)!=parse(x.ended_at):errs.append(f'第{x.sequence}轮结束时间与成交时间不一致')
   elif x.end_reason=='withdrawn':
    if x.sale_id is not None:errs.append(f'第{x.sequence}轮撤回却关联成交')
   else:errs.append(f'第{x.sequence}轮结束原因不明')
  elif i!=len(ls)-1:errs.append(f'第{x.sequence}轮未结束但已有后续轮次')
 return errs

def basis_errors(s,sale):
 errs=[];g=s.get(Garment,sale.garment_id)
 if not g:return ['衣物缺失']
 ag=None
 if sale.agreement_id is None:errs.append('成交缺少寄售协议依据')
 else:
  ag=s.get(Agreement,sale.agreement_id)
  if not ag:errs.append('成交引用的寄售协议不存在')
  else:
   if ag.id!=g.agreement_id:errs.append('成交协议与衣物当前寄售协议不一致')
   if sale.agreement_code!=ag.code:errs.append('成交协议编号快照不一致')
   if sale.price_cents<ag.floor_cents:errs.append('成交价低于协议底价')
 snap=sale.agreement_snapshot or {}
 if not snap:errs.append('成交缺少协议条款快照')
 else:
  if snap.get('agreement_id')!=sale.agreement_id or snap.get('code')!=sale.agreement_code:errs.append('协议条款快照与协议引用不一致')
  if snap.get('commission_bp')!=sale.commission_bp:errs.append('协议条款快照的佣金与成交佣金快照不一致')
  if snap.get('floor_cents') is None or sale.price_cents<snap['floor_cents']:errs.append('成交价低于协议条款快照底价')
  if ag and snap.get('consignor_id')!=ag.consignor_id:errs.append('协议条款快照的寄售人与协议不一致')
 if not isinstance(sale.commission_bp,int) or not 0<=sale.commission_bp<=5000:errs.append('佣金比例快照无效')
 else:
  if (sale.price_cents*sale.commission_bp+5000)//10000!=sale.commission_cents:errs.append('佣金金额与成交佣金快照不一致')
 if sale.price_cents-sale.commission_cents!=sale.net_cents:errs.append('应付金额与成交金额快照不一致')
 a=None
 if sale.assessment_id is None:errs.append('成交缺少采用的尺码结论')
 else:
  a=s.get(FitAssessment,sale.assessment_id)
  if not a:errs.append('成交采用的尺码结论不存在')
  else:
   if a.garment_id!=g.id:errs.append('尺码结论串件')
   if a.state!='ready' or not a.result:errs.append('成交采用的尺码结论未完成')
   else:
    if sale.fit_label!=a.result.get('label'):errs.append('成交尺码标签与结论不符')
    snap=sale.fit_snapshot or {}
    if snap.get('assessment_id')!=a.id:errs.append('成交依据快照未固定采用的适配编号')
    if snap.get('result')!=a.result:errs.append('成交依据快照与原始适配记录不一致')
   ac=parse(a.completed_at);cr=parse(a.created_at)
   if ac is None:errs.append('适配结论缺少完成时间')
   elif parse(sale.sold_at) and ac>parse(sale.sold_at):errs.append('适配结论晚于成交，不能作为成交依据')
   if cr is None:errs.append('适配结论缺少登记时间')
   elif parse(sale.sold_at) and cr>parse(sale.sold_at):errs.append('适配记录晚于成交登记，不能作为成交依据')
   elif ac and cr>ac:errs.append('适配记录时序异常')
   l=s.get(Listing,sale.listing_id) if sale.listing_id is not None else None
   if l:
    if a.id<=l.assessment_floor:errs.append('适配结论早于本轮在架，不能作为本轮成交依据')
    if parse(l.listed_at) and cr and cr<parse(l.listed_at):errs.append('适配结论早于本轮在架，不能作为本轮成交依据')
 f=s.get(Fitting,sale.fitting_id)
 if not f:errs.append('成交缺少试衣留衣记录')
 else:
  if f.garment_id!=g.id:errs.append('试衣留衣串件')
  if f.state!='completed':errs.append('试衣留衣未完成')
  if a and f.profile_id!=a.profile_id:errs.append('尺码结论与试衣匿名买家不一致')
  st,en,so=parse(f.starts),parse(f.ends),parse(sale.sold_at)
  if None in (st,en,so) or not st<=so<=en:errs.append('成交时间不在预约试衣时段内')
 if sale.listing_id is None:errs.append('成交缺少在架周期')
 else:
  l=s.get(Listing,sale.listing_id)
  if not l:errs.append('成交引用的在架周期不存在')
  else:
   if l.garment_id!=g.id:errs.append('在架周期串件')
   if l.sale_id!=sale.id:errs.append('在架周期未关联本笔成交')
   if sale.cycle!=l.sequence:errs.append('成交轮次与在架周期不一致')
   if sale.listed_at!=l.listed_at:errs.append('成交在架起始时间与周期不一致')
 rets=s.scalars(select(Return).where(Return.sale_id==sale.id)).all()
 if sale.state=='returned':
  if not rets:errs.append('退货链缺少退货记录')
  elif len(rets)>1:errs.append('退货链存在多笔退货记录')
  else:
   r=rets[0]
   if r.state not in ('pending','inspected'):errs.append('退货记录状态异常')
   if r.state=='inspected' and r.outcome not in ('relist','retire'):errs.append('退货复检去向不明')
 elif rets:errs.append('未退货成交却存在退货记录')
 pays=s.scalars(select(Payout).where(Payout.sale_id==sale.id)).all()
 if sale.state=='settled':
  if not pays:errs.append('已结算成交缺少支付记录')
  elif len(pays)>1:errs.append('同一笔成交存在多次支付')
  else:
   p=pays[0]
   if p.amount_cents!=sale.net_cents:errs.append('支付金额与成交应付金额不一致')
   if ag and p.consignor_id!=ag.consignor_id:errs.append('支付对象与协议寄售人不一致')
   if parse(p.paid_at) is None:errs.append('支付时间无效')
 elif pays:errs.append('未结算成交却存在支付记录')
 if sale.state not in ('completed','returned','settled'):errs.append('成交状态异常')
 return errs

def verify_sale(s,sale):
 errs=verify_chain(s,sale.garment_id)+basis_errors(s,sale)
 mine=[x for x in all_listings(s,sale.garment_id) if x.sale_id==sale.id]
 if len(mine)!=1:errs.append('成交对应的在架周期数量异常')
 for x in all_listings(s,sale.garment_id):
  if x.sequence<sale.cycle and x.sale_id is not None:
   ps=s.get(Sale,x.sale_id)
   if not ps:errs.append(f'第{x.sequence}轮成交记录缺失')
   else:errs+= [f'第{x.sequence}轮成交：'+e for e in basis_errors(s,ps)]
 return list(dict.fromkeys(errs))

def reconcile(s):
 shop=s.get(Shop,1);now=parse(shop.now if shop else None);out=[]
 for g in rows(s,Garment):
  ag=s.get(Agreement,g.agreement_id)
  ls=all_listings(s,g.id)
  sales=s.scalars(select(Sale).where(Sale.garment_id==g.id).order_by(Sale.id)).all()
  assessments=s.scalars(select(FitAssessment).where(FitAssessment.garment_id==g.id).order_by(FitAssessment.id)).all()
  adopted={}
  for sl in sales:
   if sl.assessment_id is not None:adopted.setdefault(sl.assessment_id,[]).append(sl.id)
  ass=[{'id':a.id,'profile_id':a.profile_id,'state':a.state,'label':(a.result or {}).get('label') if a.result else None,'created_at':a.created_at,'completed_at':a.completed_at,'adopted_by':adopted.get(a.id,[])} for a in assessments]
  chain=verify_chain(s,g.id);cycles=[];reasons=[]
  for x in ls:
   sale=s.get(Sale,x.sale_id) if x.sale_id is not None else None
   ret=payout=None;blockers=[];notes=[];closed=False
   if sale:
    ret=s.scalars(select(Return).where(Return.sale_id==sale.id)).first()
    payout=s.scalars(select(Payout).where(Payout.sale_id==sale.id)).first()
    errs=verify_sale(s,sale)
    if errs:blockers.append('依据链缺项或串错：'+'；'.join(errs))
    if sale.state=='settled':
     if payout and payout.amount_cents==sale.net_cents and not errs:closed=True;notes.append('成交与支付已闭合')
     else:blockers.append('已结算状态但支付记录不完整')
    elif sale.state=='returned':
     if ret and ret.state=='inspected':
      closed=True;notes.append('已退货并复检'+('，重新上架，本笔成交不结算' if ret.outcome=='relist' else '，衣物退还寄售人，本笔成交不结算'))
     elif ret:blockers.append('退货待复检，尚未闭合')
     else:blockers.append('退货记录缺失')
    elif sale.state=='completed':
     if now and parse(sale.return_deadline) and now<=parse(sale.return_deadline):blockers.append(f'退货期未满（至 {sale.return_deadline}）')
     if ag and now and parse(ag.ends) and now<parse(ag.ends):blockers.append(f'寄售期未满（至 {ag.ends}）')
     if g.state!='sold':blockers.append('衣物当前不在已售状态')
     if not blockers:blockers.append('已满足结算条件，尚未支付')
    else:blockers.append('成交状态异常')
   else:
    if x.ended_at is None and x.end_reason!='':blockers.append(f'第{x.sequence}轮在架记录异常')
    elif x.ended_at is None:blockers.append(f'第{x.sequence}轮在架中，尚未成交')
    elif x.end_reason=='withdrawn':closed=True;notes.append('期满撤回取衣，无成交，不涉及结算')
    else:blockers.append(f'第{x.sequence}轮已结束但无成交记录')
   cycles.append({'sequence':x.sequence,'source':x.source,'listed_at':x.listed_at,'ended_at':x.ended_at,'end_reason':x.end_reason,
    'sale':None if not sale else {'id':sale.id,'state':sale.state,'price_cents':sale.price_cents,'commission_bp':sale.commission_bp,'commission_cents':sale.commission_cents,'net_cents':sale.net_cents,'sold_at':sale.sold_at,'return_deadline':sale.return_deadline,'agreement_id':sale.agreement_id,'agreement_code':sale.agreement_code,'assessment_id':sale.assessment_id,'fit_label':sale.fit_label,'cycle':sale.cycle,'listed_at':sale.listed_at},
    'return':None if not ret else {'id':ret.id,'state':ret.state,'reason':ret.reason,'inspection':ret.inspection,'outcome':ret.outcome},
    'payout':None if not payout else {'id':payout.id,'reference':payout.reference,'amount_cents':payout.amount_cents,'consignor_id':payout.consignor_id,'paid_at':payout.paid_at},
    'closed':closed,'notes':notes,'reasons':blockers})
   if not closed:
    for r in blockers:
     if r not in reasons:reasons.append(r)
  active=active_listing(s,g.id)
  withdrawal=s.scalars(select(Withdrawal).where(Withdrawal.garment_id==g.id)).first()
  gnotes=[]
  if g.state=='unmeasured' and not withdrawal:reasons.append('未实测，尚未上架')
  if g.state in ('listed','held') and active:
   r=f'第{active.sequence}轮在架中，尚未成交'
   if ag and now and parse(ag.ends) and now>=parse(ag.ends):r+='；寄售期已满，可办理期满撤回'
   if r not in reasons:reasons.append(r)
  if g.state in ('listed','held') and not active:reasons.append('衣物在架状态缺少有效在架周期')
  if g.state=='inspection' and not any(c['return'] and c['return']['state']=='pending' for c in cycles):
   reasons.append('退货隔离待复检，尚未闭合')
  if g.state=='withdrawn' and not withdrawal:reasons.append('衣物已撤回但缺少取衣凭据')
  if g.state=='withdrawn' and withdrawal and not cycles:gnotes.append('未实测即期满撤回，取衣凭据已留档，无成交与支付')
  closed=(not reasons) and active is None and all(c['closed'] for c in cycles) and bool(cycles or g.state=='withdrawn')
  out.append({'garment':{'id':g.id,'code':g.code,'title':g.title,'state':g.state,'price_cents':g.price_cents,'agreement_id':g.agreement_id,'agreement_code':ag.code if ag else None,'commission_bp':ag.commission_bp if ag else None,'agreement_ends':ag.ends if ag else None,'consignor_id':ag.consignor_id if ag else None},
   'assessments':ass,'chain_errors':chain,'cycles':cycles,'closed':closed,'unsettled_reasons':reasons,'notes':gnotes,
   'withdrawal':None if not withdrawal else {'id':withdrawal.id,'reason':withdrawal.reason,'returned_at':withdrawal.returned_at},
   'paid_total_cents':sum(c['payout']['amount_cents'] for c in cycles if c['payout'])})
 return out

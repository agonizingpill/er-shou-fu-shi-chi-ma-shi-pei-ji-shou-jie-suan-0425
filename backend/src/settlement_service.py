from sqlalchemy import select
from .models import *
from .common import *
from .ledger import active_listing,close_listing,verify_sale,verify_chain
def payout(s,d):
 c=lock(s);sale=get(s,Sale,d['sale_id']);g=garment(s,sale.garment_id);a=get(s,Agreement,g.agreement_id)
 existing=s.scalars(select(Payout).where(Payout.sale_id==sale.id)).first()
 if sale.state=='settled' or existing:fail('该成交已结算，不重复支付')
 if sale.state!='completed':fail('只有未退货未结算成交可结算')
 if time(c.now)<=time(sale.return_deadline):fail('退货期未满，不得结算')
 if time(c.now)<time(a.ends):fail('寄售期未满，不得结算')
 if g.state!='sold':fail('衣物状态与成交不一致，不得结算')
 errs=verify_sale(s,sale)
 if errs:fail('结算前依据链校验未通过：'+'；'.join(errs))
 x=Payout(sale_id=sale.id,consignor_id=a.consignor_id,amount_cents=sale.net_cents,reference=d['reference'],paid_at=c.now);s.add(x);sale.state='settled';s.flush();return x
def withdraw(s,id,d):
 c=lock(s);g=garment(s,id);a=get(s,Agreement,g.agreement_id)
 if time(c.now)<time(a.ends):fail('寄售期未满，不得期满撤回')
 if g.state not in ['unmeasured','listed','held']:fail('已售、待复检或已撤回衣物不可撤回')
 errs=verify_chain(s,g.id)
 if errs:fail('在架周期串错，不得撤回：'+'；'.join(errs))
 for f in rows(s,Fitting):
  if f.garment_id==g.id and f.state=='reserved':f.state='cancelled'
 l=active_listing(s,g.id)
 if l:close_listing(l,c.now,'withdrawn')
 x=Withdrawal(garment_id=g.id,reason=d['reason'],returned_at=c.now);s.add(x);g.state='withdrawn';s.flush();return x
def clock(s,d):
 c=lock(s);t=time(d['now'])
 if t<time(c.now):fail('演示时钟不能倒退')
 c.now=t.isoformat();return c

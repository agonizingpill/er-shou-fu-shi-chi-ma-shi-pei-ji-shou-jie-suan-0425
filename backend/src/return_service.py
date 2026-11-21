from .models import *
from .common import *
from .ledger import create_listing,verify_sale
def request(s,d):
 c=lock(s);sale=get(s,Sale,d['sale_id']);g=garment(s,sale.garment_id)
 if sale.state!='completed' or g.state!='sold':fail('订单已退回或已结算，不可退货')
 if time(c.now)>time(sale.return_deadline):fail('已经超出七天退货期')
 errs=verify_sale(s,sale)
 if errs:fail('成交依据链缺项或串错，不能登记退货：'+'；'.join(errs))
 x=Return(**d);s.add(x);g.state='inspection';sale.state='returned';s.flush();return x
def inspect(s,id,d):
 c=lock(s);x=get(s,Return,id);sale=get(s,Sale,x.sale_id);g=garment(s,sale.garment_id);a=get(s,Agreement,g.agreement_id)
 if x.state!='pending' or g.state!='inspection':fail('退货已经复检')
 if d['outcome']=='relist' and time(c.now)>=time(a.ends):fail('寄售期已满，不能重新上架')
 x.state='inspected';x.inspection=d['inspection'];x.outcome=d['outcome']
 if x.outcome=='relist':
  g.state='listed';create_listing(s,g.id,c.now,'relist');s.flush()
 else:
  g.state='withdrawn';s.add(Withdrawal(garment_id=g.id,reason=d['inspection'],returned_at=c.now))
 s.flush();return x

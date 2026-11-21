from .models import *
from .common import *
def consignor(s,d):
 lock(s);x=Consignor(**d);s.add(x);s.flush();return x
def agreement(s,d):
 c=lock(s);get(s,Consignor,d['consignor_id'])
 if time(d['ends'])<=time(c.now):fail('新寄售协议到期时间必须晚于当前时间')
 d['ends']=time(d['ends']).isoformat();x=Agreement(**d);s.add(x);s.flush();return x
def amend(s,id,d):
 lock(s);a=get(s,Agreement,id)
 if d['commission_bp']==a.commission_bp:fail('新佣金与原佣金相同，无需修订')
 x=Amendment(agreement_id=id,old_bp=a.commission_bp,new_bp=d['commission_bp'],reason=d['reason']);s.add(x);a.commission_bp=d['commission_bp'];s.flush();return x
def intake(s,d):
 c=lock(s);a=get(s,Agreement,d['agreement_id'])
 if a.state!='active' or time(c.now)>=time(a.ends):fail('协议已结束，不接收新衣物')
 if d['price_cents']<a.floor_cents:fail('标价不得低于协议底价')
 x=Garment(**d);s.add(x);s.flush();return x

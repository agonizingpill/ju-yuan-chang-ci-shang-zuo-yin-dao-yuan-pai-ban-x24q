from fastapi import HTTPException
from sqlalchemy import select
from .lost_models import LostItem,ItemClaim
from .lost_inventory_models import LostInventory,LostInventoryLine,LostItemHold
from .zones_service import performance_context
from .staff_service import locked
from .services import pack,audit

def view(x,u):
    d=pack(x)
    if u.role=='viewer':d['identifying_detail']='保管识别信息，仅场务核验'
    return d
def create(s,d,u):
    performance_context(s,d.performance_id);data=d.model_dump();data['location']=data['location'].strip();x=LostItem(**data);s.add(x);s.flush();audit(s,u,'登记保管失物 '+x.code);return view(x,u)
def claim(s,d,u):
    x=locked(s,LostItem,d.item_id)
    if x.state!='stored':raise HTTPException(409,'失物已交付，不能再次认领')
    if s.scalar(select(ItemClaim.id).where(ItemClaim.item_id==x.id,ItemClaim.claimant==d.claimant.strip(),ItemClaim.state=='pending')):raise HTTPException(409,'此认领人已有待核申请，请修订原凭据')
    c=ItemClaim(**d.model_dump());c.claimant=c.claimant.strip();s.add(c);s.flush();audit(s,u,'失物认领申请 '+x.code);return pack(c)
def revise(s,id,d,u):
    pre=s.get(ItemClaim,id)
    if not pre:raise HTTPException(404,'认领申请不存在')
    item=locked(s,LostItem,pre.item_id);x=locked(s,ItemClaim,id)
    if x.state!='pending' or item.state!='stored':raise HTTPException(409,'已交付申请不能修改凭据')
    if d.item_id!=x.item_id or d.claimant.strip()!=x.claimant:raise HTTPException(409,'认领关联及姓名不可修改')
    x.proof=d.proof;audit(s,u,'修订认领凭据 '+str(id));return pack(x)
def handover(s,id,u):
    pre=s.get(ItemClaim,id)
    if not pre:raise HTTPException(404,'认领申请不存在')
    preview=s.get(LostItem,pre.item_id)
    if not preview:raise HTTPException(404,'失物记录不存在')
    line=s.scalar(select(LostInventoryLine).join(LostInventory,LostInventory.id==LostInventoryLine.inventory_id).where(LostInventoryLine.item_id==preview.id,LostInventory.state=='open'))
    if line:locked(s,LostInventory,line.inventory_id)
    item=locked(s,LostItem,pre.item_id)
    hold=s.scalar(select(LostItemHold).where(LostItemHold.item_id==item.id,LostItemHold.state=='active'))
    if hold:raise HTTPException(409,'该件盘点缺失已停付，须经理凭原因解除后才能交付')
    x=locked(s,ItemClaim,id)
    if item.state!='stored' or x.state!='pending':raise HTTPException(409,'此失物已经交付，不能重复领取')
    if x.proof.strip().casefold()!=item.identifying_detail.strip().casefold():raise HTTPException(409,'认领描述凭据与保管识别信息不符，拒绝交付')
    x.state='handed_over';x.handed_by=u.email;item.state='handed_over'
    line=s.scalar(select(LostInventoryLine).join(LostInventory,LostInventory.id==LostInventoryLine.inventory_id).where(LostInventoryLine.item_id==item.id,LostInventory.state=='open').with_for_update().execution_options(populate_existing=True))
    if line:
        line.claim_id=x.id;line.result='delivered';line.note='盘点期间交付，凭领取记录销账'
        audit(s,u,'盘点期间交付销账 '+item.code)
    audit(s,u,'经理核验交付 '+item.code+'→'+x.claimant);return pack(x)

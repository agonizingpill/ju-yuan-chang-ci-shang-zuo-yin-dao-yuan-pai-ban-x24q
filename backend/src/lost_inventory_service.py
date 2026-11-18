from datetime import datetime,timezone
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from .lost_inventory_models import LostInventory,LostInventoryLine,LostItemHold
from .lost_models import LostItem,ItemClaim
from .staff_service import locked
from .services import pack,audit

def stamp():return datetime.now(timezone.utc).isoformat()
def get_inventory(s,id,for_update=False):
    if for_update:x=locked(s,LostInventory,id)
    else:
        x=s.get(LostInventory,id)
        if not x:raise HTTPException(404,'盘点单不存在')
    return x
def require_open(x):
    if x.state!='open':raise HTTPException(409,'盘点已封存，不能继续修订或记实')
def lines_of(s,inv,for_update=False):
    q=select(LostInventoryLine).where(LostInventoryLine.inventory_id==inv.id).order_by(LostInventoryLine.id)
    if for_update:q=q.with_for_update().execution_options(populate_existing=True)
    return s.scalars(q).all()
def latest_claim(s,item_id):
    return s.scalar(select(ItemClaim).where(ItemClaim.item_id==item_id,ItemClaim.state=='handed_over').order_by(ItemClaim.id.desc()))
def active_hold(s,item_id,for_update=False):
    q=select(LostItemHold).where(LostItemHold.item_id==item_id,LostItemHold.state=='active')
    if for_update:q=q.with_for_update().execution_options(populate_existing=True)
    return s.scalar(q)
def line_view(s,x):
    item=s.get(LostItem,x.item_id);claim=s.get(ItemClaim,x.claim_id) if x.claim_id else None;hold=active_hold(s,x.item_id)
    return pack(x)|{'item_state':item.state if item else 'unknown','location':item.location if item else '','claimant':claim.claimant if claim else None,'claim_state':claim.state if claim else None,'held':bool(hold),'hold_id':hold.id if hold else None}
def summary(s,x):
    rows=lines_of(s,x)
    data=pack(x);data['lines']=[line_view(s,l) for l in rows]
    for value in ('pending','seen','missing','delivered'):data[value]=sum(l.result==value for l in rows)
    data['held']=sum(1 for l in data['lines'] if l['held'])
    return data
def open_inventory(s,d,u):
    location=d.location.strip()
    if s.scalar(select(LostInventory.id).where(LostInventory.location==location,LostInventory.state=='open')):raise HTTPException(409,f'保管位置“{location}”已有待结盘点，同位置只容一份；请先结案')
    held=select(LostItemHold.item_id).where(LostItemHold.state=='active')
    items=s.scalars(select(LostItem).where(LostItem.location==location,LostItem.state=='stored',LostItem.id.not_in(held)).order_by(LostItem.id)).all()
    if not items:raise HTTPException(409,'该保管位置当前没有在库失物，不能开启空盘点')
    x=LostInventory(location=location,opened_by=u.email,opened_at=stamp())
    s.add(x)
    try:s.flush()
    except IntegrityError:
        s.rollback();raise HTTPException(409,f'保管位置“{location}”已有待结盘点，同位置只容一份；请先结案')
    for item in items:s.add(LostInventoryLine(inventory_id=x.id,item_id=item.id,item_code=item.code))
    s.flush();audit(s,u,f'开启失物盘点 #{x.id} @ {location}（固定 {len(items)} 件，新入库留到下次）');return summary(s,x)
def line_in(s,inv,line_id):
    x=s.scalar(select(LostInventoryLine).where(LostInventoryLine.id==line_id,LostInventoryLine.inventory_id==inv.id))
    if not x:raise HTTPException(404,'盘点明细不属于本盘点单')
    return x
def locked_line(s,inv,line_id):
    item=locked(s,LostItem,line_in(s,inv,line_id).item_id)
    return s.scalar(select(LostInventoryLine).where(LostInventoryLine.id==line_id,LostInventoryLine.inventory_id==inv.id).with_for_update().execution_options(populate_existing=True))
def mark(s,id,line_id,d,u):
    inv=get_inventory(s,id,for_update=True);require_open(inv)
    line=locked_line(s,inv,line_id)
    if line.result!='pending':raise HTTPException(409,'该件已登记盘点结果，不能重复记实')
    item=s.get(LostItem,line.item_id)
    if item.state!='stored':raise HTTPException(409,'该件已凭领取记录交付，不计缺失')
    if d.result not in ('seen','missing'):raise HTTPException(409,'仅可逐件记实“见在”或“缺失”；交付销账在结案时按领取记录核对')
    line.result=d.result;line.note=d.note
    if d.result=='missing':line.claim_id=None
    else:
        claim=latest_claim(s,item.id);line.claim_id=claim.id if claim else None
    s.flush();audit(s,u,f'盘点#{inv.id} 记实 {item.code}：{"见在" if d.result=="seen" else "缺失"}')
    return summary(s,inv)
def revise_line(s,id,line_id,d,u):
    inv=get_inventory(s,id,for_update=True);require_open(inv)
    if d.inventory_revision!=inv.revision:raise HTTPException(409,f'清单已更新至 r{inv.revision}，旧版核对不能修订，请重新载入')
    line=locked_line(s,inv,line_id)
    if line.result=='pending':raise HTTPException(409,'该件尚未记实，请直接登记见在或缺失')
    if d.result not in ('seen','missing'):raise HTTPException(409,'仅可修订为“见在”或“缺失”；交付销账由结案核对')
    item=s.get(LostItem,line.item_id)
    if item.state!='stored':raise HTTPException(409,'该件已凭领取记录交付，不能记为缺失')
    line.result=d.result;line.note=d.note
    claim=latest_claim(s,item.id);line.claim_id=claim.id if d.result=='seen' and claim else None
    inv.revision+=1;s.flush();audit(s,u,f'盘点修订 #{inv.id}：{item.code}→{"见在" if d.result=="seen" else "缺失"}（r{inv.revision}）')
    return summary(s,inv)
def seal(s,id,d,u):
    inv=get_inventory(s,id,for_update=True)
    # 先锁交付路径可能持有的失物行，避免与交付并发时反向加锁
    if inv.state=='sealed':raise HTTPException(409,'盘点已封存，不能重复结案')
    if d.revision!=inv.revision:raise HTTPException(409,f'清单已修订至 r{inv.revision}，旧版核对拒绝结案，请重新载入')
    for item_id in sorted({l.item_id for l in lines_of(s,inv)}):locked(s,LostItem,item_id)
    rows=lines_of(s,inv,for_update=True)
    if any(l.result=='pending' for l in rows):raise HTTPException(409,'仍有未记实失物，须逐件记为见在或缺失后才能结案')
    seen=missing=delivered=0
    for line in rows:
        item=locked(s,LostItem,line.item_id)
        claim=latest_claim(s,item.id)
        if claim:
            line.result='delivered';line.claim_id=claim.id;line.note='已凭领取记录交付，不计缺失';delivered+=1
            continue
        if item.state!='stored':raise HTTPException(409,f'失物 {item.code} 状态异常，拒绝结案')
        if line.result=='missing':
            if not active_hold(s,item.id,for_update=True):
                s.add(LostItemHold(item_id=item.id,inventory_id=inv.id,reason=line.note or '盘点未见实物',created_by=u.email,created_at=stamp()))
            missing+=1
        else:seen+=1
    inv.state='sealed';inv.sealed_by=u.email;inv.sealed_at=stamp();s.flush()
    audit(s,u,f'经理结案封存盘点 #{inv.id} @ {inv.location}：见在 {seen}、缺失停付 {missing}、已交付销账 {delivered}')
    return summary(s,inv)
def release(s,hold_id,d,u):
    preview=s.get(LostItemHold,hold_id)
    if not preview:raise HTTPException(404,'停付记录不存在')
    item=locked(s,LostItem,preview.item_id)
    hold=s.scalar(select(LostItemHold).where(LostItemHold.id==hold_id).with_for_update().execution_options(populate_existing=True))
    if hold.state!='active':raise HTTPException(409,'该停付已解除，不能重复解除')
    inv=get_inventory(s,hold.inventory_id)
    if inv.state!='sealed':raise HTTPException(409,'盘点尚待结案，缺失件停付在结案时一起生效')
    if latest_claim(s,item.id):raise HTTPException(409,'该件已有领取记录，应走结案重核，不能人工解除停付')
    hold.state='released';hold.released_by=u.email;hold.released_at=stamp();hold.release_reason=d.reason
    s.flush();audit(s,u,f'经理凭原因解除缺失停付 {item.code}：{d.reason}')
    return hold_view(s,hold)
def hold_view(s,x):
    inv=s.get(LostInventory,x.inventory_id);item=s.get(LostItem,x.item_id)
    return pack(x)|{'item_code':item.code if item else '','item_state':item.state if item else '','location':inv.location if inv else '','inventory_revision':inv.revision if inv else None,'inventory_state':inv.state if inv else None}

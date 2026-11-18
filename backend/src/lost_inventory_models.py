from sqlalchemy import String,Integer,Text,ForeignKey,CheckConstraint,Index,UniqueConstraint,text
from sqlalchemy.orm import Mapped,mapped_column
from .db import Base
class LostInventory(Base):
    __tablename__='lost_inventories'
    __table_args__=(CheckConstraint("state in ('open','sealed')"),CheckConstraint('revision >= 1'),Index('one_open_inventory_per_location','location',unique=True,postgresql_where=text("state = 'open'")))
    id:Mapped[int]=mapped_column(primary_key=True)
    location:Mapped[str]=mapped_column(String(100))
    state:Mapped[str]=mapped_column(String(20),default='open')
    revision:Mapped[int]=mapped_column(Integer,default=1)
    opened_by:Mapped[str]=mapped_column(String(100))
    opened_at:Mapped[str]=mapped_column(String(40))
    sealed_by:Mapped[str|None]=mapped_column(String(100),nullable=True)
    sealed_at:Mapped[str|None]=mapped_column(String(40),nullable=True)
class LostInventoryLine(Base):
    __tablename__='lost_inventory_lines'
    __table_args__=(CheckConstraint("result in ('pending','seen','missing','delivered')"),UniqueConstraint('inventory_id','item_id'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    inventory_id:Mapped[int]=mapped_column(ForeignKey('lost_inventories.id'))
    item_id:Mapped[int]=mapped_column(ForeignKey('lost_items.id'))
    item_code:Mapped[str]=mapped_column(String(50))
    result:Mapped[str]=mapped_column(String(20),default='pending')
    note:Mapped[str]=mapped_column(Text,default='')
    claim_id:Mapped[int|None]=mapped_column(ForeignKey('item_claims.id'),nullable=True)
class LostItemHold(Base):
    __tablename__='lost_item_holds'
    __table_args__=(CheckConstraint("state in ('active','released')"),Index('one_active_hold_per_item','item_id',unique=True,postgresql_where=text("state = 'active'")))
    id:Mapped[int]=mapped_column(primary_key=True)
    item_id:Mapped[int]=mapped_column(ForeignKey('lost_items.id'))
    inventory_id:Mapped[int]=mapped_column(ForeignKey('lost_inventories.id'))
    reason:Mapped[str]=mapped_column(Text)
    state:Mapped[str]=mapped_column(String(20),default='active')
    created_by:Mapped[str]=mapped_column(String(100))
    created_at:Mapped[str]=mapped_column(String(40))
    released_by:Mapped[str|None]=mapped_column(String(100),nullable=True)
    released_at:Mapped[str|None]=mapped_column(String(40),nullable=True)
    release_reason:Mapped[str]=mapped_column(Text,default='')

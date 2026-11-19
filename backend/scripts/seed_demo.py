import os
from sqlalchemy import select
from src.config.database import Base, engine, Session
from src.models.entities import User, Node, Scenario, PageCopy
from src.utils.security import hash_password
from src.services.page_service import PAGE_KEYS, checksum_of

def seed():
    Base.metadata.create_all(engine)
    if os.getenv('SEED_DEMO','true').lower() != 'true': return
    with Session() as db:
        for role in ('admin', 'operator', 'viewer'):
            email = role + '@hengshi.local'
            if not db.scalar(select(User).where(User.email == email)):
                db.add(User(email=email, role=role, password_hash=hash_password('Drill2026!')))
        for name, role, pos in [('hs-primary','leader',8100),('hs-east','follower',8100),('hs-west','follower',8098)]:
            if not db.scalar(select(Node).where(Node.name == name)):
                db.add(Node(name=name, role=role, term=6, log_position=pos, available=True, fenced=False, vote_enabled=True))
        db.flush()
        nodes = db.scalars(select(Node).order_by(Node.id)).all()
        for node in nodes:
            for key in PAGE_KEYS:
                exists = db.scalar(select(PageCopy).where(PageCopy.node_id == node.id,
                                                          PageCopy.page_key == key))
                if not exists:
                    db.add(PageCopy(node_id=node.id, page_key=key, version=1,
                                    checksum=checksum_of(key, 1)))
        for name, fence in [('晚班隔离演练',True),('缺少隔离证明',False)]:
            if not db.scalar(select(Scenario).where(Scenario.name == name)):
                db.add(Scenario(name=name, fail_primary=True, fence_primary=fence, max_data_loss=0, required_term=7))
        db.commit()
if __name__ == '__main__': seed()

"""数据模型。

架构选型：追加式双时间事件账 + 可重建投影。
- events 为唯一事实来源（append-only，DB 触发器禁止 UPDATE/DELETE）。
- identity_edges / rule_versions / rule_withdrawals / member_directory 均为可重建投影，
  由事件重放生成，可随时通过 /api/admin/rebuild 全量重建。
"""
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Event(Base):
    """接入账：追加式双时间事件账。

    每行保存：来源哈希(source_hash)、链式哈希(chain_hash)、两类时间
    (biz_time 业务发生时间 / ingested_at 平台接收时间)、修订链(supersedes_id)、
    幂等键(idempotency_key)。
    """

    __tablename__ = "events"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    idempotency_key = Column(String(128), unique=True, nullable=False)
    source = Column(String(32), nullable=False)  # pos | online | franchise_offline | identity_desk | rule_admin
    event_type = Column(String(32), nullable=False)
    source_hash = Column(String(64), nullable=False)  # sha256(canonical payload)
    chain_hash = Column(String(64), nullable=False)  # sha256(prev chain_hash + row) 防篡改链
    biz_time = Column(DateTime(timezone=True), nullable=False)
    ingested_at = Column(DateTime(timezone=True), nullable=False)
    actor = Column(String(64))
    supersedes_id = Column(BigInteger, ForeignKey("events.id"), nullable=True)
    payload = Column(JSONB, nullable=False)


class IdentityEdge(Base):
    """身份关系图投影：合并/拆分/待核/撤销的有效区间（双时间区间）。"""

    __tablename__ = "identity_edges"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    relation_event_id = Column(BigInteger, ForeignKey("events.id"), nullable=False)
    member_a = Column(String(64), nullable=False)
    member_b = Column(String(64), nullable=False)
    relation = Column(String(16), nullable=False)  # merge | split | pending | revoke
    status = Column(String(16), nullable=False)  # active | split | revoked | pending
    biz_from = Column(DateTime(timezone=True), nullable=False)
    biz_to = Column(DateTime(timezone=True), nullable=True)
    sys_from = Column(DateTime(timezone=True), nullable=False)
    sys_to = Column(DateTime(timezone=True), nullable=True)
    target_edge_id = Column(BigInteger, nullable=True)  # split/revoke 指向的 merge 边
    reason = Column(Text)


class RuleVersion(Base):
    """等级规则版本投影：按门店、商品范围与生效区间修订。"""

    __tablename__ = "rule_versions"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    rule_event_id = Column(BigInteger, ForeignKey("events.id"), nullable=False)
    rule_id = Column(String(64), nullable=False)
    version = Column(Integer, nullable=False)
    supersedes_version = Column(Integer, nullable=True)
    store_scope = Column(JSONB, nullable=False)  # "ALL" 或门店列表
    product_scope = Column(JSONB, nullable=False)  # "ALL" 或品类列表
    window_days = Column(Integer, nullable=False)
    thresholds = Column(JSONB, nullable=False)  # {"SILVER": 300, "GOLD": 1000}
    effective_from = Column(DateTime(timezone=True), nullable=False)
    effective_to = Column(DateTime(timezone=True), nullable=True)
    sys_from = Column(DateTime(timezone=True), nullable=False)


class RuleWithdrawal(Base):
    """规则撤回投影：支持部分（按门店范围）追溯撤回。"""

    __tablename__ = "rule_withdrawals"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    withdraw_event_id = Column(BigInteger, ForeignKey("events.id"), nullable=False)
    rule_id = Column(String(64), nullable=False)
    target_version = Column(Integer, nullable=False)
    store_scope = Column(JSONB, nullable=False)
    withdraw_from = Column(DateTime(timezone=True), nullable=False)
    withdraw_to = Column(DateTime(timezone=True), nullable=True)
    sys_from = Column(DateTime(timezone=True), nullable=False)
    reason = Column(Text)


class MemberDirectory(Base):
    """会员名录投影（仅索引已知会员键，不保存会覆盖历史的“当前主档”）。"""

    __tablename__ = "member_directory"

    member_key = Column(String(64), primary_key=True)
    display_name = Column(String(128))
    first_seen_event_id = Column(BigInteger)


class Semantics(Base):
    """时间语义目录：定义查询口径。"""

    __tablename__ = "semantics"

    code = Column(String(32), primary_key=True)
    name = Column(String(64), nullable=False)
    biz_policy = Column(String(16), nullable=False)  # query
    sys_policy = Column(String(16), nullable=False)  # query | now | both
    description = Column(Text)


class Publication(Base):
    """发布库：冻结证据水位、身份关系图版本与规则集合版本。"""

    __tablename__ = "publications"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    biz_as_of = Column(DateTime(timezone=True), nullable=False)
    sys_as_of = Column(DateTime(timezone=True), nullable=False)
    watermark_event_id = Column(BigInteger, nullable=False)  # 证据水位
    identity_graph_version = Column(BigInteger, nullable=False)  # 关系图冻结版本(最大身份事件id)
    rule_set_version = Column(BigInteger, nullable=False)  # 规则集合冻结版本(最大规则事件id)
    semantics_code = Column(String(32), nullable=False)


class PublicationRow(Base):
    __tablename__ = "publication_rows"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    publication_id = Column(BigInteger, ForeignKey("publications.id"), nullable=False)
    member_key = Column(String(64), nullable=False)
    tier = Column(String(16), nullable=False)
    spend = Column(Numeric(14, 2), nullable=False)
    detail = Column(JSONB, nullable=False)


class Diff(Base):
    __tablename__ = "diffs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    pub_a = Column(BigInteger, ForeignKey("publications.id"), nullable=False)
    pub_b = Column(BigInteger, ForeignKey("publications.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)


class DiffItem(Base):
    """差异归因：把资格变化连到具体事件。"""

    __tablename__ = "diff_items"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    diff_id = Column(BigInteger, ForeignKey("diffs.id"), nullable=False)
    member_key = Column(String(64), nullable=False)
    before = Column(JSONB, nullable=False)
    after = Column(JSONB, nullable=False)
    causes = Column(JSONB, nullable=False)  # [{event_id, cause, detail}]


class Impact(Base):
    """影响索引：变化对营销名单、门店预算、训练样本的下游影响。"""

    __tablename__ = "impacts"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    diff_id = Column(BigInteger, ForeignKey("diffs.id"), nullable=False)
    dimension = Column(String(16), nullable=False)  # marketing | budget | training
    entity = Column(String(128), nullable=False)  # 下游实体键
    member_key = Column(String(64), nullable=False)
    change_type = Column(String(32), nullable=False)
    before = Column(JSONB)
    after = Column(JSONB)
    attributed_event_ids = Column(JSONB)


class User(Base):
    __tablename__ = "users"

    username = Column(String(64), primary_key=True)
    password = Column(String(128), nullable=False)
    role = Column(String(32), nullable=False)
    token = Column(String(128), nullable=False)

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class User(TimestampMixin, Base):
    __tablename__ = "users"
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(30), index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    auth_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    security_question_1: Mapped[str | None] = mapped_column(String(40), nullable=True)
    security_answer_hash_1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    security_question_2: Mapped[str | None] = mapped_column(String(40), nullable=True)
    security_answer_hash_2: Mapped[str | None] = mapped_column(String(255), nullable=True)


class AuthSession(TimestampMixin, Base):
    __tablename__ = "auth_sessions"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    auth_version: Mapped[int] = mapped_column(Integer, nullable=False)
    generation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)


class SalesLoginEvent(TimestampMixin, Base):
    __tablename__ = "sales_login_events"
    __table_args__ = (Index("ix_sales_login_events_user_created", "user_id", "created_at"),)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    source_ip: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)


class AuthRefreshToken(TimestampMixin, Base):
    __tablename__ = "auth_refresh_tokens"
    session_id: Mapped[int] = mapped_column(ForeignKey("auth_sessions.id"), index=True)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Customer(TimestampMixin, Base):
    __tablename__ = "customers"
    name: Mapped[str] = mapped_column(String(120), index=True)
    email: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="潜在客户", index=True)
    level: Mapped[str] = mapped_column(String(20), default="standard")
    sales_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, unique=True, index=True)
    balance: Mapped[float] = mapped_column(Float, default=0)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class Region(TimestampMixin, Base):
    __tablename__ = "regions"
    name: Mapped[str] = mapped_column(String(80), unique=True)
    country_code: Mapped[str] = mapped_column(String(10))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Product(TimestampMixin, Base):
    __tablename__ = "products"
    name: Mapped[str] = mapped_column(String(120), index=True)
    product_type: Mapped[str] = mapped_column(String(30), index=True)
    price: Mapped[float] = mapped_column(Float)
    billing_days: Mapped[int] = mapped_column(Integer, default=30)
    region_id: Mapped[int] = mapped_column(ForeignKey("regions.id"), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    is_in_stock: Mapped[bool] = mapped_column(Boolean, default=True)


class Node(TimestampMixin, Base):
    __tablename__ = "nodes"
    name: Mapped[str] = mapped_column(String(120), index=True)
    server: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer)
    protocol: Mapped[str] = mapped_column(String(30))
    region_id: Mapped[int] = mapped_column(ForeignKey("regions.id"), index=True)
    health_status: Mapped[str] = mapped_column(String(30), default="unknown", index=True)
    health_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    health_detail: Mapped[str | None] = mapped_column(String(255), nullable=True)
    health_checked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class CustomerLine(TimestampMixin, Base):
    __tablename__ = "customer_lines"
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("nodes.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    static_ip: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subscription_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(nullable=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    customer_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    node_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    subscription_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    node_qr_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    subscription_qr_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True, index=True)


class Order(TimestampMixin, Base):
    __tablename__ = "orders"
    order_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    source_type: Mapped[str] = mapped_column(String(30), default="owned", server_default="owned", nullable=False, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True)
    resource_category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    supplier_product_id: Mapped[int | None] = mapped_column(ForeignKey("supplier_products.id", name="fk_orders_supplier_product_id"), nullable=True, index=True)
    supplier_region_id: Mapped[int | None] = mapped_column(ForeignKey("supplier_regions.id", name="fk_orders_supplier_region_id"), nullable=True, index=True)
    supplier_segment_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("supplier_ip_segment_snapshots.id", name="fk_orders_supplier_segment_snapshot_id"), nullable=True, index=True)
    supplier_quote_id: Mapped[int | None] = mapped_column(ForeignKey("supplier_quotes.id", name="fk_orders_supplier_quote_id"), nullable=True, index=True)
    supplier_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    amount: Mapped[float] = mapped_column(Float)
    payment_method: Mapped[str] = mapped_column(String(30), default="contact_manager")
    status: Mapped[str] = mapped_column(String(30), default="pending_confirm", index=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    billing_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    unit_price: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    total_amount: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    region_id: Mapped[int | None] = mapped_column(ForeignKey("regions.id"), nullable=True)
    region_name: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    product_name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    payment_status: Mapped[str] = mapped_column(String(30), default="unpaid", nullable=False)
    assigned_sales_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    order_type: Mapped[str] = mapped_column(String(20), default="purchase", nullable=False)
    renewal_line_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "customer_lines.id",
            name="fk_orders_renewal_line_id_customer_lines",
            use_alter=True,
        ),
        nullable=True,
        index=True,
    )
    # 兼容旧接口的首条交付线路快照；正式一对多关系由 customer_lines.order_id 维护。
    delivered_line_id: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True, index=True)
    delivered_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    renewal_completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Notification(TimestampMixin, Base):
    __tablename__ = "notifications"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(160))
    content: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(30), default="system")
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class InboxThread(TimestampMixin, Base):
    __tablename__ = "inbox_threads"
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    assigned_sales_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    subject: Mapped[str] = mapped_column(String(160), index=True)
    status: Mapped[str] = mapped_column(String(30), default="open", index=True)
    related_order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True, index=True)
    related_line_id: Mapped[int | None] = mapped_column(ForeignKey("customer_lines.id"), nullable=True, index=True)
    last_message_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    customer_unread_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    staff_unread_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class InboxMessage(TimestampMixin, Base):
    __tablename__ = "inbox_messages"
    thread_id: Mapped[int] = mapped_column(ForeignKey("inbox_threads.id"), index=True)
    sender_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    sender_role: Mapped[str] = mapped_column(String(30), index=True)
    content: Mapped[str] = mapped_column(Text)
    customer_read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    staff_read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class HealthIncident(TimestampMixin, Base):
    __tablename__ = "health_incidents"
    node_id: Mapped[int] = mapped_column(ForeignKey("nodes.id"), index=True)
    fault_type: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(String(255))
    started_at: Mapped[datetime] = mapped_column(nullable=False)
    recovered_at: Mapped[datetime | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="open", index=True)
    acknowledged_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    handling_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class AuditLog(TimestampMixin, Base):
    __tablename__ = "audit_logs"
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    resource_type: Mapped[str] = mapped_column(String(50))
    resource_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class Supplier(TimestampMixin, Base):
    __tablename__ = "suppliers"
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    adapter_type: Mapped[str] = mapped_column(String(80))
    base_url: Mapped[str] = mapped_column(String(255))
    secret_ref: Mapped[str] = mapped_column(String(160))
    encrypted_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    auto_purchase_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ResourcePool(TimestampMixin, Base):
    __tablename__ = "resource_pools"
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    strategy: Mapped[str] = mapped_column(String(50), default="priority_manual_fallback")


class ResourcePoolSupplier(TimestampMixin, Base):
    __tablename__ = "resource_pool_suppliers"
    __table_args__ = (UniqueConstraint("pool_id", "supplier_id", name="uq_resource_pool_supplier"),)
    pool_id: Mapped[int] = mapped_column(ForeignKey("resource_pools.id"), index=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    priority: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class SupplierProduct(TimestampMixin, Base):
    __tablename__ = "supplier_products"
    __table_args__ = (UniqueConstraint("supplier_id", "external_product_ref", name="uq_supplier_product_ref"),)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    external_product_ref: Mapped[str] = mapped_column(String(80))
    name_snapshot: Mapped[str] = mapped_column(String(200))
    suffix: Mapped[str | None] = mapped_column(String(50), nullable=True)
    resource_category: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    local_product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), nullable=True, index=True)
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    is_available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    source_endpoint: Mapped[str] = mapped_column(String(255))
    last_synced_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class SupplierRegion(TimestampMixin, Base):
    __tablename__ = "supplier_regions"
    __table_args__ = (
        UniqueConstraint(
            "supplier_product_id", "external_country_ref", "external_city_ref",
            name="uq_supplier_product_region_ref",
        ),
    )
    supplier_product_id: Mapped[int] = mapped_column(ForeignKey("supplier_products.id"), index=True)
    external_country_ref: Mapped[str] = mapped_column(String(80))
    country_code: Mapped[str] = mapped_column(String(10), index=True)
    country_name: Mapped[str] = mapped_column(String(120))
    external_city_ref: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    city_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    local_region_id: Mapped[int | None] = mapped_column(ForeignKey("regions.id"), nullable=True, index=True)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    source_endpoint: Mapped[str] = mapped_column(String(255))
    last_synced_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ProductSupplyRule(TimestampMixin, Base):
    __tablename__ = "product_supply_rules"
    __table_args__ = (
        UniqueConstraint("product_id", "region_id", "pool_id", name="uq_product_region_pool_rule"),
    )
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    region_id: Mapped[int] = mapped_column(ForeignKey("regions.id"), index=True)
    pool_id: Mapped[int] = mapped_column(ForeignKey("resource_pools.id"), index=True)
    supplier_product_id: Mapped[int] = mapped_column(ForeignKey("supplier_products.id"), index=True)
    supplier_region_id: Mapped[int] = mapped_column(ForeignKey("supplier_regions.id"), index=True)
    mode: Mapped[str] = mapped_column(String(30), default="manual", nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)


class SupplierQuote(TimestampMixin, Base):
    __tablename__ = "supplier_quotes"
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    supplier_product_id: Mapped[int | None] = mapped_column(ForeignKey("supplier_products.id"), nullable=True, index=True)
    external_product_ref: Mapped[str] = mapped_column(String(80), index=True)
    country_code: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    duration_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    total_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    source_endpoint: Mapped[str] = mapped_column(String(255))
    quoted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)


class SupplierInventorySnapshot(TimestampMixin, Base):
    __tablename__ = "supplier_inventory_snapshots"
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    supplier_product_id: Mapped[int] = mapped_column(ForeignKey("supplier_products.id"), index=True)
    supplier_region_id: Mapped[int | None] = mapped_column(ForeignKey("supplier_regions.id"), nullable=True, index=True)
    country_code: Mapped[str] = mapped_column(String(10), index=True)
    city_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    availability: Mapped[str] = mapped_column(String(30), index=True)
    available_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_endpoint: Mapped[str] = mapped_column(String(255))
    checked_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)


class SupplierSyncRun(TimestampMixin, Base):
    __tablename__ = "supplier_sync_runs"
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    sync_type: Mapped[str] = mapped_column(String(30), index=True)
    status: Mapped[str] = mapped_column(String(30), default="running", nullable=False, index=True)
    items_seen: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    items_changed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SupplierIpSegmentSnapshot(TimestampMixin, Base):
    __tablename__ = "supplier_ip_segment_snapshots"
    __table_args__ = (
        UniqueConstraint("sync_run_id", "cidr", name="uq_supplier_segment_run_cidr"),
    )
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    supplier_product_id: Mapped[int] = mapped_column(ForeignKey("supplier_products.id"), index=True)
    supplier_region_id: Mapped[int | None] = mapped_column(ForeignKey("supplier_regions.id"), nullable=True, index=True)
    sync_run_id: Mapped[int] = mapped_column(ForeignKey("supplier_sync_runs.id"), index=True)
    country_code: Mapped[str] = mapped_column(String(10), index=True)
    city_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    cidr: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_segment_sufficient: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_request_sufficient: Mapped[bool] = mapped_column(Boolean, nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)


class RetailPriceRule(TimestampMixin, Base):
    __tablename__ = "retail_price_rules"
    __table_args__ = (
        UniqueConstraint("resource_category", "country_code", "billing_days", name="uq_retail_price_category_country_days"),
        CheckConstraint("billing_days >= 30", name="ck_retail_price_minimum_30_days"),
        CheckConstraint("unit_price > 0", name="ck_retail_price_positive"),
    )
    resource_category: Mapped[str] = mapped_column(String(50), index=True)
    country_code: Mapped[str] = mapped_column(String(10), index=True)
    billing_days: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="CNY", nullable=False)
    source_version: Mapped[str] = mapped_column(String(100), nullable=False)


class WalletAccount(TimestampMixin, Base):
    __tablename__ = "wallet_accounts"
    __table_args__ = (
        CheckConstraint("balance_cents >= 0", name="ck_wallet_balance_nonnegative"),
        UniqueConstraint("user_id"),
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    balance_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="CNY", nullable=False)


class PaymentChannelConfig(TimestampMixin, Base):
    __tablename__ = "payment_channel_configs"
    merchant_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    encrypted_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    channel_types: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    min_amount_cents: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    max_amount_cents: Mapped[int] = mapped_column(Integer, default=10000000, nullable=False)
    online_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PaymentOrder(TimestampMixin, Base):
    __tablename__ = "payment_orders"
    __table_args__ = (
        CheckConstraint("amount_cents > 0", name="ck_payment_order_amount_positive"),
        UniqueConstraint("out_trade_no", name="uq_payment_order_out_trade_no"),
        UniqueConstraint("idempotency_key", name="uq_payment_order_idempotency_key"),
    )
    out_trade_no: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True, index=True)
    sales_id_snapshot: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel: Mapped[str] = mapped_column(String(30), nullable=False)
    provider_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False, index=True)
    platform_trade_no: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    payment_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)


class WalletTransaction(TimestampMixin, Base):
    __tablename__ = "wallet_transactions"
    __table_args__ = (
        CheckConstraint("amount_cents != 0", name="ck_wallet_transaction_amount_nonzero"),
        CheckConstraint("balance_before_cents >= 0 AND balance_after_cents >= 0", name="ck_wallet_transaction_balances_nonnegative"),
        UniqueConstraint("idempotency_key", name="uq_wallet_transaction_idempotency_key"),
    )
    wallet_account_id: Mapped[int] = mapped_column(ForeignKey("wallet_accounts.id"), index=True)
    transaction_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_before_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    payment_order_id: Mapped[int | None] = mapped_column(ForeignKey("payment_orders.id"), nullable=True, index=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True, index=True)
    remark: Mapped[str | None] = mapped_column(String(500), nullable=True)


class PaymentRefund(TimestampMixin, Base):
    __tablename__ = "payment_refunds"
    __table_args__ = (UniqueConstraint("payment_order_id"),)
    payment_order_id: Mapped[int] = mapped_column(ForeignKey("payment_orders.id"), index=True)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel: Mapped[str] = mapped_column(String(30), nullable=False)
    external_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="completed", nullable=False)


class SalesCommissionEntry(TimestampMixin, Base):
    __tablename__ = "sales_commission_entries"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_sales_commission_idempotency_key"),)
    sales_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    payment_order_id: Mapped[int] = mapped_column(ForeignKey("payment_orders.id"), index=True)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    basis_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    rate_basis_points: Mapped[int] = mapped_column(Integer, default=1500, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)


class CatalogPublication(TimestampMixin, Base):
    __tablename__ = "catalog_publications"
    __table_args__ = (UniqueConstraint("product_id", "version", name="uq_catalog_publication_version"),)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    region_id: Mapped[int] = mapped_column(ForeignKey("regions.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    supplier_product_id: Mapped[int] = mapped_column(ForeignKey("supplier_products.id"))
    supplier_region_id: Mapped[int] = mapped_column(ForeignKey("supplier_regions.id"))
    inventory_snapshot_id: Mapped[int] = mapped_column(ForeignKey("supplier_inventory_snapshots.id"))
    quote_snapshot_id: Mapped[int] = mapped_column(ForeignKey("supplier_quotes.id"))
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    withdrawn_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    before_values: Mapped[dict] = mapped_column(JSON, nullable=False)
    after_values: Mapped[dict] = mapped_column(JSON, nullable=False)
    snapshot_version: Mapped[dict] = mapped_column(JSON, nullable=False)


Index("ix_customer_lines_customer_status", CustomerLine.customer_id, CustomerLine.status)
Index("ix_orders_customer_status", Order.customer_id, Order.status)

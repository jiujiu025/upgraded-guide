from datetime import datetime

import re
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, field_validator


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    display_name: str
    role: str
    must_change_password: bool = False


class LoginIn(BaseModel):
    username: str
    password: str = Field(min_length=8)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 600
    csrf_token: str
    user: UserOut


class RegisterIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=10, max_length=128)
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=160)
    phone: str | None = Field(default=None, max_length=40)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) < 3 or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", normalized):
            raise ValueError("用户名只能包含小写字母、数字、点、下划线和短横线")
        return normalized

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        checks = (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]")
        if not all(re.search(pattern, value) for pattern in checks):
            raise ValueError("密码必须包含大小写字母、数字和特殊字符")
        return value

    @field_validator("name", "email", "phone")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("字段不能为空")
        return stripped

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("邮箱格式不正确")
        return value.lower()


class CreateSalesUserIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=80)
    display_name: str = Field(min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    password: str = Field(min_length=10, max_length=128)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) < 3 or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", normalized):
            raise ValueError("用户名只能包含小写字母、数字、点、下划线和短横线")
        return normalized

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("姓名不能为空")
        return normalized

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str | None) -> str | None:
        normalized = value.strip() if value else None
        return normalized or None

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        checks = (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]")
        if not all(re.search(pattern, value) for pattern in checks):
            raise ValueError("密码必须包含大小写字母、数字和特殊字符")
        return value


SECURITY_QUESTION_IDS = {
    "childhood_friend", "birth_city", "first_school", "favorite_teacher",
    "childhood_nickname", "first_job", "favorite_book", "favorite_movie",
}


class SecurityQuestionsUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=128)
    question_1: str
    answer_1: str = Field(min_length=2, max_length=128)
    question_2: str
    answer_2: str = Field(min_length=2, max_length=128)

    @field_validator("question_1", "question_2")
    @classmethod
    def validate_question(cls, value: str) -> str:
        if value not in SECURITY_QUESTION_IDS:
            raise ValueError("请选择有效的密保问题")
        return value

    @field_validator("answer_1", "answer_2")
    @classmethod
    def normalize_answer(cls, value: str) -> str:
        normalized = " ".join(unicodedata.normalize("NFKC", value).casefold().split())
        if len(normalized) < 2:
            raise ValueError("密保答案至少需要 2 个字符")
        if len(normalized.encode("utf-8")) > 72:
            raise ValueError("密保答案过长")
        return normalized

    @field_validator("question_2")
    @classmethod
    def questions_must_differ(cls, value: str, info):
        if value == info.data.get("question_1"):
            raise ValueError("请选择两个不同的密保问题")
        return value


class PasswordRecoveryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=80)
    question_1: str
    answer_1: str = Field(min_length=1, max_length=128)
    question_2: str
    answer_2: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) < 3 or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", normalized):
            raise ValueError("用户名格式不正确")
        return normalized

    @field_validator("question_1", "question_2")
    @classmethod
    def validate_question(cls, value: str) -> str:
        if value not in SECURITY_QUESTION_IDS:
            raise ValueError("请选择有效的密保问题")
        return value

    @field_validator("answer_1", "answer_2")
    @classmethod
    def normalize_answer(cls, value: str) -> str:
        normalized = " ".join(unicodedata.normalize("NFKC", value).casefold().split())
        if len(normalized.encode("utf-8")) > 72:
            raise ValueError("密保答案过长")
        return normalized

    @field_validator("question_2")
    @classmethod
    def questions_must_differ(cls, value: str, info):
        if value == info.data.get("question_1"):
            raise ValueError("请选择两个不同的密保问题")
        return value

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        checks = (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]")
        if not all(re.search(pattern, value) for pattern in checks):
            raise ValueError("密码必须包含大小写字母、数字和特殊字符")
        if len(value.encode("utf-8")) > 72:
            raise ValueError("密码 UTF-8 长度不能超过 72 字节")
        return value


class CustomerIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str
    phone: str | None = None
    status: str = "潜在客户"
    level: str = "standard"
    sales_id: int | None = None


class CustomerOut(CustomerIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    balance: float


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    product_type: str
    price: float
    billing_days: int
    region_id: int
    is_active: bool = True
    is_in_stock: bool = True


class PublicProductOut(ProductOut):
    region: str


class RegionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    country_code: str
    is_active: bool = True


class OrderPreviewIn(BaseModel):
    product_id: int
    region_id: int
    quantity: int = Field(default=1, ge=1, le=100)
    billing_days: int = Field(gt=0)


class OrderPreviewOut(BaseModel):
    product_id: int
    product_name: str
    region_id: int
    region: str
    quantity: int
    billing_days: int
    unit_price: float
    discount: float
    total_amount: float


class LineIn(BaseModel):
    customer_id: int
    node_id: int
    product_id: int
    static_ip: str | None = None
    expires_at: datetime
    customer_note: str | None = None


class LineOut(LineIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    started_at: datetime
    status: str


class CustomerLineOut(BaseModel):
    id: int
    region: str
    ip: str | None
    product_type: str
    line_name: str
    status: str
    started_at: datetime
    expires_at: datetime
    customer_note: str | None
    node_link: str | None = None
    subscription_url: str | None = None
    node_qr_data: str | None = None
    subscription_qr_data: str | None = None
    clash_import: str | None = None
    v2ray_import: str | None = None
    nekobox_import: str | None = None


class CustomerLineNoteIn(BaseModel):
    customer_note: str | None = Field(default=None, max_length=1000)


class OrderIn(BaseModel):
    product_id: int
    region_id: int | None = None
    quantity: int = Field(default=1, ge=1, le=100)
    billing_days: int | None = Field(default=None, gt=0)
    payment_method: str = "contact_manager"
    remark: str | None = Field(default=None, max_length=1000)


class RenewalIn(BaseModel):
    billing_days: int = Field(gt=0)
    payment_method: str = "contact_manager"


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    order_no: str
    customer_id: int
    product_id: int
    source_type: str = "owned"
    resource_category: str | None = None
    amount: float
    quantity: int
    billing_days: int
    unit_price: float
    total_amount: float
    region_id: int | None
    region_name: str
    product_name: str
    payment_method: str
    payment_status: str
    status: str
    assigned_sales_id: int | None
    confirmed_at: datetime | None
    delivered_at: datetime | None
    cancelled_at: datetime | None
    order_type: str
    renewal_line_id: int | None
    delivered_line_id: int | None
    delivered_quantity: int
    renewal_completed_at: datetime | None
    remark: str | None
    created_at: datetime


class DeliverOrderIn(BaseModel):
    node_id: int
    static_ip: str | None = None
    expires_at: datetime
    customer_note: str | None = Field(default=None, max_length=1000)
    node_link: str | None = None
    subscription_url: str | None = None
    node_qr_data: str | None = None
    subscription_qr_data: str | None = None


class CompleteRenewalIn(BaseModel):
    manually_confirmed: bool


class AssignCustomerIn(BaseModel):
    sales_id: int | None


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    content: str
    category: str
    is_read: bool
    created_at: datetime


class InboxThreadCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=160)
    related_order_id: int | None = None
    related_line_id: int | None = None


class InboxThreadUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str | None = None
    assigned_sales_id: int | None = None


class InboxMessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=4000)


class InboxThreadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: int
    customer_name: str
    assigned_sales_id: int | None
    assigned_sales_name: str | None
    subject: str
    status: str
    related_order_id: int | None
    related_line_id: int | None
    last_message_at: datetime
    customer_unread_count: int
    staff_unread_count: int
    created_at: datetime
    updated_at: datetime


class InboxThreadPageOut(BaseModel):
    items: list[InboxThreadOut]
    total: int
    page: int
    page_size: int


class InboxSalesOptionOut(BaseModel):
    id: int
    display_name: str


class InboxUnreadCountOut(BaseModel):
    unread_count: int


class InboxMessageOut(BaseModel):
    id: int
    thread_id: int
    sender_role: str
    sender_display_name: str
    content: str
    customer_read_at: datetime | None
    staff_read_at: datetime | None
    created_at: datetime


class InboxMessagePageOut(BaseModel):
    items: list[InboxMessageOut]
    total: int
    page: int
    page_size: int

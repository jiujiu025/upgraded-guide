"""只读供应商适配器的构造入口。

API 依赖注入和定时任务都要造同一个适配器，抽在这里避免两处参数漂移
（超时、重试、退避任何一项不一致，都会让线上和后台看到不同的失败率）。
"""

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Supplier
from app.services.supplier_credentials import resolve_api_key
from app.suppliers.global_proxy import GlobalProxyAdapter


def build_readonly_adapter(db: Session, supplier: Supplier | None = None) -> GlobalProxyAdapter:
    """按供应商构造适配器：Key 与 base_url 都取该供应商自己的。

    supplier 为空时退回默认供应商（quanqiudaili），保持旧调用方行为不变。
    """
    settings = get_settings()
    return GlobalProxyAdapter(
        api_key=resolve_api_key(db, supplier),
        base_url=(supplier.base_url if supplier and supplier.base_url else settings.global_proxy_base_url),
        timeout_seconds=settings.supplier_http_timeout_seconds,
        max_attempts=settings.supplier_read_retry_attempts,
        backoff_base_seconds=settings.supplier_retry_backoff_seconds,
        max_retry_delay_seconds=settings.supplier_retry_max_delay_seconds,
    )

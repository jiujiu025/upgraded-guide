from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.api.inbox_routes import router as inbox_router
from app.api.finance_routes import router as finance_router
from app.api.customer_admin_routes import router as customer_admin_router
from app.api.catalog_admin_routes import router as catalog_admin_router
from app.api.supplier_routes import router as supplier_router
from app.api.task_admin_routes import router as task_admin_router
from app.api.outreach_routes import router as outreach_router
from app.api.finance_export_routes import router as finance_export_router
from app.api.notification_channel_routes import router as notification_channel_router
from app.core.config import get_settings
from app.core.logging_config import configure_logging
from app.core.request_logging import RequestLoggingMiddleware
from app.core.security_headers import SecurityHeadersMiddleware
from app.services.readiness_service import readiness_report
from app.services.registration_service import verify_rate_limit_backend

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    verify_rate_limit_backend()
    yield


app = FastAPI(title="玖玖云运营中台", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_origin], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.add_middleware(SecurityHeadersMiddleware)
# 最后添加 = 最外层：这样 404、CORS 预检和未捕获异常的响应也会带上 request_id。
app.add_middleware(RequestLoggingMiddleware)
app.include_router(router, prefix="/api")
app.include_router(inbox_router, prefix="/api")
app.include_router(finance_router, prefix="/api")
app.include_router(customer_admin_router, prefix="/api")
app.include_router(catalog_admin_router, prefix="/api")
app.include_router(supplier_router, prefix="/api")
app.include_router(task_admin_router, prefix="/api")
app.include_router(outreach_router, prefix="/api")
app.include_router(finance_export_router, prefix="/api")
app.include_router(notification_channel_router, prefix="/api")


@app.get("/healthz")
def healthz():
    """存活探针：只要进程还能响应就返回 200，用来判断"该不该重启"。

    这里故意什么都不查。数据库挂了时返回 500 只会让容器被反复重启，而重启
    治不了数据库；"能不能接流量"由 ``/readyz`` 回答。
    """
    return {"status": "ok", "service": "ops-platform"}


@app.get("/readyz")
def readyz():
    """就绪探针：数据库、共享限流 Redis、数据库结构版本全部可用才返回 200。

    任何一项 fail 都回 503，让负载均衡把流量摘走；响应体里带上逐项结果，
    排障时不用登机器就能看出是"库连不上"还是"迁移没跑"。
    """
    payload, ready = readiness_report()
    return JSONResponse(status_code=200 if ready else 503, content=payload)

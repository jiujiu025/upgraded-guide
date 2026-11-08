from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.api.inbox_routes import router as inbox_router
from app.api.finance_routes import router as finance_router
from app.api.customer_admin_routes import router as customer_admin_router
from app.api.catalog_admin_routes import router as catalog_admin_router
from app.api.supplier_routes import router as supplier_router
from app.api.task_admin_routes import router as task_admin_router
from app.api.outreach_routes import router as outreach_router
from app.api.finance_export_routes import router as finance_export_router
from app.core.config import get_settings
from app.core.security_headers import SecurityHeadersMiddleware
from app.services.registration_service import verify_rate_limit_backend

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    verify_rate_limit_backend()
    yield


app = FastAPI(title="玖玖云运营中台", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_origin], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.add_middleware(SecurityHeadersMiddleware)
app.include_router(router, prefix="/api")
app.include_router(inbox_router, prefix="/api")
app.include_router(finance_router, prefix="/api")
app.include_router(customer_admin_router, prefix="/api")
app.include_router(catalog_admin_router, prefix="/api")
app.include_router(supplier_router, prefix="/api")
app.include_router(task_admin_router, prefix="/api")
app.include_router(outreach_router, prefix="/api")
app.include_router(finance_export_router, prefix="/api")


@app.get("/healthz")
def healthz():
    return {"status": "ok", "service": "ops-platform"}

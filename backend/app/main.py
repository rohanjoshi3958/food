import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import Base, engine
from app.db_migrate import run_migrations
from app.routers import auth, cookbook, ingredients, meals, receipts
from app.storage import LocalUploadStorage, get_storage

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    storage = get_storage()
    if isinstance(storage, LocalUploadStorage):
        # Only the local fallback needs writable directories; in S3 mode the
        # container filesystem is never touched for uploads.
        storage.ensure_directories()
        logger.info("Uploads: local disk (UPLOADS_BUCKET unset)")
    else:
        logger.info("Uploads: S3 bucket %s", settings.uploads_bucket)
    Base.metadata.create_all(bind=engine)
    run_migrations()
    yield


app = FastAPI(title="Food API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(auth.router, prefix="/api")
app.include_router(receipts.router, prefix="/api")
app.include_router(ingredients.router, prefix="/api")
app.include_router(meals.router, prefix="/api")
app.include_router(cookbook.router, prefix="/api")

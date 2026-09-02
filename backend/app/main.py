from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import Base, engine
from app.db_migrate import run_migrations
from app.routers import auth, cookbook, ingredients, meals, receipts


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.meal_upload_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.cookbook_upload_dir).mkdir(parents=True, exist_ok=True)
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

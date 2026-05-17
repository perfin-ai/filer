from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from filer_backend import __version__
from filer_backend.api.health import router as health_router


def create_app() -> FastAPI:
    app = FastAPI(title="Filer Backend", version=__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:1420", "tauri://localhost"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    return app


app = create_app()

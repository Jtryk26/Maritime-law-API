from fastapi import APIRouter

from . import documents, imports

api_router = APIRouter()
api_router.include_router(documents.router)
api_router.include_router(imports.router)

__all__ = ["api_router"]

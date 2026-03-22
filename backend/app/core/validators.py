from bson import ObjectId
from fastapi import HTTPException


def valid_object_id(value: str) -> str:
    """Validate that a string is a valid MongoDB ObjectId."""
    if not ObjectId.is_valid(value):
        raise HTTPException(status_code=400, detail=f"Invalid ID format: {value}")
    return value

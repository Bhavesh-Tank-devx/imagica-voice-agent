"""Shared Pydantic base model for all request/response schemas."""
from pydantic import BaseModel, ConfigDict


class AppModel(BaseModel):
    """Base model for every schema in this project.

    Strips surrounding whitespace from strings and allows population by either
    field name or alias, so inbound webhook payloads are forgiving.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        str_strip_whitespace=True,
    )

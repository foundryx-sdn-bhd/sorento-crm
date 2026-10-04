from typing import List

from pydantic import BaseModel


class ContactBrandRef(BaseModel):
    id: str
    brand_name: str


class ContactBrandsUpdate(BaseModel):
    """Replace a contact's accessible brands with this exact set (empty = every brand)."""
    brand_ids: List[str] = []


class ContactBrandsResponse(BaseModel):
    brand_ids: List[str] = []
    brands: List[ContactBrandRef] = []

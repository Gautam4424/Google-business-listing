"""Phase 6 endpoints: the project's service list, service areas and keywords."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import Keyword, Project, ProjectService
from app.schemas.project import ServiceArea
from app.services import keywords as kw
from app.services.service_catalog import add_user_service, build_catalog, services_by_kind

router = APIRouter(prefix="/projects/{project_id}", tags=["services & keywords"])


class ProjectServiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    kind: str = Field(description="service | customer_type | generic")
    selected: bool = Field(description="Core service: used to generate keywords")
    user_added: bool
    score: float
    review_mentions: int
    sources: list[dict]


class ServicesOut(BaseModel):
    services: list[ProjectServiceOut]
    customer_types: list[ProjectServiceOut]
    generic: list[ProjectServiceOut]


class ServiceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)


class ServiceUpdate(BaseModel):
    selected: bool | None = None
    name: str | None = Field(default=None, min_length=2, max_length=200)
    kind: str | None = Field(default=None, pattern="^(service|customer_type|generic)$")


class KeywordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    keyword: str
    service: str | None
    location_name: str
    latitude: float | None
    longitude: float | None
    language: str
    country: str
    device: str
    source: str
    pattern: str | None
    active: bool


class KeywordsOut(BaseModel):
    keywords: list[KeywordOut]
    preview: dict
    notes: list[str] = Field(default_factory=list)


class KeywordCreate(BaseModel):
    keyword: str = Field(min_length=3, max_length=300)
    location_name: str | None = None


class KeywordUpdate(BaseModel):
    active: bool


def _project(db: Session, project_id: uuid.UUID) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


def _services_out(db: Session, project: Project) -> ServicesOut:
    groups = services_by_kind(db, project)
    return ServicesOut(**{k: [ProjectServiceOut.model_validate(s) for s in groups.get(v, [])] for k, v in (
        ("services", "service"), ("customer_types", "customer_type"), ("generic", "generic"),
    )})  # fmt: skip


def _keywords_out(db: Session, project: Project, notes: list[str] | None = None) -> KeywordsOut:
    rows = db.scalars(
        select(Keyword)
        .where(Keyword.project_id == project.id)
        .order_by(Keyword.active.desc(), Keyword.service, Keyword.pattern, Keyword.keyword)
    ).all()
    return KeywordsOut(
        keywords=[KeywordOut.model_validate(k) for k in rows],
        preview=kw.preview(db, project),
        notes=notes or [],
    )


# ---------- services ----------


@router.get("/services", response_model=ServicesOut)
def list_services(project_id: uuid.UUID, db: Session = Depends(get_db)) -> ServicesOut:
    """The unified service list: services (tick the core ones), customer types, generic Google types."""
    return _services_out(db, _project(db, project_id))


@router.post("/services/refresh", response_model=ServicesOut)
def refresh_services(project_id: uuid.UUID, db: Session = Depends(get_db)) -> ServicesOut:
    """Rebuild from the latest audit data (Google categories, website, reviews). Your choices are kept."""
    project = _project(db, project_id)
    build_catalog(db, project)
    return _services_out(db, project)


@router.post("/services", response_model=ProjectServiceOut, status_code=status.HTTP_201_CREATED)
def create_service(project_id: uuid.UUID, body: ServiceCreate, db: Session = Depends(get_db)):
    return add_user_service(db, _project(db, project_id), body.name)


@router.patch("/services/{service_id}", response_model=ProjectServiceOut)
def update_service(
    project_id: uuid.UUID, service_id: uuid.UUID, body: ServiceUpdate, db: Session = Depends(get_db)
):
    project = _project(db, project_id)
    row = db.get(ProjectService, service_id)
    if row is None or row.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Service not found")
    if body.name is not None:
        row.name = body.name.strip()
    if body.kind is not None:
        row.kind = body.kind
        if body.kind != "service":
            row.selected = False
    if body.selected is not None:
        if body.selected and row.kind != "service":
            raise HTTPException(422, "Only services can be core services")
        row.selected = body.selected
    row.user_edited = True
    db.commit()
    return row


# ---------- service areas ----------


@router.put("/service-areas", response_model=list[ServiceArea])
def set_service_areas(project_id: uuid.UUID, body: list[ServiceArea], db: Session = Depends(get_db)):
    """Cities/suburbs the business serves. Missing coordinates are looked up (1 cheap Google search each)."""
    project = _project(db, project_id)
    if not body:
        raise HTTPException(422, "At least one service area is required")
    known = {a["name"]: a for a in project.service_areas or []}
    areas = []
    for area in body:
        a = area.model_dump()
        if a["latitude"] is None and a["name"] in known:
            a = {**known[a["name"]], **{k: v for k, v in a.items() if v is not None}}
        if a["latitude"] is None and kw.get_places_client() is not None:
            found = kw.resolve_area(db, a["name"], project.country)
            if not found:
                raise HTTPException(422, f"Could not find '{a['name']}' on Google Maps: check the spelling")
            a["latitude"], a["longitude"] = found[0], found[1]
        areas.append(a)
    project.service_areas = areas
    db.commit()
    return project.service_areas


# ---------- keywords ----------


@router.post("/keywords/generate", response_model=KeywordsOut)
def generate_keywords(project_id: uuid.UUID, db: Session = Depends(get_db)) -> KeywordsOut:
    """Brief §2: "{service} in {city}", "{service} near me", "{service} {city}" for every core service and
    service area, plus your own keywords. Uses no SerpApi credits; active keywords are capped."""
    project = _project(db, project_id)
    try:
        result = kw.generate(db, project)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _keywords_out(db, project, result["notes"])


@router.get("/keywords", response_model=KeywordsOut)
def list_keywords(project_id: uuid.UUID, db: Session = Depends(get_db)) -> KeywordsOut:
    return _keywords_out(db, _project(db, project_id))


@router.post("/keywords", response_model=KeywordOut, status_code=status.HTTP_201_CREATED)
def create_keyword(project_id: uuid.UUID, body: KeywordCreate, db: Session = Depends(get_db)):
    project = _project(db, project_id)
    try:
        return kw.add_user_keyword(db, project, body.keyword, body.location_name)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def _keyword(db: Session, project: Project, keyword_id: uuid.UUID) -> Keyword:
    row = db.get(Keyword, keyword_id)
    if row is None or row.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Keyword not found")
    return row


@router.patch("/keywords/{keyword_id}", response_model=KeywordOut)
def update_keyword(
    project_id: uuid.UUID, keyword_id: uuid.UUID, body: KeywordUpdate, db: Session = Depends(get_db)
):
    project = _project(db, project_id)
    try:
        return kw.set_active(db, project, _keyword(db, project, keyword_id), body.active)
    except kw.KeywordCapReached as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/keywords/{keyword_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_keyword(project_id: uuid.UUID, keyword_id: uuid.UUID, db: Session = Depends(get_db)) -> Response:
    project = _project(db, project_id)
    db.delete(_keyword(db, project, keyword_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

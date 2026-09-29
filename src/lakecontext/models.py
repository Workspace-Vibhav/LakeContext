from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Column(Model):
    name: str = Field(min_length=1)
    data_type: str = Field(min_length=1)
    description: str = ""


class Table(Model):
    name: str = Field(pattern=r"^[^.\s]+\.[^.\s]+\.[^.\s]+$")
    description: str = ""
    owner: str = ""
    table_type: str = "TABLE"
    columns: list[Column]

    @model_validator(mode="after")
    def unique_columns(self):
        names = [c.name.casefold() for c in self.columns]
        if len(names) != len(set(names)):
            raise ValueError(f"Duplicate columns in {self.name}")
        return self


class Query(Model):
    id: str = Field(min_length=1)
    sql: str = Field(min_length=1)


class Term(Model):
    name: str = Field(min_length=1)
    definition: str = Field(min_length=1)
    tables: list[str] = Field(min_length=1)


class Lineage(Model):
    source: str
    target: str
    evidence: str = Field(min_length=1)


class Snapshot(Model):
    version: Literal[1] = 1
    source: str = Field(min_length=1)
    principal: str = Field(min_length=1)
    captured_at: str = Field(default_factory=utcnow)
    tables: list[Table]
    queries: list[Query] = Field(default_factory=list)
    terms: list[Term] = Field(default_factory=list)
    lineage: list[Lineage] = Field(default_factory=list)

    @model_validator(mode="after")
    def references(self):
        names = [t.name for t in self.tables]
        if len(names) != len({n.casefold() for n in names}):
            raise ValueError("Duplicate table names")
        if len({q.id for q in self.queries}) != len(self.queries):
            raise ValueError("Duplicate query IDs")
        if len({t.name for t in self.terms}) != len(self.terms):
            raise ValueError("Duplicate term names")
        known = set(names)
        for term in self.terms:
            if not set(term.tables) <= known:
                raise ValueError(f"Unknown table in term {term.name}")
        for edge in self.lineage:
            if edge.source not in known or edge.target not in known:
                raise ValueError("Unknown table in lineage")
        timestamp = datetime.fromisoformat(self.captured_at)
        if timestamp.tzinfo is None:
            raise ValueError("captured_at must include timezone")
        return self

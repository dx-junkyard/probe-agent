"""Finite, bounded write contracts for IID APIs."""
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field

Kind = Literal['supplement', 'correction', 'open_question', 'hypothesis']


class Request(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    request_id: str = Field(min_length=1, max_length=128)


class Target(BaseModel):
    model_config = ConfigDict(extra='forbid')
    session_id: int = Field(gt=0)
    target_kind: Literal['session', 'qa', 'intent_item', 'claim'] = 'session'
    row_id: Optional[int] = Field(default=None, gt=0)
    revision_id: Optional[int] = Field(default=None, gt=0)
    section: Optional[str] = Field(default=None, max_length=100)
    name: Optional[str] = Field(default=None, max_length=300)


class ManualDraft(Request):
    kind: Kind
    text: str = Field(min_length=1, max_length=4000)


class EditCandidate(ManualDraft):
    expected_revision: int = Field(gt=0)
    decision: Literal['pending', 'held', 'rejected'] = 'pending'
    selected_targets: list[Target] = Field(default_factory=list, max_length=20)
    split_held_targets: list[Target] = Field(default_factory=list, max_length=20)


class Selection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    candidate_id: int = Field(gt=0)
    expected_revision: int = Field(gt=0)


class SaveContributions(Request):
    items: list[Selection] = Field(min_length=1, max_length=20)


class Extract(Request):
    through_turn_number: int = Field(gt=0)


class SearchTargets(Request):
    expected_revision: int = Field(gt=0)
    cursor: int = Field(default=0, ge=0)


class Preview(Request):
    contribution_link_ids: list[int] = Field(min_length=1, max_length=20)


class Apply(Request):
    preview_token: str = Field(min_length=1, max_length=128)
    selected_link_ids: list[int] = Field(min_length=1, max_length=20)

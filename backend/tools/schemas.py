"""Pydantic schemas for LangChain StructuredTool inputs."""

from pydantic import BaseModel, Field


class ResearchSearchInput(BaseModel):
    query: str = Field(..., description="Natural-language research question or retrieval query.")


class PaperIdInput(BaseModel):
    paper_id: str = Field(..., description="Stable paper id or filename. Future stages will map this to Paper.id.")
    question: str = Field("", description="Optional focus question for the paper summary.")


class ComparePapersInput(BaseModel):
    query: str = Field("Compare the selected papers", description="User comparison question or focus.")
    paper_ids: list[str | int] = Field(default_factory=list, description="Optional current-user Paper.id values to compare.")
    filenames: list[str] = Field(default_factory=list, description="Optional current-user filenames or titles to compare.")
    compare_aspects: list[str] = Field(
        default_factory=lambda: ["problem", "method", "contribution", "dataset", "metric", "limitation"],
        description="Optional comparison aspects/columns.",
    )


class ReviewerCommentsInput(BaseModel):
    comments: str = Field(..., description="Reviewer comments or decision letter text.")
    paper_id: str = Field("", description="Optional paper id or filename associated with the comments.")


class DraftRebuttalInput(BaseModel):
    comments: str = Field(..., description="Reviewer comments to respond to.")
    paper_id: str | int | None = Field(None, description="Optional current-user Paper.id for the rebuttal target.")


class RelatedWorkInput(BaseModel):
    topic: str = Field(..., description="Research topic or claim for the related-work section.")
    constraints: str = Field("", description="Optional scope, venue, time range, or style constraints.")


class ResearchWritingInput(BaseModel):
    task_type: str = Field(..., description="Writing task type, such as Generate Related Work or Rewrite Abstract.")
    topic: str = Field("", description="Optional research topic or writing target.")
    user_text: str = Field("", description="Optional draft text to polish, rewrite, or check.")
    paper_ids: list[str | int] = Field(default_factory=list, description="Optional current-user Paper.id values.")
    writing_style: str = Field("general academic", description="Target style, such as TMC, IWQoS, NSFC, or general academic.")
    language: str = Field("en", description="Output language: zh or en.")

from sqlmodel import SQLModel, Field, Relationship
from typing import Optional, List
from datetime import datetime


class Project(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    description: Optional[str] = None
    style: Optional[str] = None
    aspect_ratio: str = Field(default="16:9")
    # Persisted narrative seed. Set by auto-plan (the user's textarea), reused
    # by AI Expand so per-scene prompts respect the same story direction the
    # original plan was anchored to. Survives refreshes / re-opens.
    story_seed: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    songs: List["Song"] = Relationship(
        back_populates="project",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    scenes: List["Scene"] = Relationship(
        back_populates="project",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    characters: List["Character"] = Relationship(
        back_populates="project",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    jobs: List["GenerationJob"] = Relationship(
        back_populates="project",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class Song(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id")
    title: str
    artist: Optional[str] = None
    # source: "lyria" | "suno" | "upload"
    source: str = Field(default="upload")
    file_path: Optional[str] = None
    duration: Optional[float] = None
    bpm: Optional[float] = None
    key: Optional[str] = None
    lyrics: Optional[str] = None
    # JSON string: [{word, start, end, confidence}]
    transcription_json: Optional[str] = None
    # JSON string: [float, ...]  beat timestamps in seconds
    beats_json: Optional[str] = None
    # JSON string: [{start, end, label}]  musical sections
    sections_json: Optional[str] = None
    # JSON string: {theme, narrative, mood, visual_world, characters_in_lyrics, suggested_visual_style}
    theme_analysis: Optional[str] = None
    # status: "pending" | "analyzing" | "ready" | "error"
    status: str = Field(default="pending")
    created_at: datetime = Field(default_factory=datetime.utcnow)

    project: Optional[Project] = Relationship(back_populates="songs")


class Character(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id")
    name: str
    description: str
    reference_image_path: Optional[str] = None  # active portrait — pointer into CharacterAsset
    lora_url: Optional[str] = None
    trigger_word: Optional[str] = None
    # portrait_status: "idle" | "generating" | "done" | "error"
    portrait_status: str = Field(default="idle")
    portrait_error: Optional[str] = None
    portrait_model: Optional[str] = None  # last model used
    created_at: datetime = Field(default_factory=datetime.utcnow)

    project: Optional[Project] = Relationship(back_populates="characters")
    portraits: List["CharacterAsset"] = Relationship(
        back_populates="character",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class CharacterAsset(SQLModel, table=True):
    """Versioned portrait history for a character. Multiple may exist; one
    has is_active=True and is what scene image generation uses as the
    reference portrait."""
    id: Optional[int] = Field(default=None, primary_key=True)
    character_id: int = Field(foreign_key="character.id", index=True)
    file_path: str
    model_used: Optional[str] = None  # e.g. "gemini-3.1-flash-image" or "uploaded"
    cost_usd: float = Field(default=0.0)
    is_active: bool = Field(default=True, index=True)
    # Snapshot of `Character.description` at the moment this portrait was
    # generated/uploaded. Activating the asset also restores this onto the
    # parent character — so a "Blindfolded" portrait variant carries a
    # blindfold-aware description, switching back to a plain portrait restores
    # the plain description, and every scene's image_prompt stays consistent
    # with whichever portrait is currently active.
    description: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    character: Optional[Character] = Relationship(back_populates="portraits")


class Scene(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id")
    order: int
    audio_start: float
    audio_end: float
    lyrics_segment: Optional[str] = None
    description: Optional[str] = None
    video_prompt: Optional[str] = None
    image_prompt: Optional[str] = None
    reference_image_path: Optional[str] = None
    video_path: Optional[str] = None
    video_model: str = Field(default="kling-v3.0-pro")
    image_model: str = Field(default="gemini-3.1-flash-image")
    resolution: str = Field(default="720p")  # 720p / 1080p / 4K — must be supported by chosen video_model
    align_to_beats: bool = Field(default=True)
    prompts_expanded: bool = Field(default=False)  # True once a prompt has been generated (planner or wand)

    # Scene chaining (opt-in, off by default).
    # When `chain_from_prev` is True, this scene's video generation uses the
    # PREVIOUS scene's extracted last frame as its first_frame_path instead
    # of its own planned reference still. The previous scene must already
    # have a rendered video (so we can extract its actual last frame).
    # Result: pixel-identical handoff at the seam — the video opens on the
    # exact pixels the previous scene closed on.
    chain_from_prev: bool = Field(default=False)
    # Populated automatically after every successful video gen — the path
    # to the JPG we extracted from the rendered video's final frame. Used
    # by the NEXT scene's video gen when its chain_from_prev is True.
    extracted_last_frame_path: Optional[str] = None
    # status: "pending" | "generating_image" | "image_ready" | "generating_video" | "done" | "error" | "cancelled"
    status: str = Field(default="pending")
    error_message: Optional[str] = None
    openrouter_job_id: Optional[str] = None
    cancel_requested: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    project: Optional[Project] = Relationship(back_populates="scenes")
    assets: List["SceneAsset"] = Relationship(
        back_populates="scene",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    prompt_versions: List["ScenePromptVersion"] = Relationship(
        back_populates="scene",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class SceneAsset(SQLModel, table=True):
    """One generated asset (image or video) for a scene.

    Multiple assets per scene can exist; one per asset_type carries
    is_active=True and is what the next pipeline step consumes. Files on
    disk are named with asset_id so regenerations don't clobber.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    scene_id: int = Field(foreign_key="scene.id", index=True)
    asset_type: str  # "image" | "video"
    file_path: str
    model_used: Optional[str] = None
    cost_usd: float = Field(default=0.0)
    cost_detail: Optional[str] = None
    metadata_json: Optional[str] = None  # JSON: resolution, duration, prompt, refs
    is_active: bool = Field(default=True, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    scene: Optional["Scene"] = Relationship(back_populates="assets")


class ScenePromptVersion(SQLModel, table=True):
    """Versioned history of a scene's image_prompt or video_prompt.

    Every time a prompt changes (auto-plan generates one, AI Expand rewrites,
    Soften rewrites for content-policy reasons, or the user manually edits),
    a new row is written and marked active. Mirrors how SceneAsset tracks
    image/video versions.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    scene_id: int = Field(foreign_key="scene.id", index=True)
    prompt_type: str  # "image" | "video"
    text: str
    source: str  # "plan" | "expand" | "soften" | "manual"
    cost_usd: float = Field(default=0.0)
    is_active: bool = Field(default=True, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    scene: Optional["Scene"] = Relationship(back_populates="prompt_versions")


class GenerationJob(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id")
    scene_id: Optional[int] = None
    # job_type: "image" | "video" | "music" | "transcription" | "llm_plan"
    #         | "llm_expand" | "assembly"
    job_type: str
    # provider: "openrouter" | "fal" | "suno" | "ffmpeg"
    provider: str = Field(default="openrouter")
    external_id: Optional[str] = None
    # status: "pending" | "running" | "completed" | "failed"
    status: str = Field(default="pending")
    result_url: Optional[str] = None
    result_path: Optional[str] = None
    error: Optional[str] = None
    # Estimated cost in USD; charged regardless of pass/fail unless 0
    cost_usd: float = Field(default=0.0)
    # Free-form details: "{model} × {duration}s" or similar
    cost_detail: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None

    project: Optional[Project] = Relationship(back_populates="jobs")

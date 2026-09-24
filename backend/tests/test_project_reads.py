from contextlib import contextmanager

from sqlalchemy import event
from sqlmodel import Session

from app.models import Character, CharacterAsset, Project, Scene, SceneAsset, ScenePromptVersion, Song
from app.routers.projects import get_project, list_projects
from app.routers.scenes import list_scenes


@contextmanager
def counted_selects(engine):
    statements = []

    def record(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def seed_project(db, name, scene_count):
    project = Project(name=name)
    db.add(project)
    db.flush()
    project_id = project.id
    db.add(Song(project_id=project_id, title=name, status="ready"))
    character = Character(project_id=project_id, name="Lead", description="Lead character")
    db.add(character)
    db.flush()
    db.add(CharacterAsset(character_id=character.id, file_path=f"{name}/portrait.jpg"))
    for order in range(1, scene_count + 1):
        scene = Scene(
            project_id=project_id, order=order, audio_start=order - 1,
            audio_end=order, status="done" if order % 2 else "pending",
        )
        db.add(scene)
        db.flush()
        db.add(SceneAsset(scene_id=scene.id, asset_type="image", file_path=f"{name}/{order}.jpg"))
        db.add(ScenePromptVersion(scene_id=scene.id, prompt_type="image", text=f"{name} {order}", source="manual"))
    db.commit()
    return project_id


def test_project_list_counts_with_fixed_query_count(test_engine):
    with Session(test_engine) as db:
        first = seed_project(db, "First", 3)
        second = seed_project(db, "Second", 20)
        db.add(Project(name="Empty"))
        db.commit()

    with Session(test_engine) as db, counted_selects(test_engine) as queries:
        projects = list_projects(db)
        assert len(queries) == 3
        by_id = {project["id"]: project for project in projects}
        assert by_id[first]["song_count"] == 1
        assert by_id[first]["scene_count"] == 3
        assert by_id[first]["scenes_done"] == 2
        assert by_id[second]["scenes_done"] == 10
        empty = next(project for project in projects if project["name"] == "Empty")
        assert empty["scene_count"] == empty["song_count"] == empty["scenes_done"] == 0


def test_project_histories_are_batched_and_do_not_leak_between_projects(test_engine):
    with Session(test_engine) as db:
        project_id = seed_project(db, "Wanted", 20)
        seed_project(db, "Other", 3)

    with Session(test_engine) as db, counted_selects(test_engine) as queries:
        project = get_project(project_id, db)
        assert len(queries) == 7  # project, song, scenes, characters, portraits, assets, prompts
        assert len(project["scenes"]) == 20
        assert len(project["characters"][0]["portraits"]) == 1
        for scene in project["scenes"]:
            assert len(scene["assets"]) == len(scene["prompt_versions"]) == 1
            assert scene["assets"][0]["scene_id"] == scene["id"]
            assert scene["prompt_versions"][0]["text"].startswith("Wanted ")

    with Session(test_engine) as db, counted_selects(test_engine) as queries:
        scenes = list_scenes(project_id, db)
        assert len(queries) == 3
        assert [scene["order"] for scene in scenes] == list(range(1, 21))

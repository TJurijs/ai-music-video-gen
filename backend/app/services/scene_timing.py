"""Keep generated versions tied to the song window they were made for."""
import json
from sqlmodel import select
from app.models import SceneAsset


def video_timing_matches(scene, asset) -> bool:
    if asset is None:
        return True  # Legacy scenes may only have a file pointer.
    try:
        meta = json.loads(asset.metadata_json or "{}")
        if not isinstance(meta, dict):
            return False
        return all(key not in meta or abs(float(meta[key]) - getattr(scene, key)) < 0.001
                   for key in ("audio_start", "audio_end"))
    except (ValueError, TypeError):
        return False


def active_video_timing_matches(db, scene) -> bool:
    asset = db.exec(select(SceneAsset).where(SceneAsset.scene_id == scene.id,
        SceneAsset.asset_type == "video", SceneAsset.is_active == True)).first()
    return video_timing_matches(scene, asset)


def preserve_video_timing(db, scene):
    """Stamp legacy versions before moving a scene, without changing files."""
    assets = db.exec(select(SceneAsset).where(SceneAsset.scene_id == scene.id,
                                             SceneAsset.asset_type == "video")).all()
    if scene.video_path and not any(a.file_path == scene.video_path for a in assets):
        # Preserve pointer-only legacy media as a real version before moving it.
        from app.services.versioning import make_active
        asset = SceneAsset(scene_id=scene.id, asset_type="video", file_path=scene.video_path,
                           model_used=scene.video_model)
        make_active(db, target=asset, siblings_filter=[SceneAsset.scene_id == scene.id,
                                                     SceneAsset.asset_type == "video"])
        assets.append(asset)
    for asset in assets:
        try:
            meta = json.loads(asset.metadata_json or "{}")
        except (ValueError, TypeError):
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        meta.setdefault("audio_start", scene.audio_start)
        meta.setdefault("audio_end", scene.audio_end)
        asset.metadata_json = json.dumps(meta)
        db.add(asset)

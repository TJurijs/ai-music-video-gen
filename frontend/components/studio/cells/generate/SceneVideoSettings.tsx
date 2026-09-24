"use client";
import { useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { useConfirm } from "@/components/ConfirmDialog";
import { isSceneBusy } from "@/lib/generationView";
import type { ModelsConfig, Project, Scene } from "@/lib/types";
import { audioUsesFirstFrame, usesSongAudio, videoAspects, videoDurations, videoProvider, videoProviderLabel, videoRate, videoResolutions, videoSettingsForScene } from "@/lib/videoModels";
import { fmtCost } from "./shared";

export default function SceneVideoSettings({ scene, project, models, disabled, onRefresh, onError, onDirtyChange, onSavingChange }: {
  scene: Scene;
  project?: Project;
  models: ModelsConfig;
  disabled: boolean;
  onRefresh: () => void;
  onError: (message: string | null) => void;
  onDirtyChange: (dirty: boolean) => void;
  onSavingChange: (saving: boolean) => void;
}) {
  const confirm = useConfirm();
  const [modelKey, setModelKey] = useState(scene.video_model);
  const [length, setLength] = useState(Number((scene.audio_end - scene.audio_start).toFixed(3)));
  const [quality, setQuality] = useState(scene.resolution);
  const [songSync, setSongSync] = useState(usesSongAudio(models.video[scene.video_model], scene.audio_sync_enabled));
  const [saving, setSaving] = useState(false);
  const submissionLock = useRef(false);
  const savedLength = Number((scene.audio_end - scene.audio_start).toFixed(3));
  const model = models.video[modelKey];
  const audio = usesSongAudio(model, songSync);
  const provider = videoProviderLabel(videoProvider(model, audio));
  const durations = model ? videoDurations(model, audio) : [];
  const qualities = model ? videoResolutions(model, audio) : [];
  const timingChanged = Math.abs(length - savedLength) > 0.001;
  const dirty = modelKey !== scene.video_model || timingChanged || quality !== scene.resolution
    || audio !== usesSongAudio(models.video[scene.video_model], scene.audio_sync_enabled);
  const laterScenes = (project?.scenes || []).filter((candidate) => candidate.order > scene.order);
  const affectedScenes = [scene, ...laterScenes];
  const affectedVideos = affectedScenes.filter((candidate) => candidate.video_url).length;
  // A recovered fractional tail may remain on the timeline; a new length must
  // be an exact provider option. Changing model/quality never rounds that tail.
  const lengthSupported = timingChanged ? durations.includes(length) : durations.includes(Math.round(length));
  const validation = !model ? "Choose an available model."
    : model.available === false ? model.unavailable_reason || "This model is unavailable."
    : !lengthSupported ? `Choose a supported length for ${model.name}.`
    : !qualities.includes(quality) ? "Choose a supported quality."
    : project && !videoAspects(model, audio).includes(project.aspect_ratio) ? `${model.name} does not support ${project.aspect_ratio}.`
    : timingChanged && affectedScenes.some(isSceneBusy) ? "Wait for this scene and later scenes to finish rendering before changing length."
    : null;
  const rate = model ? videoRate(model, quality, audio) : undefined;
  const savedAudio = usesSongAudio(models.video[scene.video_model], scene.audio_sync_enabled);

  useEffect(() => {
    setModelKey(scene.video_model);
    setLength(Number((scene.audio_end - scene.audio_start).toFixed(3)));
    setQuality(scene.resolution);
    setSongSync(savedAudio);
  }, [scene.id, scene.video_model, scene.audio_start, scene.audio_end, scene.resolution, savedAudio]);
  useEffect(() => { onDirtyChange(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => () => { onDirtyChange(false); onSavingChange(false); }, [onDirtyChange, onSavingChange]);

  const selectModel = (key: string) => {
    const next = models.video[key];
    if (!next) return;
    const settings = videoSettingsForScene(next, { ...scene, resolution: quality, audio_sync_enabled: songSync, audio_end: scene.audio_start + length });
    setModelKey(key);
    setSongSync(settings.audio_sync_enabled);
    setQuality(settings.resolution);
  };
  const selectAudio = (enabled: boolean) => {
    setSongSync(enabled);
    const available = videoResolutions(model, enabled);
    if (!available.includes(quality)) setQuality(available[0]);
  };
  const reset = () => {
    setModelKey(scene.video_model);
    setLength(savedLength);
    setQuality(scene.resolution);
    setSongSync(usesSongAudio(models.video[scene.video_model], scene.audio_sync_enabled));
    onError(null);
  };
  const save = async () => {
    if (!dirty || validation || disabled || submissionLock.current) return;
    submissionLock.current = true;
    setSaving(true);
    onSavingChange(true);
    onError(null);
    try {
      if (timingChanged) {
        const delta = Number((length - savedLength).toFixed(3));
        const ok = await confirm({
          title: `Change scene #${scene.order} to ${length}s?`,
          message: [
            `This scene keeps its start time and changes from ${savedLength}s to ${length}s.`,
            laterScenes.length ? `${laterScenes.length} later scene${laterScenes.length === 1 ? " moves" : "s move"} ${Math.abs(delta)}s ${delta > 0 ? "later" : "earlier"} in the song. Their lengths stay the same, with no new gaps or overlaps.` : "This changes where the scene plan ends.",
            affectedVideos ? `${affectedVideos} existing video${affectedVideos === 1 ? " needs" : "s need"} regeneration for the new song timing. Originals stay in variant history.` : null,
            "Lyrics are updated for the new timing. Review scene descriptions and prompts before generating.",
          ].filter(Boolean).join("\n\n"),
          confirmLabel: "Change scene timing",
        });
        if (!ok) return;
      }
      const settings = { video_model: modelKey, resolution: quality, audio_sync_enabled: audio };
      if (timingChanged) await api.scenes.updateTiming(scene.id, { ...settings, duration: length });
      else await api.scenes.update(scene.id, settings);
      await onRefresh();
    } catch (cause) {
      onError(cause instanceof Error ? cause.message : "Could not save scene settings.");
    } finally {
      submissionLock.current = false;
      setSaving(false);
      onSavingChange(false);
    }
  };
  const locked = disabled || saving;
  const selectClass = "w-full min-w-0 rounded-md border border-white/15 bg-surface-2 px-2 py-2 text-xs text-zinc-200 outline-none focus:border-accent disabled:opacity-50";

  return <div className="border-b border-white/5 bg-black/10 px-3 py-3 space-y-2">
    <div className="grid grid-cols-2 items-end gap-2 sm:grid-cols-[minmax(10rem,2fr)_minmax(5rem,0.7fr)_minmax(5rem,0.7fr)_minmax(8rem,1fr)]">
      <label className="min-w-0 text-[11px] text-zinc-400"><span className="mb-1 block">Video model</span>
        <select aria-label={`Video model for scene ${scene.order}`} className={selectClass} value={modelKey} disabled={locked} onChange={(event) => selectModel(event.target.value)}>
          {!models.video[modelKey] && <option value={modelKey}>{modelKey} · unavailable</option>}
          {Object.entries(models.video).map(([key, item]) => <option key={key} value={key} disabled={item.available === false}>{item.name}{item.available === false ? " · unavailable" : ""}</option>)}
        </select>
      </label>
      <label className="text-[11px] text-zinc-400"><span className="mb-1 block">Length</span>
        <select aria-label={`Video length for scene ${scene.order}`} className={selectClass} value={length} disabled={locked} onChange={(event) => setLength(Number(event.target.value))}>
          {!durations.includes(length) && <option value={length}>{length}s{lengthSupported ? " · current" : " · choose length"}</option>}
          {durations.map((value) => <option key={value} value={value}>{value}s</option>)}
        </select>
      </label>
      <label className="text-[11px] text-zinc-400"><span className="mb-1 block">Quality</span>
        <select aria-label={`Video quality for scene ${scene.order}`} className={selectClass} value={quality} disabled={locked} onChange={(event) => setQuality(event.target.value)}>
          {!qualities.includes(quality) && <option value={quality}>{quality} · unavailable</option>}
          {qualities.map((value) => <option key={value} value={value}>{value}</option>)}
        </select>
      </label>
      <label className="text-[11px] text-zinc-400"><span className="mb-1 block">Audio reference</span>
        <select aria-label={`Audio reference for scene ${scene.order}`} className={selectClass} value={audio ? "on" : "off"} disabled={locked || !model?.supports_audio_input || model.requires_audio_input} onChange={(event) => selectAudio(event.target.value === "on")}>
          <option value="off">{model?.supports_audio_input ? "Off · no song input" : "Unavailable"}</option>
          {model?.supports_audio_input && <option value="on">{model.requires_audio_input ? "Scene song · required" : "Scene song · fal"}</option>}
        </select>
      </label>
    </div>
    {model && <p className="text-[11px] leading-relaxed text-zinc-400"><span className="font-medium text-zinc-200">Provider: {provider}</span>{" · "}{audio
      ? audioUsesFirstFrame(model) ? "Scene song + first-frame image." : "Scene song + scene/cast image references; no exact first frame."
      : "Standard video; song is added during assembly."}{audio && <span> Audio guidance does not guarantee precise lip sync. Assembly uses the original song.</span>}</p>}
    <div className="flex flex-wrap items-center justify-between gap-2 text-[11px]">
      <p className={validation ? "text-amber-300" : "text-zinc-400"}>
        {validation || (dirty ? "Unsaved settings — save before generating." : "Settings for the next video. Existing variants stay in history.")}
        {!validation && rate !== undefined && <span className="ml-2 whitespace-nowrap text-emerald-300">Est. {fmtCost(rate * length)} / video · {provider}</span>}
      </p>
      {dirty && <div className="flex shrink-0 gap-2">
        <button type="button" disabled={saving} onClick={reset} className="rounded-md px-2 py-1.5 text-zinc-400 hover:text-white disabled:opacity-50">Cancel</button>
        <button type="button" disabled={locked || !!validation} onClick={save} className="flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-white hover:bg-accent-hover disabled:opacity-50">{saving && <Loader2 className="h-3 w-3 animate-spin" />}{timingChanged ? "Save length & settings" : "Save settings"}</button>
      </div>}
    </div>
  </div>;
}

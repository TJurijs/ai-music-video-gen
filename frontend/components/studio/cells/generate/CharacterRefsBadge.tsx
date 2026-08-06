"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Image as ImageIcon, Link2, Users } from "lucide-react";
import { api } from "@/lib/api";
import type { Scene, Character } from "@/lib/types";
import { textMentionsCharacter } from "./shared";

export default function CharacterRefsBadge({ scene, onModeChange, disabled }: {
  scene: Scene;
  onModeChange: (mode: "frame" | "character") => void;
  disabled?: boolean;
}) {
  const qc = useQueryClient();
  const project = qc.getQueryData<any>(["project", scene.project_id]);
  const allChars: Character[] = project?.characters || [];
  const { data: models } = useQuery({ queryKey: ["models"], queryFn: api.models.list });
  const modelCfg = models?.video?.[scene.video_model];
  const modelUsesRefs = !!modelCfg?.supports_reference_images;
  const audioSyncActive = !!scene.audio_sync_enabled && !!modelCfg?.supports_audio_input;
  const audioUsesFrame = audioSyncActive && modelCfg?.audio_input_mode === "wan_i2v";
  const characterOnlyActive = modelUsesRefs
    && !audioSyncActive
    && scene.video_reference_mode === "character";
  const frameOnlyActive = modelUsesRefs && !audioSyncActive && !characterOnlyActive;
  const audioUsesCharacterRefs = modelUsesRefs && audioSyncActive && !audioUsesFrame;
  const portraitsAreSent = characterOnlyActive || audioUsesCharacterRefs;

  if (allChars.length === 0) return null;

  const haystack = `${scene.video_prompt || ""} ${scene.image_prompt || ""} ${scene.description || ""}`.toLowerCase();
  const isNamed = (character: Character) => {
    const name = (character.name || "").toLowerCase().trim();
    return textMentionsCharacter(haystack, name);
  };
  const willBePassed = (character: Character) =>
    portraitsAreSent && isNamed(character) && !!character.reference_image_url;

  const passedCount = allChars.filter(willBePassed).length;
  const modelName = modelCfg?.name || scene.video_model;

  return (
    <div className="px-3 pb-2 -mt-1 flex items-center flex-wrap gap-1.5">
      {modelUsesRefs && (
        <div className="inline-flex overflow-hidden rounded-md border border-white/10 bg-surface-3 mr-1">
          <button
            type="button"
            disabled={disabled}
            onClick={() => onModeChange("frame")}
            className={`inline-flex items-center gap-1 px-2 py-1 text-[9px] transition-colors disabled:opacity-50 ${
              frameOnlyActive
                ? "bg-blue-500/15 text-blue-300"
                : "text-zinc-500 hover:text-zinc-300"
            }`}
            title={audioSyncActive
              ? "Switch to frame-only input. This turns off reference audio and uses this scene's still (or the previous scene's last frame) as the exact first frame."
              : "Exact frame mode: use this scene's still (or the previous scene's last frame) as the first frame. Separate character portraits are not sent."}
          >
            <ImageIcon className="w-2.5 h-2.5" /> Frame only
          </button>
          <button
            type="button"
            disabled={disabled}
            onClick={() => onModeChange("character")}
            className={`inline-flex items-center gap-1 border-l border-white/10 px-2 py-1 text-[9px] transition-colors disabled:opacity-50 ${
              characterOnlyActive
                ? "bg-violet-500/15 text-violet-300"
                : "text-zinc-500 hover:text-zinc-300"
            }`}
            title="Send named cast portraits only. This turns off reference audio; no saved scene still, first/last frame, or chained prior frame is sent."
          >
            <Users className="w-2.5 h-2.5" /> Character refs only
          </button>
        </div>
      )}

      {modelUsesRefs && (
        <span
          className={`rounded border px-2 py-1 text-[9px] font-semibold ${
            characterOnlyActive
              ? "border-violet-400/25 bg-violet-500/10 text-violet-200"
              : audioSyncActive
                ? "border-amber-400/25 bg-amber-500/10 text-amber-200"
                : "border-blue-400/25 bg-blue-500/10 text-blue-200"
          }`}
          title="This input source will be used the next time you press + Vid."
        >
          {audioSyncActive
            ? audioUsesFrame
              ? "Next video: audio + exact frame"
              : "Next video: audio + scene/cast refs"
            : characterOnlyActive
              ? "Next video: character refs only · no first frame"
              : "Next video: exact first frame · no character refs"}
        </span>
      )}

      <Link2 className="w-2.5 h-2.5 text-zinc-500 shrink-0" />
      <span
        className="text-[10px] text-zinc-500"
        title={
          characterOnlyActive
            ? `Character refs only is active. Named portraits are sent to ${modelName}; no scene frame or reference audio is sent.`
            : audioUsesFrame
              ? `${modelName} audio mode uses the scene still or chained frame as its identity anchor; separate portraits are not sent.`
              : audioUsesCharacterRefs
                ? `${modelName} audio mode sends the song slice, a scene or chained image when available, and named cast portraits. Choose Character refs only to omit audio and the scene frame.`
            : modelUsesRefs
              ? "Frame mode is active. Exact frame anchors and separate character references are mutually exclusive, so portraits are not sent."
              : `${modelName} does not support separate input_references on this OpenRouter route.`
        }
      >
        {characterOnlyActive || audioUsesCharacterRefs
          ? <>Passed as ref <span className="font-mono text-zinc-400">{passedCount}/{allChars.length}</span>:</>
          : modelUsesRefs
            ? <>Frame mode - refs not sent:</>
            : <>Refs not used by {modelName}:</>}
      </span>

      {allChars.map((character) => {
        const named = isNamed(character);
        const hasPortrait = !!character.reference_image_url;
        const passed = willBePassed(character);
        const title = !modelUsesRefs
          ? `${modelName} does not support separate character references.`
          : audioUsesFrame
            ? `${modelName} audio mode uses the exact scene frame plus driving audio, so ${character.name}'s portrait is not sent separately.`
          : !portraitsAreSent
            ? `Frame mode is active, so ${character.name}'s portrait is not sent separately.`
            : passed
              ? `${character.name}: named in the scene and portrait available - sent as an input reference.`
              : named && !hasPortrait
                ? `${character.name} is named but has no portrait. Generate or upload one first.`
                : `${character.name} is not named in this scene's prompt, so the portrait is not sent.`;
        return (
          <span
            key={character.id}
            className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 border transition-opacity ${
              passed
                ? "bg-emerald-500/10 border-emerald-500/40"
                : "bg-surface-3/40 border-white/5 opacity-60"
            }`}
            title={title}
          >
            {hasPortrait ? (
              <img
                src={character.reference_image_url}
                alt=""
                className={`w-3.5 h-3.5 rounded-full object-cover ${passed ? "" : "grayscale"}`}
              />
            ) : (
              <span className="w-3.5 h-3.5 rounded-full bg-surface-2 border border-white/10 flex items-center justify-center text-[7px] text-zinc-600">?</span>
            )}
            <span className={`text-[10px] ${passed ? "text-emerald-200" : "text-zinc-500 line-through decoration-zinc-700"}`}>
              {character.name}
            </span>
            {named && !hasPortrait && <AlertCircle className="w-2.5 h-2.5 text-amber-400" />}
          </span>
        );
      })}
    </div>
  );
}

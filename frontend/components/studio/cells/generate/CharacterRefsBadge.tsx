"use client";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Link2 } from "lucide-react";
import { api } from "@/lib/api";
import type { Scene, Character } from "@/lib/types";

export default function CharacterRefsBadge({ scene }: { scene: Scene }) {
  // Shows the project's character cast and marks the ones that will actually
  // reach the video model as `input_references`. THREE conditions all needed:
  //   1. Name appears in scene's prompts/description (case-insensitive)
  //   2. Character has a portrait on disk
  //   3. The scene's chosen video MODEL effectively uses input_references
  //      (Seedance variants on OpenRouter — yes; Kling/Veo — no, refs are
  //      silently dropped on those routes; we don't even send them now).
  // If condition 3 fails, the badge greys out the whole row with a
  // "model ignores refs" explainer rather than showing characters as
  // passed-but-not-honored.
  const qc = useQueryClient();
  const project = qc.getQueryData<any>(["project", scene.project_id]);
  const allChars: Character[] = project?.characters || [];

  // Read the chosen model's capability flag.
  const { data: models } = useQuery({ queryKey: ["models"], queryFn: api.models.list });
  const modelCfg = models?.video?.[scene.video_model];
  const modelUsesRefs = !!modelCfg?.supports_reference_images;

  if (allChars.length === 0) return null;

  const haystack = `${scene.video_prompt || ""} ${scene.image_prompt || ""} ${scene.description || ""}`.toLowerCase();
  const isNamed = (c: Character) =>
    !!c.name && haystack.includes(c.name.toLowerCase());
  // Final "actually passed to model" requires all three conditions —
  // including that the model uses refs at all on this route.
  const willBePassed = (c: Character) =>
    modelUsesRefs && isNamed(c) && !!c.reference_image_url;

  const passedCount = allChars.filter(willBePassed).length;
  const modelName = modelCfg?.name || scene.video_model;

  return (
    <div className="px-3 pb-2 -mt-1 flex items-center flex-wrap gap-1.5">
      <Link2 className="w-2.5 h-2.5 text-zinc-500 shrink-0" />
      <span
        className="text-[10px] text-zinc-500"
        title={
          modelUsesRefs
            ? `Characters whose portrait will be attached to this scene's video gen as input_references. ${modelName} uses these as a strong identity anchor (Seedance R2V path). Identity preservation also depends on whether the character's face is visible in the first_frame.`
            : `${modelName} does NOT use input_references on the OpenRouter route — they would be silently dropped, so we don't send them. Character identity comes ENTIRELY from the first_frame on this model. To use character refs, switch to a Seedance variant.`
        }
      >
        {modelUsesRefs
          ? <>Passed as ref <span className="font-mono text-zinc-400">{passedCount}/{allChars.length}</span>:</>
          : <>Refs not used by {modelName}:</>
        }
      </span>
      {allChars.map((c) => {
        const named = isNamed(c);
        const hasPortrait = !!c.reference_image_url;
        const passed = named && hasPortrait;
        return (
          <span
            key={c.id}
            className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 border transition-opacity ${
              passed
                ? "bg-emerald-500/10 border-emerald-500/40"
                : "bg-surface-3/40 border-white/5 opacity-60"
            }`}
            title={
              !modelUsesRefs
                ? `${modelName} doesn't use input_references on the OpenRouter route — ${c.name}'s portrait isn't sent. Switch to a Seedance variant if you want this character as a reference.`
                : passed
                  ? `${c.name}: named in this scene + has a portrait → portrait attached as input_reference. Strong identity anchor on Seedance.`
                  : named && !hasPortrait
                    ? `${c.name} is named in this scene's prompt but has no portrait yet — NOT passed to the model. Generate a portrait in the Characters step to attach it.`
                    : `${c.name} is NOT named in this scene's prompt — not passed. Edit the prompt to include the name if this character should appear.`
            }
          >
            {hasPortrait ? (
              <img
                src={c.reference_image_url}
                alt=""
                className={`w-3.5 h-3.5 rounded-full object-cover ${passed ? "" : "grayscale"}`}
              />
            ) : (
              <span className="w-3.5 h-3.5 rounded-full bg-surface-2 border border-white/10 flex items-center justify-center text-[7px] text-zinc-600">
                ?
              </span>
            )}
            <span
              className={`text-[10px] ${
                passed ? "text-emerald-200" : "text-zinc-500 line-through decoration-zinc-700"
              }`}
            >
              {c.name}
            </span>
            {named && !hasPortrait && (
              <AlertCircle
                className="w-2.5 h-2.5 text-amber-400"
              />
            )}
          </span>
        );
      })}
    </div>
  );
}

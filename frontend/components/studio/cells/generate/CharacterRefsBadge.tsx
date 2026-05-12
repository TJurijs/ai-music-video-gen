"use client";
import { useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Link2 } from "lucide-react";
import type { Scene, Character } from "@/lib/types";

export default function CharacterRefsBadge({ scene }: { scene: Scene }) {
  // Shows every character in the project cast, visually marking which ones
  // are referenced by THIS scene's prompts (and therefore get their portrait
  // attached to image + video gen via input_references). Mirrors backend's
  // _find_character_references substring-matching exactly.
  const qc = useQueryClient();
  const project = qc.getQueryData<any>(["project", scene.project_id]);
  const allChars: Character[] = project?.characters || [];

  if (allChars.length === 0) return null;

  const haystack = `${scene.video_prompt || ""} ${scene.image_prompt || ""} ${scene.description || ""}`.toLowerCase();
  const isReferenced = (c: Character) =>
    !!c.name && haystack.includes(c.name.toLowerCase());

  const referencedCount = allChars.filter(isReferenced).length;

  return (
    <div className="px-3 pb-2 -mt-1 flex items-center flex-wrap gap-1.5">
      <Link2 className="w-2.5 h-2.5 text-zinc-500 shrink-0" />
      <span className="text-[10px] text-zinc-500">
        Cast in this scene <span className="font-mono text-zinc-400">{referencedCount}/{allChars.length}</span>:
      </span>
      {allChars.map((c) => {
        const refed = isReferenced(c);
        const hasPortrait = !!c.reference_image_url;
        return (
          <span
            key={c.id}
            className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 border transition-opacity ${
              refed
                ? "bg-emerald-500/10 border-emerald-500/40"
                : "bg-surface-3/40 border-white/5 opacity-60"
            }`}
            title={
              refed
                ? hasPortrait
                  ? `${c.name} is named in this scene's prompt — portrait will be attached as an identity anchor.`
                  : `${c.name} is named in this scene's prompt but has no portrait yet — generate one in the Characters step so the model gets an identity anchor.`
                : `${c.name} is NOT named in this scene's prompt. Portrait won't be attached. If ${c.name} should appear here, edit the prompt to include the name.`
            }
          >
            {hasPortrait ? (
              <img
                src={c.reference_image_url}
                alt=""
                className={`w-3.5 h-3.5 rounded-full object-cover ${refed ? "" : "grayscale"}`}
              />
            ) : (
              <span className="w-3.5 h-3.5 rounded-full bg-surface-2 border border-white/10 flex items-center justify-center text-[7px] text-zinc-600">
                ?
              </span>
            )}
            <span
              className={`text-[10px] ${
                refed ? "text-emerald-200" : "text-zinc-500 line-through decoration-zinc-700"
              }`}
            >
              {c.name}
            </span>
            {refed && !hasPortrait && (
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

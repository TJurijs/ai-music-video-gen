"use client";
import { useState } from "react";
import { Search, ChevronDown } from "lucide-react";
import type { ModelsConfig, Project, Scene, VideoModel } from "@/lib/types";
import { audioUsesFirstFrame, videoAspects, videoDurations, videoProvider, videoProviderLabel, videoProviderRoutes, videoRate, videoResolutions } from "@/lib/videoModels";

function lengths(values: number[]) {
  const sorted = [...values].sort((a,b) => a-b);
  if (!sorted.length) return "—";
  return sorted.length === sorted.at(-1)! - sorted[0] + 1 ? `${sorted[0]}–${sorted.at(-1)}s` : sorted.map(v => `${v}s`).join(", ");
}
function controls(model: VideoModel, audio: boolean) {
  return { first: audio ? audioUsesFirstFrame(model) : model.supports_first_frame,
    refs: audio ? !audioUsesFirstFrame(model) : model.supports_reference_images };
}
function guardrail(model: VideoModel) {
  const h = model.history;
  if (!h || !h.provider_attempts) return "Not enough history";
  if (h.policy_rejections) return `${h.policy_rejections} policy refusal${h.policy_rejections === 1 ? "" : "s"} recorded`;
  if (h.possible_policy_rejections) return "Possible filtering recorded";
  return "No policy refusals recorded";
}
const selectClass = "w-full rounded-lg border border-white/10 bg-surface px-2.5 py-2 text-xs text-zinc-200 outline-none focus:border-accent";

export default function VideoModelCheatSheet({ models, project, scenes = [] }: { models: ModelsConfig; project?: Project; scenes?: Scene[] }) {
  const [search,setSearch] = useState("");
  const [mode,setMode] = useState("all");
  const [provider,setProvider] = useState("all");
  const [quality,setQuality] = useState("all");
  const [duration,setDuration] = useState(15);
  const [onlyLength,setOnlyLength] = useState(false);
  const [input,setInput] = useState("all");
  const [policy,setPolicy] = useState("all");
  const [projectId,setProjectId] = useState("all");
  const [sort,setSort] = useState("used");
  const [expanded,setExpanded] = useState<string | null>(null);
  const all = Object.entries(models.video);
  const projects = Array.from(new Map(all.flatMap(([,m]) => (m.history?.projects || []).map(p => [p.id,p] as const))).values());
  const resolutions = Array.from(new Set(all.flatMap(([,m]) => [...m.resolutions,...m.audio_resolutions || []]))).sort((a,b)=>parseInt(a)-parseInt(b));
  const route = (m: VideoModel) => mode === "song" || (mode === "all" && (!!m.requires_audio_input || (provider === "fal" && !!m.supports_audio_input)));
  const entries = all.filter(([key,m]) => {
    const audio = route(m), c = controls(m,audio), h=m.history;
    return `${key} ${m.name}`.toLowerCase().includes(search.toLowerCase())
      && (mode !== "song" || m.supports_audio_input) && (mode !== "standard" || !m.requires_audio_input)
      && (provider === "all" || videoProvider(m,audio) === provider)
      && (quality === "all" || videoResolutions(m,audio).includes(quality))
      && (!onlyLength || videoDurations(m,audio).includes(duration))
      && (input === "all" || input === "first" && c.first || input === "refs" && c.refs)
      && (projectId === "all" || h?.projects.some(p=>String(p.id)===projectId))
      && (policy === "all" || policy === "refusals" && !!h?.policy_rejections || policy === "none" && h?.guardrail_status === "none_observed" || policy === "unknown" && (!h || ["no_history","possible_rejections"].includes(h.guardrail_status)));
  }).sort(([,a],[,b]) => sort === "name" ? a.name.localeCompare(b.name) : (b.history?.attempts || 0)-(a.history?.attempts || 0));
  const reset = () => { setSearch(""); setMode("all"); setProvider("all"); setQuality("all"); setOnlyLength(false); setInput("all"); setPolicy("all"); setProjectId("all"); };
  return <div className="space-y-4">
    <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-4">
      <label className="sm:col-span-2"><span className="mb-1 block text-xs text-zinc-400">Find a model</span><span className="relative block"><Search className="absolute left-3 top-2.5 h-3.5 w-3.5 text-zinc-500"/><input autoFocus value={search} onChange={e=>setSearch(e.target.value)} placeholder="Search your models…" className={`${selectClass} pl-9`}/></span></label>
      <label><span className="mb-1 block text-xs text-zinc-400">Audio reference</span><select value={mode} onChange={e=>setMode(e.target.value)} className={selectClass}><option value="all">Any mode</option><option value="standard">Without song input</option><option value="song">With song input · in app</option></select></label>
      <label><span className="mb-1 block text-xs text-zinc-400">Provider</span><select value={provider} onChange={e=>setProvider(e.target.value)} className={selectClass}><option value="all">All providers</option><option value="openrouter">OpenRouter</option><option value="fal">fal</option></select></label>
      <label><span className="mb-1 block text-xs text-zinc-400">Quality</span><select value={quality} onChange={e=>setQuality(e.target.value)} className={selectClass}><option value="all">Any resolution</option>{resolutions.map(r=><option key={r}>{r}</option>)}</select></label>
      <label><span className="mb-1 block text-xs text-zinc-400">Reference control in app</span><select value={input} onChange={e=>setInput(e.target.value)} className={selectClass}><option value="all">Any inputs</option><option value="first">First frame</option><option value="refs">Character references</option></select></label>
      <label><span className="mb-1 block text-xs text-zinc-400">Guardrails · your history</span><select value={policy} onChange={e=>setPolicy(e.target.value)} className={selectClass}><option value="all">All histories</option><option value="none">No recorded policy refusals</option><option value="refusals">Recorded policy refusals</option><option value="unknown">Unknown / possible filtering</option></select></label>
      <label><span className="mb-1 block text-xs text-zinc-400">Used in project</span><select value={projectId} onChange={e=>setProjectId(e.target.value)} className={selectClass}><option value="all">All projects</option>{projects.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
      <label><span className="mb-1 block text-xs text-zinc-400">Order</span><select value={sort} onChange={e=>setSort(e.target.value)} className={selectClass}><option value="used">Most used first</option><option value="name">Model name</option></select></label>
    </div>
    <div className="flex flex-wrap items-center gap-4 rounded-lg border border-white/10 bg-white/[0.02] p-3 text-xs text-zinc-300">
      <label className="flex items-center gap-2">Compare price for <input aria-label="Comparison duration in seconds" type="number" min={1} max={60} value={duration} onChange={e=>setDuration(Math.max(1,Math.min(60,Number(e.target.value)||1)))} className="w-14 rounded border border-white/10 bg-surface px-2 py-1.5"/> seconds</label>
      <label className="flex items-center gap-2"><input type="checkbox" checked={onlyLength} onChange={e=>setOnlyLength(e.target.checked)} className="accent-violet-500"/>Only models supporting this length</label>
      <span className="ml-auto text-zinc-400">{entries.length} of {all.length} models</span><button onClick={reset} className="text-violet-300 hover:underline">Reset filters</button>
    </div>
    <p className="text-xs leading-relaxed text-zinc-400">Audio reference sends the scene’s song segment to the model. It does not guarantee exact singing or lip sync. Prices and inputs below follow the displayed route; all connected providers are listed on each model.</p>
    <div className="grid gap-3 lg:grid-cols-2">
      {entries.map(([key,m])=>{
        const audio=route(m), c=controls(m,audio), h=m.history;
        const rs=videoResolutions(m,audio), res=quality === "all" ? rs.includes("720p") ? "720p" : rs[0] : quality;
        const supported=videoDurations(m,audio).includes(duration), rate=videoRate(m,res,audio);
        const activeHere=scenes.filter(s=>s.video_model===key).length;
        return <article key={key} className="rounded-xl border border-white/10 bg-white/[0.02] p-4">
          <div className="flex items-start justify-between gap-3"><div><h3 className="text-sm font-semibold text-white">{m.name}</h3><p className="mt-1 text-[11px] text-zinc-400">Showing {videoProviderLabel(videoProvider(m,audio))} · {audio ? "Audio reference" : "Standard video"}{activeHere ? ` · Selected on ${activeHere} scene${activeHere===1?"":"s"}` : ""}</p></div><div className="text-right"><span className="text-sm font-medium text-emerald-300">{supported && rate != null ? `$${(rate*duration).toFixed(2)}` : "—"}</span><p className="mt-1 text-[10px] text-zinc-500">{duration}s · {res}</p></div></div>
          <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[10px]"><span className="text-zinc-500">Providers</span>{videoProviderRoutes(m).map(r=><span key={r.label} className={`rounded border px-1.5 py-0.5 ${r.provider === "fal" ? "border-amber-400/20 bg-amber-500/5 text-amber-200" : "border-violet-400/20 bg-violet-500/5 text-violet-200"}`}>{r.label}</span>)}</div>
          <div className="mt-3 grid grid-cols-2 gap-2 text-[11px]">
            {[["First frame",c.first ? "Available" : "Not in this mode"],["Character refs",c.refs ? "Available" : "Not in this mode"]].map(([label,value])=><div key={label} className="rounded-lg bg-black/15 p-2"><p className="text-zinc-500">{label}</p><p className={`mt-1 ${value === "Available" ? "text-emerald-300" : "text-zinc-400"}`}>{value}</p></div>)}
          </div>
          <dl className="mt-3 grid grid-cols-[90px_1fr] gap-y-2 text-xs"><dt className="text-zinc-500">Lengths</dt><dd className="text-zinc-200">{lengths(videoDurations(m,audio))}{!supported && <span className="ml-2 text-amber-300">{duration}s unavailable</span>}</dd><dt className="text-zinc-500">Quality</dt><dd className="text-zinc-300">{rs.join(" / ")}</dd><dt className="text-zinc-500">Audio reference</dt><dd className={m.supports_audio_input ? "text-emerald-300" : "text-zinc-400"}>{m.supports_audio_input ? `${m.requires_audio_input ? "Required" : "Available"} on fal · ${lengths(videoDurations(m,true))}` : m.audio_reference_support === "provider" ? "Provider supports references · not connected" : "Unavailable in app"}</dd></dl>
          <div className="mt-3 border-t border-white/5 pt-3"><p className={`text-xs ${h?.policy_rejections ? "text-amber-300" : "text-zinc-300"}`}>{guardrail(m)}</p><p className="mt-1 text-[11px] leading-relaxed text-zinc-500">{h ? `${h.attempts} attempts · ${h.completed} completed jobs · ${h.failed} failed · ${h.projects.length} projects` : "Historical counts unavailable"}</p></div>
          <button type="button" aria-expanded={expanded===key} aria-controls={`details-${key}`} onClick={()=>setExpanded(expanded===key?null:key)} className="mt-3 flex items-center gap-1 text-xs text-violet-300"><ChevronDown className={`h-3.5 w-3.5 ${expanded===key?"rotate-180":""}`}/>Details & history</button>
          {expanded===key && <div id={`details-${key}`} className="mt-3 space-y-2 border-t border-white/5 pt-3 text-xs leading-relaxed text-zinc-400">
            <p><span className="text-zinc-200">Inputs: </span>{audio ? c.first ? "Scene still or chained frame is the first-frame anchor. Separate character portraits are not sent." : "Scene image and character portraits are sent together as references; this does not anchor an exact first frame." : m.reference_note || "Uses the selected scene frame."}</p>
            <p><span className="text-zinc-200">Audio: </span>{m.audio_note || "Original song is added during assembly."}</p>
            <p><span className="text-zinc-200">Formats: </span>{videoAspects(m,audio).join(" / ")}</p>
            <p><span className="text-zinc-200">Recorded evidence: </span>{h?.evidence_note || "Not enough stored evidence to assess restrictions."}</p>
            {h && <><p>{h.policy_rejections} confirmed policy refusals / {h.provider_attempts} provider attempts. {h.possible_policy_rejections || 0} possible filtering failures counted separately. Local retiming edits ({h.local_edits}) do not count as new generations.</p>
              {h.routes?.map(r => <p key={`${r.provider}-${r.route}`}><span className="text-zinc-300">{r.provider === "fal" ? "fal · audio reference" : r.provider === "openrouter" ? "OpenRouter · standard" : r.provider}:</span> {r.attempts} attempts, {r.completed} completed, {r.policy_rejections} confirmed refusals.</p>)}
              <p>Projects: {h.projects.map(p=>p.name).join(", ") || "—"}. Currently active on {h.active_scenes} scenes across all projects.</p></>}
          </div>}
        </article>;
      })}
    </div>
    {!entries.length && <div className="rounded-xl border border-white/10 p-8 text-center text-sm text-zinc-400">No models match these filters.<button onClick={reset} className="ml-2 text-violet-300 underline">Reset filters</button></div>}
    <p className="text-[11px] leading-relaxed text-zinc-500">Video-only price estimates exclude stills. These comparison controls do not change any scenes. {models.verified_at && `Capability catalog checked ${models.verified_at}.`} History includes all stored projects; deleted or cleared errors cannot be reconstructed.</p>
  </div>;
}

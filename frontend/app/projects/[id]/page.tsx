"use client";
import { useQuery } from "@tanstack/react-query";
import { useParams, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import FlowStudio from "@/components/studio/FlowStudio";
import { isSceneBusy } from "@/lib/generationView";

export default function StudioPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const projectId = Number(id);

  const { data: project, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.projects.get(projectId),
    refetchInterval: (query) => {
      const data = query.state.data;
      return data?.scenes?.some(isSceneBusy) || data?.songs?.some((song) => ["generating", "analyzing"].includes(song.status))
        || data?.characters?.some((character) => character.portrait_status === "generating") ? 4000 : 15000;
    },
    retry: false,
  });

  const { data: jobs = [] } = useQuery({
    queryKey: ["jobs", projectId],
    queryFn: () => api.generation.getJobs(projectId),
    refetchInterval: project?.scenes?.some(isSceneBusy) ? 4000 : 15000,
    enabled: !!project,
  });

  const { data: costs } = useQuery({
    queryKey: ["costs", projectId],
    queryFn: () => api.generation.getCosts(projectId),
    refetchInterval: project?.scenes?.some(isSceneBusy) ? 8000 : 30000,
    enabled: !!project,
  });

  if (isError && !project) {
    const is404 = (error as Error)?.message?.startsWith("404");
    return (
      <div className="flex flex-col items-center justify-center h-screen bg-surface text-center px-4 gap-3">
        <div className="text-zinc-300 text-sm font-medium">
          {is404 ? "Project not found" : "Couldn't load project"}
        </div>
        <p className="text-xs text-zinc-500 max-w-xs">
          {is404 ? `Project #${projectId} doesn't exist (or was deleted).` : (error as Error)?.message}
        </p>
        {!is404 && <button onClick={() => refetch()} disabled={isFetching} className="text-xs px-4 py-2 bg-accent hover:bg-accent-hover rounded-lg disabled:opacity-50">{isFetching ? "Reconnecting…" : "Try again"}</button>}
        <button
          onClick={() => router.push("/projects")}
          className="text-xs px-3 py-1.5 bg-accent hover:bg-accent-hover text-white rounded-lg transition-colors"
        >
          Back to projects
        </button>
      </div>
    );
  }

  if (isLoading || !project) {
    return (
      <div className="flex items-center justify-center h-screen bg-surface text-zinc-500 text-sm">
        Loading project...
      </div>
    );
  }

  return (
    <>
    {isError && <div role="status" className="flex items-center justify-between gap-3 bg-amber-950 px-4 py-2 text-xs text-amber-200"><span>Connection interrupted. Showing the last saved project; checking again automatically.</span><button onClick={() => refetch()} disabled={isFetching} className="shrink-0 underline disabled:opacity-50">{isFetching ? "Reconnecting…" : "Reconnect now"}</button></div>}
    <FlowStudio
      project={project}
      song={project.songs?.[0]}
      scenes={project.scenes ?? []}
      characters={project.characters ?? []}
      jobs={jobs}
      costs={costs}
    />
    </>
  );
}

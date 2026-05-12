"use client";
import { useQuery } from "@tanstack/react-query";
import { useParams, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import FlowStudio from "@/components/studio/FlowStudio";

export default function StudioPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const projectId = Number(id);

  const { data: project, isLoading, isError, error } = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api.projects.get(projectId),
    refetchInterval: 4000,
    retry: false,
  });

  const { data: jobs = [] } = useQuery({
    queryKey: ["jobs", projectId],
    queryFn: () => api.generation.getJobs(projectId),
    refetchInterval: 3000,
    enabled: !!project,
  });

  const { data: costs } = useQuery({
    queryKey: ["costs", projectId],
    queryFn: () => api.generation.getCosts(projectId),
    refetchInterval: 4000,
    enabled: !!project,
  });

  if (isError) {
    const is404 = (error as Error)?.message?.startsWith("404");
    return (
      <div className="flex flex-col items-center justify-center h-screen bg-surface text-center px-4 gap-3">
        <div className="text-zinc-300 text-sm font-medium">
          {is404 ? "Project not found" : "Couldn't load project"}
        </div>
        <p className="text-xs text-zinc-500 max-w-xs">
          {is404 ? `Project #${projectId} doesn't exist (or was deleted).` : (error as Error)?.message}
        </p>
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
    <FlowStudio
      project={project}
      song={project.songs?.[0]}
      scenes={project.scenes ?? []}
      characters={project.characters ?? []}
      jobs={jobs}
      costs={costs}
    />
  );
}

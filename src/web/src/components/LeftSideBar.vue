<script setup lang="ts">
import {
  Archive,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  CircleHelp,
  Copy,
  Folder,
  FolderOpen,
  FolderPlus,
  LoaderCircle,
  Mail,
  MessageSquarePlus,
  MoreVertical,
  PanelLeft,
  Pencil,
  Star,
  Search,
  Settings,
  Split,
  Trash2,
  Workflow,
  X,
  Clock,
} from "@lucide/vue";
import { computed, nextTick, onMounted, onUnmounted, ref } from "vue";
import ChatSearchDialog from "./ChatSearchDialog.vue";
import type { ProjectNavItem, SessionNavSummary } from "../types";

defineOptions({
  name: "LeftSideBar",
});

const props = defineProps<{
  projects: ProjectNavItem[];
  archivedSessionCount?: number;
  activeSessionId: string;
  collapsed: boolean;
  hasProjects: boolean;
  projectsLoading?: boolean;
  width?: number;
  isDragging?: boolean;
  /** Enables the Workflows nav entry for future-product previews. */
  workflowsEnabled?: boolean;
  workflowsActive?: boolean;
  automatedTasksActive?: boolean;
  searchOpen?: boolean;
  /** Project ids that are collapsed, sourced from persisted user settings. */
  foldedProjectIds?: string[];
}>();

const emit = defineEmits<{
  toggle: [];
  addProject: [rootPath: string, name?: string];
  renameProject: [projectId: string, name: string];
  removeProject: [projectId: string];
  openProjectInFinder: [projectId: string];
  newSession: [projectId: string];
  openSettings: [];
  openWorkflows: [];
  openAutomatedTasks: [];
  openSearch: [];
  closeSearch: [];
  updateFoldedProjects: [projectIds: string[]];
  selectSession: [id: string];
  toggleSessionStar: [id: string];
  renameSession: [id: string, title: string];
  markSessionUnread: [id: string];
  archiveSession: [id: string];
  deleteSession: [id: string];
}>();

const editingSessionId = ref<string | null>(null);
const editingTitle = ref("");
const renameInput = ref<HTMLInputElement | null>(null);
const menuSessionId = ref<string | null>(null);
const menuPosition = ref({ top: 0, left: 0 });
const menuEl = ref<HTMLElement | null>(null);
const deleteDialogSession = ref<SessionNavSummary | null>(null);
const searchOpen = ref(false);
const effectiveSearchOpen = computed(() => props.searchOpen ?? searchOpen.value);

function setRenameInput(el: unknown) {
  renameInput.value = (el as HTMLInputElement | null) ?? null;
}

function startRename(session: SessionNavSummary) {
  editingSessionId.value = session.id;
  editingTitle.value = session.title;
  void nextTick(() => {
    renameInput.value?.focus();
    renameInput.value?.select();
  });
}

function commitRename() {
  const id = editingSessionId.value;
  if (!id) return;
  const title = editingTitle.value.trim();
  editingSessionId.value = null;
  if (title) emit("renameSession", id, title);
}

function cancelRename() {
  editingSessionId.value = null;
}

const projectDialogOpen = ref(false);
const projectName = ref("");
const projectPath = ref("");
const projectError = ref("");
const projectSettingsProjectId = ref<string | null>(null);
const projectSettingsName = ref("");
const projectSettingsError = ref("");
const projectPathCopied = ref(false);
const nowMs = ref(Date.now());
const visibleSessionCounts = ref<Record<string, number>>({});
// Fold state lives in persisted user settings: it arrives via the foldedProjectIds
// prop and toggling emits the new list up to App.vue, which saves it to /api/settings.
const foldedProjectIdSet = computed(() => new Set(props.foldedProjectIds ?? []));
let ageTimer: number | undefined;
let projectPathCopiedTimer: number | undefined;

const scrollContainer = ref<HTMLElement | null>(null);
const scrollContent = ref<HTMLElement | null>(null);
const needsScroll = ref(false);
let resizeObserver: ResizeObserver | null = null;

function checkScroll() {
  if (!scrollContainer.value) return;
  const el = scrollContainer.value;
  needsScroll.value = el.scrollHeight > el.clientHeight;
}

const DEFAULT_VISIBLE_SESSIONS = 10;
const SHOW_MORE_SESSION_INCREMENT = 50;
const hasMultipleProjects = computed(() => props.projects.length > 1);
const firstProjectId = computed(() => props.projects[0]?.id ?? "");
const allSessions = computed(() =>
  props.projects.flatMap((project) => project.sessions),
);
const activeMenuSession = computed(
  () =>
    allSessions.value.find((session) => session.id === menuSessionId.value) ??
    null,
);
const projectSettingsProject = computed(
  () =>
    props.projects.find((project) => project.id === projectSettingsProjectId.value) ??
    null,
);
const projectSettingsPath = computed(() => projectSettingsProject.value?.path ?? "");
const projectSettingsHasActiveSessions = computed(() =>
  projectSettingsProject.value ? projectHasActiveSessions(projectSettingsProject.value) : false,
);
const sessionSelectionSuppressed = computed(() => false);

function openProjectDialog() {
  projectDialogOpen.value = true;
  projectError.value = "";
}

function closeProjectDialog() {
  projectDialogOpen.value = false;
  projectName.value = "";
  projectPath.value = "";
  projectError.value = "";
}

function submitProject() {
  const rootPath = projectPath.value.trim();
  if (!rootPath) {
    projectError.value = "Please enter a project directory.";
    return;
  }
  emit("addProject", rootPath, projectName.value.trim() || undefined);
  closeProjectDialog();
}

function openProjectSettings(project: ProjectNavItem) {
  projectSettingsProjectId.value = project.id;
  projectSettingsName.value = project.name;
  projectSettingsError.value = "";
  resetProjectPathCopied();
}

function closeProjectSettings() {
  projectSettingsProjectId.value = null;
  projectSettingsName.value = "";
  projectSettingsError.value = "";
  resetProjectPathCopied();
}

function submitProjectSettings() {
  const project = projectSettingsProject.value;
  if (!project) return;
  const name = projectSettingsName.value.trim();
  if (!name) {
    projectSettingsError.value = "Please enter a project name.";
    return;
  }
  emit("renameProject", project.id, name);
  closeProjectSettings();
}

function removeSettingsProject() {
  const project = projectSettingsProject.value;
  if (!project) return;
  if (projectHasActiveSessions(project)) {
    projectSettingsError.value = "Stop active sessions before removing this project.";
    return;
  }
  emit("removeProject", project.id);
  closeProjectSettings();
}

function openSettingsProjectInFinder() {
  const project = projectSettingsProject.value;
  if (!project) return;
  emit("openProjectInFinder", project.id);
}

async function copySettingsProjectPath() {
  const project = projectSettingsProject.value;
  if (!project) return;
  if (!navigator.clipboard) {
    projectSettingsError.value = "Clipboard is unavailable.";
    return;
  }
  try {
    await navigator.clipboard.writeText(project.path);
    projectSettingsError.value = "";
    projectPathCopied.value = true;
    if (projectPathCopiedTimer) window.clearTimeout(projectPathCopiedTimer);
    projectPathCopiedTimer = window.setTimeout(resetProjectPathCopied, 1200);
  } catch {
    projectSettingsError.value = "Unable to copy path.";
  }
}

function resetProjectPathCopied() {
  projectPathCopied.value = false;
  if (!projectPathCopiedTimer) return;
  window.clearTimeout(projectPathCopiedTimer);
  projectPathCopiedTimer = undefined;
}

function startNewChat(projectId = firstProjectId.value) {
  if (!projectId) return;
  emit("newSession", projectId);
}

function openSearch() {
  closeSessionMenu();
  if (props.searchOpen === undefined) searchOpen.value = true;
  emit("openSearch");
}

function closeSearch() {
  if (props.searchOpen === undefined) searchOpen.value = false;
  emit("closeSearch");
}

function selectSearchSession(id: string) {
  closeSearch();
  emit("selectSession", id);
}

function openSessionMenu(session: SessionNavSummary, event: MouseEvent) {
  const target = event.currentTarget as HTMLElement | null;
  if (menuSessionId.value === session.id) {
    closeSessionMenu();
    return;
  }
  if (target) {
    const rect = target.getBoundingClientRect();
    const menuWidth = 188;
    menuPosition.value = {
      top: Math.max(8, Math.min(rect.bottom + 6, window.innerHeight - 190)),
      left: Math.max(
        8,
        Math.min(rect.right - menuWidth, window.innerWidth - menuWidth - 8),
      ),
    };
  }
  menuSessionId.value = session.id;
}

function closeSessionMenu() {
  menuSessionId.value = null;
}

function renameFromMenu() {
  const session = activeMenuSession.value;
  closeSessionMenu();
  if (session) startRename(session);
}

function markUnreadFromMenu() {
  const session = activeMenuSession.value;
  closeSessionMenu();
  if (session) emit("markSessionUnread", session.id);
}

function askDeleteFromMenu() {
  const session = activeMenuSession.value;
  closeSessionMenu();
  if (session) deleteDialogSession.value = session;
}

function cancelDelete() {
  deleteDialogSession.value = null;
}

function confirmDelete() {
  const session = deleteDialogSession.value;
  deleteDialogSession.value = null;
  if (session) emit("deleteSession", session.id);
}

function handleDocumentMouseDown(event: MouseEvent) {
  const target = event.target as Node | null;
  if (target && menuEl.value?.contains(target)) return;
  closeSessionMenu();
}

function handleDocumentKeydown(event: KeyboardEvent) {
  if (event.key === "Escape") {
    closeSessionMenu();
    cancelDelete();
    closeProjectDialog();
    closeProjectSettings();
  }
}

function formatSessionAge(value: string) {
  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) return "now";

  const seconds = Math.max(0, Math.floor((nowMs.value - timestamp) / 1000));
  if (seconds < 60) return "now";

  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;

  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;

  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d`;

  const weeks = Math.floor(days / 7);
  if (weeks < 5) return `${weeks}w`;

  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo`;

  return `${Math.floor(days / 365)}y`;
}

function sessionAgeSource(session: SessionNavSummary) {
  return session.lastActivityAt ?? session.createdAt;
}

function sessionItemClass(session: SessionNavSummary) {
  if (
    !sessionSelectionSuppressed.value &&
    session.id === props.activeSessionId
  ) {
    return "bg-[var(--surface-active)] text-[color:var(--text-primary)]";
  }

  return "text-[color:var(--text-secondary)] hover:bg-[var(--surface-hover)] hover:text-[color:var(--text-primary)]";
}

function isSessionMenuOpen(session: SessionNavSummary) {
  return menuSessionId.value === session.id;
}

function sessionIndicatorClass(session: SessionNavSummary) {
  return isSessionMenuOpen(session)
    ? "hidden"
    : "group-hover:hidden group-focus-within:hidden";
}

function isUnreadLikeSession(session: SessionNavSummary) {
  return session.unread || session.attention === "success";
}

function isWaitingForInput(session: SessionNavSummary) {
  return session.status === "waiting_input" || Boolean(session.waitingInput);
}

function isAutomatedTaskSession(session: SessionNavSummary) {
  return Boolean(session.automatedTaskId);
}

function isForkedSession(session: SessionNavSummary) {
  return Boolean(session.forkedFromSessionId);
}

function projectHasActiveSessions(project: ProjectNavItem) {
  return project.sessions.some(
    (session) =>
      session.status === "queued" ||
      session.status === "running" ||
      session.status === "waiting_input" ||
      Boolean(session.waitingInput),
  );
}

function unreadLikeLabel(session: SessionNavSummary) {
  return session.attention === "success" ? "Review needed" : "Unread";
}

function sessionLimit(project: ProjectNavItem) {
  if (!hasMultipleProjects.value) return project.sessions.length;
  return Math.min(
    visibleSessionCounts.value[project.id] ?? DEFAULT_VISIBLE_SESSIONS,
    project.sessions.length,
  );
}

function visibleSessions(project: ProjectNavItem) {
  return project.sessions.slice(0, sessionLimit(project));
}

function canShowMoreSessions(project: ProjectNavItem) {
  return (
    hasMultipleProjects.value && sessionLimit(project) < project.sessions.length
  );
}

function canShowLessSessions(project: ProjectNavItem) {
  return (
    hasMultipleProjects.value &&
    project.sessions.length > DEFAULT_VISIBLE_SESSIONS &&
    sessionLimit(project) >= project.sessions.length
  );
}

function showMoreSessions(project: ProjectNavItem) {
  const current = sessionLimit(project);
  visibleSessionCounts.value = {
    ...visibleSessionCounts.value,
    [project.id]: Math.min(
      current + SHOW_MORE_SESSION_INCREMENT,
      project.sessions.length,
    ),
  };
}

function showLessSessions(project: ProjectNavItem) {
  visibleSessionCounts.value = {
    ...visibleSessionCounts.value,
    [project.id]: DEFAULT_VISIBLE_SESSIONS,
  };
}

function isProjectFolded(project: ProjectNavItem) {
  return foldedProjectIdSet.value.has(project.id);
}

function toggleProjectFold(project: ProjectNavItem) {
  const current = props.foldedProjectIds ?? [];
  const next = current.includes(project.id)
    ? current.filter((id) => id !== project.id)
    : [...current, project.id];
  emit("updateFoldedProjects", next);
}

onMounted(() => {
  ageTimer = window.setInterval(() => {
    nowMs.value = Date.now();
  }, 60_000);
  document.addEventListener("mousedown", handleDocumentMouseDown);
  document.addEventListener("keydown", handleDocumentKeydown);

  if (scrollContainer.value) {
    resizeObserver = new ResizeObserver(() => {
      checkScroll();
    });
    resizeObserver.observe(scrollContainer.value);
    if (scrollContent.value) {
      resizeObserver.observe(scrollContent.value);
    }
    checkScroll();
  }
});

onUnmounted(() => {
  if (ageTimer) window.clearInterval(ageTimer);
  if (projectPathCopiedTimer) window.clearTimeout(projectPathCopiedTimer);
  document.removeEventListener("mousedown", handleDocumentMouseDown);
  document.removeEventListener("keydown", handleDocumentKeydown);

  if (resizeObserver) {
    resizeObserver.disconnect();
  }
});
</script>

<template>
  <aside
    class="flex h-screen shrink-0 flex-col border-r border-[color:var(--border-layout)] bg-[var(--sidebar-bg)] text-[color:var(--text-primary)] relative overflow-hidden"
    :class="[
      collapsed ? 'w-[52px]' : '',
      isDragging ? '' : 'transition-[width] duration-300 ease-in-out',
    ]"
    :style="!collapsed ? { width: width ? `${width}px` : '280px' } : undefined"
  >
    <div
      class="panel-header border-b-0 border-[color:var(--border-layout)] px-4"
    >
      <div v-if="!collapsed" class="flex min-w-0 flex-1 items-center gap-2">
        <span
          class="truncate text-[18px] tracking-normal text-[color:var(--text-primary)]"
          >Handa</span
        >
      </div>
      <button
        class="grid h-8 w-8 place-items-center rounded-lg text-[color:var(--text-muted)] transition hover:text-[color:var(--text-primary)] focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--border-subtle)]"
        :class="
          collapsed ? 'mx-auto cursor-e-resize' : 'ml-auto cursor-w-resize'
        "
        type="button"
        :aria-label="collapsed ? 'Open sidebar' : 'Close sidebar'"
        v-tooltip="collapsed ? 'Open sidebar' : 'Close sidebar'"
        @click="emit('toggle')"
      >
        <PanelLeft :size="18" />
      </button>
    </div>

    <div
      v-if="collapsed"
      class="flex flex-1 flex-col items-center w-[52px] px-2 py-3"
    >
      <div class="space-y-1">
        <button
          class="nav-icon disabled:cursor-not-allowed disabled:opacity-40"
          type="button"
          v-tooltip="'New chat'"
          :disabled="!firstProjectId"
          @click="startNewChat()"
        >
          <MessageSquarePlus :size="18" />
        </button>
        <button
          class="nav-icon"
          type="button"
          v-tooltip="'Search'"
          aria-label="Search"
          @click="openSearch"
        >
          <Search :size="18" />
        </button>
        <button
          class="nav-icon"
          :class="{ 'bg-[var(--surface-active)] !text-[color:var(--text-primary)]': automatedTasksActive }"
          type="button"
          v-tooltip="'Automated Tasks'"
          aria-label="Automated Tasks"
          @click="emit('openAutomatedTasks')"
        >
          <Clock :size="18" />
        </button>
      </div>
      <div class="mt-auto space-y-1">
        <button
          class="nav-icon"
          type="button"
          v-tooltip="'Settings'"
          @click="emit('openSettings')"
        >
          <Settings :size="18" />
        </button>
      </div>
    </div>

    <template v-else>
      <div class="space-y-1 px-3 py-3 text-[14px]">
        <button
          class="sidebar-action disabled:cursor-not-allowed disabled:opacity-40"
          type="button"
          :disabled="!firstProjectId"
          @click="startNewChat()"
        >
          <MessageSquarePlus :size="16" />
          <span class="truncate">New chat</span>
        </button>
        <button class="sidebar-action" type="button" @click="openSearch">
          <Search :size="16" />
          <span class="truncate">Search</span>
        </button>
        <button
          class="sidebar-action"
          :class="{ 'bg-[var(--surface-active)] !text-[color:var(--text-primary)]': automatedTasksActive }"
          type="button"
          @click="emit('openAutomatedTasks')"
        >
          <Clock :size="16" />
          <span class="truncate">Automated Tasks</span>
        </button>
        <button
          v-if="workflowsEnabled"
          class="sidebar-action"
          :class="{ 'bg-[var(--surface-active)] !text-[color:var(--text-primary)]': workflowsActive }"
          type="button"
          data-testid="sidebar-workflows"
          @click="emit('openWorkflows')"
        >
          <Workflow :size="16" />
          <span class="truncate">Workflows</span>
        </button>
      </div>

      <div class="group mb-2 flex h-8 shrink-0 items-center gap-2 px-4">
        <p
          class="min-w-0 flex-1 text-[13px] font-medium text-[color:var(--text-faint)]"
        >
          Projects
        </p>
        <button
          class="grid h-7 w-7 place-items-center rounded-lg text-[color:var(--text-muted)] opacity-0 transition hover:bg-[var(--surface-hover)] hover:text-[color:var(--text-primary)] group-hover:opacity-100 focus:opacity-100 focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--border-subtle)]"
          type="button"
          aria-label="Add project"
          v-tooltip="'Add project'"
          @click="openProjectDialog"
        >
          <FolderPlus :size="16" />
        </button>
      </div>

      <div
        ref="scrollContainer"
        class="min-h-0 flex-1 overflow-y-auto px-3 pb-4"
      >
        <div ref="scrollContent" class="flex flex-col">
          <div v-if="projectsLoading" class="px-1 pb-5">
            <div
              class="flex items-center gap-2 text-[13px] leading-5 text-[color:var(--text-muted)]"
            >
              <LoaderCircle class="animate-spin" :size="15" />
              <span>Loading projects...</span>
            </div>
          </div>
          <div v-else-if="!hasProjects" class="px-1 pb-5">
            <p
              class="mb-3 text-[13px] leading-5 text-[color:var(--text-muted)]"
            >
              Add a project to choose where Handa runs tasks.
            </p>
            <button
              class="quiet-button w-full"
              type="button"
              @click="openProjectDialog"
            >
              <FolderPlus :size="15" />
              <span>Add project</span>
            </button>
          </div>
          <div
            v-for="project in projects"
            :key="project.id"
            :class="isProjectFolded(project) ? 'mb-1' : 'mb-5'"
          >
            <div
              class="group -mx-3 flex h-8 w-[calc(100%+1.5rem)] items-center text-[14px] font-medium text-[color:var(--text-secondary)] transition hover:bg-[var(--surface-hover)] hover:text-[color:var(--text-primary)]"
              :data-testid="`project-row-${project.id}`"
            >
              <button
                class="flex h-full shrink-0 items-center pl-4 pr-2 focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--border-subtle)]"
                type="button"
                :aria-label="
                  isProjectFolded(project)
                    ? `Expand ${project.name}`
                    : `Collapse ${project.name}`
                "
                :aria-expanded="!isProjectFolded(project)"
                v-tooltip="isProjectFolded(project) ? 'Expand' : 'Collapse'"
                @click.stop="toggleProjectFold(project)"
              >
                <Folder class="shrink-0 group-hover:hidden" :size="16" />
                <component
                  :is="isProjectFolded(project) ? ChevronRight : ChevronDown"
                  class="hidden shrink-0 group-hover:block"
                  :size="16"
                />
              </button>
              <button
                class="flex h-full min-w-0 flex-1 items-center pr-1 text-left focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--border-subtle)]"
                type="button"
                @click="startNewChat(project.id)"
              >
                <span
                  class="min-w-0 flex-1 truncate text-left"
                  v-tooltip="{ content: project.name, overflowOnly: true }"
                  >{{ project.name }}</span
                >
              </button>
              <div
                class="flex h-full shrink-0 items-center gap-0.5 pr-3 opacity-0 transition group-hover:opacity-100 group-focus-within:opacity-100"
              >
                <button
                  class="grid h-6 w-6 place-items-center rounded-md text-[color:var(--text-muted)] transition hover:bg-[var(--surface-hover)] hover:text-[color:var(--text-primary)] focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--border-subtle)]"
                  type="button"
                  :aria-label="`Project settings for ${project.name}`"
                  v-tooltip="'Project settings'"
                  @click.stop="openProjectSettings(project)"
                >
                  <Pencil :size="15" />
                </button>
                <button
                  class="grid h-6 w-6 place-items-center rounded-md text-[color:var(--text-muted)] transition hover:bg-[var(--surface-hover)] hover:text-[color:var(--text-primary)] focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--border-subtle)]"
                  type="button"
                  :aria-label="`New chat in ${project.name}`"
                  v-tooltip="'New chat'"
                  @click.stop="startNewChat(project.id)"
                >
                  <MessageSquarePlus :size="15" />
                </button>
              </div>
            </div>

            <div v-if="!isProjectFolded(project) && project.sessions.length">
              <div
                v-for="session in visibleSessions(project)"
                :key="session.id"
                class="session-row group relative -mx-3 flex h-8 w-[calc(100%+1.5rem)] items-center gap-2 rounded-none px-4 text-left text-[13px] transition cursor-default"
                :class="sessionItemClass(session)"
                :data-session-id="session.id"
                :data-session-state="session.attention ?? session.status"
                @click="emit('selectSession', session.id)"
              >
                <button
                  class="grid h-5 w-4 shrink-0 place-items-center p-0 transition focus:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                  :class="[
                    session.starred
                      ? 'opacity-100'
                      : 'text-[color:var(--text-faint)] opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 group-focus-visible:opacity-100',
                  ]"
                  data-session-action="star"
                  type="button"
                  :aria-label="
                    session.starred
                      ? `Unstar ${session.title}`
                      : `Star ${session.title}`
                  "
                  v-tooltip="session.starred ? 'Unstar' : 'Star'"
                  @click.stop="emit('toggleSessionStar', session.id)"
                >
                  <Star :size="16" fill="none" />
                </button>
                <div
                  class="flex min-w-0 flex-1 items-center gap-2 text-left transition-none group-hover:pr-11 group-focus-within:pr-11 focus:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                >
                  <input
                    v-if="editingSessionId === session.id"
                    :ref="setRenameInput"
                    v-model="editingTitle"
                    class="min-w-0 flex-1 rounded-sm bg-[var(--surface-active)] px-1 text-[13px] text-[color:var(--text-primary)] focus:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                    type="text"
                    data-session-action="rename-input"
                    @click.stop
                    @keydown.enter.prevent="commitRename"
                    @keydown.esc.prevent="cancelRename"
                    @blur="commitRename"
                  />
                  <span
                    v-else
                    class="flex min-w-0 flex-1 items-center gap-1.5"
                    @dblclick.stop="startRename(session)"
                  >
                    <Clock
                      v-if="isAutomatedTaskSession(session)"
                      :size="12"
                      class="shrink-0 text-[color:var(--text-muted)]"
                      aria-hidden="true"
                      data-session-title-icon="automated"
                    />
                    <Split
                      v-else-if="isForkedSession(session)"
                      :size="12"
                      class="shrink-0 text-[color:var(--text-muted)]"
                      aria-hidden="true"
                      data-session-title-icon="forked"
                    />
                    <span class="min-w-0 flex-1 truncate">{{ session.title }}</span>
                  </span>
                </div>
                <span
                  class="pointer-events-none absolute right-3.5 top-1/2 flex h-5 -translate-y-1/2 items-center gap-1"
                >
                  <button
                    class="session-row-action-button pointer-events-auto h-5 w-5 place-items-center rounded-md p-0 text-[color:var(--text-muted)] transition hover:bg-[var(--surface-hover)] hover:text-[color:var(--text-primary)] focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--border-subtle)]"
                    :class="isSessionMenuOpen(session) ? 'is-open' : ''"
                    data-session-action="menu"
                    type="button"
                    aria-label="Session menu"
                    v-tooltip="'Session menu'"
                    @click.stop.prevent="openSessionMenu(session, $event)"
                  >
                    <MoreVertical :size="16" />
                  </button>
                  <button
                    class="session-row-action-button pointer-events-auto h-5 w-5 place-items-center rounded-md p-0 text-[color:var(--text-muted)] transition hover:bg-[var(--surface-hover)] hover:text-[color:var(--text-primary)] focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--border-subtle)]"
                    :class="isSessionMenuOpen(session) ? 'is-open' : ''"
                    data-session-action="archive"
                    type="button"
                    :aria-label="`Archive ${session.title}`"
                    v-tooltip="'Archive'"
                    @click.stop.prevent="emit('archiveSession', session.id)"
                  >
                    <Archive :size="16" />
                  </button>
                </span>
                <span
                  class="ml-auto flex h-5 shrink-0 items-center justify-end text-[13px] text-[color:var(--text-muted)]"
                >
                  <span
                    v-if="session.status === 'running'"
                    class="grid h-4 w-4 shrink-0 place-items-center text-[color:var(--text-muted)]"
                    :class="sessionIndicatorClass(session)"
                    data-session-indicator="running"
                  >
                    <LoaderCircle class="animate-spin" :size="16" />
                  </span>
                  <span
                    v-else-if="isWaitingForInput(session)"
                    class="grid h-4 w-4 shrink-0 place-items-center text-[color:var(--accent)]"
                    :class="sessionIndicatorClass(session)"
                    data-session-indicator="waiting-input"
                    v-tooltip="'Waiting for your input'"
                  >
                    <CircleHelp :size="16" />
                  </span>
                  <span
                    v-else-if="session.attention === 'error'"
                    class="grid h-4 w-4 shrink-0 place-items-center text-destructive"
                    :class="sessionIndicatorClass(session)"
                    data-session-indicator="error"
                  >
                    <CircleAlert :size="16" />
                  </span>
                  <span
                    v-else-if="isUnreadLikeSession(session)"
                    class="grid h-4 w-4 shrink-0 place-items-center rounded-full bg-[color:var(--accent-soft)] text-[color:var(--accent)]"
                    :class="sessionIndicatorClass(session)"
                    :data-session-indicator="
                      session.attention === 'success' ? 'success' : 'unread'
                    "
                    v-tooltip="unreadLikeLabel(session)"
                    :aria-label="unreadLikeLabel(session)"
                  >
                    <span class="h-1.5 w-1.5 rounded-full bg-[color:var(--accent)]"></span>
                  </span>
                  <span
                    v-else
                    class="text-[12px] text-[color:var(--text-faint)]"
                    :class="sessionIndicatorClass(session)"
                    data-session-indicator="age"
                  >
                    {{ formatSessionAge(sessionAgeSource(session)) }}
                  </span>
                </span>
              </div>
              <button
                v-if="canShowMoreSessions(project)"
                class="-mx-3 flex h-8 w-[calc(100%+1.5rem)] items-center justify-start rounded-none pl-10 pr-4 text-[12px] font-normal text-[color:var(--text-faint)] transition hover:bg-[var(--surface-hover)] hover:text-[color:var(--text-muted)] focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--border-subtle)]"
                type="button"
                data-testid="project-sessions-show-more"
                :data-project-id="project.id"
                @click="showMoreSessions(project)"
              >
                Show more
              </button>
              <button
                v-else-if="canShowLessSessions(project)"
                class="-mx-3 flex h-8 w-[calc(100%+1.5rem)] items-center justify-start rounded-none pl-10 pr-4 text-[12px] font-normal text-[color:var(--text-faint)] transition hover:bg-[var(--surface-hover)] hover:text-[color:var(--text-muted)] focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--border-subtle)]"
                type="button"
                data-testid="project-sessions-show-less"
                :data-project-id="project.id"
                @click="showLessSessions(project)"
              >
                Show less
              </button>
            </div>
            <p
              v-else-if="!isProjectFolded(project)"
              class="pl-7 pr-3 text-[13px] text-[color:var(--text-faint)]"
            >
              No recent sessions
            </p>
          </div>
        </div>
      </div>

      <div
        class="p-3"
        :class="
          needsScroll
            ? 'border-t border-[color:var(--border-layout)]'
            : 'border-t border-transparent'
        "
      >
        <button
          class="sidebar-action"
          type="button"
          @click="emit('openSettings')"
        >
          <Settings :size="16" />
          <span class="truncate">Settings</span>
        </button>
      </div>
    </template>

    <div
      v-if="activeMenuSession"
      ref="menuEl"
      class="fixed z-50 w-[188px] rounded-md border border-[color:var(--border-subtle)] bg-[var(--surface)] py-1 text-[13px] text-[color:var(--text-primary)] shadow-2xl"
      :style="{ top: `${menuPosition.top}px`, left: `${menuPosition.left}px` }"
      role="menu"
      @click.stop
      @mousedown.stop
    >
      <button
        class="context-menu-item"
        type="button"
        role="menuitem"
        @click="renameFromMenu"
      >
        <Pencil :size="15" />
        <span>Rename</span>
      </button>
      <button
        class="context-menu-item"
        type="button"
        role="menuitem"
        @click="markUnreadFromMenu"
      >
        <Mail :size="15" />
        <span>Mark as unread</span>
      </button>
      <div class="my-1 border-t border-[color:var(--border-muted)]"></div>
      <button
        class="context-menu-item text-destructive hover:bg-destructive-soft hover:text-destructive"
        type="button"
        role="menuitem"
        @click="askDeleteFromMenu"
      >
        <Trash2 :size="15" />
        <span>Delete</span>
      </button>
    </div>

    <div
      v-if="deleteDialogSession"
      class="fixed inset-0 z-50 grid place-items-center bg-[var(--overlay)] px-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="delete-session-title"
      @click.self="cancelDelete"
    >
      <div
        class="w-full max-w-[380px] rounded-lg border border-[color:var(--border-subtle)] bg-[var(--surface)] p-4 shadow-2xl"
      >
        <h2
          id="delete-session-title"
          class="text-[15px] font-semibold text-[color:var(--text-primary)]"
        >
          Delete session?
        </h2>
        <p class="mt-2 text-[13px] leading-5 text-[color:var(--text-muted)]">
          This removes “{{ deleteDialogSession.title }}” from Handa. The delete
          is treated as permanent in the UI.
        </p>
        <div class="mt-5 flex justify-end gap-2">
          <button class="quiet-button" type="button" @click="cancelDelete">
            Cancel
          </button>
          <button
            class="inline-flex h-8 items-center justify-center rounded-lg bg-destructive px-3 text-[13px] font-medium text-destructive-foreground transition hover:opacity-90"
            type="button"
            @click="confirmDelete"
          >
            Delete
          </button>
        </div>
      </div>
    </div>

    <div
      v-if="projectDialogOpen"
      class="fixed inset-0 z-50 grid place-items-center bg-[var(--overlay)] px-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="add-project-title"
      @click.self="closeProjectDialog"
    >
      <form
        class="w-full max-w-[420px] rounded-lg border border-[color:var(--border-subtle)] bg-[var(--surface)] p-4 shadow-2xl"
        @submit.prevent="submitProject"
      >
        <div class="mb-4 flex items-center gap-3">
          <h2
            id="add-project-title"
            class="min-w-0 flex-1 text-[15px] font-semibold text-[color:var(--text-primary)]"
          >
            Add project
          </h2>
          <button
            class="icon-button h-7 w-7"
            type="button"
            aria-label="Close"
            @click="closeProjectDialog"
          >
            <X :size="16" />
          </button>
        </div>
        <label class="mb-3 block">
          <span
            class="mb-1 block text-[12px] font-medium text-[color:var(--text-muted)]"
            >Name</span
          >
          <input
            v-model="projectName"
            class="h-9 w-full rounded-lg border border-[color:var(--border-subtle)] bg-[var(--panel-bg)] px-3 text-[14px] text-[color:var(--text-primary)] outline-none transition placeholder:text-[color:var(--text-faint)] focus:border-[color:var(--accent)]"
            type="text"
            placeholder="Defaults to folder name"
          />
        </label>
        <label class="block">
          <span
            class="mb-1 block text-[12px] font-medium text-[color:var(--text-muted)]"
            >Directory</span
          >
          <input
            v-model="projectPath"
            class="h-9 w-full rounded-lg border border-[color:var(--border-subtle)] bg-[var(--panel-bg)] px-3 font-mono text-[13px] text-[color:var(--text-primary)] outline-none transition placeholder:text-[color:var(--text-faint)] focus:border-[color:var(--accent)]"
            type="text"
            placeholder="/Users/yadong/dev2/my-project"
            autofocus
          />
        </label>
        <p v-if="projectError" class="mt-2 text-[12px] text-destructive">
          {{ projectError }}
        </p>
        <div class="mt-5 flex justify-end gap-2">
          <button
            class="quiet-button"
            type="button"
            @click="closeProjectDialog"
          >
            Cancel
          </button>
          <button
            class="inline-flex h-8 items-center justify-center rounded-lg bg-foreground px-3 text-[13px] font-medium text-background transition hover:bg-accent hover:text-accent-foreground disabled:cursor-not-allowed disabled:bg-surface-active disabled:text-faint-foreground"
            type="submit"
            :disabled="!projectPath.trim()"
          >
            Add
          </button>
        </div>
      </form>
    </div>

    <div
      v-if="projectSettingsProject"
      class="fixed inset-0 z-50 grid place-items-center bg-[var(--overlay)] px-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="project-settings-title"
      @click.self="closeProjectSettings"
    >
      <form
        class="w-full max-w-[440px] rounded-lg border border-[color:var(--border-subtle)] bg-[var(--surface)] p-4 shadow-2xl"
        @submit.prevent="submitProjectSettings"
      >
        <div class="mb-4 flex items-center gap-3">
          <h2
            id="project-settings-title"
            class="min-w-0 flex-1 text-[15px] font-semibold text-[color:var(--text-primary)]"
          >
            Project settings
          </h2>
          <button
            class="icon-button h-7 w-7"
            type="button"
            aria-label="Close"
            @click="closeProjectSettings"
          >
            <X :size="16" />
          </button>
        </div>

        <label class="mb-3 block">
          <span
            class="mb-1 block text-[12px] font-medium text-[color:var(--text-muted)]"
            >Name</span
          >
          <input
            v-model="projectSettingsName"
            class="h-9 w-full rounded-lg border border-[color:var(--border-subtle)] bg-[var(--panel-bg)] px-3 text-[14px] text-[color:var(--text-primary)] outline-none transition placeholder:text-[color:var(--text-faint)] focus:border-[color:var(--accent)]"
            type="text"
            autofocus
          />
        </label>

        <label class="block">
          <span
            class="mb-1 block text-[12px] font-medium text-[color:var(--text-muted)]"
            >Path</span
          >
          <input
            class="h-9 w-full rounded-lg border border-[color:var(--border-subtle)] bg-[var(--panel-bg)] px-3 font-mono text-[13px] text-[color:var(--text-secondary)] outline-none"
            type="text"
            readonly
            :value="projectSettingsPath"
          />
        </label>

        <div class="mt-3 flex flex-wrap gap-2">
          <button
            class="quiet-button gap-1.5"
            type="button"
            @click="copySettingsProjectPath"
          >
            <Copy :size="15" />
            <span>{{ projectPathCopied ? "Copied" : "Copy path" }}</span>
          </button>
          <button
            class="quiet-button gap-1.5"
            type="button"
            @click="openSettingsProjectInFinder"
          >
            <FolderOpen :size="15" />
            <span>Open in Finder</span>
          </button>
        </div>

        <p v-if="projectSettingsError" class="mt-3 text-[12px] text-destructive">
          {{ projectSettingsError }}
        </p>

        <div class="mt-5 border-t border-[color:var(--border-muted)] pt-4">
          <p
            class="mb-2 text-[12px] leading-5"
            :class="
              projectSettingsHasActiveSessions
                ? 'text-destructive'
                : 'text-[color:var(--text-muted)]'
            "
          >
            {{
              projectSettingsHasActiveSessions
                ? "Stop active sessions before removing this project."
                : "Project files stay on disk."
            }}
          </p>
          <button
            class="quiet-button gap-1.5 text-destructive hover:bg-destructive-soft hover:text-destructive disabled:cursor-not-allowed disabled:opacity-50"
            type="button"
            :disabled="projectSettingsHasActiveSessions"
            @click="removeSettingsProject"
          >
            <Trash2 :size="15" />
            <span>Remove from Handa</span>
          </button>
        </div>

        <div class="mt-5 flex justify-end gap-2">
          <button
            class="quiet-button"
            type="button"
            @click="closeProjectSettings"
          >
            Cancel
          </button>
          <button
            class="inline-flex h-8 items-center justify-center rounded-lg bg-foreground px-3 text-[13px] font-medium text-background transition hover:bg-accent hover:text-accent-foreground disabled:cursor-not-allowed disabled:bg-surface-active disabled:text-faint-foreground"
            type="submit"
            :disabled="!projectSettingsName.trim()"
          >
            Save
          </button>
        </div>
      </form>
    </div>

    <ChatSearchDialog
      :open="effectiveSearchOpen"
      :projects="projects"
      :active-session-id="activeSessionId"
      :loading="projectsLoading"
      @close="closeSearch"
      @select-session="selectSearchSession"
    />
  </aside>
</template>

<style scoped>
.session-row-action-button {
  display: none;
}

.session-row-action-button.is-open,
.session-row:focus-within .session-row-action-button {
  display: grid;
}

@media (hover: hover) {
  .session-row:hover .session-row-action-button {
    display: grid;
  }

  .session-row:hover [data-session-indicator] {
    display: none;
  }
}

.session-row:focus-within [data-session-indicator] {
  display: none;
}
</style>

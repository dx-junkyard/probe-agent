// Trace Analyzer builder templates (Issue #157).
//
// Pure, deterministic form-state -> declarative analyzer spec builders. These
// never make an open-ended decision: they only assemble the same declarative
// JSON a user could hand-write, from explicit choices the builder offers out of
// the real system context. The generated spec is still validated fail-closed by
// the server on save, so this stays an authoring convenience, not a second
// validation authority (Principle 6).

export type AnalyzerTemplateId =
  | "entity_lineage"
  | "component_table"
  | "shadow_diff"
  | "field_transition";

export interface AnalyzerTemplateMeta {
  id: AnalyzerTemplateId;
  label: string;
  description: string;
}

export const ANALYZER_TEMPLATES: AnalyzerTemplateMeta[] = [
  {
    id: "entity_lineage",
    label: "Entity lineage view",
    description:
      "Follow one entity's projection fields across components over time.",
  },
  {
    id: "component_table",
    label: "Component projection table",
    description:
      "List projection rows for a component / projection / phase as a table.",
  },
  {
    id: "shadow_diff",
    label: "Shadow diff",
    description:
      "Compare shadow_current vs shadow_candidate on the fields you choose.",
  },
  {
    id: "field_transition",
    label: "Field transition",
    description:
      "See how one field of an entity changes across components and time.",
  },
];

export interface BuilderState {
  entityType: string;
  entityId: string;
  components: string[];
  projectionName: string;
  phases: string[];
  fields: string[];
  field: string; // single-field templates
  timeStart: string;
  timeEnd: string;
}

export const EMPTY_BUILDER_STATE: BuilderState = {
  entityType: "",
  entityId: "",
  components: [],
  projectionName: "",
  phases: [],
  fields: [],
  field: "",
  timeStart: "",
  timeEnd: "",
};

export interface AnalyzerSpec {
  source: "trace_projections";
  filter?: Record<string, unknown>;
  select: { name: string; path: string }[];
  group_by?: string[];
  order_by?: { name: string; dir: "asc" | "desc" }[];
  compare?: { phases: [string, string]; fields: string[]; entity_type?: string };
}

export interface BuildResult {
  spec: AnalyzerSpec | null;
  errors: string[];
}

// A field name is used both as a select name and inside a `$.fields.<name>`
// path. The path grammar only accepts [A-Za-z0-9_-] key segments, so a field
// with other characters cannot be addressed and must be rejected up front
// rather than producing a spec the server will reject.
const PATH_KEY_RE = /^[A-Za-z0-9_-]+$/;

function fieldSelects(fields: string[], errors: string[]): { name: string; path: string }[] {
  const out: { name: string; path: string }[] = [];
  for (const f of fields) {
    if (!PATH_KEY_RE.test(f)) {
      errors.push(`Field "${f}" has characters that cannot be addressed by a path.`);
      continue;
    }
    out.push({ name: f, path: `$.fields.${f}` });
  }
  return out;
}

function timeWindow(state: BuilderState): Record<string, number> | null {
  const w: Record<string, number> = {};
  const start = Number(state.timeStart);
  const end = Number(state.timeEnd);
  if (state.timeStart.trim() && Number.isFinite(start)) w.start = start;
  if (state.timeEnd.trim() && Number.isFinite(end)) w.end = end;
  return Object.keys(w).length ? w : null;
}

export function buildSpec(
  template: AnalyzerTemplateId,
  state: BuilderState,
): BuildResult {
  const errors: string[] = [];
  const window = timeWindow(state);

  if (template === "entity_lineage" || template === "field_transition") {
    if (!state.entityType) errors.push("Choose an entity type.");
    if (!state.entityId) errors.push("Choose an entity id.");
  }

  if (template === "entity_lineage") {
    if (!state.fields.length) errors.push("Choose at least one field to follow.");
    const select = [
      { name: "timestamp", path: "$.timestamp" },
      { name: "component", path: "$.component_id" },
      { name: "phase", path: "$.phase" },
      ...fieldSelects(state.fields, errors),
    ];
    if (errors.length) return { spec: null, errors };
    const filter: Record<string, unknown> = {
      entity: { type: state.entityType, id: state.entityId },
    };
    if (window) filter.time_window = window;
    return {
      spec: {
        source: "trace_projections",
        filter,
        select,
        order_by: [{ name: "timestamp", dir: "asc" }],
      },
      errors,
    };
  }

  if (template === "field_transition") {
    if (!state.field) errors.push("Choose the field to track.");
    const select = [
      { name: "timestamp", path: "$.timestamp" },
      { name: "component", path: "$.component_id" },
      ...fieldSelects(state.field ? [state.field] : [], errors),
    ];
    if (errors.length) return { spec: null, errors };
    const filter: Record<string, unknown> = {
      entity: { type: state.entityType, id: state.entityId },
    };
    if (state.components.length) filter.components = state.components;
    if (window) filter.time_window = window;
    return {
      spec: {
        source: "trace_projections",
        filter,
        select,
        group_by: [state.field],
        order_by: [{ name: "timestamp", dir: "asc" }],
      },
      errors,
    };
  }

  if (template === "component_table") {
    if (!state.components.length && !state.projectionName) {
      errors.push("Choose a component or a projection to scope the table.");
    }
    const select = [
      { name: "trace", path: "$.trace_id" },
      { name: "component", path: "$.component_id" },
      { name: "phase", path: "$.phase" },
      ...fieldSelects(state.fields, errors),
    ];
    if (errors.length) return { spec: null, errors };
    const filter: Record<string, unknown> = {};
    if (state.components.length) filter.components = state.components;
    if (state.projectionName) filter.projection_name = state.projectionName;
    if (state.phases.length) filter.phases = state.phases;
    if (window) filter.time_window = window;
    return {
      spec: {
        source: "trace_projections",
        filter: Object.keys(filter).length ? filter : undefined,
        select,
        order_by: [{ name: "trace", dir: "asc" }],
      },
      errors,
    };
  }

  // shadow_diff
  if (!state.fields.length) errors.push("Choose at least one field to compare.");
  const compareFields: string[] = [];
  for (const f of state.fields) {
    if (!PATH_KEY_RE.test(f)) {
      errors.push(`Field "${f}" has characters that cannot be addressed by a path.`);
    } else {
      compareFields.push(f);
    }
  }
  if (errors.length) return { spec: null, errors };
  const filter: Record<string, unknown> = {};
  if (state.components.length) filter.components = state.components;
  if (state.projectionName) filter.projection_name = state.projectionName;
  if (window) filter.time_window = window;
  const compare: AnalyzerSpec["compare"] = {
    phases: ["shadow_current", "shadow_candidate"],
    fields: compareFields,
  };
  if (state.entityType) compare.entity_type = state.entityType;
  return {
    spec: {
      source: "trace_projections",
      filter: Object.keys(filter).length ? filter : undefined,
      // select is required + non-empty even for compare specs.
      select: [{ name: "trace", path: "$.trace_id" }],
      compare,
    },
    errors,
  };
}

// A short human-readable summary of a spec for the review gate (Issue #157):
// what it targets, which projection/field/phase it reads, and how it filters.
export function summarizeSpec(spec: unknown): string[] {
  if (!spec || typeof spec !== "object") return ["Invalid spec"];
  const s = spec as Partial<AnalyzerSpec> & { filter?: Record<string, unknown> };
  const lines: string[] = [];
  const filter = (s.filter ?? {}) as Record<string, unknown>;
  const entity = filter.entity as { type?: string; id?: string } | undefined;
  if (entity?.type) lines.push(`Entity: ${entity.type} = ${entity.id ?? "*"}`);
  if (Array.isArray(filter.components) && filter.components.length) {
    lines.push(`Components: ${(filter.components as string[]).join(", ")}`);
  }
  if (filter.projection_name) lines.push(`Projection: ${String(filter.projection_name)}`);
  if (Array.isArray(filter.phases) && filter.phases.length) {
    lines.push(`Phases: ${(filter.phases as string[]).join(", ")}`);
  }
  const tw = filter.time_window as { start?: number; end?: number } | undefined;
  if (tw && (tw.start != null || tw.end != null)) {
    lines.push(`Time window: ${tw.start ?? "…"} – ${tw.end ?? "…"}`);
  }
  if (Array.isArray(s.select) && s.select.length) {
    lines.push(`Reads: ${s.select.map((x) => x.name).join(", ")}`);
  }
  if (Array.isArray(s.group_by) && s.group_by.length) {
    lines.push(`Grouped by: ${s.group_by.join(", ")}`);
  }
  if (s.compare) {
    lines.push(
      `Shadow diff: ${s.compare.phases.join(" vs ")} on ${s.compare.fields.join(", ")}`,
    );
  }
  return lines.length ? lines : ["Reads projection rows with no filter"];
}

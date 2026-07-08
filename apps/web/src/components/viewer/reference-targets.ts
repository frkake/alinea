import type { DocBlock, DocSection } from "@/components/viewer/document-types";

function normalizeRef(value: string | null | undefined): string | null {
  const raw = value?.trim();
  if (!raw) return null;
  return raw.startsWith("#") ? raw.slice(1) : raw;
}

function canonicalRef(value: string | null | undefined): string | null {
  const normalized = normalizeRef(value);
  if (!normalized) return null;
  return normalized
    .toLowerCase()
    .replace(/^(figure|fig)[.:_\s-]+/, "fig-")
    .replace(/^(table|tbl)[.:_\s-]+/, "tbl-")
    .replace(/^(equation|eq)[.:_\s-]+/, "eq-")
    .replace(/^(section|sec)[.:_\s-]+/, "sec-")
    .replace(/[.:_\s]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
}

function addTarget(map: Map<string, string>, key: string | null | undefined, blockId: string) {
  const normalized = normalizeRef(key);
  if (!normalized || map.has(normalized)) return;
  map.set(normalized, blockId);
  const canonical = canonicalRef(normalized);
  if (canonical && !map.has(canonical)) map.set(canonical, blockId);
}

function addNumberTargets(map: Map<string, string>, kind: string, number: string | null | undefined, blockId: string) {
  const n = number?.trim();
  if (!n) return;
  if (kind === "figure") {
    addTarget(map, `fig:${n}`, blockId);
    addTarget(map, `figure:${n}`, blockId);
    addTarget(map, `fig-${n}`, blockId);
  } else if (kind === "table") {
    addTarget(map, `tbl:${n}`, blockId);
    addTarget(map, `table:${n}`, blockId);
    addTarget(map, `tbl-${n}`, blockId);
  } else if (kind === "equation") {
    addTarget(map, `eq:${n}`, blockId);
    addTarget(map, `equation:${n}`, blockId);
    addTarget(map, `eq-${n}`, blockId);
  } else if (kind === "section") {
    addTarget(map, `sec:${n}`, blockId);
    addTarget(map, `section:${n}`, blockId);
    addTarget(map, `sec-${n}`, blockId);
  }
}

function addBlock(map: Map<string, string>, block: DocBlock) {
  addTarget(map, block.id, block.id);
  addTarget(map, block.label, block.id);
  addNumberTargets(map, block.type, block.number, block.id);
}

export function buildReferenceTargetMap(sections: DocSection[]): Map<string, string> {
  const map = new Map<string, string>();
  const walk = (items: DocSection[]) => {
    for (const section of items) {
      const firstBlock = section.blocks?.[0] ?? null;
      if (firstBlock) {
        addTarget(map, section.id, firstBlock.id);
        addNumberTargets(map, "section", section.heading?.number, firstBlock.id);
      }
      for (const block of section.blocks ?? []) addBlock(map, block);
      for (const child of section.sections ?? []) walk([child]);
    }
  };
  walk(sections);
  return map;
}

export function resolveReferenceTarget(map: Map<string, string>, ref: string | null | undefined): string | null {
  const normalized = normalizeRef(ref);
  if (!normalized) return null;
  return map.get(normalized) ?? map.get(canonicalRef(normalized) ?? "") ?? null;
}

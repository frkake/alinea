import type { DocBlock, DocSection } from "@/components/viewer/document-types";

function normalizeRef(value: string | null | undefined): string | null {
  const raw = value?.trim();
  if (!raw) return null;
  return raw.startsWith("#") ? raw.slice(1) : raw;
}

function addTarget(map: Map<string, string>, key: string | null | undefined, blockId: string) {
  const normalized = normalizeRef(key);
  if (!normalized || map.has(normalized)) return;
  map.set(normalized, blockId);
}

function addBlock(map: Map<string, string>, block: DocBlock) {
  addTarget(map, block.id, block.id);
  addTarget(map, block.label, block.id);
  if (block.type === "equation" && block.number) addTarget(map, `eq:${block.number}`, block.id);
}

export function buildReferenceTargetMap(sections: DocSection[]): Map<string, string> {
  const map = new Map<string, string>();
  const walk = (items: DocSection[]) => {
    for (const section of items) {
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
  return map.get(normalized) ?? null;
}

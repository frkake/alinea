import type { DocBlock, DocSection } from "@/components/viewer/document-types";

function norm(value: string | null | undefined): string {
  return (value ?? "").trim();
}

export function isSectionHeadingBlock(section: DocSection, block: DocBlock): boolean {
  if (block.type !== "heading") return false;
  const heading = section.heading;
  return norm(block.number) === norm(heading?.number) && norm(block.title) === norm(heading?.title);
}

export function sectionHeadingBlock(section: DocSection): DocBlock | null {
  const first = section.blocks?.[0];
  return first && isSectionHeadingBlock(section, first) ? first : null;
}

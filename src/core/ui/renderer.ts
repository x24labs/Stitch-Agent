// OpenTUI-backed terminal renderer
// Replaces raw ANSI cursor/write calls with OpenTUI's native double-buffered,
// differential rendering pipeline. Eliminates flickering.

import {
  ASCIIFontRenderable,
  BoxRenderable,
  type CliRenderer,
  type CliRendererConfig,
  type StyledText,
  TextRenderable,
  createCliRenderer,
} from "@opentui/core";

export interface RendererElements {
  root: BoxRenderable;
  renderer: CliRenderer;
}

const RENDERER_CONFIG: CliRendererConfig = {
  exitOnCtrlC: false,
  screenMode: "alternate-screen",
  targetFps: 30,
  maxFps: 30,
  useMouse: false,
};

export async function createRenderer(): Promise<RendererElements> {
  const renderer = await createCliRenderer(RENDERER_CONFIG);
  const root = new BoxRenderable(renderer, {
    id: "root",
    flexDirection: "column",
    width: "100%",
    height: "100%",
  });
  renderer.root.add(root);
  return { root, renderer };
}

// Helper: create a text renderable and add to parent
export function addText(
  ctx: CliRenderer,
  parent: BoxRenderable,
  id: string,
  opts?: {
    content?: StyledText | string;
    flexGrow?: number;
    live?: boolean;
    wrapMode?: "none" | "char" | "word";
  },
): TextRenderable {
  const text = new TextRenderable(ctx, {
    id,
    content: opts?.content ?? "",
    flexGrow: opts?.flexGrow,
    live: opts?.live,
    wrapMode: opts?.wrapMode ?? "none",
  });
  parent.add(text);
  return text;
}

// Helper: create a box renderable and add to parent
export function addBox(
  ctx: CliRenderer,
  parent: BoxRenderable,
  id: string,
  opts?: Partial<ConstructorParameters<typeof BoxRenderable>[1]>,
): BoxRenderable {
  const box = new BoxRenderable(ctx, { id, ...opts });
  parent.add(box);
  return box;
}

// Helper: create an ASCII font renderable and add to parent
export function addAsciiFont(
  ctx: CliRenderer,
  parent: BoxRenderable,
  id: string,
  text: string,
  opts?: {
    color?: string;
    font?: "tiny" | "block" | "shade" | "slick" | "huge" | "grid" | "pallet";
  },
): ASCIIFontRenderable {
  const ascii = new ASCIIFontRenderable(ctx, {
    id,
    text,
    font: opts?.font ?? "block",
    color: opts?.color,
  });
  parent.add(ascii);
  return ascii;
}

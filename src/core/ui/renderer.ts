// OpenTUI-backed terminal renderer
// Replaces raw ANSI cursor/write calls with OpenTUI's native double-buffered,
// differential rendering pipeline. Eliminates flickering.

import {
  BoxRenderable,
  type CliRenderer,
  type CliRendererConfig,
  createCliRenderer,
} from "@opentui/core";

interface RendererElements {
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

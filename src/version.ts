declare const __STITCH_VERSION__: string;

export const VERSION: string = typeof __STITCH_VERSION__ === "string" ? __STITCH_VERSION__ : "dev";

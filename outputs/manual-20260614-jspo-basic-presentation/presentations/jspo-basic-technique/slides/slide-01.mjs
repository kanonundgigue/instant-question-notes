import { buildSlide } from "./slide-common.mjs";

export async function slide01(presentation, ctx) {
  return buildSlide(presentation, ctx, 1);
}

import { h } from "vue";
// uni-app rewrites template <text> to uni-text. SVG needs the native text element.
export default function SvgLabel(props: Record<string, any>, { slots }: any) {
  return h("text", props, slots.default?.());
}

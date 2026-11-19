// Draws the list price on each Higgsfield node and keeps it current as widgets change.
// ComfyUI's own price badge only shows on comfy.org partner nodes, which require a
// comfy.org sign-in to run, so these nodes draw their own badge from web/rates.json.
import { app } from "../../scripts/app.js";

const RATES_URL = new URL("./rates.json", import.meta.url);
let rates = null;

const widget = (node, name) => node.widgets?.find((w) => w.name === name)?.value;

function perClip(table, node) {
  const rate = table?.[widget(node, "resolution")];
  const seconds = widget(node, "duration");
  return rate == null || seconds == null ? null : `≈ $${(rate * seconds).toFixed(2)}`;
}

// Badge text for each node type, or null when a value is missing.
const PRICES = {
  Seedance25_Higgsfield: (node) => perClip(rates["seedance-2.5"], node),
  Kling40_Higgsfield: (node) => perClip(rates["kling-4.0"], node),
  GenjutsuMotionTransfer_Higgsfield: (node) => {
    const rate = rates[widget(node, "model")]?.[widget(node, "resolution")];
    return rate == null ? null : `≈ $${rate.toFixed(4)}/s`;
  },
};

app.registerExtension({
  name: "jutsuhq.Higgsfield.price",
  async setup() {
    try {
      rates = (await (await fetch(RATES_URL)).json()).per_second;
      app.graph?.setDirtyCanvas(true, true);
    } catch (err) {
      console.warn("[Higgsfield] could not load rates.json", err);
    }
  },
  nodeCreated(node) {
    const price = PRICES[node.comfyClass];
    if (!price || !window.LGraphBadge || !Array.isArray(node.badges)) return;
    node.badges.push(() => {
      const text = rates ? price(node) : null;
      return new window.LGraphBadge({ text: text ?? "$ …", bgColor: "#2b5d34", fgColor: "#ffffff" });
    });
  },
});

// Knowledge about model files: which are weights, GGUF quantizations, and
// which files to tick by default. Pure functions, no DOM.

const WEIGHT_FILE = /\.(safetensors|gguf|bin|pt|pth|onnx|h5|msgpack|tflite|ckpt|mlmodel)$/i;

export const isWeight = (path) => WEIGHT_FILE.test(path);
export const isGguf = (path) => /\.gguf$/i.test(path);
export const isSafetensors = (path) => /\.safetensors$/i.test(path);

/** "Llama-3.2-3B-Instruct-Q4_K_M.gguf" -> "Q4_K_M" ("" if not a quantized GGUF) */
export function quantOf(path) {
  const fileName = path.split("/").pop();
  const match = fileName.match(/(?:^|[-_.])((?:I?Q\d(?:_[A-Z0-9]+)*)|BF16|F16|F32)\.gguf$/i);
  return match ? match[1].toUpperCase() : "";
}

// Most useful quantizations first; anything else follows alphabetically.
const QUANT_ORDER = ["Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0", "Q3_K_M", "IQ4_XS", "Q4_K_S", "Q2_K", "BF16", "F16"];
const quantRank = (q) => (QUANT_ORDER.includes(q) ? QUANT_ORDER.indexOf(q) : 99);

/** Distinct quantizations in a file list, most useful first. */
export function quantizations(paths) {
  const found = new Set(paths.filter(isGguf).map(quantOf).filter(Boolean));
  return [...found].sort((a, b) => quantRank(a) - quantRank(b) || a.localeCompare(b));
}

export const DEFAULT_QUANT = "Q4_K_M";

/**
 * File presets shown as chips in the model panel. Each returns a predicate
 * that decides whether a path should be ticked. Non-weight files (configs,
 * tokenizers) are always kept, except by "all"/"none" semantics noted below.
 */
export const presets = {
  all: () => () => true,
  safetensors: () => (path) => !isWeight(path) || isSafetensors(path),
  configsOnly: () => (path) => !isWeight(path),
  quant: (q) => (path) => !isWeight(path) || quantOf(path) === q,
  /** "*Q5_K_M*" style pattern, matched against the full path or the file name. */
  pattern(glob) {
    const regex = new RegExp(
      "^" + glob.split("*").map((part) => part.replace(/[.+?^${}()|[\]\\]/g, "\\$&")).join(".*") + "$",
      "i",
    );
    return (path) => !isWeight(path) || regex.test(path) || regex.test(path.split("/").pop());
  },
};

/**
 * What to tick when a repo is opened, and which preset chip that corresponds to.
 * - many GGUF files: just the default quantization (or none if it's missing)
 * - the same weights in several formats: safetensors only
 * - otherwise: everything
 */
export function defaultSelection(paths) {
  const ggufCount = paths.filter(isGguf).length;
  const weights = paths.filter(isWeight);
  const hasSafetensors = weights.some(isSafetensors);
  const hasOtherWeights = weights.some((p) => !isSafetensors(p));

  if (ggufCount > 1) {
    return quantizations(paths).includes(DEFAULT_QUANT)
      ? { preset: `quant:${DEFAULT_QUANT}`, keep: presets.quant(DEFAULT_QUANT) }
      : { preset: "configsOnly", keep: presets.configsOnly() };
  }
  if (hasSafetensors && hasOtherWeights) return { preset: "safetensors", keep: presets.safetensors() };
  return { preset: "all", keep: presets.all() };
}

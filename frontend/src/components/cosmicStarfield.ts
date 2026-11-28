import { TAU, clamp, type RgbColor } from "./intro/vendorCatalog";

export interface SpectralClass {
  readonly name: string;
  readonly core: RgbColor;
  readonly mid: RgbColor;
  readonly halo: RgbColor;
}

// 真实天文黑体光谱色阶：蓝白巨星 / 冰白星核 / 香槟暖白 / 琥珀铜橙 / 深赤橙 / 微星云紫
export const COSMIC_SPECTRA: readonly SpectralClass[] = [
  {
    name: "blueGiant",
    core: [255, 255, 255],
    mid: [168, 208, 255],
    halo: [74, 134, 248],
  },
  {
    name: "iceWhite",
    core: [255, 255, 255],
    mid: [226, 238, 255],
    halo: [142, 174, 232],
  },
  {
    name: "champagne",
    core: [255, 253, 244],
    mid: [255, 224, 182],
    halo: [216, 152, 86],
  },
  {
    name: "amberCopper",
    core: [255, 244, 228],
    mid: [244, 150, 88],
    halo: [194, 82, 38],
  },
  {
    name: "deepRust",
    core: [255, 226, 202],
    mid: [222, 108, 66],
    halo: [152, 52, 34],
  },
  {
    name: "nebulaViolet",
    core: [255, 250, 255],
    mid: [204, 180, 255],
    halo: [130, 94, 242],
  },
];

export interface CosmicSpriteAtlas {
  orbs: HTMLCanvasElement[];
  flares: HTMLCanvasElement[];
  streaks: HTMLCanvasElement[];
  bokehStreaks: HTMLCanvasElement[];
  lightBokehStreaks: HTMLCanvasElement[];
}

export interface WormholeStar {
  angle: number; // 相对消失点的径向角度
  radial: number; // 初始距消失点距离（按视口基准半径归一化）
  z0: number; // 初始透视深度（越大越远、滞留中心；越小越近、高速甩出）
  spectral: number;
  drawRadius: number;
  luminance: number;
  hasFlare: boolean;
  seed: number;
}

export interface ForegroundBokehStreak {
  angle: number;
  radial: number; // 仅分布在画面中周边，掠过镜头四角制造厚重景深，不遮挡中央正文
  z0: number;
  spectral: number;
  thickness: number; // 光学散景带宽
  luminance: number;
  seed: number;
}

export interface DeepSpaceStar {
  nx: number; // -1.04 .. 1.04（全视口归一化坐标）
  ny: number;
  depth: number; // 视差深度 0.18 .. 0.95
  spectral: number;
  drawRadius: number; // 精灵绘制半径（像素）
  luminance: number; // 0.18 .. 1.0
  twinkleSpeed: number;
  twinklePhase: number;
  twinkleDepth: number;
  hasFlare: boolean;
}

export interface GalaxyRingStar {
  angle: number; // 基础轨道角
  radius: number; // 相对星环基准半径 0.72 .. 1.54
  armWeight: number; // 属于主旋臂核心的权重 0 .. 1
  knotBoost: number; // 处于星团密聚节点的亮度加成 0 .. 1
  spectral: number;
  drawRadius: number; // 精灵绘制半径（像素），幂律分布拉开大小层次
  luminance: number; // 0.16 .. 1.0
  orbitSpeed: number; // 极缓慢开普勒差速公转
  wobblePhase: number;
  twinkleSpeed: number;
  twinklePhase: number;
  twinkleDepth: number;
  hasFlare: boolean;
  seed: number;
}

// 确定性伪随机数（避免每次重渲染星图跳变）
function createRng(seed: number) {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) | 0;
    let t = Math.imul(state ^ (state >>> 15), 1 | state);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function gaussianRand(rand: () => number): number {
  const u1 = Math.max(1e-6, rand());
  const u2 = rand();
  return Math.sqrt(-2 * Math.log(u1)) * Math.cos(TAU * u2);
}

function pickSpectral(rand: () => number, bias: "cool" | "warm" | "balanced"): number {
  const r = rand();
  if (bias === "cool") {
    if (r < 0.44) return 0; // blueGiant
    if (r < 0.76) return 1; // iceWhite
    if (r < 0.86) return 2; // champagne
    if (r < 0.94) return 3; // amberCopper
    if (r < 0.97) return 4; // deepRust
    return 5; // nebulaViolet
  }
  if (bias === "warm") {
    if (r < 0.18) return 0;
    if (r < 0.36) return 1;
    if (r < 0.56) return 2;
    if (r < 0.84) return 3;
    if (r < 0.95) return 4;
    return 5;
  }
  if (r < 0.32) return 0;
  if (r < 0.59) return 1;
  if (r < 0.75) return 2;
  if (r < 0.91) return 3;
  if (r < 0.96) return 4;
  return 5;
}

let cachedAtlas: CosmicSpriteAtlas | null = null;

function buildOrbSprite(spec: SpectralClass, size: number): HTMLCanvasElement {
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  if (!ctx) return canvas;

  const half = size * 0.5;
  const g = ctx.createRadialGradient(half, half, 0, half, half, half);
  g.addColorStop(0, `rgba(${spec.core[0]}, ${spec.core[1]}, ${spec.core[2]}, 1)`);
  g.addColorStop(0.11, `rgba(${spec.core[0]}, ${spec.core[1]}, ${spec.core[2]}, 0.96)`);
  g.addColorStop(0.24, `rgba(${spec.mid[0]}, ${spec.mid[1]}, ${spec.mid[2]}, 0.74)`);
  g.addColorStop(0.46, `rgba(${spec.halo[0]}, ${spec.halo[1]}, ${spec.halo[2]}, 0.28)`);
  g.addColorStop(0.74, `rgba(${spec.halo[0]}, ${spec.halo[1]}, ${spec.halo[2]}, 0.06)`);
  g.addColorStop(1, `rgba(${spec.halo[0]}, ${spec.halo[1]}, ${spec.halo[2]}, 0)`);

  ctx.fillStyle = g;
  ctx.fillRect(0, 0, size, size);
  return canvas;
}

function buildFlareSprite(spec: SpectralClass, size: number): HTMLCanvasElement {
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  if (!ctx) return canvas;

  const half = size * 0.5;

  // 柔和外星晕
  const halo = ctx.createRadialGradient(half, half, 0, half, half, half * 0.88);
  halo.addColorStop(0, `rgba(${spec.core[0]}, ${spec.core[1]}, ${spec.core[2]}, 1)`);
  halo.addColorStop(0.1, `rgba(${spec.core[0]}, ${spec.core[1]}, ${spec.core[2]}, 0.96)`);
  halo.addColorStop(0.22, `rgba(${spec.mid[0]}, ${spec.mid[1]}, ${spec.mid[2]}, 0.68)`);
  halo.addColorStop(0.48, `rgba(${spec.halo[0]}, ${spec.halo[1]}, ${spec.halo[2]}, 0.24)`);
  halo.addColorStop(1, `rgba(${spec.halo[0]}, ${spec.halo[1]}, ${spec.halo[2]}, 0)`);
  ctx.fillStyle = halo;
  ctx.fillRect(0, 0, size, size);

  // 极克制的光学四向衍射星芒
  ctx.save();
  ctx.translate(half, half);
  for (let i = 0; i < 2; i++) {
    ctx.save();
    ctx.rotate((i * Math.PI) / 2);
    const spikeLen = half * 0.92;
    const sg = ctx.createLinearGradient(-spikeLen, 0, spikeLen, 0);
    sg.addColorStop(0, `rgba(${spec.mid[0]}, ${spec.mid[1]}, ${spec.mid[2]}, 0)`);
    sg.addColorStop(0.38, `rgba(${spec.mid[0]}, ${spec.mid[1]}, ${spec.mid[2]}, 0.28)`);
    sg.addColorStop(0.5, `rgba(${spec.core[0]}, ${spec.core[1]}, ${spec.core[2]}, 0.92)`);
    sg.addColorStop(0.62, `rgba(${spec.mid[0]}, ${spec.mid[1]}, ${spec.mid[2]}, 0.28)`);
    sg.addColorStop(1, `rgba(${spec.mid[0]}, ${spec.mid[1]}, ${spec.mid[2]}, 0)`);
    ctx.fillStyle = sg;
    ctx.beginPath();
    ctx.ellipse(0, 0, spikeLen, Math.max(1.1, size * 0.017), 0, 0, TAU);
    ctx.fill();
    ctx.restore();
  }
  ctx.restore();

  return canvas;
}

function buildStreakSprite(
  spec: SpectralClass,
  width = 128,
  height = 32,
): HTMLCanvasElement {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) return canvas;

  const cy = height * 0.5;

  const drawSoftEllipse = (
    cx: number,
    rx: number,
    ry: number,
    stops: readonly [number, string][],
  ) => {
    ctx.save();
    ctx.translate(cx, cy);
    ctx.scale(rx, ry);
    const g = ctx.createRadialGradient(0, 0, 0, 0, 0, 1);
    for (const [offset, color] of stops) g.addColorStop(offset, color);
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(0, 0, 1, 0, TAU);
    ctx.fill();
    ctx.restore();
  };

  // 1) 柔和宽幅光谱大气外晕（X/Y 双向平滑高斯衰减，彻底消除生硬三角轮廓）
  drawSoftEllipse(width * 0.6, width * 0.56, height * 0.44, [
    [0, `rgba(${spec.mid[0]}, ${spec.mid[1]}, ${spec.mid[2]}, 0.34)`],
    [0.35, `rgba(${spec.halo[0]}, ${spec.halo[1]}, ${spec.halo[2]}, 0.19)`],
    [0.72, `rgba(${spec.halo[0]}, ${spec.halo[1]}, ${spec.halo[2]}, 0.05)`],
    [1, `rgba(${spec.halo[0]}, ${spec.halo[1]}, ${spec.halo[2]}, 0)`],
  ]);

  // 2) 中层高饱和流光束
  drawSoftEllipse(width * 0.66, width * 0.46, height * 0.16, [
    [0, `rgba(${spec.core[0]}, ${spec.core[1]}, ${spec.core[2]}, 0.72)`],
    [0.42, `rgba(${spec.mid[0]}, ${spec.mid[1]}, ${spec.mid[2]}, 0.44)`],
    [0.78, `rgba(${spec.halo[0]}, ${spec.halo[1]}, ${spec.halo[2]}, 0.1)`],
    [1, `rgba(${spec.halo[0]}, ${spec.halo[1]}, ${spec.halo[2]}, 0)`],
  ]);

  // 3) 极细锐白炽光丝核心（仅占贴图高度 ~6%，确保拖尾纤细如星光针）
  drawSoftEllipse(width * 0.72, width * 0.34, height * 0.062, [
    [0, `rgba(${spec.core[0]}, ${spec.core[1]}, ${spec.core[2]}, 1)`],
    [0.52, `rgba(${spec.core[0]}, ${spec.core[1]}, ${spec.core[2]}, 0.78)`],
    [0.85, `rgba(${spec.mid[0]}, ${spec.mid[1]}, ${spec.mid[2]}, 0.24)`],
    [1, `rgba(${spec.mid[0]}, ${spec.mid[1]}, ${spec.mid[2]}, 0)`],
  ]);

  // 4) 头部致密星核点光（位于 x = 0.88）
  drawSoftEllipse(width * 0.88, height * 0.16, height * 0.16, [
    [0, `rgba(${spec.core[0]}, ${spec.core[1]}, ${spec.core[2]}, 1)`],
    [0.34, `rgba(${spec.core[0]}, ${spec.core[1]}, ${spec.core[2]}, 0.76)`],
    [0.68, `rgba(${spec.mid[0]}, ${spec.mid[1]}, ${spec.mid[2]}, 0.24)`],
    [1, `rgba(${spec.halo[0]}, ${spec.halo[1]}, ${spec.halo[2]}, 0)`],
  ]);

  return canvas;
}

function buildBokehStreakSprite(
  spec: SpectralClass,
  width = 160,
  height = 48,
): HTMLCanvasElement {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) return canvas;

  const cy = height * 0.5;
  const drawSoftEllipse = (
    cx: number,
    rx: number,
    ry: number,
    stops: readonly [number, string][],
  ) => {
    ctx.save();
    ctx.translate(cx, cy);
    ctx.scale(rx, ry);
    const g = ctx.createRadialGradient(0, 0, 0, 0, 0, 1);
    for (const [offset, color] of stops) g.addColorStop(offset, color);
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(0, 0, 1, 0, TAU);
    ctx.fill();
    ctx.restore();
  };

  // 纯失焦光学散景大光带：无锐白硬核，靠厚实宽幅的柔和色晕表现贴近镜头掠过的体量感
  drawSoftEllipse(width * 0.56, width * 0.52, height * 0.46, [
    [0, `rgba(${spec.halo[0]}, ${spec.halo[1]}, ${spec.halo[2]}, 0.3)`],
    [0.45, `rgba(${spec.halo[0]}, ${spec.halo[1]}, ${spec.halo[2]}, 0.14)`],
    [0.8, `rgba(${spec.halo[0]}, ${spec.halo[1]}, ${spec.halo[2]}, 0.03)`],
    [1, `rgba(${spec.halo[0]}, ${spec.halo[1]}, ${spec.halo[2]}, 0)`],
  ]);

  drawSoftEllipse(width * 0.65, width * 0.4, height * 0.25, [
    [0, `rgba(${spec.mid[0]}, ${spec.mid[1]}, ${spec.mid[2]}, 0.46)`],
    [0.52, `rgba(${spec.mid[0]}, ${spec.mid[1]}, ${spec.mid[2]}, 0.2)`],
    [1, `rgba(${spec.halo[0]}, ${spec.halo[1]}, ${spec.halo[2]}, 0)`],
  ]);

  drawSoftEllipse(width * 0.76, width * 0.22, height * 0.13, [
    [0, `rgba(${spec.core[0]}, ${spec.core[1]}, ${spec.core[2]}, 0.52)`],
    [0.58, `rgba(${spec.mid[0]}, ${spec.mid[1]}, ${spec.mid[2]}, 0.16)`],
    [1, `rgba(${spec.mid[0]}, ${spec.mid[1]}, ${spec.mid[2]}, 0)`],
  ]);

  return canvas;
}

function buildLightBokehSprite(
  rgb: RgbColor,
  width = 160,
  height = 48,
): HTMLCanvasElement {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) return canvas;

  const cy = height * 0.5;
  const drawSoftEllipse = (
    cx: number,
    rx: number,
    ry: number,
    stops: readonly [number, string][],
  ) => {
    ctx.save();
    ctx.translate(cx, cy);
    ctx.scale(rx, ry);
    const g = ctx.createRadialGradient(0, 0, 0, 0, 0, 1);
    for (const [offset, color] of stops) g.addColorStop(offset, color);
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(0, 0, 1, 0, TAU);
    ctx.fill();
    ctx.restore();
  };

  // 浅色模式专用失焦棱镜软光带：双向平滑羽化，彻底消除圆角胶囊硬边
  drawSoftEllipse(width * 0.56, width * 0.52, height * 0.44, [
    [0, `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, 0.28)`],
    [0.48, `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, 0.12)`],
    [0.82, `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, 0.025)`],
    [1, `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, 0)`],
  ]);

  drawSoftEllipse(width * 0.68, width * 0.36, height * 0.19, [
    [0, `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, 0.44)`],
    [0.56, `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, 0.14)`],
    [1, `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, 0)`],
  ]);

  return canvas;
}

const LIGHT_BOKEH_PALETTE: readonly RgbColor[] = [
  [124, 58, 237], // 靛紫
  [216, 55, 126], // 玫粉
  [99, 102, 241], // 极光蓝紫
];

export function getCosmicSpriteAtlas(): CosmicSpriteAtlas {
  if (cachedAtlas) return cachedAtlas;
  cachedAtlas = {
    orbs: COSMIC_SPECTRA.map((spec) => buildOrbSprite(spec, 32)),
    flares: COSMIC_SPECTRA.map((spec) => buildFlareSprite(spec, 64)),
    streaks: COSMIC_SPECTRA.map((spec) => buildStreakSprite(spec, 128, 32)),
    bokehStreaks: COSMIC_SPECTRA.map((spec) => buildBokehStreakSprite(spec, 160, 48)),
    lightBokehStreaks: LIGHT_BOKEH_PALETTE.map((rgb) => buildLightBokehSprite(rgb, 160, 48)),
  };
  return cachedAtlas;
}

export function createDeepSpaceStars(count = 170, seed = 20261127): DeepSpaceStar[] {
  const rand = createRng(seed);
  const stars: DeepSpaceStar[] = [];

  for (let i = 0; i < count; i++) {
    const tier = rand();
    let drawRadius: number;
    let luminance: number;
    let hasFlare = false;

    if (tier < 0.76) {
      // 极微远景暗尘
      drawRadius = 1.6 + Math.pow(rand(), 1.8) * 1.9;
      luminance = 0.16 + rand() * 0.34;
    } else if (tier < 0.955) {
      // 清晰中景远星
      drawRadius = 3.2 + rand() * 2.4;
      luminance = 0.48 + rand() * 0.36;
    } else {
      // 稀疏深空孤亮巨星（呼应宇宙远景里的亮点）
      drawRadius = 6.2 + rand() * 4.4;
      luminance = 0.82 + rand() * 0.18;
      hasFlare = rand() > 0.38;
    }

    stars.push({
      nx: (rand() - 0.5) * 2.08,
      ny: (rand() - 0.5) * 2.08,
      depth: 0.18 + rand() * 0.77,
      spectral: pickSpectral(rand, "balanced"),
      drawRadius,
      luminance,
      twinkleSpeed: 0.7 + rand() * 2.1,
      twinklePhase: rand() * TAU,
      twinkleDepth: 0.14 + rand() * 0.36,
      hasFlare,
    });
  }

  return stars;
}

export function createGalaxyRingStars(count = 1520, seed = 6061997): GalaxyRingStar[] {
  const rand = createRng(seed);
  const stars: GalaxyRingStar[] = [];

  // 3 条优雅环绕主旋臂 + 星团聚簇中心（部分偏冷蓝白、部分偏暖琥珀橙）
  const armCount = 3;
  const armBaseAngles = [0.22, 0.22 + TAU / 3, 0.22 + (2 * TAU) / 3];
  const knots = [
    { arm: 0, t: 0.18, bias: "cool" as const },
    { arm: 0, t: 0.52, bias: "warm" as const },
    { arm: 0, t: 0.78, bias: "cool" as const },
    { arm: 1, t: 0.24, bias: "cool" as const },
    { arm: 1, t: 0.58, bias: "warm" as const },
    { arm: 1, t: 0.84, bias: "balanced" as const },
    { arm: 2, t: 0.2, bias: "warm" as const },
    { arm: 2, t: 0.48, bias: "cool" as const },
    { arm: 2, t: 0.76, bias: "warm" as const },
  ];

  const armStarCount = Math.round(count * 0.76);

  for (let i = 0; i < count; i++) {
    let angle: number;
    let radius: number;
    let armWeight: number;
    let knotBoost = 0;
    let bias: "cool" | "warm" | "balanced" = "balanced";

    if (i < armStarCount) {
      // 旋臂星带：从内环 0.82 舒展旋出至 1.44，自带自然疏密与团簇
      const arm = i % armCount;
      const inKnot = rand() < 0.34;
      let t: number;

      if (inKnot) {
        const armKnots = knots.filter((k) => k.arm === arm);
        const knot = armKnots[Math.floor(rand() * armKnots.length)];
        t = clamp(knot.t + gaussianRand(rand) * 0.085, 0.02, 0.98);
        bias = knot.bias;
        knotBoost = 0.45 + rand() * 0.55;
      } else {
        t = Math.pow(rand(), 0.92);
        bias = rand() < 0.56 ? "cool" : rand() < 0.86 ? "warm" : "balanced";
      }

      const sweep = 2.45;
      const spineAngle = armBaseAngles[arm] + t * sweep;
      const spineRadius = 0.8 + t * 0.56;

      // 旋臂宽度在两端微散，中间紧实如星流
      const radialSpread = (0.042 + t * 0.055) * (inKnot ? 0.65 : 1.0);
      const angularSpread = (0.09 + t * 0.14) * (inKnot ? 0.55 : 1.0);

      const radialJitter = gaussianRand(rand) * radialSpread;
      const angularJitter = gaussianRand(rand) * angularSpread;

      angle = spineAngle + angularJitter;
      radius = clamp(spineRadius + radialJitter, 0.66, 1.56);
      armWeight = Math.exp(-((radialJitter / Math.max(radialSpread, 0.02)) ** 2) * 0.65);
    } else {
      // 环绕主椭圆星尘带（保底环绕完整性，并响应推理档位特效）
      angle = rand() * TAU;
      const bandOffset = gaussianRand(rand) * 0.11;
      radius = clamp(0.99 + bandOffset, 0.72, 1.42);
      armWeight = Math.exp(-((bandOffset / 0.09) ** 2)) * 0.72;
      bias = rand() < 0.5 ? "balanced" : rand() < 0.78 ? "cool" : "warm";
    }

    // 幂律大小与亮度分布：绝大部分为细腻微尘，少量中星，极少数醒目亮巨星
    const tier = rand();
    let drawRadius: number;
    let luminance: number;
    let hasFlare = false;

    if (tier < 0.71) {
      // 71% 微型星尘颗粒
      drawRadius = 1.65 + Math.pow(rand(), 1.6) * 2.15;
      luminance = 0.2 + rand() * 0.38;
    } else if (tier < 0.945) {
      // 23.5% 明亮中星（不同色彩最容易辨识的一层）
      drawRadius = 3.6 + Math.pow(rand(), 1.3) * 3.1;
      luminance = 0.54 + rand() * 0.36;
    } else {
      // 5.5% 璀璨巨星/亮行星核
      drawRadius = 6.8 + Math.pow(rand(), 1.4) * 5.8;
      luminance = 0.84 + rand() * 0.16;
      hasFlare = tier > 0.974;
    }

    // 外缘极远端星点自然偏细偏柔，增强宇宙纵深
    const outerSoft = radius > 1.26 ? 1 - clamp((radius - 1.26) / 0.36) * 0.38 : 1;
    drawRadius *= outerSoft;
    luminance *= outerSoft;

    stars.push({
      angle,
      radius,
      armWeight,
      knotBoost,
      spectral: pickSpectral(rand, bias),
      drawRadius,
      luminance,
      orbitSpeed: (0.011 + 0.014 / Math.max(radius, 0.7)) * (0.82 + rand() * 0.36),
      wobblePhase: rand() * TAU,
      twinkleSpeed: 1.0 + rand() * 2.5,
      twinklePhase: rand() * TAU,
      twinkleDepth: 0.16 + rand() * 0.42,
      hasFlare,
      seed: rand(),
    });
  }

  return stars;
}

export function createWormholeStars(count = 420, seed = 20261128): WormholeStar[] {
  const rand = createRng(seed);
  const stars: WormholeStar[] = [];

  for (let i = 0; i < count; i++) {
    const tier = rand();
    // 黄金角分布叠加随机微扰，保证 360° 无死角且自然无辐条
    const angle = (i * 2.399963229728653 + (rand() - 0.5) * 0.28) % TAU;
    let radial: number;
    let z0: number;
    let drawRadius: number;
    let luminance: number;
    let hasFlare = false;
    let bias: "cool" | "warm" | "balanced" = "balanced";

    if (tier < 0.34) {
      // 远景奇点核心星群：挤在原输入框中心深处，蓄压与加速全程提供强烈远近参照
      radial = 0.012 + Math.pow(rand(), 1.4) * 0.26;
      z0 = 5.4 + rand() * 4.8;
      drawRadius = 1.1 + Math.pow(rand(), 1.6) * 1.45;
      luminance = 0.24 + rand() * 0.42;
      bias = rand() < 0.65 ? "cool" : "balanced";
    } else if (tier < 0.62) {
      // 第三波破壁星流（较深 z0 = 3.2..4.9）：承接主加速中后段，持续撑住绵密推背感
      radial = 0.036 + Math.pow(rand(), 1.05) * 0.38;
      z0 = 3.15 + rand() * 1.85;
      drawRadius = 1.7 + Math.pow(rand(), 1.35) * 2.0;
      luminance = 0.42 + rand() * 0.38;
      bias = rand() < 0.54 ? "cool" : rand() < 0.84 ? "warm" : "balanced";
    } else if (tier < 0.84) {
      // 第二波峰值推背星流（中景 z0 = 1.9..3.2）：在加速度峰值段全屏爆裂拉丝
      radial = 0.065 + Math.pow(rand(), 0.9) * 0.46;
      z0 = 1.85 + rand() * 1.35;
      drawRadius = 2.1 + Math.pow(rand(), 1.25) * 2.4;
      luminance = 0.56 + rand() * 0.36;
      hasFlare = rand() > 0.82;
      bias = rand() < 0.5 ? "cool" : rand() < 0.82 ? "warm" : "balanced";
    } else {
      // 第一波先导近景亮星（z0 = 1.0..1.9）：破开惯性后率先拉长并从边缘高速甩出
      radial = 0.11 + Math.pow(rand(), 0.8) * 0.52;
      z0 = 1.02 + rand() * 0.88;
      drawRadius = 3.1 + Math.pow(rand(), 1.2) * 3.1;
      luminance = 0.74 + rand() * 0.26;
      hasFlare = rand() > 0.56;
      bias = rand() < 0.46 ? "cool" : rand() < 0.8 ? "warm" : "balanced";
    }

    stars.push({
      angle,
      radial,
      z0,
      spectral: pickSpectral(rand, bias),
      drawRadius,
      luminance,
      hasFlare,
      seed: rand(),
    });
  }

  return stars;
}

export function createForegroundBokehStreaks(
  count = 42,
  seed = 7772026,
): ForegroundBokehStreak[] {
  const rand = createRng(seed);
  const streaks: ForegroundBokehStreak[] = [];

  for (let i = 0; i < count; i++) {
    // 沿 360° 均匀分布于视口中周边，随 3 波深度分批贴近镜头四角呼啸掠过，建立厚重体量感
    const angle = (i * 2.399963229728653 + (rand() - 0.5) * 0.36) % TAU;
    const wave = i % 3;
    const z0 =
      wave === 0
        ? 1.12 + rand() * 0.72
        : wave === 1
          ? 1.92 + rand() * 0.92
          : 2.88 + rand() * 1.05;
    const radial = 0.34 + rand() * 0.42;
    const bias = rand() < 0.52 ? "cool" : rand() < 0.86 ? "warm" : "balanced";

    streaks.push({
      angle,
      radial,
      z0,
      spectral: pickSpectral(rand, bias),
      thickness: 14 + rand() * 20,
      luminance: 0.22 + rand() * 0.24,
      seed: rand(),
    });
  }

  return streaks;
}

export function stampBokehStreak(
  ctx: CanvasRenderingContext2D,
  atlas: CosmicSpriteAtlas,
  spectral: number,
  tailX: number,
  tailY: number,
  headX: number,
  headY: number,
  thickness: number,
  alpha: number,
  light = false,
): void {
  if (alpha <= 0.008) return;
  const dx = headX - tailX;
  const dy = headY - tailY;
  const len = Math.hypot(dx, dy);
  if (len < 16) return;

  const sprites = light ? atlas.lightBokehStreaks : atlas.bokehStreaks;
  const idx = spectral % sprites.length;
  const sprite = sprites[idx];
  const drawLen = len * 1.18;
  const halfH = Math.min(thickness, len * 0.2);

  ctx.save();
  ctx.translate(tailX, tailY);
  ctx.rotate(Math.atan2(dy, dx));
  ctx.globalAlpha = clamp(alpha, 0, 1);
  ctx.drawImage(sprite, 0, -halfH, drawLen, halfH * 2);
  ctx.restore();
}

export function stampCosmicStar(
  ctx: CanvasRenderingContext2D,
  atlas: CosmicSpriteAtlas,
  spectral: number,
  x: number,
  y: number,
  radius: number,
  alpha: number,
  hasFlare = false,
): void {
  if (alpha <= 0.01 || radius <= 0.3) return;
  const idx = spectral % atlas.orbs.length;
  const sprite = hasFlare ? atlas.flares[idx] : atlas.orbs[idx];
  const r = hasFlare ? radius * 1.48 : radius;
  ctx.globalAlpha = clamp(alpha, 0, 1);
  ctx.drawImage(sprite, x - r, y - r, r * 2, r * 2);
}

export function stampCosmicStreak(
  ctx: CanvasRenderingContext2D,
  atlas: CosmicSpriteAtlas,
  spectral: number,
  tailX: number,
  tailY: number,
  headX: number,
  headY: number,
  headRadius: number,
  alpha: number,
  hasFlare = false,
): void {
  if (alpha <= 0.01 || headRadius <= 0.25) return;
  const dx = headX - tailX;
  const dy = headY - tailY;
  const len = Math.hypot(dx, dy);
  if (len < 4.5) {
    stampCosmicStar(ctx, atlas, spectral, headX, headY, headRadius, alpha, hasFlare);
    return;
  }

  const idx = spectral % atlas.streaks.length;
  const sprite = atlas.streaks[idx];
  // 贴图星核点光位于 x = 0.88，同时约束半高上限与长宽比，保证拖尾始终纤细修长
  const drawLen = len / 0.88;
  const sleekHalfH = clamp(headRadius * 1.18, 2.2, 10.5);
  const halfH = Math.min(sleekHalfH, Math.max(2.2, len * 0.22));

  ctx.save();
  ctx.translate(tailX, tailY);
  ctx.rotate(Math.atan2(dy, dx));
  ctx.globalAlpha = clamp(alpha, 0, 1);
  ctx.drawImage(sprite, 0, -halfH, drawLen, halfH * 2);
  ctx.restore();

  if (hasFlare && alpha > 0.34 && len > 18) {
    stampCosmicStar(
      ctx,
      atlas,
      spectral,
      headX,
      headY,
      Math.min(headRadius * 0.78, 6.5),
      alpha * 0.46,
      true,
    );
  }
}

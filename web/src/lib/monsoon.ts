// The kaskazi: the north-east monsoon, drawn as a field of fine streaks blowing from the upper
// right towards the coast at the lower left, in depth, and bending around the pointer.
//
// Plain WebGL 1, one draw call for the streaks and one for their heads, no library. Each particle
// is stateless: its position is a function of its seed and the time, worked out in the vertex
// shader, so the CPU only updates a few uniforms a frame. This module is loaded on its own, once
// the page has loaded, and only where the device can afford it (see useMonsoon). It blows behind
// the landing page's headline and, more sparsely, behind the headers of the other pages.

const VERTEX = `
attribute vec4 a_seed;   // x: phase along the flow, y: across, z: speed, w: depth (0 far, 1 near)
attribute float a_end;   // where on its trail a vertex sits: 0 the tail, 1 the head
uniform float u_time;
uniform float u_aspect;
uniform vec2 u_pointer;  // clip space
uniform float u_push;    // 0..1, eased in while the pointer moves over the stage
uniform vec2 u_tilt;     // -1..1, the pointer's position, for parallax
uniform vec4 u_card;     // a card the wind parts around, in clip space: centre, half size (zero if none)
uniform float u_dpr;
uniform mediump float u_points; // shared with the fragment shader, so the same precision
uniform vec2 u_resolution;
varying float v_alpha;
varying float v_tone;
varying vec2 v_centre;   // a head's centre and size in pixels: the fragment shader draws the soft
varying float v_size;    // disc from gl_FragCoord (gl_PointCoord isn't reliable on every GPU)

void main() {
  float speed = 0.02 + a_seed.z * 0.035;
  float trail = 0.025 + a_seed.z * 0.035;
  float s = fract(a_seed.x + u_time * speed) - (1.0 - a_end) * trail;

  vec2 dir = normalize(vec2(1.0, 0.38));
  vec2 perp = vec2(-dir.y, dir.x);
  float span = u_aspect * 1.45 + 0.3;
  float along = mix(span, -span, s);
  float across = (a_seed.y * 2.0 - 1.0) * 1.7;
  // Gusts: the flow curves with where a streak is and how far it has come, so each trail bends.
  float meander = 0.34 * sin(along * 0.7 + across * 0.9 + a_seed.w * 6.283 + u_time * 0.15)
                + 0.06 * sin(along * 2.6 - u_time * 0.35 + a_seed.x * 11.0);
  vec2 p = dir * along + perp * (across + meander);

  float z = a_seed.w;
  float depth = 2.3 - z * 1.3;
  vec2 q = (p + u_tilt * (z - 0.4) * 0.25) / depth * 1.6;
  q.x /= u_aspect;

  // The wind parts around the story card, as air around a body: streaks that would cross it are
  // carried round its edges, so the flow hugs the card.
  if (u_card.z > 0.0) {
    vec2 local = (q - u_card.xy) * vec2(u_aspect, 1.0);
    vec2 size = u_card.zw * vec2(u_aspect, 1.0) * 1.12;
    vec2 e = local / size;
    float r = length(e);
    float wrap = 1.0 - smoothstep(0.2, 1.7, r);
    q += (normalize(e + vec2(1e-4)) * size * wrap * 0.55) / vec2(u_aspect, 1.0);
  }

  vec2 d = q - u_pointer;
  d.x *= u_aspect;
  float near = exp(-dot(d, d) / 0.03);
  vec2 away = normalize(d + vec2(1e-4)) * u_push * near * (0.05 + 0.05 * z);
  away.x /= u_aspect;
  q += away;

  gl_Position = vec4(q, 0.0, 1.0);
  // (smoothstep needs its edges in order: GLSL leaves the reverse undefined, and GPUs differ.)
  float edges = smoothstep(0.0, 0.14, s) * (1.0 - smoothstep(0.82, 1.0, s));
  float glow = 1.0 + near * u_push * 0.8;
  // Trails are faint; heads are soft points, larger and fainter the nearer they are (depth of field).
  v_alpha = u_points > 0.5
    ? edges * (0.45 + 0.55 * z) * (1.2 - 0.5 * z) * glow
    : pow(a_end, 2.0) * edges * (0.1 + 0.35 * z) * glow;
  v_tone = fract(a_seed.x * 13.7 + a_seed.y * 3.1);
  v_size = u_points * step(0.999, a_end) * (2.8 + z * z * 8.0) * u_dpr;
  gl_PointSize = v_size;
  v_centre = (q * 0.5 + 0.5) * u_resolution;
}
`;

const FRAGMENT = `
precision mediump float;
uniform vec3 u_ink;
uniform vec3 u_accent;
uniform vec3 u_saffron;
uniform float u_gain;
uniform float u_points;
varying float v_alpha;
varying float v_tone;
varying vec2 v_centre;
varying float v_size;

void main() {
  vec3 colour = v_tone < 0.2 ? u_ink : (v_tone < 0.6 ? u_accent : u_saffron);
  float a = v_alpha * u_gain;
  if (u_points > 0.5) {
    float r = distance(gl_FragCoord.xy, v_centre) / max(0.5 * v_size, 0.5);
    a *= 1.0 - smoothstep(0.35, 1.0, r);
  }
  gl_FragColor = vec4(colour * a, a);
}
`;

const SEGMENTS = 6;

export interface Monsoon {
  stop(): void;
}

type Rgb = [number, number, number];

function rgb(value: string, fallback: Rgb): Rgb {
  const digits = value.trim().match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i)?.[1];
  if (!digits) return fallback;
  const h = digits.length === 3 ? [...digits].map((c) => c + c).join("") : digits;
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255) as Rgb;
}

function compile(gl: WebGLRenderingContext, type: number, source: string): WebGLShader | null {
  const shader = gl.createShader(type);
  if (!shader) return null;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (gl.getShaderParameter(shader, gl.COMPILE_STATUS)) return shader;
  if (import.meta.env.DEV) console.warn(gl.getShaderInfoLog(shader));
  return null;
}

/** Starts the wind on `canvas`, or returns null where WebGL isn't available. */
export function startMonsoon(
  canvas: HTMLCanvasElement,
  particles: number,
  obstacle: (() => DOMRect | null) | null,
  onFirstFrame: () => void,
  onTooSlow: () => void,
): Monsoon | null {
  // A context that would be slow (software rendering, a blocklisted GPU) is refused: the still
  // frame is better than a wind that stutters and holds up the page.
  const gl = canvas.getContext("webgl", {
    alpha: true,
    premultipliedAlpha: true,
    antialias: true,
    powerPreference: "low-power",
    failIfMajorPerformanceCaveat: true,
  });
  if (!gl) return null;
  // Software renderers (SwiftShader, llvmpipe) draw on the CPU, and the browser doesn't always flag
  // them as slow: they get the still frame.
  const info = gl.getExtension("WEBGL_debug_renderer_info");
  const renderer = String(gl.getParameter(info ? info.UNMASKED_RENDERER_WEBGL : gl.RENDERER));
  if (/swiftshader|llvmpipe|softpipe|software/i.test(renderer)) {
    gl.getExtension("WEBGL_lose_context")?.loseContext();
    return null;
  }
  const vs = compile(gl, gl.VERTEX_SHADER, VERTEX);
  const fs = compile(gl, gl.FRAGMENT_SHADER, FRAGMENT);
  const program = gl.createProgram();
  if (!vs || !fs || !program) return null;
  gl.attachShader(program, vs);
  gl.attachShader(program, fs);
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    if (import.meta.env.DEV) console.warn(gl.getProgramInfoLog(program));
    return null;
  }
  gl.useProgram(program);

  // Each particle is a trail of SEGMENTS short lines, every vertex its seed and where on the trail
  // it sits, so the shader can bend the trail along the flow and fade it towards the tail.
  let state = 0x9e3779b9;
  const random = () => {
    state = (state + 0x6d2b79f5) | 0;
    let t = Math.imul(state ^ (state >>> 15), 1 | state);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const data = new Float32Array(particles * SEGMENTS * 2 * 5);
  for (let i = 0; i < particles; i++) {
    const seed = [random(), random(), random(), Math.pow(random(), 1.6)];
    for (let k = 0; k < SEGMENTS; k++) {
      data.set([...seed, k / SEGMENTS, ...seed, (k + 1) / SEGMENTS], (i * SEGMENTS + k) * 10);
    }
  }
  const vertices = particles * SEGMENTS * 2;
  const buffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
  const seedAt = gl.getAttribLocation(program, "a_seed");
  const endAt = gl.getAttribLocation(program, "a_end");
  gl.enableVertexAttribArray(seedAt);
  gl.vertexAttribPointer(seedAt, 4, gl.FLOAT, false, 20, 0);
  gl.enableVertexAttribArray(endAt);
  gl.vertexAttribPointer(endAt, 1, gl.FLOAT, false, 20, 16);

  const u = (name: string) => gl.getUniformLocation(program, name);
  const at = {
    time: u("u_time"),
    aspect: u("u_aspect"),
    pointer: u("u_pointer"),
    push: u("u_push"),
    tilt: u("u_tilt"),
    card: u("u_card"),
    resolution: u("u_resolution"),
    dpr: u("u_dpr"),
    points: u("u_points"),
    gain: u("u_gain"),
  };

  // The colours of wherever the canvas sits (the staff console is dark whatever the visitor's
  // setting), and a little more strength on a dark ground.
  const style = getComputedStyle(canvas);
  const paper = rgb(style.getPropertyValue("--paper"), [0.95, 0.93, 0.89]);
  const dark = 0.2126 * paper[0] + 0.7152 * paper[1] + 0.0722 * paper[2] < 0.4;
  gl.uniform3fv(u("u_ink"), rgb(style.getPropertyValue("--ink"), [0.1, 0.14, 0.25]));
  gl.uniform3fv(u("u_accent"), rgb(style.getPropertyValue("--accent"), [0.68, 0.29, 0.14]));
  gl.uniform3fv(u("u_saffron"), rgb(style.getPropertyValue("--saffron"), [0.88, 0.67, 0.27]));
  gl.uniform1f(at.gain, dark ? 0.9 : 0.75);
  gl.enable(gl.BLEND);
  gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);

  const dpr = Math.min(window.devicePixelRatio || 1, 1.75);
  let width = 1;
  let height = 1;
  const resize = () => {
    const box = canvas.getBoundingClientRect();
    width = Math.max(1, Math.round(box.width * dpr));
    height = Math.max(1, Math.round(box.height * dpr));
    canvas.width = width;
    canvas.height = height;
    gl.viewport(0, 0, width, height);
    gl.uniform2f(at.resolution, width, height);
    const card = obstacle?.() ?? null;
    if (card && box.width > 0 && box.height > 0) {
      const cx = ((card.left + card.width / 2 - box.left) / box.width) * 2 - 1;
      const cy = -(((card.top + card.height / 2 - box.top) / box.height) * 2 - 1);
      gl.uniform4f(at.card, cx, cy, card.width / box.width, card.height / box.height);
    } else gl.uniform4f(at.card, 0, 0, 0, 0);
  };
  resize();
  const resized = new ResizeObserver(resize);
  resized.observe(canvas);

  // The pointer, eased so the wind parts and closes smoothly.
  const target = { x: 0, y: 0, push: 0 };
  const now = { x: 0, y: 0, push: 0 };
  // The wind sits under the page's text, so the pointer is read from the element that holds both.
  const host: HTMLElement = canvas.parentElement?.closest("section") ?? canvas.parentElement ?? canvas;
  const onMove = (e: PointerEvent) => {
    const box = canvas.getBoundingClientRect();
    target.x = ((e.clientX - box.left) / box.width) * 2 - 1;
    target.y = -(((e.clientY - box.top) / box.height) * 2 - 1);
    target.push = 1;
  };
  const onLeave = () => {
    target.push = 0;
  };
  host.addEventListener("pointermove", onMove, { passive: true });
  host.addEventListener("pointerleave", onLeave, { passive: true });

  let frame = 0;
  let running = false;
  let first = true;
  let elapsed = 0;
  let last = 0;
  // The first frames are timed: a device that can't keep the wind above about 25 frames a second
  // gets the still frame instead.
  const timings: number[] = [];
  let stopped = false;
  const draw = (t: number) => {
    if (stopped) return;
    frame = requestAnimationFrame(draw);
    if (last && timings.length < 20) {
      timings.push(t - last);
      if (timings.length === 20) {
        const sorted = [...timings].sort((a, b) => a - b);
        if ((sorted[10] ?? 0) > 40) {
          stopped = true;
          cancelAnimationFrame(frame);
          onTooSlow();
          return;
        }
      }
    }
    elapsed += Math.min(0.05, last ? (t - last) / 1000 : 0);
    last = t;
    const k = 0.06;
    now.x += (target.x - now.x) * k;
    now.y += (target.y - now.y) * k;
    now.push += (target.push - now.push) * 0.04;
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.uniform1f(at.time, elapsed);
    gl.uniform1f(at.aspect, width / height);
    gl.uniform2f(at.pointer, now.x, now.y);
    gl.uniform1f(at.push, now.push);
    gl.uniform2f(at.tilt, now.x, now.y);
    gl.uniform1f(at.dpr, dpr);
    gl.uniform1f(at.points, 0);
    gl.drawArrays(gl.LINES, 0, vertices);
    gl.uniform1f(at.points, 1);
    gl.drawArrays(gl.POINTS, 0, vertices);
    if (first) {
      first = false;
      onFirstFrame();
    }
  };
  const start = () => {
    if (running || stopped) return;
    running = true;
    last = 0;
    frame = requestAnimationFrame(draw);
  };
  const pause = () => {
    running = false;
    cancelAnimationFrame(frame);
  };

  // Drawn only while it can be seen: not in a hidden tab, not scrolled away.
  let onScreen = true;
  const seen = new IntersectionObserver((entries) => {
    onScreen = entries[0]?.isIntersecting ?? true;
    if (onScreen && !document.hidden) start();
    else pause();
  });
  seen.observe(canvas);
  const onVisibility = () => (document.hidden || !onScreen ? pause() : start());
  document.addEventListener("visibilitychange", onVisibility);
  const onLost = (e: Event) => {
    e.preventDefault();
    pause();
  };
  canvas.addEventListener("webglcontextlost", onLost);
  start();

  return {
    stop() {
      pause();
      seen.disconnect();
      resized.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
      host.removeEventListener("pointermove", onMove);
      host.removeEventListener("pointerleave", onLeave);
      canvas.removeEventListener("webglcontextlost", onLost);
      gl.getExtension("WEBGL_lose_context")?.loseContext();
    },
  };
}

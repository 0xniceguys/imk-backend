#include <flutter/runtime_effect.glsl>

uniform vec2 uSize;
uniform float uTime;
uniform float uNoiseScale;
uniform float uSpread;
uniform float uRoughness;
uniform float uFlowStrength;
uniform float uOpacity;
uniform float uBrightness;
uniform float uSoftness;
uniform sampler2D uTexture;

out vec4 fragColor;

float rand(vec2 n) {
  return fract(sin(dot(n, vec2(12.9898, 78.233))) * 43758.5453123);
}

float noise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  f = f * f * (3.0 - 2.0 * f);

  float a = rand(i);
  float b = rand(i + vec2(1.0, 0.0));
  float c = rand(i + vec2(0.0, 1.0));
  float d = rand(i + vec2(1.0, 1.0));

  return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
}

float fbm(vec2 p) {
  float total = 0.0;
  float amplitude = 0.5;
  for (int i = 0; i < 3; i++) {
    total += noise(p) * amplitude;
    p = p * 2.03 + vec2(17.13, 9.37);
    amplitude *= 0.54;
  }
  return total;
}

float alphaAt(vec2 uv) {
  if (uv.x <= 0.0 || uv.y <= 0.0 || uv.x >= 1.0 || uv.y >= 1.0) {
    return 0.0;
  }
  return texture(uTexture, uv).a;
}

float ringPresence(vec2 uv, vec2 px, float radiusPx) {
  float presence = 0.0;

  for (int i = 0; i < 6; i++) {
    float fi = float(i);
    float angle = 6.28318530718 * fi / 6.0;
    float ring = mix(0.2, 1.0, fract(fi * 0.37 + 0.19));
    vec2 dir = vec2(cos(angle), sin(angle));
    presence = max(presence, alphaAt(uv + dir * radiusPx * ring * px));
  }

  return presence;
}

float trailPresence(
  vec2 uv,
  vec2 px,
  float radiusPx,
  float time,
  float noiseScale,
  float roughness,
  float flowStrength
) {
  float acc = 0.0;
  float weightSum = 0.0;

  for (int i = 0; i < 5; i++) {
    float fi = float(i);
    float t = (fi + 1.0) / 5.0;
    float distancePx = t * radiusPx * 1.95;
    float driftNoise = fbm(vec2(
      uv.x * (1.15 + noiseScale * 0.05) - time * (0.8 + flowStrength * 1.1),
      uv.y * (0.8 + noiseScale * 0.06) + fi * 2.73
    ));
    float drift = (driftNoise - 0.5) * distancePx * (0.38 + roughness * 0.95);
    vec2 offsetA = vec2(-distancePx, drift);
    vec2 offsetB = vec2(-distancePx * 0.78, -drift * 0.72);
    float weight = (1.0 - t * 0.66) * mix(1.0, 0.65, t);
    float sampleAlpha = max(alphaAt(uv + offsetA * px), alphaAt(uv + offsetB * px));
    acc += sampleAlpha * weight;
    weightSum += weight;
  }

  return weightSum > 0.0 ? acc / weightSum : 0.0;
}

float flameField(
  vec2 p,
  float time,
  float noiseScale,
  float roughness,
  float flowStrength
) {
  float scale = 0.55 + noiseScale * 0.08;
  vec2 flow = p * vec2(scale * 1.85, scale * 0.95);
  flow.x -= time * (0.55 + flowStrength * 1.15);
  flow.y += (fbm(flow * 0.72 + vec2(-time * 0.5, 0.0)) - 0.5) * (0.42 + roughness * 1.35);
  flow.y += sin(flow.x * 1.7 - time * (0.3 + flowStrength * 0.5)) * (0.1 + roughness * 0.22);

  float layerA = fbm(flow + vec2(-time * 0.22, 0.0));
  float layerB = fbm(flow * vec2(2.2, 1.4) + vec2(-time * 0.65, 4.2));
  float layerC = fbm(flow * vec2(3.05, 2.0) + vec2(-time * 1.02, -7.8));

  return clamp(layerA * 0.58 + layerB * 0.32 + layerC * 0.22, 0.0, 1.0);
}

void main() {
  vec2 uv = FlutterFragCoord().xy / uSize;
  vec2 px = 1.0 / uSize;
  float baseAlpha = alphaAt(uv);
  float outside = smoothstep(0.0, 0.98, 1.0 - baseAlpha);

  float spreadT = clamp(uSpread / 8.0, 0.0, 1.0);
  float radiusPx = mix(8.0, 124.0, spreadT);

  float shell = ringPresence(uv, px, radiusPx * 0.42);
  float trail = trailPresence(
    uv,
    px,
    radiusPx,
    uTime,
    uNoiseScale,
    uRoughness,
    uFlowStrength
  );

  float ignition = shell * 0.14;
  float source = max(ignition, trail * 1.08);
  float softnessT = clamp(uSoftness / 2.0, 0.0, 1.0);
  source = pow(clamp(source, 0.0, 1.0), mix(2.4, 0.86, softnessT));

  vec2 centered = (uv - 0.5) * vec2(uSize.x / uSize.y, 1.0);
  float field = flameField(centered, uTime, uNoiseScale, uRoughness, uFlowStrength);

  float plumeFalloff = 1.0 - smoothstep(0.28, 1.0, spreadT) * smoothstep(0.55, 0.98, source);
  float flameMask = smoothstep(0.34, 0.9, field + source * 0.12);
  float intensity = outside * source * flameMask * plumeFalloff;
  intensity *= mix(0.45, 1.0, uOpacity);
  intensity = clamp(intensity, 0.0, 0.78);

  vec3 outerColor = vec3(0.16, 0.10, 0.01);
  vec3 midColor = vec3(0.56, 0.37, 0.02);
  vec3 innerColor = vec3(1.0, 0.772549, 0.0);
  float heat = smoothstep(0.1, 0.96, field + source * 0.14);
  vec3 flameRgb = mix(outerColor, midColor, heat);
  flameRgb = mix(flameRgb, innerColor, smoothstep(0.68, 1.0, heat) * 0.55);
  flameRgb *= (0.38 + uBrightness * 0.42);

  fragColor = vec4(flameRgb * intensity, intensity);
}

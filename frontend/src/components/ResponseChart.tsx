import type { FreqResponse } from "../types";

/** 테스트 단계의 결과에서 시뮬레이션 주파수 응답을 찾는다 (연산증폭기 opamp_ac 회로). */
export function extractResponses(outputs: Record<string, unknown>): FreqResponse[] {
  const response = (outputs as { freq_response?: FreqResponse }).freq_response;
  return response && Array.isArray(response.freq_hz) && response.freq_hz.length > 1 ? [response] : [];
}

const W = 640;
const H = 250;
const PAD = { left: 54, right: 14, top: 14, bottom: 40 };

const hz = (f: number) => {
  if (f >= 1e6) return `${Number((f / 1e6).toPrecision(3))} MHz`;
  if (f >= 1e3) return `${Number((f / 1e3).toPrecision(3))} kHz`;
  return `${Number(f.toPrecision(3))} Hz`;
};
const tickHz = (f: number) => hz(f).replace(" Hz", "").replace(" kHz", "k").replace(" MHz", "M");

/** 1, 2, 5 × 10^n 간격의 보기 좋은 눈금 (범위 안의 값만) */
function niceTicks(min: number, max: number, target = 5): number[] {
  const raw = (max - min) / target;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * pow).find((s) => s >= raw) ?? 10 * pow;
  const ticks: number[] = [];
  for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) ticks.push(Math.round(v * 1e6) / 1e6);
  return ticks;
}

export default function ResponseChart({ response }: { response: FreqResponse }) {
  const { freq_hz: freqs, gain_db: gains, marks } = response;
  const lMin = Math.log10(freqs[0]);
  const lMax = Math.log10(freqs[freqs.length - 1]);

  const refLevel = marks?.dc_gain_db !== undefined ? marks.dc_gain_db - 3.0103 : undefined;
  const values = refLevel !== undefined ? [...gains, refLevel] : [...gains];
  let gMin = Math.min(...values);
  let gMax = Math.max(...values);
  const margin = (gMax - gMin) * 0.08 || 1;
  gMin -= margin;
  gMax += margin;

  const x = (f: number) =>
    PAD.left + ((Math.min(Math.max(Math.log10(f), lMin), lMax) - lMin) / (lMax - lMin || 1)) * (W - PAD.left - PAD.right);
  const y = (g: number) => H - PAD.bottom - ((g - gMin) / (gMax - gMin)) * (H - PAD.top - PAD.bottom);
  const yTicks = niceTicks(gMin, gMax);
  const decades: number[] = [];
  // 응답 범위가 대역폭 ± 3자리를 반올림한 값이라 끝이 눈금(10의 거듭제곱)에서 살짝 벗어날 수 있어 여유를 둔다
  for (let k = Math.ceil(lMin - 0.01); k <= Math.floor(lMax + 0.01); k += 1) decades.push(10 ** k);
  const path = freqs.map((f, i) => `${i === 0 ? "M" : "L"}${x(f).toFixed(1)} ${y(gains[i]).toFixed(1)}`).join(" ");

  const bw = marks?.bw_hz ?? undefined;
  const readF = marks?.freq_hz ?? undefined;
  const readG = marks?.gain_db_at_freq ?? undefined;
  const inRange = (f?: number) => f !== undefined && f >= freqs[0] && f <= freqs[freqs.length - 1];

  return (
    <figure className="wave">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="주파수 응답">
        {yTicks.map((g) => (
          <g key={`y${g}`}>
            <line className="wave-grid" x1={PAD.left} x2={W - PAD.right} y1={y(g)} y2={y(g)} />
            <text className="wave-tick" x={PAD.left - 8} y={y(g) + 4} textAnchor="end">
              {g.toFixed(0)}
            </text>
          </g>
        ))}
        {decades.map((f) => (
          <g key={`x${f}`}>
            <line className="wave-grid" x1={x(f)} x2={x(f)} y1={PAD.top} y2={H - PAD.bottom} />
            <text className="wave-tick" x={x(f)} y={H - PAD.bottom + 16} textAnchor="middle">
              {tickHz(f)}
            </text>
          </g>
        ))}
        <text className="wave-label" x={(PAD.left + W - PAD.right) / 2} y={H - 6} textAnchor="middle">
          주파수 (Hz, 로그 눈금)
        </text>
        <text className="wave-label" x={14} y={H / 2} textAnchor="middle" transform={`rotate(-90 14 ${H / 2})`}>
          이득 (dB)
        </text>
        {refLevel !== undefined && (
          <line className="wave-line wave-ref" x1={PAD.left} x2={W - PAD.right} y1={y(refLevel)} y2={y(refLevel)} />
        )}
        {inRange(bw) && (
          <line className="wave-line wave-ref" x1={x(bw!)} x2={x(bw!)} y1={PAD.top} y2={H - PAD.bottom} />
        )}
        <path className="wave-line wave-result" d={path} fill="none" />
        {inRange(readF) && readG !== undefined && (
          <circle className="wave-dot" cx={x(readF!)} cy={y(readG)} r={5} />
        )}
      </svg>
      <figcaption className="wave-legend">
        <span>
          <i className="wave-swatch wave-result" /> 시뮬레이션 (ngspice)
        </span>
        {bw !== undefined && (
          <span>
            <i className="wave-swatch wave-ref" /> −3 dB 대역폭 {hz(bw)}
          </span>
        )}
        {readF !== undefined && readG !== undefined && (
          <span>
            <i className="wave-swatch wave-after" /> {hz(readF)}에서 {readG.toFixed(1)} dB (
            {(10 ** (readG / 20)).toPrecision(3)}배)
          </span>
        )}
      </figcaption>
    </figure>
  );
}

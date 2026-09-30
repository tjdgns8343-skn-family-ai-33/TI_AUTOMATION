import type { Waveform } from "../types";

export interface NamedWave {
  name: string;
  kind: "result" | "before" | "after";
  wave: Waveform;
}

/** 테스트 단계의 결과에서 시뮬레이션 파형을 찾는다: 단일 실행은 waveform, 수정 전·후 비교는 before·after 안에 있다. */
export function extractWaveforms(outputs: Record<string, unknown>): NamedWave[] {
  const found: NamedWave[] = [];
  const pick = (node: unknown, name: string, kind: NamedWave["kind"]) => {
    const wave = (node as { waveform?: Waveform } | undefined)?.waveform;
    if (wave && Array.isArray(wave.time_us) && wave.time_us.length > 1) found.push({ name, kind, wave });
  };
  pick(outputs, "결과", "result");
  pick(outputs.before, "변경 전", "before");
  pick(outputs.after, "변경 후", "after");
  return found;
}

const W = 640;
const H = 250;
const PAD = { left: 54, right: 14, top: 14, bottom: 40 };

export default function WaveChart({ waves }: { waves: NamedWave[] }) {
  const base = waves[0].wave;
  const tMin = base.time_us[0];
  const tMax = base.time_us[base.time_us.length - 1];
  const refV = base.ref_v;

  const values = waves.flatMap((w) => w.wave.v);
  if (refV !== undefined) values.push(refV);
  let vMin = Math.min(...values);
  let vMax = Math.max(...values);
  const margin = (vMax - vMin) * 0.08 || 0.5;
  vMin -= margin;
  vMax += margin;

  const x = (t: number) => PAD.left + ((t - tMin) / (tMax - tMin || 1)) * (W - PAD.left - PAD.right);
  const y = (v: number) => H - PAD.bottom - ((v - vMin) / (vMax - vMin)) * (H - PAD.top - PAD.bottom);
  const yTicks = Array.from({ length: 5 }, (_, i) => vMin + ((vMax - vMin) * i) / 4);
  const xTicks = Array.from({ length: 6 }, (_, i) => tMin + ((tMax - tMin) * i) / 5);
  const path = (w: Waveform) =>
    w.time_us.map((t, i) => `${i === 0 ? "M" : "L"}${x(t).toFixed(1)} ${y(w.v[i]).toFixed(1)}`).join(" ");

  return (
    <figure className="wave">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="출력 전압 파형">
        {yTicks.map((v) => (
          <g key={`y${v}`}>
            <line className="wave-grid" x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)} />
            <text className="wave-tick" x={PAD.left - 8} y={y(v) + 4} textAnchor="end">
              {v.toFixed(1)}
            </text>
          </g>
        ))}
        {xTicks.map((t) => (
          <text key={`x${t}`} className="wave-tick" x={x(t)} y={H - PAD.bottom + 16} textAnchor="middle">
            {Math.round(t)}
          </text>
        ))}
        <text className="wave-label" x={(PAD.left + W - PAD.right) / 2} y={H - 6} textAnchor="middle">
          시간 (µs)
        </text>
        <text className="wave-label" x={14} y={H / 2} textAnchor="middle" transform={`rotate(-90 14 ${H / 2})`}>
          출력 전압 (V)
        </text>
        {refV !== undefined && (
          <line className="wave-line wave-ref" x1={PAD.left} x2={W - PAD.right} y1={y(refV)} y2={y(refV)} />
        )}
        {waves.map((w) => (
          <path key={w.name} className={`wave-line wave-${w.kind}`} d={path(w.wave)} fill="none" />
        ))}
      </svg>
      <figcaption className="wave-legend">
        {waves.map((w) => (
          <span key={w.name}>
            <i className={`wave-swatch wave-${w.kind}`} /> {w.name}
          </span>
        ))}
        {refV !== undefined && (
          <span>
            <i className="wave-swatch wave-ref" /> 목표 출력 전압 {refV} V
          </span>
        )}
      </figcaption>
    </figure>
  );
}

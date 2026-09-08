import React, { useEffect, useState } from 'react';
import { Trace } from './BrakingReview';

const modes = [
  ['stock', 'Stock model', '#0369a1'],
  ['smooth', 'Brake candidate', '#b45309'],
  ['personal', 'Personal approach', '#7e22ce'],
];
const n = (v, unit = '') => (Number.isFinite(v) ? `${v.toFixed(2)}${unit}` : 'unavailable');

function valueAt(rows, t, key) {
  if (!rows?.length || t < rows[0].t || t > rows.at(-1).t) return null;
  let lo = 0,
    hi = rows.length - 1;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if (rows[mid].t <= t) lo = mid;
    else hi = mid - 1;
  }
  return t - rows[lo].t <= 0.3 ? rows[lo][key] : null;
}

export default function BrakingExperiments({ validation, event, cursor, jump, reviewManual, decisions }) {
  const [traces, setTraces] = useState({});
  const [error, setError] = useState('');
  const [window, setWindow] = useState('approach');
  const result = validation?.recorded_cases?.find((c) => c.event === event?.id);
  useEffect(() => {
    const controller = new AbortController();
    setTraces({});
    setError('');
    if (result) {
      const sources = [
        ...modes.map(([mode]) => [mode, result.traffic?.[mode]?.url]),
        ['commands', result.reproduction?.url],
      ].filter(([, url]) => url);
      Promise.all(
        sources.map(async ([mode, url]) => {
          const response = await fetch(url, { signal: controller.signal });
          if (!response.ok) throw Error('Unable to load braking experiment');
          return [mode, (await response.json()).samples];
        }),
      )
        .then((pairs) => setTraces(Object.fromEntries(pairs)))
        .catch((e) => {
          if (e.name !== 'AbortError') setError(e.message);
        });
    }
    return () => controller.abort();
  }, [result]);
  const reference = validation?.manual_reference;
  if (!reference) return null;
  const rows = (event?.samples || []).filter((r, i) => i % 5 === 0 && (window === 'approach' || r.t >= -5));
  const chart = (title, unit, key, recordedKey = key, multiplier = 1) =>
    rows.length > 1 && (
      <Trace
        title={title}
        unit={unit}
        rows={rows}
        cursor={cursor}
        setCursor={(i) => jump(rows[i].t)}
        series={[
          {
            label: 'Recorded',
            color: '#111827',
            value: (r) => (Number.isFinite(r[recordedKey]) ? r[recordedKey] * multiplier : null),
          },
          ...modes.map(([mode, label, color]) => ({
            label,
            color,
            value: (r) => {
              const v = valueAt(traces[mode], r.t, key);
              return Number.isFinite(v) ? v * multiplier : null;
            },
          })),
        ]}
      />
    );
  return (
    <section aria-label="Braking experiments">
      <h3>A smoother stop must also finish promptly</h3>
      <div className="review-toolbar">
        {(reference.collection?.review_ids || [])
          .filter((id) => !['representative', 'exclude'].includes(decisions?.[id]))
          .map((id, i) => (
            <button key={id} onClick={() => reviewManual(id)}>
              Review manual example {i + 1}
            </button>
          ))}
      </div>
      <p>
        {reference.collection?.qualifying_examples ?? reference.examples.length} qualifying manual examples
        toward a target of {reference.collection?.target_examples ?? 10}. Your observed final phase below 4.5
        mph: {n(Math.min(...reference.examples.map((e) => e.low_speed_seconds)))}–
        {n(Math.max(...reference.examples.map((e) => e.low_speed_seconds)))} s.
        {reference.split?.holdout_route
          ? ' A new route is reserved for independent evaluation.'
          : ' A new recorded trip is still needed for independent evaluation.'}
      </p>
      {reference.approach_candidate && (
        <p>
          Observed manual gap: {n(reference.approach_candidate.observed_gap, ' m')}. The experimental planner
          target is bounded to {n(reference.approach_candidate.gap, ' m')} by the existing profile limits;
          actual simulated gaps are shown separately below. The curve has limited manual support and is not
          enabled on the car.
        </p>
      )}
      {result && (
        <>
          <h3>Reconstructed traffic: the planner runs again</h3>
          <p>
            Each model follows the recorded lead car’s reconstructed motion using its own speed and distance.
            The lead is assumed not to react to the simulated car. These are provisional experiments, not
            recorded outcomes.
          </p>
          <label>
            Replay window{' '}
            <select aria-label="Replay window" value={window} onChange={(e) => setWindow(e.target.value)}>
              <option value="approach">Whole approach</option>
              <option value="finish">Final five seconds</option>
            </select>
          </label>
          {error && <p role="alert">{error}</p>}
          {modes.map(
            ([mode, label]) =>
              result.traffic?.[mode]?.error && (
                <p key={mode}>
                  {label}: {result.traffic[mode].error}
                </p>
              ),
          )}
          {chart('Replayed speed', 'mph', 'v', 'v', 2.236936292)}
          {chart('Replayed acceleration', 'm/s²', 'a')}
          {chart('Replayed lead gap', 'm', 'gap', 'd')}
          <table className="braking-comparison">
            <thead>
              <tr>
                <th>Metric</th>
                <th>Recorded</th>
                {modes.map(([mode, label]) => (
                  <th key={mode}>{label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr>
                <th>Stopped by end of recording</th>
                <td>Yes</td>
                {modes.map(([mode]) => (
                  <td key={mode}>
                    {result.traffic?.[mode]?.stopped === true
                      ? 'Yes'
                      : result.traffic?.[mode]?.stopped === false
                        ? 'No'
                        : 'unavailable'}
                  </td>
                ))}
              </tr>
              {[
                ['Below 4.5 mph', 'low_speed_seconds', ' s'],
                ['Final jerk, 95th percentile', 'final_jerk_p95', ' m/s³'],
                ['Peak acceleration', 'min_accel', ' m/s²'],
                ['Speed rebound', 'speed_rebound', ' m/s'],
                ['Settled gap', 'settled_gap', ' m'],
                ['Braking onset relative to recorded stop', 'braking_onset', ' s'],
              ].map(([label, key, unit]) => (
                <tr key={key}>
                  <th>{label}</th>
                  <td>{n(key === 'settled_gap' ? event.settled_radar_gap : event[key], unit)}</td>
                  {modes.map(([mode]) => (
                    <td key={mode}>{n(result.traffic?.[mode]?.[key], unit)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          <details>
            <summary>Can the response model reproduce the recording?</summary>
            <p>
              This separate experiment applies the recorded gas and brake commands. It does not rerun the
              planner. Acceleration RMSE: {n(result.reproduction?.accel_rmse, ' m/s²')}; speed RMSE:{' '}
              {n(result.reproduction?.speed_rmse, ' m/s')}.
              {result.reproduction?.error && ` ${result.reproduction.error}.`} Peak, jolt, and stop-timing
              checks below must also pass before model improvements are trusted.
            </p>
            {rows.length > 1 && (
              <Trace
                title="Recorded-command pressure response"
                unit="raw CAN units"
                rows={rows}
                cursor={cursor}
                setCursor={(i) => jump(rows[i].t)}
                series={[
                  { label: 'Recorded pressure', color: '#111827', value: (r) => r.pressure },
                  {
                    label: 'Predicted pressure',
                    color: '#0369a1',
                    value: (r) => valueAt(traces.commands, r.t, 'pressure'),
                  },
                ]}
              />
            )}
          </details>
        </>
      )}
    </section>
  );
}

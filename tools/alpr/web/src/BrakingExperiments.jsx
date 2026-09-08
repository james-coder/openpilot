import React, { useEffect, useState } from 'react';
import BrakingDiagnostics from './BrakingDiagnostics';
import { Trace } from './BrakingReview';

const allModes = [
  ['stock', 'Stock model', '#0369a1'],
  ['smooth', 'Brake candidate', '#b45309'],
  ['personal', 'Personal stop', '#7e22ce'],
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
  const modes = validation?.profile_kind === 'brake' ? allModes.slice(0, 2) : allModes;
  const [traces, setTraces] = useState({});
  const [error, setError] = useState('');
  const [window, setWindow] = useState('approach');
  const result = validation?.recorded_cases?.find((c) => c.event === event?.id);
  const previous = validation?.previous_checkpoint?.recorded_cases?.find(c => c.event === event?.id)?.traffic?.personal;
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
  const trainingExamples = reference.examples.filter((e) => e.route !== reference.split?.holdout_route);
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
          ...(['v', 'a'].includes(key) && traces.personal?.some(r => Number.isFinite(r['function_' + key])) ? [
            { label: 'Function target', color: '#15803d', value: r => {
              const value = valueAt(traces.personal, r.t, 'function_' + key);
              return Number.isFinite(value) ? value * multiplier : null;
            } },
            ...(key === 'a' ? [{ label: 'Planner target', color: '#be123c', value: r => valueAt(traces.personal, r.t, 'target') }] : []),
          ] : []),
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
      {validation.readiness && (
        <p role="status">
          Supervised tests: <strong>{validation.readiness.test_ready ? 'Ready' : 'Not ready'}</strong>.
          {' '}Vehicle checks: <strong>{validation.readiness.vehicle_validated ? 'Passed' : 'Pending'}</strong>.
          {' '}Normal driving: <strong>{validation.readiness.road_ready ? 'Validated' : 'Not validated'}</strong>.
        </p>
      )}
      {validation.collision_experiments && (
        <details aria-label="Collision protection research" open>
          <summary>Collision protection: not available in this candidate</summary>
          <p>The ASCM is unpowered. The fork’s stock tuning does not restore factory emergency braking.
            A forward-collision warning and a smooth stop do not establish collision protection.</p>
          <p>{validation.collision_experiments.note}</p>
          <p>These examples evaluate braking before a comfort curve ends. A negative minimum gap means
            the assumed maximum braking is already insufficient. “Mitigation” still requests the assumed
            braking limit; it does not claim avoidance. None of these requests reaches the car.</p>
          <div style={{ overflowX: 'auto' }}>
            <table className="braking-comparison">
              <thead><tr><th>Hypothetical situation</th><th>Initial gap</th><th>Speed</th>
                <th>Response delay + age</th><th>Assumed net braking limit</th><th>Decision</th>
                <th>Required braking</th><th>Minimum gap at limit</th></tr></thead>
              <tbody>{validation.collision_experiments.cases.map(c => <tr key={c.name}>
                <td>{c.name}</td><td>{n(c.gap, ' m')}</td><td>{n(c.ego_speed, ' m/s')}</td>
                <td>{n(c.assumptions.response_seconds + c.age, ' s')}</td>
                <td>{n(c.assumptions.ego_brake, ' m/s²')}</td><td>{c.result.state.replaceAll('_', ' ')}</td>
                <td>{n(c.result.required_brake, ' m/s²')}</td><td>{n(c.result.minimum_gap_at_limit, ' m')}</td>
              </tr>)}</tbody>
            </table>
          </div>
          <p>Research references: <a href="https://www.chevrolet.com/ownercenter/content/dam/gmownercenter/gmna/dynamic/manuals/2017/Chevrolet/Volt/2k17volt1stPrint.pdf#page=211">2017 Volt manual, page 210</a>,
            {' '}<a href="https://xr793.com/wp-content/uploads/2017/07/2017-Chevrolet-Volt.pdf#page=20">Chevrolet equipment brochure</a>,
            {' '}<a href="https://techlink.mynetworkcontent.com/wp-content/uploads/2017/11/GM_TechLink_19_October_2017.pdf#page=4">GM guidance distinguishing ACC braking</a>,
            {' '}<a href="https://patents.google.com/patent/US20130110368A1/en">GM collision-braking patent</a>,
            {' '}<a href="https://autowarefoundation.github.io/autoware_universe/main/control/autoware_autonomous_emergency_braking/">Autoware AEB</a>.</p>
        </details>
      )}
      {validation.qualification_groups && (
        <div aria-label="Braking qualification by cause">
          <h3>What still needs to pass</h3>
          {validation.qualification_groups.map(group => (
            <details key={group.name} open={group.status === 'blocked'}>
              <summary>{group.name}: {group.status}</summary>
              {group.failed_checks.length > 0 && <ul>{group.failed_checks.map(name => <li key={name}>{name}</li>)}</ul>}
              {group.status === 'pending' && <p>Current-source device measurements are required.</p>}
            </details>
          ))}
          <p>Motion metrics now include signed speed and static friction. Holding capacity is reported separately from acceleration.</p>
        </div>
      )}
      <p>{validation.profile_kind === 'brake'
        ? 'Current candidate: brake control with the existing planner and following distance. Personal approach learning is deferred.'
        : 'Current candidate: a fitted polynomial stopping function and brake control.'}</p>
      <BrakingDiagnostics diagnostics={validation.diagnostics} event={event} reproduction={result?.reproduction} />
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
        toward a target of {reference.collection?.target_examples ?? 10}. The {trainingExamples.length} fitting examples
        finish the phase below 4.5 mph in {n(Math.min(...trainingExamples.map((e) => e.low_speed_seconds)))}–
        {n(Math.max(...trainingExamples.map((e) => e.low_speed_seconds)))} s.
        {reference.split?.holdout_route
          ? ' The reserved route is excluded from fitting but was previously inspected.'
          : ' A new recorded trip is still needed for independent evaluation.'}
      </p>
      {validation.profile_kind !== 'brake' && reference.approach_candidate && (
        <p>
          The experimental planner target is {n(reference.approach_candidate.gap, ' m')};
          actual simulated gaps are shown separately below. The controller aims to meet timing, smoothness,
          and gap targets together. This page reports qualification, not the mode currently selected on the car.
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
          {validation.metric_version >= 2 && <>
            {chart('Actual simulated motion', 'm/s²', 'physical_accel', 'vehicle_ax')}
            {chart('Stationary holding margin', 'm/s²', 'hold_margin', 'unavailable')}
          </>}
          <p>
            A completed stop requires a full second at rest. Unfinished replays have no final stopping time
            or settled-gap measurement; they are not extended beyond the recorded lead observations.
          </p>
          {previous && <details>
            <summary>Previous candidate for this stop</summary>
            <p>{validation.previous_checkpoint.note}</p>
            <p>Completed: {previous.stopped ? 'yes' : 'no'}; low-speed phase: {n(previous.low_speed_seconds, ' s')};
              {' '}settled gap: {n(previous.settled_gap, ' m')}; final jerk p95: {n(previous.final_jerk_p95, ' m/s³')}.</p>
          </details>}
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
                ['Simulated rollback distance', 'rollback_distance', ' m'],
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

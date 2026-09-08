import React, { useEffect, useRef, useState } from 'react';
import LegacyReview, { Trace } from './BrakingReview';
import BrakingExperiments from './BrakingExperiments';
import BrakingFunction from './BrakingFunction';
import { DisplayControls, useDisplaySettings } from './DisplayControls';

const MPH = 2.236936292;
const n = (v, unit = '') => (Number.isFinite(v) ? `${v.toFixed(2)}${unit}` : 'unavailable');
const localTime = (e) =>
  e.stop_utc ? new Date(e.stop_utc).toLocaleTimeString([], { timeZone: 'America/Denver' }) : e.id;
const get = async (url, signal) => {
  const r = await fetch(url, { signal });
  if (!r.ok) throw Error(`${r.status}: Analysis unavailable`);
  return r.json();
};

export default function NativeBrakingReview() {
  const [index, setIndex] = useState(null),
    [error, setError] = useState('');
  const [id, setId] = useState(''),
    [event, setEvent] = useState(null),
    [scope, setScope] = useState('autonomous');
  const [phase, setPhase] = useState('finish');
  const [cursor, setCursor] = useState(-3),
    [seek, setSeek] = useState({ t: -3 });
  const [comparison, setComparison] = useState(null),
    [compareId, setCompareId] = useState('');
  const [decisions, setDecisions] = useState(null),
    [saveError, setSaveError] = useState(''),
    [saving, setSaving] = useState(false);
  const [validation, setValidation] = useState(null);
  const [display, setDisplay] = useDisplaySettings();
  useEffect(() => {
    const controller = new AbortController();
    get('/api/braking', controller.signal)
      .then(setIndex)
      .catch((e) => {
        if (e.name !== 'AbortError') setError(e.message);
      });
    get('/api/braking/decisions', controller.signal)
      .then(setDecisions)
      .catch(() => {});
    get('/api/braking/validation', controller.signal)
      .then(setValidation)
      .catch(() => {});
    return () => controller.abort();
  }, []);
  const choices =
    index?.events.filter(
      (e) => scope === 'all' || (scope === 'manual' ? e.recommended_manual : e.kind === scope),
    ) || [];
  const selected = choices.find((e) => e.id === id) || choices[0];
  useEffect(() => {
    setEvent(null);
    if (!selected) return;
    const controller = new AbortController();
    get(selected.detail_url, controller.signal)
      .then((e) => {
        setEvent(e);
        setCursor(-3);
        setSeek({ t: -3 });
      })
      .catch((e) => {
        if (e.name !== 'AbortError') setError(e.message);
      });
    return () => controller.abort();
  }, [selected?.id]);
  const manual = index?.events.filter((e) => e.recommended_manual) || [];
  const reference =
    manual.find((e) => e.id === compareId) ||
    [...manual].sort(
      (a, b) =>
        Math.abs(a.max_speed_mph - (selected?.max_speed_mph || 0)) -
        Math.abs(b.max_speed_mph - (selected?.max_speed_mph || 0)),
    )[0];
  useEffect(() => {
    setComparison(null);
    if (!reference) return;
    const controller = new AbortController();
    get(reference.detail_url, controller.signal)
      .then(setComparison)
      .catch(() => {});
    return () => controller.abort();
  }, [reference?.id]);
  if (error.startsWith('404')) return <LegacyReview />;
  if (!index) return <div className="content">{error || 'Loading full-rate braking review…'}</div>;
  const jump = (t) => {
    setCursor(t);
    setSeek({ t });
  };
  const rows = event?.samples || [];
  const sample = rows.reduce((a, r) => (!a || Math.abs(r.t - cursor) < Math.abs(a.t - cursor) ? r : a), null);
  const plot = rows
    .filter((r, i) => i % 5 === 0 && (phase === 'approach' || r.t >= -5))
    .map((r, i, all) => ({
      ...r,
      brake_pressed: r.foot,
      stable_lead: i === 0 || r.track === all[i - 1].track,
    }));
  const line = (label, color, key, active = false) => ({
    label,
    color,
    value: (r) => (active && !r.active ? null : r[key]),
  });
  const trace = (title, unit, series) => (
    <Trace
      title={title}
      unit={unit}
      rows={plot}
      series={series}
      cursor={cursor}
      setCursor={(i) => jump(plot[i].t)}
    />
  );
  async function save(decision) {
    setSaving(true);
    setSaveError('');
    try {
      const r = await fetch('/api/braking/decisions/' + event.id, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ decision, revision: decisions.revision }),
      });
      const value = await r.json();
      if (!r.ok) throw Error(value.error || 'Unable to save');
      setDecisions(value);
    } catch (e) {
      setSaveError(e.message);
    } finally {
      setSaving(false);
    }
  }
  return (
    <div className="content braking-content">
      <div className="eyebrow">SEPTEMBER 7 · FULL-RATE RECORDINGS</div>
      <h1>What happens before the stop?</h1>
      <p className="intro">
        Follow the video and the brake response together. Compare the final stop with your manual braking.
      </p>
      <div className="assisted-summary">
        <span>
          <strong>{index.segments}</strong> archived log segments
        </span>
        <span>
          <strong>{index.events.filter((e) => e.kind === 'autonomous').length}</strong> autonomous stops
        </span>
        <span>
          <strong>{manual.length}</strong> manual finishes selected for comparison
        </span>
      </div>
      <p className="auto-note">
        {index.hardware} Times are Mountain time. Zero marks entry below 0.3 m/s followed by a sustained stop.
      </p>
      <div className="review-toolbar">
        <label>
          Show{' '}
          <select
            aria-label="Braking event selection"
            value={scope}
            onChange={(e) => setScope(e.target.value)}
          >
            <option value="autonomous">Autonomous stops</option>
            <option value="manual">Manual reference finishes</option>
            <option value="takeover">Driver interventions</option>
            <option value="all">All stops</option>
          </select>
        </label>
        <select aria-label="Recorded stop" value={selected?.id || ''} onChange={(e) => setId(e.target.value)}>
          {choices.map((e) => (
            <option key={e.id} value={e.id}>
              {localTime(e)} · {e.kind} · {n(e.max_speed_mph, ' mph')}
            </option>
          ))}
        </select>
      </div>
      {error && <p role="alert">{error}</p>}
      {!selected && <p>No events in this selection.</p>}
      {selected && !event && <p>Loading event…</p>}
      {event && sample && (
        <>
          <DisplayControls settings={display} setSettings={setDisplay} />
          <RoadVideos key={event.id} event={event} seek={seek} onTime={setCursor} display={display} />
          <label className="field">
            Inspect time: {n(cursor, ' s')}
            <input
              aria-label="Inspect braking time"
              type="range"
              min={Math.ceil(event.start * 100) / 100}
              max={Math.floor(event.end * 100) / 100}
              step=".01"
              value={cursor}
              onChange={(e) => jump(Number(e.target.value))}
            />
          </label>
          <div className="braking-readout">
            <span>
              Speed <strong>{n(sample.v * MPH, ' mph')}</strong>
            </span>
            <span>
              Acceleration <strong>{n(sample.a, ' m/s²')}</strong>
            </span>
            <span>
              Lead range <strong>{n(sample.d, ' m')}</strong>
            </span>
            <span>
              Brake state <strong>{sample.state || 'unknown'}</strong>
            </span>
            <span>
              Driver input{' '}
              <strong>{sample.foot ? 'brake pedal' : sample.regen ? 'regen paddle' : 'none'}</strong>
            </span>
            <span>
              Control{' '}
              <strong>
                {sample.active === undefined ? 'unknown' : sample.active ? 'active' : 'inactive'}
              </strong>
            </span>
          </div>
          <div className="review-toolbar">
            <button onClick={() => setPhase('finish')}>Final 5 seconds</button>
            <button onClick={() => setPhase('approach')}>Whole approach</button>
          </div>
          {trace('Speed', 'mph', [
            { label: 'Vehicle', color: '#0369a1', value: (r) => r.v * MPH },
            {
              label: 'Lead',
              color: '#7c3aed',
              value: (r) => (r.radar_valid && r.lead && r.stable_lead ? r.vl * MPH : null),
            },
          ])}
          {trace('Acceleration and braking', 'm/s²', [
            line('Estimated vehicle', '#c2410c', 'a'),
            line('Planner', '#7c3aed', 'target', true),
            line('Controller command', '#0369a1', 'cmd', true),
            line('IMU device axis', '#15803d', 'imu_ax'),
          ])}
          <details>
            <summary>Brake pressure, regeneration, and controller correction</summary>
            {trace('Friction-brake pressure', 'raw CAN units', [
              line('Reported pressure', '#be123c', 'pressure'),
            ])}
            {trace('Friction-brake command', 'raw CAN units', [
              line('Applied command', '#0369a1', 'applied_brake', true),
            ])}
            {trace('Regenerative braking', 'raw CAN units', [line('Reported regen', '#15803d', 'regen_raw')])}
            {trace('Controller correction', 'm/s²', [line('Integral correction', '#7c3aed', 'i', true)])}
          </details>
          {trace('Reported lead distance', 'm', [
            {
              label: 'Lead range',
              color: '#0369a1',
              value: (r) => (r.radar_valid && r.lead && r.stable_lead ? r.d : null),
            },
          ])}
          <p className="footnote">
            Valid control coverage: {n(event.control_coverage * 100, '%')}. Metrics use native samples; graphs
            draw every fifth sample. Pink marks your brake pedal and gold the regen paddle. IMU is a
            device-axis measurement, without grade correction.
          </p>
          <h2>Compare your manual finish</h2>
          <select
            aria-label="Manual comparison"
            value={reference?.id || ''}
            onChange={(e) => setCompareId(e.target.value)}
          >
            {manual.map((e) => (
              <option key={e.id} value={e.id}>
                {localTime(e)} · {n(e.max_speed_mph, ' mph')} approach · {e.kind}
              </option>
            ))}
          </select>
          {comparison && (
            <>
              <button
                onClick={() => {
                  setScope('manual');
                  setId(reference.id);
                }}
              >
                Open this manual stop’s video
              </button>
              <table className="braking-comparison">
                <thead>
                  <tr>
                    <th>Metric</th>
                    <th>Selected stop</th>
                    <th>Manual finish</th>
                  </tr>
                </thead>
                <tbody>
                  {[
                    ['Final phase below 4.5 mph', 'low_speed_seconds', ' s'],
                    ['Final 3 s peak deceleration', 'final_min_accel', ' m/s²'],
                    ['Final-stop jerk, 95th percentile', 'final_jerk_p95', ' m/s³'],
                    ['Settled radar gap', 'settled_radar_gap', ' m'],
                  ].map(([label, key, unit]) => (
                    <tr key={key}>
                      <th>{label}</th>
                      <td>{n(event[key], unit)}</td>
                      <td>{n(comparison[key], unit)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p>
                Different traffic conditions. “Takeover” references use the manual finish after intervention;
                the earlier approach includes openpilot.
              </p>
              <Trace
                title="Final speed comparison"
                unit="mph"
                rows={plot.filter((r) => r.t >= -5)}
                cursor={cursor}
                setCursor={(i) => jump(plot.filter((r) => r.t >= -5)[i].t)}
                series={[
                  { label: 'Selected stop', color: '#0369a1', value: (r) => r.v * MPH },
                  {
                    label: 'Manual finish',
                    color: '#c2410c',
                    value: (r) =>
                      comparison.samples.reduce((a, p) => (Math.abs(p.t - r.t) < Math.abs(a.t - r.t) ? p : a))
                        .v * MPH,
                  },
                ]}
              />
            </>
          )}
          {event.recommended_manual && decisions && (
            <div className="review-toolbar">
              <strong>Does this finish reflect your usual braking?</strong>
              <button disabled={saving} onClick={() => save('representative')}>
                Yes, representative
              </button>
              <button disabled={saving} onClick={() => save('exclude')}>
                Exclude this example
              </button>
              <span>{decisions.data[event.id] || 'unreviewed'}</span>
            </div>
          )}
          {saveError && <p role="alert">{saveError}</p>}
        </>
      )}
      <h2>Controller validation</h2>
      <p>
        {validation?.summary ||
          'Prototype validation is being prepared. Stock braking remains active on the device.'}
      </p>
      <BrakingFunction event={event} cursor={cursor} jump={jump} selectEvent={id => { setScope('manual'); setId(id); }} />
      <BrakingExperiments
        validation={validation}
        event={event}
        cursor={cursor}
        jump={jump}
        decisions={decisions?.data}
        reviewManual={(id) => {
          setScope('manual');
          setId(id);
          window.scrollTo({ top: 0, behavior: 'smooth' });
        }}
      />
      {validation?.checks && (
        <details>
          <summary>Validation checks and remaining work</summary>
          <ul>
            {validation.checks.map((c) => (
              <li key={c.name}>
                {c.pass ? 'Pass' : 'Pending / failed'} · {c.stage === 'diagnostic' ? 'Comparison only · ' : ''}{c.name}: {c.detail}
              </li>
            ))}
          </ul>
        </details>
      )}
      <details>
        <summary>Measurement limits</summary>
        <ul>
          {index.limits.map((x) => (
            <li key={x}>{x}</li>
          ))}
        </ul>
      </details>
    </div>
  );
}

function RoadVideos({ event, seek, onTime, display }) {
  const road = useRef(null),
    wide = useRef(null);
  const first = event.videos?.fcamera,
    second = event.videos?.ecamera;
  useEffect(() => {
    for (const [ref, source] of [
      [road, first],
      [wide, second],
    ])
      if (ref.current && source) {
        ref.current.pause();
        ref.current.currentTime = Math.max(0, Math.min(source.end - source.start, seek.t - source.start));
      }
  }, [seek, first, second]);
  const sync = () => {
    if (!road.current || !first) return;
    const time = road.current.currentTime + first.start;
    onTime(time);
    if (wide.current && second && Math.abs(wide.current.currentTime - (time - second.start)) > 0.12)
      wide.current.currentTime = Math.max(0, time - second.start);
  };
  return (
    <>
      <svg width="0" height="0" aria-hidden="true">
        <defs>
          <filter id="braking-tones" colorInterpolationFilters="sRGB">
            <feComponentTransfer>
              {['R', 'G', 'B'].map((c) =>
                React.createElement('feFunc' + c, {
                  key: c,
                  type: 'linear',
                  slope: display.contrast,
                  intercept: (1 - display.contrast) / 2,
                }),
              )}
            </feComponentTransfer>
            <feComponentTransfer>
              {['R', 'G', 'B'].map((c) =>
                React.createElement('feFunc' + c, {
                  key: c,
                  type: 'gamma',
                  amplitude: display.brightness,
                  exponent: 1 / display.gamma,
                  offset: 0,
                }),
              )}
            </feComponentTransfer>
          </filter>
        </defs>
      </svg>
      {first ? (
        <div className="braking-videos">
          <div>
            <strong>Road camera · playback controls</strong>
            <video
              ref={road}
              src={first.url}
              controls
              muted
              playsInline
              preload="metadata"
              style={{ filter: 'url(#braking-tones)' }}
              onLoadedMetadata={() => {
                road.current.currentTime = Math.max(0, seek.t - first.start);
              }}
              onTimeUpdate={sync}
              onPlay={() => wide.current?.play().catch(() => {})}
              onPause={() => wide.current?.pause()}
            />
          </div>
          {second && (
            <div>
              <strong>Wide camera · synchronized</strong>
              <video
                ref={wide}
                src={second.url}
                muted
                playsInline
                preload="metadata"
                onLoadedMetadata={() => {
                  wide.current.currentTime = Math.max(0, seek.t - second.start);
                }}
                style={{ filter: 'url(#braking-tones)' }}
              />
            </div>
          )}
        </div>
      ) : (
        <p>Video clips are still being prepared. Full-rate telemetry is available below.</p>
      )}
    </>
  );
}

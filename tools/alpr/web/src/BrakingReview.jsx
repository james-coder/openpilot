import React, { useEffect, useState } from 'react';

const number = (v, unit = '') => (Number.isFinite(v) ? `${v.toFixed(2)}${unit}` : 'unavailable');
const MPH = 2.236936292;

export default function BrakingReview() {
  const [data, setData] = useState(null),
    [error, setError] = useState(''),
    [scope, setScope] = useState('takeovers'),
    [eventId, setEventId] = useState(''),
    [cursor, setCursor] = useState(0);
  useEffect(() => {
    fetch('/api/braking-audit')
      .then(async (r) => {
        if (!r.ok) throw Error('Braking analysis is not available yet.');
        return r.json();
      })
      .then((d) => {
        setData(d);
        if (!d.events.some((e) => e.long_active_before_brake || e.regen_takeover)) setScope('all');
      })
      .catch((e) => setError(e.message));
  }, []);
  if (error)
    return (
      <div className="content">
        <h1>Braking review</h1>
        <p>{error}</p>
      </div>
    );
  if (!data) return <div className="loading">Loading recorded braking traces…</div>;
  const eligible = data.events.filter(
    (e) =>
      scope === 'all' ||
      (scope === 'lead' ? e.lead_near_stop : e.long_active_before_brake || e.regen_takeover),
  );
  const event = eligible.find((e) => e.id === eventId) || eligible[0];
  const selected = event?.samples[Math.min(cursor, event.samples.length - 1)];
  return (
    <div className="content braking-content">
      <div className="eyebrow">RECORDED DATA · NO CONTROL CHANGES</div>
      <h1>What happens before the stop?</h1>
      <p className="intro">
        Compare your pedal input, vehicle response and openpilot’s commands. Zero seconds marks entry below
        0.3 m/s, followed by a sustained stop.
      </p>
      <div className="assisted-summary">
        <span>
          <strong>{data.summary.segments}</strong> retained segments
        </span>
        <span>
          <strong>{data.summary.qualifying_stops}</strong> qualifying stops
        </span>
        <span>
          <strong>
            {data.summary.lead_stops_with_prior_brake_press} / {data.summary.stops_with_lead_near_stop}
          </strong>{' '}
          nearby-lead stops with a prior brake press
        </span>
      </div>
      <p className="auto-note">
        Recorded active-control personalities:{' '}
        <strong>
          {Object.keys(data.summary.mode_counts_while_long_active)
            .filter((k) => k.startsWith('personality_') && k !== 'personality_unknown')
            .map((k) => k.slice(12))
            .join(', ') || 'unavailable'}
        </strong>
        . Experimental mode:{' '}
        <strong>
          {[
            data.summary.mode_counts_while_long_active.standard_mode && 'off',
            data.summary.mode_counts_while_long_active.experimental && 'on',
          ]
            .filter(Boolean)
            .join(', ') || 'unavailable'}
        </strong>
        . Settings were unavailable for some samples. These recordings are a selected sample; they cannot
        establish “always,” driver intent, or what the car would have done without intervention.
      </p>
      <div className="review-toolbar">
        <label>
          Show{' '}
          <select
            aria-label="Braking event selection"
            value={scope}
            onChange={(e) => {
              setScope(e.target.value);
              setCursor(0);
            }}
          >
            <option value="takeovers">Pedal interventions after active control</option>
            <option value="lead">Stops with a nearby stopped lead</option>
            <option value="all">All qualifying stops</option>
          </select>
        </label>
        <select
          aria-label="Recorded stop"
          value={event?.id || ''}
          onChange={(e) => {
            setEventId(e.target.value);
            setCursor(0);
          }}
        >
          {eligible.map((e, i) => (
            <option key={e.id} value={e.id}>
              Example {i + 1} · {e.route.split('--')[0]} · {e.id.split('/').at(-1)}
            </option>
          ))}
        </select>
      </div>
      {!event && <p>No qualifying events in this selection.</p>}
      {event && (
        <>
          <p>
            Brake application at {number(event.brake_speed == null ? null : event.brake_speed * MPH, ' mph')}{' '}
            · {number(event.brake_before_stop_s, ' s')} before the stop.{' '}
            {event.lead_near_stop
              ? `A slow/stopped lead was reported near the stop, at roughly ${number(event.lead_distance_at_stop, ' m')}.`
              : 'No qualifying nearby stopped lead was reported at the end; road context needs video review.'}
          </p>
          <label className="field">
            Inspect time: {number(selected.t, ' s')}
            <input
              type="range"
              aria-label="Inspect braking time"
              min="0"
              max={event.samples.length - 1}
              value={Math.min(cursor, event.samples.length - 1)}
              onChange={(e) => setCursor(Number(e.target.value))}
            />
          </label>
          <div className="braking-readout">
            <span>
              Speed <strong>{number(selected.speed * MPH, ' mph')}</strong>
            </span>
            <span>
              Acceleration <strong>{number(selected.accel, ' m/s²')}</strong>
            </span>
            <span>
              Driver brake <strong>{selected.brake_pressed ? 'pressed' : 'released'}</strong>
            </span>
            <span>
              Regen paddle <strong>{selected.regen ? 'pressed' : 'released'}</strong>
            </span>
            <span>
              Longitudinal control{' '}
              <strong>
                {selected.long_active === undefined
                  ? 'unknown'
                  : selected.long_active
                    ? 'active'
                    : 'inactive'}
              </strong>
            </span>
            <span>
              Planner source <strong>{selected.plan_source || 'unknown'}</strong>
            </span>
          </div>
          <Trace
            title="Speed"
            unit="mph"
            rows={event.samples}
            cursor={selected.t}
            setCursor={setCursor}
            series={[
              { label: 'Your speed', color: '#0369a1', value: (r) => r.speed * MPH },
              {
                label: 'Reported lead speed',
                color: '#7c3aed',
                value: (r) => (r.radar_valid && r.lead_status ? r.lead_speed * MPH : null),
              },
            ]}
          />
          <Trace
            title="Acceleration and braking"
            unit="m/s²"
            rows={event.samples}
            cursor={selected.t}
            setCursor={setCursor}
            series={[
              { label: 'Measured vehicle acceleration', color: '#c2410c', value: (r) => r.accel },
              {
                label: 'Planner target while active',
                color: '#7c3aed',
                dashed: true,
                value: (r) => (r.long_active ? r.target_accel : null),
              },
              {
                label: 'Command while active',
                color: '#0369a1',
                value: (r) => (r.long_active ? r.command_accel : null),
              },
            ]}
          />
          <Trace
            title="Reported lead distance"
            unit="m"
            rows={event.samples}
            cursor={selected.t}
            setCursor={setCursor}
            series={[
              {
                label: 'Lead distance',
                color: '#0369a1',
                value: (r) => (r.radar_valid && r.lead_status ? r.lead_distance : null),
              },
            ]}
          />
          <p className="footnote">
            Pink shading marks your brake pedal; gold shading marks regen-paddle input. Commands are hidden
            after longitudinal control becomes inactive. Lead changes and gaps must not be interpreted as a
            continuous vehicle trajectory.
          </p>
        </>
      )}
      <details>
        <summary>Interpretation and existing controls</summary>
        <p>
          The MPC already penalizes jerk (changes in acceleration). This checkout uses following times of 1.25
          s for Aggressive, 1.45 s for Standard and 1.75 s for Relaxed. Aggressive halves the jerk weighting.
          Relaxed is the first existing setting to compare for comfort; it is not a demonstrated fix for these
          recordings.
        </p>
        <p>
          Learned gas gating can encourage coasting. Experimental mode additionally uses the driving model’s
          acceleration/stop output with the MPC. Neither is automatic learning of your personal braking style.{' '}
          <a href="https://blog.comma.ai/098release/" target="_blank" rel="noreferrer">
            Comma’s gas-gating explanation
          </a>
          .
        </p>
        <p>
          A personalized model would need speed, lead range and relative speed, road context, actuator
          response and uncertainty. A speed-only regression cannot describe the same-speed cases of following
          a moving vehicle versus approaching a stopped one. Compare existing settings and collect complete
          approaches before fitting and validating a bounded preference model offline.
        </p>
        <ul>
          {data.limits.map((text) => (
            <li key={text}>{text}</li>
          ))}
        </ul>
      </details>
    </div>
  );
}

function Trace({ title, unit, rows, series, cursor, setCursor }) {
  const width = 1000,
    height = 250,
    left = 75,
    right = 20,
    top = 20,
    bottom = 40;
  const first = rows[0].t,
    last = rows.at(-1).t;
  const values = series.flatMap((s) => rows.map(s.value)).filter(Number.isFinite);
  let low = Math.min(0, ...values),
    high = Math.max(1, ...values);
  const pad = (high - low) * 0.08;
  low -= pad;
  high += pad;
  const x = (t) => left + ((t - first) / (last - first)) * (width - left - right),
    y = (v) => top + ((high - v) / (high - low)) * (height - top - bottom);
  const path = (s) => {
    let active = false;
    return rows
      .map((r, i) => {
        const value = s.value(r);
        if (!Number.isFinite(value)) {
          active = false;
          return '';
        }
        const command = active && (!i || r.t - rows[i - 1].t < 0.3) ? 'L' : 'M';
        active = true;
        return `${command}${x(r.t).toFixed(2)},${y(value).toFixed(2)}`;
      })
      .join(' ');
  };
  return (
    <section className="braking-chart">
      <h2>
        {title} <small>{unit}</small>
      </h2>
      <div className="trace-legend">
        {series.map((s) => (
          <span key={s.label}>
            <i style={{ background: s.color }} />
            {s.label}
          </span>
        ))}
      </div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`${title} over time`}
        onPointerMove={(e) => {
          const box = e.currentTarget.getBoundingClientRect();
          const t =
            first +
            ((((e.clientX - box.left) / box.width) * width - left) / (width - left - right)) * (last - first);
          let nearest = 0;
          rows.forEach((r, i) => {
            if (Math.abs(r.t - t) < Math.abs(rows[nearest].t - t)) nearest = i;
          });
          setCursor(nearest);
        }}
      >
        {rows.map(
          (r, i) =>
            (r.brake_pressed || r.regen) && (
              <rect
                key={i}
                x={x(r.t)}
                y={top}
                width={Math.max(1, x(rows[Math.min(i + 1, rows.length - 1)].t) - x(r.t))}
                height={height - top - bottom}
                fill={r.brake_pressed ? '#fce7f3' : '#fef3c7'}
              />
            ),
        )}
        {[0, 0.25, 0.5, 0.75, 1].map((f) => (
          <g key={f}>
            <line
              x1={left}
              x2={width - right}
              y1={y(low + (high - low) * f)}
              y2={y(low + (high - low) * f)}
              stroke="#cbd5e1"
            />
            <text x={left - 10} y={y(low + (high - low) * f) + 5} textAnchor="end">
              {(low + (high - low) * f).toFixed(1)}
            </text>
            <text x={x(first + (last - first) * f)} y={height - 10} textAnchor="middle">
              {(first + (last - first) * f).toFixed(1)} s
            </text>
          </g>
        ))}
        {series.map((s) => (
          <path
            key={s.label}
            d={path(s)}
            fill="none"
            stroke={s.color}
            strokeWidth="3"
            strokeDasharray={s.dashed ? '7 4' : undefined}
          />
        ))}
        <line
          x1={x(cursor)}
          x2={x(cursor)}
          y1={top}
          y2={height - bottom}
          stroke="#0f172a"
          strokeDasharray="3 3"
        />
      </svg>
    </section>
  );
}

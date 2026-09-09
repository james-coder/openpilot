import React, { useEffect, useState } from 'react';
import { Trace } from './BrakingReview';

export default function BrakingProtection() {
  const [report, setReport] = useState(null);
  const [selected, setSelected] = useState('stationary target');
  const [cursor, setCursor] = useState(0);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    fetch('/api/braking/protection', { signal: controller.signal })
      .then(r => { if (!r.ok) throw Error('Protection bench report unavailable'); return r.json(); })
      .then(setReport).catch(e => { if (e.name !== 'AbortError') setError(e.message); });
    return () => controller.abort();
  }, []);
  if (!report) return error ? <p>{error}</p> : null;
  const test = report.cases.find(c => c.name === selected) || report.cases[0];
  const row = test.trace.find(r => r.t >= cursor) || test.trace.at(-1);
  return <section aria-label="Protection command bench">
    <h3>Protective braking: requested and applied commands</h3>
    <p>Production controls and GM CAN generation are connected in this bench. Vehicle activation is blocked.
      Physical brake calibration, target validation and whole-device timing evidence are still required.</p>
    <p>These are synthetic inputs with an assumed friction response and zero regeneration.
      Command 400 is the existing CAN limit; its physical stopping capability has not been measured.</p>
    <label>Synthetic protection scenario{' '}
      <select aria-label="Synthetic protection scenario" value={test.name} onChange={e => { setSelected(e.target.value); setCursor(0); }}>
        {report.cases.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
      </select>
    </label>
    <p>Command bounds: {test.bounded ? 'passed' : 'failed'}. Brake arbitration: {test.arbitration_pass ? 'passed' : 'failed'}.
      {' '}First intervention: {test.first_intervention === null ? 'none' : `${test.first_intervention.toFixed(2)} s`}.
      {' '}Minimum longitudinal separation: {test.minimum_gap.toFixed(2)} m.</p>
    <p>State at {row.t.toFixed(2)} s: <strong>{row.state}</strong> · {row.reason.replaceAll('_', ' ')}.
      {' '}Backend: {row.backend_accepted ? 'accepted' : 'inactive or rejected'} ({row.backend_reason.replaceAll('_', ' ')}).</p>
    <Trace title="Protective brake command" unit="CAN units" rows={test.trace} cursor={cursor} setCursor={i => setCursor(test.trace[i].t)}
      series={[{ label: 'Requested minimum', color: '#c2410c', value: r => r.requested_floor },
        { label: 'Applied command', color: '#1d4ed8', value: r => r.applied_brake }]} />
    <Trace title="Protection scenario separation" unit="m" rows={test.trace} cursor={cursor} setCursor={i => setCursor(test.trace[i].t)}
      series={[{ label: 'Longitudinal separation', color: '#166534', value: r => r.gap }]} />
    <p>An adjacent vehicle can pass the ego position without a collision. Insufficient initial distance and friction
      outside the assumed bounds demonstrate limits, even when the permitted brake command is requested.</p>
  </section>;
}

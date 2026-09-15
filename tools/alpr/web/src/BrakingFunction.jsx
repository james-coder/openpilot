import React, { useEffect, useState } from 'react';
import { Trace } from './BrakingReview';

const n = (v) => Number.isFinite(v) ? v.toFixed(3) : 'unavailable';
export default function BrakingFunction({ event, cursor, jump, selectEvent }) {
  const [report, setReport] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    fetch('/api/braking/function-fit', { signal: controller.signal })
      .then(r => { if (!r.ok) throw Error('Function fit unavailable'); return r.json(); })
      .then(setReport).catch(e => { if (e.name !== 'AbortError') setError(e.message); });
    return () => controller.abort();
  }, []);
  if (!report) return error ? <p>{error}</p> : null;
  const selected = report.cases.find(c => c.id === event?.id);
  const fit = selected?.windows.filter(w => w.feasible).at(-1);
  const rows = fit?.samples || [];
  const recorded = (t, key) => {
    const samples = event?.samples || [];
    const row = samples.find(r => r.t >= t);
    return row && row.t - t <= .1 ? row[key] : null;
  };
  return <section aria-label="Fitted stopping function">
    <h3>Your braking fitted to a smooth function</h3>
    <p>Two shared shape parameters: {report.curve.shape.map(n).join(', ')}. Target gap: {report.curve.gap} m.
      Speed, acceleration and jerk reach zero at the mathematical endpoint. This is a target curve; vehicle response is tested separately.</p>
    <div className="review-toolbar">
      {report.cases.map((c, i) => <button key={c.id} onClick={() => selectEvent(c.id)}>
        Manual stop {i + 1} · {c.partition}{c.windows.some(w => w.feasible) ? '' : ' · unavailable'}
      </button>)}
    </div>
    <p>{report.evaluation_note}</p>
    {selected && <p>{selected.windows.filter(w => !w.feasible).map(w => `${w.entry} m/s: ${w.reason}.`).join(' ')}</p>}
    {report.excluded.map(e => <p key={e.id}>Excluded {e.id}: {e.reason}.</p>)}
    {fit && <>
      <p>Review marker: 0 s. Confirmed recorded standstill: {n(fit.stop)} s.
        Function endpoint: {n(fit.start + fit.duration_fit)} s. Speed fit error: {n(fit.speed_rmse)} m/s.
        Final 100 ms: acceleration {n(fit.terminal_accel)} m/s²; jerk {n(fit.terminal_jerk)} m/s³.
        Final movement from 0.3 to 0.03 m/s: {n(fit.creep_seconds)} s.</p>
      {[['Speed: recording and fitted function', 'm/s', 'v'], ['Acceleration: recording and fitted function', 'm/s²', 'a'],
        ['Jerk of the fitted function', 'm/s³', 'j']].map(([title, unit, key]) =>
        <Trace key={key} title={title} unit={unit} rows={rows} cursor={cursor} setCursor={i => jump(rows[i].t)}
          series={[...(key === 'j' ? [] : [{ label: 'Recorded', color: '#111827', value: r => recorded(r.t, key === 'v' ? 'vraw' : key) }]),
            { label: 'Fitted function', color: '#7e22ce', value: r => r[key] }]} />)}
    </>}
  </section>;
}

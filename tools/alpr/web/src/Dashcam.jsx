import React, { useEffect, useRef, useState } from 'react';
import { ExternalLink } from 'lucide-react';

const f1 = (x) => (typeof x === 'number' ? x.toFixed(1) : '–');
const f2 = (x) => (typeof x === 'number' ? x.toFixed(2) : '–');
const label = (name) => name.replace(/\.jpg$/, '').replace(/_/g, ' · ').replace(/-/g, ' ');

const FPS = 20;
const RATES = [0.1, 0.25, 0.5, 1, 2];

export default function Dashcam() {
  const vid = useRef(null);
  const [rate, setRate] = useState(1);
  const [items, setItems] = useState(null);
  const [error, setError] = useState('');
  const [pick, setPick] = useState('');
  // Step by whole frames: snap to the nearest frame boundary, then land just inside the target frame.
  const frame = (n) => {
    const v = vid.current;
    if (!v) return;
    v.pause();
    v.currentTime = Math.min(Math.max(0, (Math.round(v.currentTime * FPS) + n) / FPS + 0.0125), v.duration || 0);
  };
  const seek = (dt) => {
    const v = vid.current;
    if (v) v.currentTime = Math.min(Math.max(0, v.currentTime + dt), v.duration || 0);
  };
  const toggle = () => {
    const v = vid.current;
    if (v) (v.paused ? v.play() : v.pause());
  };
  useEffect(() => {
    const onKey = (e) => {
      if (e.target.closest?.('select,input,textarea') || e.ctrlKey || e.metaKey || e.altKey) return;
      const keys = { ',': () => frame(-1), '.': () => frame(1), j: () => seek(-1), l: () => seek(1), k: toggle };
      if (keys[e.key]) {
        e.preventDefault();
        keys[e.key]();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
  useEffect(() => {
    if (vid.current) vid.current.playbackRate = rate;
  }, [rate, pick, items]);
  useEffect(() => {
    fetch('/api/dashcam')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error('Dashcam list unavailable'))))
      .then((d) => {
        setItems(d.items);
        setPick((p) => p || d.items[0]?.id || '');
      })
      .catch((e) => setError(e.message));
  }, []);
  if (error) return <div className="content"><p className="intro">{error}</p></div>;
  if (!items) return <div className="content"><p className="intro">Loading…</p></div>;
  if (!items.length) return <div className="content"><h1>Dashcam</h1><p className="intro">No incident reports yet.</p></div>;
  const it = items.find((i) => i.id === pick) || items[0];
  const base = `/dashcam-files/${it.id}/`;
  const rad = it.radar_after_stop || {};
  const tiles = [
    ['Speed at brake press', `${f1(it.speed_at_onset_mph)} mph`, `${it.left_blinker_during ? 'Left' : it.right_blinker_during ? 'Right' : 'No'} turn signal on`],
    ['Time to stop', `${f1(it.time_to_stop_s)} s`, `about ${f1(it.stopping_distance_imu_m)} m (from the accelerometer)`],
    ['Peak braking', `${f2(it.peak_decel_g_smoothed)} g`, `${f2(it.peak_decel_g_raw)} g with ABS pulses`],
    ['ABS-like wheel lock', `${it.abs_like_first_rel_s != null ? `${it.abs_like_first_rel_s > 0 ? '+' : ''}${f2(it.abs_like_first_rel_s)} to +${f2(it.abs_like_last_rel_s)} s` : 'none'}`, 'inferred from wheel speeds'],
    ['Radar after stop', `${f2(rad.min_range_m)}–${f2(rad.max_range_m)} m`, `${f1(Math.abs(rad.y_m))} m ${rad.y_m < 0 ? 'right' : 'left'} of center`],
    ['Jolt during / after stop', `${f2(it.jolt_peak_during_stop_g)} / ${f2(it.jolt_peak_after_standstill_g)} g`, `bumps on this drive: up to ${f2(it.jolt_normal_driving_max_g)} g`],
  ];
  return (
    <div className="content">
      <div className="eyebrow">DASHCAM · INCIDENT REVIEW</div>
      <h1>Hard brake · {it.onset_wall_mdt} MDT</h1>
      {items.length > 1 && (
        <p>
          <select value={it.id} onChange={(e) => setPick(e.target.value)}>
            {items.map((i) => <option key={i.id} value={i.id}>{i.id} · {i.onset_wall_mdt}</option>)}
          </select>
        </p>
      )}
      <p className="intro">
        Road and wide cameras side by side with turn signal, brake pedal, ABS, g-force and nearest radar return
        shown live. Keys: <kbd>,</kbd> and <kbd>.</kbd> step one frame, <kbd>J</kbd> and <kbd>L</kbd> jump a second,
        <kbd>K</kbd> plays or pauses.
      </p>
      <video ref={vid} controls preload="metadata" src={base + 'video.mp4'} style={{ width: '100%', maxWidth: 1400, background: '#000' }}
        onLoadedMetadata={(e) => { e.currentTarget.playbackRate = rate; }} />
      <p style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
        <button className="button" onClick={() => seek(-1)}>−1 s</button>
        <button className="button" onClick={() => frame(-1)}>◀ frame</button>
        <button className="button" onClick={toggle}>Play / pause</button>
        <button className="button" onClick={() => frame(1)}>frame ▶</button>
        <button className="button" onClick={() => seek(1)}>+1 s</button>
        <span style={{ marginLeft: 12 }}>Speed</span>
        {RATES.map((r) => (
          <button key={r} className={'button' + (r === rate ? ' primary' : '')} onClick={() => setRate(r)}>{r}×</button>
        ))}
      </p>
      <p>
        <a className="button" href={base + 'index.html'} target="_blank" rel="noreferrer">
          Standalone report <ExternalLink size={16} />
        </a>{' '}
        <a className="button" href={base + 'video.mp4'} download>Download video</a>
      </p>
      <div className="metrics">
        {tiles.map(([l, v, n]) => (
          <div className="metric" key={l}><span>{l}</span><strong>{v}</strong><small>{n}</small></div>
        ))}
      </div>
      <h2>Graph</h2>
      <img src={base + 'graph.png'} alt="Speed, braking, ABS and turn signal over time" style={{ maxWidth: '100%' }} />
      <h2>Stills (full resolution)</h2>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(340px,1fr))', gap: 8 }}>
        {it.stills.map((s) => (
          <figure key={s} style={{ margin: 0 }}>
            <a href={base + 'stills/' + s} target="_blank" rel="noreferrer"><img src={base + 'stills/' + s} alt={label(s)} style={{ width: '100%' }} /></a>
            <figcaption><small>{label(s)}</small></figcaption>
          </figure>
        ))}
      </div>
      {it.notes && (
        <>
          <h2>Notes from review</h2>
          <p style={{ whiteSpace: 'pre-wrap' }}>{it.notes.trim()}</p>
        </>
      )}
      <h2>Radar tracks within 15 m</h2>
      <table>
        <thead><tr><th>id</th><th>closest (m)</th><th>when (s)</th><th>forward (m)</th><th>lateral (m)</th><th>m/s</th></tr></thead>
        <tbody>
          {it.tracks.map((t) => (
            <tr key={t.id}><td>{t.id}</td><td>{f1(t.closest_range_m)}</td><td>{t.t_rel_s > 0 ? '+' : ''}{f2(t.t_rel_s)}</td><td>{f1(t.x_m)}</td><td>{t.y_m > 0 ? '+' : ''}{f1(t.y_m)}</td><td>{t.range_rate_ms > 0 ? '+' : ''}{f1(t.range_rate_ms)}</td></tr>
          ))}
        </tbody>
      </table>
      <p className="intro">
        Radar range is measured from behind the front bumper to the strongest reflection, so the real gap may be a
        little smaller, and the radar did not track the sedan until after the stop. Parking-sensor distances are not recorded. See the standalone report for what the data can and
        cannot rule out.
      </p>
    </div>
  );
}

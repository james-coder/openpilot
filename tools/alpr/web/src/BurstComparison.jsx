import React, { useEffect, useRef, useState } from 'react';
import { enhance } from './DisplayControls';

export default function BurstComparison({ encounterId, settings }) {
  const [report, setReport] = useState(null),
    [open, setOpen] = useState(false),
    [method, setMethod] = useState('fourier');
  useEffect(() => {
    let active = true;
    setReport(null);
    fetch('/api/assisted/fusion')
      .then((r) => (r.ok ? r.json() : {}))
      .then((d) => {
        if (active) {
          setReport(d[encounterId] || null);
          setMethod(d[encounterId]?.preferred_method || 'fourier');
        }
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [encounterId]);
  if (!report) return null;
  const chosen = report.methods.find((m) => m.id === method) || report.methods[0];
  return (
    <section className="burst-panel">
      <button className="button" onClick={() => setOpen(!open)}>
        {open ? 'Hide combined-frame comparison' : 'Compare combined frames'} · {report.used_frames} aligned
        views
      </button>
      {open && (
        <>
          <h3>Can several frames reveal more?</h3>
          <p>
            {report.used_frames} views aligned; {report.input_frames - report.used_frames} clipped or poorly
            aligned views excluded. These combinations have not resolved the uncertain characters. Compare
            against the original before accepting a character.
          </p>
          <label>
            Combination method{' '}
            <select
              aria-label="Combination method"
              value={method}
              onChange={(e) => setMethod(e.target.value)}
            >
              {report.methods
                .filter((m) => m.id !== 'reference')
                .map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label}
                  </option>
                ))}
            </select>
          </label>
          <div className="burst-grid">
            <figure>
              <figcaption>Best individual frame · same display settings</figcaption>
              <ProcessedCrop
                path={report.methods[0].image}
                settings={settings}
                label="Burst reference frame"
              />
            </figure>
            <figure>
              <figcaption>{chosen.label} · experimental</figcaption>
              <ProcessedCrop path={chosen.image} settings={settings} label="Combined plate frames" />
            </figure>
          </div>
          <p className="footnote">
            Classical alignment and pixel combination. No generative model or OCR-guided reconstruction. This
            comparison does not replace the original frame or automatically confirm its text.
          </p>
          {report.imu_summary && (
            <div className="auto-note">
              <strong>Motion sensors are available</strong>
              <p>
                Gyroscope and accelerometer: about {Math.round(report.imu_summary.gyroscope.median_rate_hz)}{' '}
                samples/sec. Integrated gyro magnitude over this sequence is about{' '}
                {Math.round((report.imu_summary.gyro_integrated_norm_rad * 180) / Math.PI)}° of angular travel
                (not a calibrated camera pose).
                {report.gyro_experiment
                  ? ' Gyro-guided methods are now available above. Their exposure durations are assumptions, not measurements; stronger settings can create false edges.'
                  : ' Gyroscope correction is not applied to these combined images.'}
              </p>
            </div>
          )}
          {report.gyro_experiment && chosen.id.startsWith('gyro_') && (
            <p className="validation">
              Experimental motion correction: measured gyro direction, assumed exposure. Camera motion only;
              truck motion and HDR mixing remain unresolved. Do not accept a character from new edges alone.
            </p>
          )}
          <details>
            <summary>Alignment details</summary>
            <p>
              All comparisons use {report.native_width} × {report.native_height} output pixels before browser
              enlargement.
            </p>
            <ul>
              {report.sources.map((s) => (
                <li key={s.frame}>
                  Frame {s.frame}:{' '}
                  {s.used
                    ? `included · alignment correlation ${s.correlation.toFixed(3)}`
                    : 'excluded · ' + s.reason}
                </li>
              ))}
            </ul>
          </details>
        </>
      )}
    </section>
  );
}
function ProcessedCrop({ path, settings, label }) {
  const canvas = useRef(null),
    [image, setImage] = useState(null);
  useEffect(() => {
    let active = true;
    setImage(null);
    const i = new Image();
    i.onload = () => {
      if (active) setImage(i);
    };
    i.src = '/' + path;
    return () => {
      active = false;
    };
  }, [path]);
  useEffect(() => {
    if (!image || !canvas.current) return;
    const c = canvas.current;
    c.width = image.width;
    c.height = image.height;
    const ctx = c.getContext('2d');
    ctx.drawImage(image, 0, 0);
    enhance(ctx, c.width, c.height, settings);
  }, [image, settings]);
  return <canvas ref={canvas} aria-label={label} />;
}

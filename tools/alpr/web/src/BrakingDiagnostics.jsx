import React from 'react';

const n = (v, unit = '') => Number.isFinite(v) ? `${v.toFixed(2)}${unit}` : 'unavailable';

export default function BrakingDiagnostics({ diagnostics, event, reproduction }) {
  if (!diagnostics) return null;
  const selected = diagnostics.events.find((e) => e.id === event?.id);
  const onset = diagnostics.pressure_onset_experiment;
  return (
    <section aria-label="Brake response diagnosis">
      <h3>Where the response model goes wrong</h3>
      <p>
        {n(diagnostics.training_low_seconds, ' s')} of qualifying autonomous braking below 4.5 mph
        remain in the cached training windows. The {diagnostics.takeover_events} takeover events add{' '}
        {n(diagnostics.takeover_low_seconds, ' s')} in that range.
      </p>
      <p>
        Pressure onset: {onset?.identified ? 'a training fit is available' : 'not identified from the available transitions'}.
        {' '}The onset experiment is offline and has not changed the controller calibration.
      </p>
      {selected && (
        <table>
          <thead><tr><th>Diagnostic stage</th><th>Selected stop</th></tr></thead>
          <tbody>
            <tr><td>Command → measured pressure</td><td>{n(selected.command_pressure_rmse_raw)} raw-unit RMSE</td></tr>
            <tr><td>Low-speed pressure error</td><td>{n(selected.low_pressure_rmse_raw)} raw-unit RMSE</td></tr>
            <tr><td>Complete command → stop replay</td><td>{n(reproduction?.stop_time, ' s')} timing error</td></tr>
          </tbody>
        </table>
      )}
      <details>
        <summary>Isolate pressure error from vehicle-motion error</summary>
        <p>
          Each uninterrupted episode starts at recorded wheel speed. Comparing predicted pressure with
          measured pressure isolates part of the error; both diagnostics use recorded speed for the response
          calculation. Only the complete replay allows vehicle speed to evolve independently.
        </p>
        <table>
          <thead><tr><th>Episode</th><th>Predicted pressure: speed error</th><th>Measured pressure: speed error</th></tr></thead>
          <tbody>{selected?.motion_episodes.map((e, i) => (
            <tr key={i}><td>{n(e.start)}–{n(e.end)} s</td>
              <td>{n(e.command_pressure_to_motion.end_speed_error, ' m/s')}</td>
              <td>{n(e.measured_pressure_to_motion.end_speed_error, ' m/s')}</td></tr>
          ))}</tbody>
        </table>
      </details>
      <details>
        <summary>Compare manual and autonomous measured-pressure response</summary>
        <p>
          These separate fits use reported zero regen and measured pressure. Different coefficients
          mean the current assumptions do not support pooling the observations; neither fit is a released calibration.
        </p>
        <table>
          <thead><tr><th>Input source</th><th>Observed time</th><th>Raw-pressure coefficient</th><th>Episode speed-change error</th></tr></thead>
          <tbody>{Object.entries(diagnostics.physical_response).map(([source, fit]) => (
            <tr key={source}><td>{source}</td><td>{n(fit.seconds, ' s')}</td>
              <td>{n(fit.coefficients?.[0])}</td><td>{n(fit.episode_velocity_change_rmse, ' m/s')}</td></tr>
          ))}</tbody>
        </table>
      </details>
      <p>Measurements still needed:</p>
      <ul>{diagnostics.missing_measurements.map((text) => <li key={text}>{text}</li>)}</ul>
    </section>
  );
}

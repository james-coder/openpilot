import React, { useEffect, useState } from 'react';

export default function CanReview() {
  const [coverage, setCoverage] = useState(null);
  useEffect(() => {
    fetch('/api/can-coverage')
      .then((r) => (r.ok ? r.json() : null))
      .then(setCoverage)
      .catch(() => {});
  }, []);
  return (
    <div className="content">
      <div className="eyebrow">DESKTOP RENDER OF THE DEVICE PANEL</div>
      <h1>CAN debugger improvements</h1>
      <p className="intro">
        Readable bus buttons, units and named values, explicit graph controls, and five minutes to inspect
        while parked. These previews use the changed device UI code; they are not screenshots of an installed
        update.
      </p>
      <figure>
        <img
          className="can-preview"
          src="/diagnostics-ui/can-list.png"
          alt="CAN list with separated bus buttons and decoded values"
        />
        <figcaption>
          Full message and signal names are available by tapping a row. Unknown frames retain raw bytes.
        </figcaption>
      </figure>
      <figure>
        <img
          className="can-preview"
          src="/diagnostics-ui/can-graph.png"
          alt="High-contrast CAN graph with freeze and close buttons"
        />
        <figcaption>
          Illustrative trace: thick cyan line, visible axes, current value, and a 30-second window. Only Close
          dismisses the graph.
        </figcaption>
      </figure>
      {coverage && (
        <>
          <h2>What the current files can decode</h2>
          <table>
            <thead>
              <tr>
                <th>Bus</th>
                <th>Observed message IDs</th>
                <th>Defined IDs</th>
                <th>Defined signals</th>
              </tr>
            </thead>
            <tbody>
              {coverage.buses.map((b) => (
                <tr key={b.bus}>
                  <td>{b.name}</td>
                  <td>{b.observed_messages}</td>
                  <td>{b.known_observed_messages}</td>
                  <td>{b.known_observed_signals}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p>
            The matched DBCs already cover the defined signals above. The UI now exposes physical units and
            named states instead of displaying only bare numbers. Other bundled DBCs describe different
            networks; matching a message number alone does not establish a valid decoding.
          </p>
          <p className="footnote">{coverage.limits}</p>
        </>
      )}
      <p>
        While moving, engaged, or missing fresh onroad vehicle state, the normal short inactivity timeout
        applies. The parked inspection timeout is five minutes; closing the panel restores the normal setting.
      </p>
    </div>
  );
}

import React from 'react';

export default function PlateContext({ context, update, uncertain, hint, stateCodes, vehicleClass }) {
  return (
    <>
      <label className="checkbox">
        <input
          type="checkbox"
          checked={Boolean(uncertain)}
          onChange={(e) =>
            update({
              certainty: e.target.checked ? 'tentative' : 'unspecified',
              ...(e.target.checked ? {} : { alternatives: [] }),
            })
          }
        />{' '}
        Some characters are uncertain
      </label>
      <details open={context.jurisdiction !== '' || uncertain ? true : undefined}>
        <summary>State, plate design & alternatives (optional)</summary>
        <label className="field">
          Issuing state
          <select
            aria-label="Issuing state"
            value={context.jurisdiction}
            onChange={(e) => update({ jurisdiction: e.target.value, plate_style: 'unknown' })}
          >
            <option value="">Unknown / outside US</option>
            {stateCodes.map((code) => (
              <option key={code}>{code}</option>
            ))}
          </select>
        </label>
        <label className="field">
          Plate design
          <select
            aria-label="Plate design"
            value={context.plate_style}
            onChange={(e) => update({ plate_style: e.target.value })}
          >
            <option value="unknown">Not sure</option>
            {context.jurisdiction === 'UT' && (
              <>
                <option value="ut_skier">Life Elevated Skier · standard</option>
                <option value="ut_arches">Life Elevated Arches · standard</option>
              </>
            )}
            <option value="personalized">Personalized</option>
            <option value="other">Other / specialty / older design</option>
          </select>
        </label>
        {hint ? (
          <p className="suggestion-note">
            {hint.description}{' '}
            {hint.matches ? 'Your reading fits this pattern.' : 'Your reading differs from this pattern.'}{' '}
            {hint.known
              ? 'A format match does not confirm a character.'
              : 'The design is unconfirmed; this is only a possibility.'}{' '}
            <a href={hint.source} target="_blank" rel="noreferrer">
              Utah DMV
            </a>
          </p>
        ) : (
          <p className="suggestion-note">
            No format rule applied. State context is saved for future OCR comparisons.
          </p>
        )}
        <label className="field">
          Vehicle type
          <select
            aria-label="Vehicle type"
            value={context.vehicle_type}
            onChange={(e) => update({ vehicle_type: e.target.value })}
          >
            <option value="">Use detector suggestion ({vehicleClass || 'unknown'})</option>
            {['car', 'truck', 'bus', 'motorcycle', 'other'].map((v) => (
              <option key={v}>{v}</option>
            ))}
          </select>
        </label>
        {uncertain && (
          <>
            <label className="field">
              Alternative readings (one per line)
              <textarea
                aria-label="Alternative readings"
                value={context.alternatives.join('\n')}
                onChange={(e) => update({ alternatives: e.target.value.split('\n') })}
              />
            </label>
            <label className="field">
              What is uncertain?
              <input
                aria-label="Uncertainty note"
                value={context.uncertainty_note}
                onChange={(e) => update({ uncertainty_note: e.target.value })}
              />
            </label>
          </>
        )}
      </details>
    </>
  );
}

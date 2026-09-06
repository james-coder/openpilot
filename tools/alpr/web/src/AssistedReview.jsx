import BurstComparison from './BurstComparison';
import { stateCodes, formatHint } from '../plate-formats.mjs';
import PlateContext from './PlateContext';
import React, { useState, useEffect, useRef } from 'react';
import { ArrowLeft, ArrowRight, CheckCircle2, Download, ScanLine, X, Edit3 } from 'lucide-react';
import { DisplayControls, useDisplaySettings, enhance } from './DisplayControls';
const api = async (url, options) => {
  const r = await fetch(url, { ...options, signal: AbortSignal.timeout(15000) }),
    d = await r.json();
  if (!r.ok) throw Error(d.error || 'Request failed');
  return d;
};

export default function AssistedReview() {
  const [queue, setQueue] = useState(null),
    [reviews, setReviews] = useState(null),
    [error, setError] = useState(''),
    [n, setN] = useState(0),
    [scope, setScope] = useState('close'),
    [status, setStatus] = useState(''),
    [busy, setBusy] = useState(false),
    [advance, setAdvance] = useState(true);
  const [settings, setSettings] = useDisplaySettings();
  useEffect(() => {
    Promise.all([api('/api/assisted/queue'), api('/api/assisted/reviews')])
      .then(([q, r]) => {
        setQueue(q);
        setReviews(r);
        if (!q.encounters.some((e) => e.tier === 'close')) setScope('best');
      })
      .catch((e) => setError(e.message));
  }, []);
  if (error && !queue)
    return (
      <div className="content">
        <h1>Preparing easier examples</h1>
        <p>{error}</p>
        <button onClick={() => location.reload()}>Check again</button>
      </div>
    );
  if (!queue || !reviews) return <div className="loading">Loading prepared vehicle encounters…</div>;
  const eligible = queue.encounters.filter(
    (e) =>
      scope === 'all' ||
      (scope === 'best' && e.tier !== 'explore') ||
      (scope === 'close' && e.tier === 'close') ||
      (scope === 'baseline' && reviews.data.decisions[e.id]?.baseline),
  );
  const index = Math.min(n, Math.max(0, eligible.length - 1)),
    encounter = eligible[index];
  const reviewed = Object.keys(reviews.data.decisions).length,
    baseline = Object.values(reviews.data.decisions).filter((r) => r.baseline).length;
  const save = async (review) => {
    setBusy(true);
    setStatus('Saving review…');
    setError('');
    try {
      const result = await api('/api/assisted/reviews', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ revision: reviews.revision, encounter_id: encounter.id, review }),
      });
      setReviews(result);
      try {
        localStorage.removeItem('road-review-draft:' + encounter.id);
      } catch {}
      setStatus('Review saved');
      if (advance) {
        const next = eligible.findIndex((e, i) => i > index && !result.data.decisions[e.id]);
        if (next >= 0) setN(next);
      }
    } catch (e) {
      setError(e.message);
      setStatus('Not saved');
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="content assisted-content">
      <div className="report-heading">
        <div>
          <div className="eyebrow">PREPARED ON THE RTX 4090</div>
          <h1>Start with the clearest plates.</h1>
          <p className="intro">
            We found the vehicles and plate boxes. Check the best view, correct anything wrong, and confirm
            once per encounter.
          </p>
        </div>
        <a className="button" href="/api/assisted/export">
          <Download size={16} /> Download reviews
        </a>
      </div>
      <div className="assisted-summary">
        <span>
          <strong>{queue.stats.close_encounters}</strong> close-range candidates
        </span>
        <span>
          <strong>{reviewed}</strong> reviewed encounters
        </span>
        <span>
          <strong>{baseline}</strong> clear baselines confirmed
        </span>
        <a href="#manual">Your original annotations are preserved</a>
      </div>
      <DisplayControls settings={settings} setSettings={setSettings} />
      <div className="review-toolbar">
        <div className="group">
          <button
            aria-label="Previous encounter"
            disabled={index === 0}
            onClick={() => {
              setN(index - 1);
              setError('');
            }}
          >
            <ArrowLeft size={16} />
          </button>
          <strong>
            Vehicle {index + 1} / {eligible.length}
          </strong>
          <button
            aria-label="Next encounter"
            disabled={index >= eligible.length - 1}
            onClick={() => {
              setN(index + 1);
              setError('');
            }}
          >
            <ArrowRight size={16} />
          </button>
          <select
            aria-label="Candidate selection"
            value={scope}
            onChange={(e) => {
              setScope(e.target.value);
              setN(0);
              setError('');
            }}
          >
            <option value="close">Closest & clearest · roughly 3–8 m</option>
            <option value="best">More large-plate examples</option>
            <option value="all">All prepared candidates</option>
            <option value="baseline">My confirmed clear baseline</option>
          </select>
        </div>
        <span role="status" className="save-state">
          {status}
        </span>
        <label className="checkbox">
          <input type="checkbox" checked={advance} onChange={(e) => setAdvance(e.target.checked)} /> Next
          after saving
        </label>
      </div>
      {error && (
        <div className="alert">
          {error} <button onClick={() => location.reload()}>Reload saved reviews</button>
        </div>
      )}
      {encounter ? (
        <Encounter
          key={encounter.id}
          encounter={encounter}
          queue={queue}
          decision={reviews.data.decisions[encounter.id]}
          settings={settings}
          onSave={save}
          busy={busy}
        />
      ) : (
        <div className="empty">
          <h2>No examples in this selection yet</h2>
          <p>Choose another selection, or confirm a readable plate as a clear baseline.</p>
        </div>
      )}
      <p className="footnote">
        Radar distances are approximate matches to the camera view. Machine suggestions need your
        confirmation; these assisted reviews are recorded separately from independent accuracy labels.
      </p>
    </div>
  );
}

function Encounter({ encounter, queue, decision, settings, onSave, busy }) {
  const stored = () => {
    try {
      const d = JSON.parse(localStorage.getItem('road-review-draft:' + encounter.id));
      return d?.baseSavedAt === (decision?.saved_at || null) ? d : null;
    } catch {
      return null;
    }
  };
  const draft = useRef(stored()).current;
  const initialSample =
    encounter.samples.find((s) => s.id === (draft?.sample_id || decision?.sample_id)) || encounter.samples[0];
  const [sample, setSample] = useState(initialSample),
    [box, setBox] = useState(draft?.box || decision?.box || initialSample.box),
    [text, setText] = useState(draft?.text ?? decision?.text ?? encounter.suggested_text),
    [lighting, setLighting] = useState(draft?.lighting || decision?.lighting || 'auto'),
    [vehicleId, setVehicleId] = useState(draft?.vehicle_id || decision?.vehicle_id || encounter.id),
    [context, setContext] = useState(
      Object.fromEntries(
        Object.entries({
          jurisdiction: '',
          plate_style: 'unknown',
          certainty: 'unspecified',
          alternatives: [],
          uncertainty_note: '',
          vehicle_type: '',
        }).map(([key, fallback]) => [key, draft?.[key] ?? decision?.[key] ?? fallback]),
      ),
    ),
    [edit, setEdit] = useState(false),
    [message, setMessage] = useState('');
  const touched = useRef(false);
  const remember = () => {
    touched.current = true;
  };
  useEffect(() => {
    if (!touched.current) return;
    try {
      localStorage.setItem(
        'road-review-draft:' + encounter.id,
        JSON.stringify({
          ...context,
          baseSavedAt: decision?.saved_at || null,
          sample_id: sample.id,
          box,
          text,
          lighting,
          vehicle_id: vehicleId,
        }),
      );
    } catch {}
  }, [sample, box, text, lighting, vehicleId, context]);
  const choose = (s) => {
    remember();
    setSample(s);
    setBox(s.box);
    setEdit(false);
    setMessage('');
  };
  const commit = (state, baseline = false) => {
    if (['confirmed', 'corrected'].includes(state) && !/[a-z0-9]/i.test(text)) {
      setMessage('Enter the plate text, or choose “Can’t read it”.');
      return;
    }
    onSave({
      ...context,
      alternatives: context.alternatives.map((v) => v.trim()).filter(Boolean),
      certainty: baseline ? 'certain' : context.certainty,
      sample_id: sample.id,
      box,
      text,
      lighting,
      vehicle_id: vehicleId,
      baseline,
      state,
    });
  };
  const uncertain = context.certainty === 'tentative' || context.alternatives.some((v) => v.trim());
  const hint = formatHint(context.jurisdiction, context.plate_style, text);
  const updateContext = (values) => {
    remember();
    setContext((old) => ({ ...old, ...values }));
  };
  const corrected = text !== encounter.suggested_text || JSON.stringify(box) !== JSON.stringify(sample.box);
  return (
    <>
      <div className="assisted-layout">
        <section className="focus-panel">
          <div className="focus-heading">
            <div>
              <span className="badge">
                {sample.radar ? `≈ ${sample.radar.range_m.toFixed(1)} m · radar` : 'Camera-selected example'}
              </span>
              <h2>The plate, up close.</h2>
            </div>
            <div>
              <strong>{Math.round(box[2] - box[0])} px</strong>
              <small>original plate width</small>
            </div>
          </div>
          <EnhancedImage sample={sample} box={box} settings={settings} crop />
          <div className="sample-strip">
            {encounter.samples.map((s, i) => (
              <button
                key={s.id}
                className={s.id === sample.id ? 'selected' : ''}
                aria-label={'View ' + (i + 1)}
                onClick={() => choose(s)}
              >
                <img src={'/' + s.crop} alt={'Plate view ' + (i + 1)} />
                <span>{s.radar ? `≈${s.radar.range_m.toFixed(1)} m` : `${s.width} px`}</span>
              </button>
            ))}
          </div>
          <p className="sample-hint">
            {encounter.observation_count} observations grouped automatically · choose another moment if a
            character is unclear.
          </p>
          <BurstComparison encounterId={encounter.id} settings={settings} />
          <div className="context-heading">
            <h3>Vehicle context</h3>
            <button onClick={() => setEdit(!edit)}>
              <Edit3 size={14} />
              {edit ? 'Finish adjusting' : 'Adjust plate box'}
            </button>
          </div>
          <EnhancedImage
            sample={sample}
            box={box}
            settings={settings}
            editing={edit}
            onBox={(b) => {
              remember();
              setBox(b);
              setEdit(false);
            }}
          />
          <div className="image-caption">
            <span>
              {sample.camera === 'fcamera' ? 'Narrow road camera' : 'Wide road camera'} · frame {sample.frame}
            </span>
            <span>{sample.lighting.local_time?.replace('T', ' ') || 'Time unavailable'}</span>
          </div>
        </section>
        <aside className="confirm-panel">
          <div className="eyebrow">QUICK CONFIRMATION</div>
          <h2>Is this right?</h2>
          <p>The box and text are suggestions. Correct either one if needed.</p>
          <label className="field">
            Suggested plate text
            <input
              aria-label="Suggested plate text"
              autoComplete="off"
              spellCheck="false"
              value={text}
              onChange={(e) => {
                remember();
                setText(e.target.value);
              }}
            />
          </label>
          <small className="suggestion-note">No need to guess. You can mark it unreadable.</small>
          <PlateContext
            context={context}
            update={updateContext}
            uncertain={uncertain}
            hint={hint}
            stateCodes={stateCodes}
            vehicleClass={sample.vehicle_class}
          />
          {decision && (
            <div className="saved-decision">
              <CheckCircle2 size={15} /> Saved:{' '}
              {decision.baseline
                ? 'clear baseline'
                : decision.certainty === 'tentative'
                  ? 'tentative reading'
                  : decision.state}
            </div>
          )}
          <button
            disabled={busy}
            className="button primary"
            onClick={() => commit(corrected ? 'corrected' : 'confirmed')}
          >
            <CheckCircle2 size={17} />
            {uncertain ? 'Save tentative reading' : corrected ? 'Save correction' : 'Box & text are correct'}
          </button>
          <button
            disabled={busy || uncertain}
            className="button baseline-button"
            onClick={() => commit(corrected ? 'corrected' : 'confirmed', true)}
          >
            Every character is clear — add to baseline
          </button>
          <div className="secondary-actions">
            <button disabled={busy} onClick={() => commit('unreadable')}>
              <ScanLine size={16} /> Can’t read it
            </button>
            <button disabled={busy} onClick={() => commit('rejected')}>
              <X size={16} /> Not a plate
            </button>
          </div>
          {message && (
            <p role="alert" className="validation">
              {message}
            </p>
          )}
          <div className="auto-note">
            <strong>Already filled in</strong>
            <p>
              Vehicle group assigned automatically.
              <br />
              Lighting: {sample.lighting.value} (estimated).
              <br />
              Best views ranked by pixel size and sharpness.
            </p>
          </div>
          <details>
            <summary>Correct vehicle grouping or lighting</summary>
            <label className="field">
              Vehicle group
              <select
                aria-label="Vehicle group"
                value={vehicleId}
                onChange={(e) => {
                  remember();
                  setVehicleId(e.target.value);
                }}
              >
                {queue.encounters.map((e, i) => (
                  <option key={e.id} value={e.id}>
                    Vehicle {i + 1}
                    {e.id === encounter.id ? ' · automatic' : ''}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              Lighting override
              <select
                aria-label="Lighting override"
                value={lighting}
                onChange={(e) => {
                  remember();
                  setLighting(e.target.value);
                }}
              >
                {['auto', 'day', 'dusk', 'night', 'unknown'].map((v) => (
                  <option key={v}>{v}</option>
                ))}
              </select>
            </label>
          </details>
          <p className="footnote">
            A clear baseline is your confirmation that every character is readable. Brightness controls reveal
            existing pixels; they do not reconstruct missing detail.
          </p>
        </aside>
      </div>
    </>
  );
}

function EnhancedImage({ sample, box, settings, crop = false, editing = false, onBox }) {
  const canvas = useRef(null),
    start = useRef(null),
    [image, setImage] = useState(null),
    [drag, setDrag] = useState(null),
    [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    setImage(null);
    setError('');
    const i = new Image();
    i.onload = () => {
      if (active) setImage(i);
    };
    i.onerror = () => {
      if (active) setError('Could not load the frame.');
    };
    i.src = '/' + sample.image;
    return () => {
      active = false;
    };
  }, [sample.image]);
  useEffect(() => {
    if (!image || !canvas.current) return;
    const c = canvas.current,
      ctx = c.getContext('2d');
    if (crop) {
      const pad = 6,
        x = Math.max(0, box[0] - pad),
        y = Math.max(0, box[1] - pad),
        w = Math.min(image.width - x, box[2] - box[0] + pad * 2),
        h = Math.min(image.height - y, box[3] - box[1] + pad * 2);
      c.width = w;
      c.height = h;
      ctx.drawImage(image, x, y, w, h, 0, 0, w, h);
      enhance(ctx, w, h, settings);
    } else {
      c.width = image.width;
      c.height = image.height;
      ctx.drawImage(image, 0, 0);
      enhance(ctx, c.width, c.height, settings);
      ctx.lineWidth = 5;
      ctx.strokeStyle = '#80b8ff';
      const v = sample.vehicle_box;
      ctx.strokeRect(v[0], v[1], v[2] - v[0], v[3] - v[1]);
      ctx.strokeStyle = '#58ffab';
      ctx.strokeRect(box[0], box[1], box[2] - box[0], box[3] - box[1]);
      if (drag) {
        ctx.strokeStyle = 'white';
        ctx.strokeRect(drag[0], drag[1], drag[2] - drag[0], drag[3] - drag[1]);
      }
    }
  }, [image, box, settings, crop, drag]);
  const xy = (e) => {
    const r = canvas.current.getBoundingClientRect();
    return [
      Math.round(Math.max(0, Math.min(image.width, ((e.clientX - r.left) * image.width) / r.width))),
      Math.round(Math.max(0, Math.min(image.height, ((e.clientY - r.top) * image.height) / r.height))),
    ];
  };
  return (
    <div className={crop ? 'plate-focus' : 'vehicle-context'}>
      {!image && <p>{error || 'Loading original pixels…'}</p>}
      <canvas
        aria-label={crop ? 'Enlarged plate' : 'Vehicle with suggested plate box'}
        ref={canvas}
        style={{ display: image ? 'block' : 'none', cursor: editing ? 'crosshair' : 'default' }}
        onPointerDown={(e) => {
          if (!editing || !image) return;
          start.current = xy(e);
          canvas.current.setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          if (start.current) setDrag([...start.current, ...xy(e)]);
        }}
        onPointerCancel={() => {
          start.current = null;
          setDrag(null);
        }}
        onPointerUp={(e) => {
          if (!start.current) return;
          const a = start.current,
            b = xy(e);
          start.current = null;
          setDrag(null);
          const next = [
            Math.min(a[0], b[0]),
            Math.min(a[1], b[1]),
            Math.max(a[0], b[0]),
            Math.max(a[1], b[1]),
          ];
          if (next[2] - next[0] > 3 && next[3] - next[1] > 3) onBox(next);
        }}
      />
    </div>
  );
}

import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Aperture,
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronRight,
  Download,
  ExternalLink,
  FileText,
  Home,
  Layers,
  Maximize,
  Radar,
  ScanLine,
  Trash2,
  Wifi,
} from 'lucide-react';
import './style.css';
import AssistedReview from './AssistedReview';
import { DisplayControls, useDisplaySettings, enhance } from './DisplayControls';

const nav = [
  ['overview', 'Study overview', Home],
  ['review', 'Confirm close examples', ScanLine],
  ['manual', 'Original annotations', FileText],
  ['radar', 'Radar & video', Radar],
  ['models', 'Model comparison', Layers],
  ['sampling', 'Sampling comparison', Aperture],
  ['results', 'Study findings', FileText],
  ['reliability', 'Device reliability', CheckCircle2],
];
const titles = Object.fromEntries(nav.map(([id, label]) => [id, label]));
const download = (data, name = 'labels.json') => {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
const request = async (url, options) => {
  const res = await fetch(url, { ...options, signal: AbortSignal.timeout(15000) });
  const data = await res.json();
  if (!res.ok) {
    const error = new Error(data.error || 'Request failed');
    error.status = res.status;
    throw error;
  }
  return data;
};

function App() {
  const [page, setPage] = useState(location.hash.slice(1) || 'overview');
  const [study, setStudy] = useState(null),
    [data, setData] = useState(null),
    [error, setError] = useState('');
  const [saveStatus, setSaveStatus] = useState('Loading…'),
    [staleDraft, setStaleDraft] = useState(null);
  const current = useRef(null),
    revision = useRef(null),
    saved = useRef(null),
    busy = useRef(false),
    blocked = useRef(false),
    timer = useRef(null);
  const draftKey = useRef('');
  useEffect(() => {
    const onHash = () => setPage(location.hash.slice(1) || 'overview');
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  const save = async () => {
    if (busy.current || blocked.current || !current.current || current.current === saved.current) return;
    busy.current = true;
    setSaveStatus('Saving…');
    const snapshot = current.current;
    try {
      const result = await request('/api/labels', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ revision: revision.current, data: snapshot }),
      });
      revision.current = result.revision;
      saved.current = snapshot;
      try {
        if (current.current === snapshot) localStorage.removeItem(draftKey.current);
        else
          localStorage.setItem(
            draftKey.current,
            JSON.stringify({ revision: revision.current, data: current.current }),
          );
      } catch {
        /* Server remains the authoritative saved copy. */
      }
      setSaveStatus(current.current === snapshot ? 'All changes saved' : 'Unsaved changes');
    } catch (e) {
      blocked.current = e.status === 409;
      setSaveStatus(e.message);
    } finally {
      busy.current = false;
    }
    if (!blocked.current && current.current !== snapshot) timer.current = setTimeout(save, 700);
  };
  useEffect(() => {
    Promise.all([request('/api/study'), request('/api/labels')])
      .then(([s, labels]) => {
        setStudy(s);
        revision.current = labels.revision;
        saved.current = labels.data;
        let initial = labels.data;
        draftKey.current = 'road-review:' + labels.data.dataset_id;
        try {
          const draft = JSON.parse(localStorage.getItem(draftKey.current));
          if (draft?.data?.dataset_id === labels.data.dataset_id) {
            if (draft.revision === labels.revision) initial = draft.data;
            else setStaleDraft(draft.data);
          }
        } catch {
          /* An unavailable browser cache does not prevent server saves. */
        }
        current.current = initial;
        setData(initial);
        setSaveStatus(initial === labels.data ? 'All changes saved' : 'Restored local draft');
        if (initial !== labels.data) timer.current = setTimeout(save, 700);
      })
      .catch((e) => setError(e.message));
    const beforeUnload = (e) => {
      if (current.current !== saved.current) {
        e.preventDefault();
        e.returnValue = '';
      }
    };
    window.addEventListener('beforeunload', beforeUnload);
    return () => {
      clearTimeout(timer.current);
      window.removeEventListener('beforeunload', beforeUnload);
    };
  }, []);
  const update = (next) => {
    current.current = next;
    setData(next);
    setSaveStatus(
      blocked.current
        ? 'Another tab or reviewer saved changes. Download your draft, then reload to get the saved version.'
        : 'Unsaved changes',
    );
    try {
      localStorage.setItem(draftKey.current, JSON.stringify({ revision: revision.current, data: next }));
    } catch {
      setSaveStatus('Unsaved changes · browser backup unavailable');
    }
    clearTimeout(timer.current);
    timer.current = setTimeout(save, 700);
  };
  const reviewed = data?.frames.filter((f) => f.reviewed).length || 0;
  return (
    <div className="shell">
      <aside className="sidebar">
        <a className="brand" href="#overview">
          <span className="brand-icon">
            <ScanLine size={23} />
          </span>
          <span>
            Road Review<small>COMMA VIDEO STUDY</small>
          </span>
        </a>
        <div className="nav-caption">WORKSPACE</div>
        <nav>
          {nav.map(([id, label, Icon]) => (
            <a key={id} href={'#' + id} className={page === id ? 'active' : ''}>
              <Icon size={18} />
              {label}
              {page === id && <ChevronRight size={15} />}
            </a>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <span className="live-dot" /> Local network{' '}
          <p>
            September 6, 2026 study
            <br />
            Saved on this workstation
          </p>
        </div>
      </aside>
      <main>
        <header>
          <div className="breadcrumb">
            Workspace <ChevronRight size={14} /> <strong>{titles[page] || 'Study overview'}</strong>
          </div>
          <span className="badge">
            <Wifi size={13} /> LOCAL
          </span>
        </header>
        {error ? (
          <div className="alert">
            Could not load the study: {error}. <button onClick={() => location.reload()}>Try again</button>
          </div>
        ) : !data ? (
          <div className="loading">Loading your local study…</div>
        ) : (
          <>
            {staleDraft && page === 'manual' && (
              <div className="alert">
                This browser has an older unsaved draft. The current server copy is loaded.{' '}
                <button onClick={() => download(staleDraft, 'recovered-draft.json')}>
                  Download older draft
                </button>
                <button
                  onClick={() => {
                    localStorage.removeItem(draftKey.current);
                    setStaleDraft(null);
                  }}
                >
                  Dismiss
                </button>
              </div>
            )}
            {page === 'review' ? (
              <AssistedReview />
            ) : page === 'manual' ? (
              <Review
                data={data}
                update={update}
                reviewed={reviewed}
                saveStatus={saveStatus}
                retry={save}
                blocked={blocked.current}
              />
            ) : page === 'radar' ? (
              <Report
                title="Let distance guide the capture"
                subtitle="Compare five recorded frames with nearby radar tracks. Lane spacing in the map is illustrative."
                src="/radar-example.html"
                extra={
                  <a className="button" href="#radar-notes">
                    Read the radar findings <ArrowRight size={16} />
                  </a>
                }
              />
            ) : page === 'models' ? (
              <Report
                title="Compare the same plate crops"
                subtitle="S, T and Paddle predictions. These are model outputs; confidence is not measured accuracy."
                src="/comparison.html"
              />
            ) : page === 'sampling' ? (
              <Report
                title="More frames, or more image detail?"
                subtitle="Native, tiled and full-rate sampling of the same close-vehicle scene."
                src="/comparison-close-vehicle.html"
              />
            ) : ['results', 'reliability', 'radar-notes'].includes(page) ? (
              <Document name={page === 'radar-notes' ? 'radar' : page} />
            ) : (
              <Overview study={study} data={data} reviewed={reviewed} />
            )}
          </>
        )}
      </main>
    </div>
  );
}

function Overview({ study, data, reviewed }) {
  return (
    <div className="content">
      <div className="eyebrow">OFFLINE RESEARCH · 30 SEGMENTS</div>
      <h1>A closer look at the road.</h1>
      <p className="intro">
        Review the footage, test radar-assisted selection, and build an independent set of plate labels.
      </p>
      <div className="metrics">
        {[
          [study.complete_segments, 'Segments downloaded', 'Both road cameras + radar logs'],
          [data.frames.length, 'Frames for review', 'Original image detail'],
          [
            reviewed,
            'Frames reviewed',
            `${Math.round((reviewed / data.frames.length) * 100)}% of the review set`,
          ],
          [
            (study.verified_bytes / 1e9).toFixed(2) + ' GB',
            'Verified recordings',
            'Available without the device',
          ],
        ].map(([value, label, note]) => (
          <div className="metric" key={label}>
            <span>{label}</span>
            <strong>{value}</strong>
            <small>{note}</small>
          </div>
        ))}
      </div>
      <div className="feature-grid">
        <section className="feature">
          <div className="eyebrow">START HERE</div>
          <h2>The machine prepares. You confirm.</h2>
          <p>
            Start with close vehicles and large plate crops. Boxes, suggested text and vehicle groups are
            already prepared. Brightness controls stay where you set them.
          </p>
          <a className="button primary" href="#review">
            Confirm close examples <ArrowRight size={17} />
          </a>
          <div className="progress">
            <span style={{ width: `${(reviewed / data.frames.length) * 100}%` }} />
          </div>
          <small>
            {reviewed} of {data.frames.length} frames reviewed
          </small>
        </section>
        <a href="#radar" className="image-card">
          <img
            src="/runs/s-native-5fps/00000004--cfc3880dd5--106/fcamera/context-001120.jpg"
            alt="Road scene with a pickup ahead and another in the left lane"
          />
          <div>
            <span className="eyebrow">RADAR + VIDEO</span>
            <h2>From 19 m to 6 m.</h2>
            <p>
              See how the plate gains detail as the vehicle approaches. <ArrowRight size={17} />
            </p>
          </div>
        </a>
      </div>
      <div className="section-heading">
        <h2>Explore the evidence</h2>
        <span>Everything served locally</span>
      </div>
      <div className="link-grid">
        {[
          ['models', Layers, 'Model comparison', 'Compare three recognizers on shared crops.'],
          ['sampling', Aperture, 'Sampling experiment', 'Explore tiling and higher frame rates.'],
          [
            'reliability',
            CheckCircle2,
            'Reliability findings',
            'Memory, fingerprinting and device validation.',
          ],
        ].map(([id, Icon, title, text]) => (
          <a href={'#' + id} className="link-card" key={id}>
            <Icon size={22} />
            <h3>{title}</h3>
            <p>{text}</p>
            <ArrowRight size={17} />
          </a>
        ))}
      </div>
      <p className="footnote">
        Recognition accuracy is awaiting independent labels. For unbiased annotation, review frames before
        consulting model predictions.
      </p>
    </div>
  );
}

function Report({ title, subtitle, src, extra }) {
  return (
    <div className="content report-content">
      <div className="report-heading">
        <div>
          <h1>{title}</h1>
          <p className="intro">{subtitle}</p>
        </div>
        <a className="button" href={src} target="_blank" rel="noreferrer">
          Open report <ExternalLink size={16} />
        </a>
      </div>
      {extra && <div className="extra">{extra}</div>}
      <iframe title={title} src={src} />
    </div>
  );
}
function Document({ name }) {
  const [text, setText] = useState('Loading…');
  useEffect(() => {
    let active = true;
    setText('Loading…');
    fetch('/api/docs/' + name)
      .then((r) => {
        if (!r.ok) throw Error('Could not load report');
        return r.text();
      })
      .then((t) => {
        if (active) setText(t);
      })
      .catch((e) => setText(e.message));
    return () => {
      active = false;
    };
  }, [name]);
  const links = {
    'README.md': '#overview',
    'RESULTS.md': '#results',
    'RADAR_PRIORITIZATION.md': '#radar-notes',
  };
  return (
    <div className="content">
      <article className="document">
        <Markdown
          remarkPlugins={[remarkGfm]}
          components={{
            a: ({ href, children }) => (
              <a
                href={links[href] || href}
                target={href?.startsWith('https:') ? '_blank' : undefined}
                rel="noreferrer"
              >
                {children}
              </a>
            ),
          }}
        >
          {text}
        </Markdown>
      </article>
    </div>
  );
}

function Review({ data, update, reviewed, saveStatus, retry, blocked }) {
  const [displaySettings, setDisplaySettings] = useDisplaySettings();
  const [n, setN] = useState(0),
    [camera, setCamera] = useState('all'),
    [unreviewed, setUnreviewed] = useState(false);
  const [zoom, setZoom] = useState(0.5),
    [size, setSize] = useState([1928, 1208]),
    [ready, setReady] = useState(false),
    [imageError, setImageError] = useState('');
  const [drag, setDrag] = useState(null),
    [validation, setValidation] = useState('');
  const canvas = useRef(null),
    viewport = useRef(null),
    img = useRef(null),
    start = useRef(null);
  const frame = data.frames[n];
  const indices = data.frames.flatMap((f, i) =>
    (camera === 'all' || camera === f.camera) && (!unreviewed || !f.reviewed || i === n) ? [i] : [],
  );
  const updateFrame = (patch) => {
    const frames = [...data.frames];
    frames[n] = { ...frame, ...patch };
    update({ ...data, frames });
    setValidation('');
  };
  const updatePlate = (i, patch) =>
    updateFrame({ reviewed: false, plates: frame.plates.map((p, j) => (j === i ? { ...p, ...patch } : p)) });
  const fit = () => setZoom(Math.min(1, (viewport.current.clientWidth - 24) / size[0]));
  const go = (next) => {
    setN(next);
    setDrag(null);
    start.current = null;
    setValidation('');
  };
  const next = (direction) => {
    const i = indices.indexOf(n);
    if (indices[i + direction] !== undefined) go(indices[i + direction]);
  };
  useEffect(() => {
    setReady(false);
    setImageError('');
    const image = new Image();
    let active = true;
    image.onload = () => {
      if (!active) return;
      img.current = image;
      setSize([image.naturalWidth, image.naturalHeight]);
      setReady(true);
    };
    image.onerror = () => {
      if (active) setImageError('Frame could not load. Check your connection and reload.');
    };
    image.src = '/' + frame.image;
    return () => {
      active = false;
    };
  }, [frame.image]);
  useEffect(() => {
    if (ready) fit();
  }, [ready]);
  useEffect(() => {
    if (!ready || !canvas.current) return;
    const c = canvas.current,
      ctx = c.getContext('2d');
    ctx.clearRect(0, 0, c.width, c.height);
    ctx.drawImage(img.current, 0, 0);
    enhance(ctx, c.width, c.height, displaySettings);
    ctx.lineWidth = 3 / zoom;
    ctx.font = `${15 / zoom}px system-ui`;
    frame.plates.forEach((p, i) => {
      ctx.strokeStyle = '#65ffbd';
      ctx.strokeRect(p.box[0], p.box[1], p.box[2] - p.box[0], p.box[3] - p.box[1]);
      ctx.fillStyle = '#102b32';
      ctx.fillRect(p.box[0], Math.max(0, p.box[1] - 24 / zoom), 32 / zoom, 24 / zoom);
      ctx.fillStyle = '#fff';
      ctx.fillText(i + 1, p.box[0] + 7 / zoom, Math.max(18 / zoom, p.box[1] - 6 / zoom));
    });
    if (drag) {
      ctx.strokeStyle = '#fff';
      ctx.setLineDash([6 / zoom, 4 / zoom]);
      ctx.strokeRect(drag[0], drag[1], drag[2] - drag[0], drag[3] - drag[1]);
      ctx.setLineDash([]);
    }
  }, [ready, frame.plates, drag, zoom, size, displaySettings]);
  const xy = (e) => {
    const r = canvas.current.getBoundingClientRect();
    return [
      Math.round(Math.max(0, Math.min(size[0], ((e.clientX - r.left) * size[0]) / r.width))),
      Math.round(Math.max(0, Math.min(size[1], ((e.clientY - r.top) * size[1]) / r.height))),
    ];
  };
  const mark = () => {
    if (frame.plates.some((p) => !p.encounter.trim() || (p.readable && !/[a-z0-9]/i.test(p.text)))) {
      setValidation('Give each plate an encounter ID. Add text for every plate marked readable.');
      return;
    }
    updateFrame({ reviewed: !frame.reviewed });
  };
  const encounters = [
    ...new Set(
      data.frames
        .filter(
          (f) =>
            f.segment.split('--').slice(0, -1).join('--') ===
            frame.segment.split('--').slice(0, -1).join('--'),
        )
        .flatMap((f) => f.plates.map((p) => p.encounter))
        .filter(Boolean),
    ),
  ];
  return (
    <div className="content review-content">
      <div className="report-heading">
        <div>
          <div className="eyebrow">INDEPENDENT ANNOTATION</div>
          <h1>Read what the image tells you.</h1>
        </div>
        <button className="button" onClick={() => download(data)}>
          <Download size={16} /> Download labels
        </button>
      </div>
      <p className="intro">
        Box every visible plate, including unreadable ones. Reuse an encounter ID for the same vehicle within
        a route. Check the whole image before marking a frame reviewed.
      </p>
      <DisplayControls settings={displaySettings} setSettings={setDisplaySettings} />
      <div className="review-toolbar">
        <div className="group">
          <button aria-label="Previous frame" disabled={indices.indexOf(n) <= 0} onClick={() => next(-1)}>
            <ArrowLeft size={17} />
          </button>
          <span className="frame-counter">
            Frame {n + 1} <span>/ {data.frames.length}</span>
          </span>
          <button
            aria-label="Next frame"
            disabled={indices.indexOf(n) === indices.length - 1}
            onClick={() => next(1)}
          >
            <ArrowRight size={17} />
          </button>
          <select aria-label="Jump to frame" value={n} onChange={(e) => go(Number(e.target.value))}>
            {indices.map((i) => (
              <option key={i} value={i}>
                {i + 1} · {data.frames[i].segment} · {data.frames[i].camera} · {data.frames[i].frame}
                {data.frames[i].reviewed ? ' ✓' : ''}
              </option>
            ))}
          </select>
        </div>
        <span className="save-state" role="status">
          {saveStatus === 'All changes saved' && <Check size={15} />} {saveStatus}
        </span>
      </div>
      {saveStatus !== 'All changes saved' &&
        !['Saving…', 'Unsaved changes', 'Restored local draft'].includes(saveStatus) && (
          <div className="alert">
            {blocked ? (
              <button onClick={() => location.reload()}>Reload saved version</button>
            ) : (
              <button onClick={retry}>Retry save</button>
            )}{' '}
            Download labels to keep a separate copy of your current work.
          </div>
        )}
      <div className="review-layout">
        <section className="image-panel">
          <div className="image-tools">
            <label>
              Camera{' '}
              <select
                value={camera}
                onChange={(e) => {
                  setCamera(e.target.value);
                  const i = data.frames.findIndex(
                    (f) => e.target.value === 'all' || f.camera === e.target.value,
                  );
                  if (i >= 0) go(i);
                }}
              >
                <option value="all">Both cameras</option>
                <option value="fcamera">Narrow road</option>
                <option value="ecamera">Wide road</option>
              </select>
            </label>
            <label className="checkbox">
              <input type="checkbox" checked={unreviewed} onChange={(e) => setUnreviewed(e.target.checked)} />{' '}
              Unreviewed
            </label>
            <div className="zoom-tools">
              <button onClick={fit}>
                <Maximize size={14} /> Fit
              </button>
              <label>
                Zoom{' '}
                <select aria-label="Zoom" value={zoom} onChange={(e) => setZoom(Number(e.target.value))}>
                  {![0.25, 0.5, 1, 2, 3, 4].includes(zoom) && (
                    <option value={zoom}>{Math.round(zoom * 100)}%</option>
                  )}
                  {[0.25, 0.5, 1, 2, 3, 4].map((z) => (
                    <option key={z} value={z}>
                      {z * 100}%
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </div>
          <div className="viewport" ref={viewport}>
            {!ready && <p>{imageError || 'Loading original frame…'}</p>}
            <canvas
              ref={canvas}
              width={size[0]}
              height={size[1]}
              aria-label="Road frame: drag to draw a plate box"
              style={{ width: size[0] * zoom, height: size[1] * zoom, display: ready ? 'block' : 'none' }}
              onPointerDown={(e) => {
                if (!ready) return;
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
                const end = xy(e),
                  a = start.current;
                start.current = null;
                setDrag(null);
                const box = [
                  Math.min(a[0], end[0]),
                  Math.min(a[1], end[1]),
                  Math.max(a[0], end[0]),
                  Math.max(a[1], end[1]),
                ];
                if (box[2] - box[0] > 3 && box[3] - box[1] > 3)
                  updateFrame({
                    reviewed: false,
                    plates: [
                      ...frame.plates,
                      { box, text: '', encounter: '', readable: false, blur: 'unknown' },
                    ],
                  });
              }}
            />
          </div>
          <div className="image-caption">
            <span>
              {frame.segment} · {frame.camera} · frame {frame.frame}
            </span>
            <span>
              {size[0]} × {size[1]} · {frame.split} split
            </span>
          </div>
        </section>
        <aside className="annotation-panel">
          <h2>
            Frame notes <span>{frame.plates.length} plates</span>
          </h2>
          <label className="field">
            Lighting
            <select
              aria-label="Lighting"
              value={frame.lighting}
              onChange={(e) => updateFrame({ lighting: e.target.value })}
            >
              {['unknown', 'day', 'dusk', 'night'].map((v) => (
                <option key={v}>{v}</option>
              ))}
            </select>
          </label>
          <datalist id="encounters">
            {encounters.map((v) => (
              <option key={v} value={v} />
            ))}
          </datalist>
          {!frame.plates.length && (
            <div className="empty">
              <ScanLine size={28} />
              <h3>No plate boxes yet</h3>
              <p>
                Drag on the image to add one. A reviewed frame with no plates is a valid negative example.
              </p>
            </div>
          )}
          {frame.plates.map((p, i) => (
            <div className="plate-card" key={i}>
              <div className="plate-heading">
                <strong>
                  <span>{i + 1}</span> Plate annotation
                </strong>
                <button
                  aria-label={'Delete plate ' + (i + 1)}
                  onClick={() =>
                    updateFrame({ reviewed: false, plates: frame.plates.filter((_, j) => i !== j) })
                  }
                >
                  <Trash2 size={15} />
                </button>
              </div>
              <label className="field">
                Encounter ID
                <input
                  list="encounters"
                  value={p.encounter}
                  placeholder="e.g. vehicle-01"
                  onChange={(e) => updatePlate(i, { encounter: e.target.value })}
                />
              </label>
              <label className="field">
                Plate text
                <input
                  value={p.text}
                  placeholder="Only what you can read"
                  onChange={(e) => updatePlate(i, { text: e.target.value })}
                />
              </label>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={p.readable}
                  onChange={(e) => updatePlate(i, { readable: e.target.checked })}
                />{' '}
                Confidently readable
              </label>
              <label className="field">
                Sharpness
                <select
                  aria-label="Sharpness"
                  value={p.blur}
                  onChange={(e) => updatePlate(i, { blur: e.target.value })}
                >
                  {['unknown', 'sharp', 'blurred'].map((v) => (
                    <option key={v}>{v}</option>
                  ))}
                </select>
              </label>
              <small>
                {Math.round(p.box[2] - p.box[0])} × {Math.round(p.box[3] - p.box[1])} original pixels
              </small>
            </div>
          ))}
          {validation && (
            <p className="validation" role="alert">
              {validation}
            </p>
          )}
          <button
            className={'button review-mark ' + (frame.reviewed ? 'complete' : 'primary')}
            disabled={!ready}
            onClick={mark}
          >
            <CheckCircle2 size={17} />
            {frame.reviewed ? 'Reviewed · click to reopen' : 'Mark frame reviewed'}
          </button>
          <p className="footnote">
            {reviewed} / {data.frames.length} complete · changes save automatically
          </p>
        </aside>
      </div>
    </div>
  );
}

createRoot(document.getElementById('root')).render(<App />);

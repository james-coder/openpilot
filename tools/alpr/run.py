"""Offline ALPR study. Run with the isolated tools/alpr environment."""
import argparse
import csv
import hashlib
import html
import importlib.metadata
import json
import statistics
import time
from collections import Counter
from pathlib import Path

import av
import cv2
import numpy as np
import psutil


def sha256(path):
  with Path(path).open('rb') as f:
    return hashlib.file_digest(f, 'sha256').hexdigest()


def iou(a, b):
  intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
  area = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
  return intersection / area if area > 0 else 0.


def windows(width, height, tiled):
  if not tiled:
    return [(0, 0, width, height)]
  def starts(n):
    return sorted({*range(0, max(1, n - 639), 512), max(0, n - 640)})
  return [(x, y, min(x + 640, width), min(y + 640, height)) for y in starts(height) for x in starts(width)]


def detect(detector, frame, tiled):
  detections = []
  for x, y, x2, y2 in windows(frame.shape[1], frame.shape[0], tiled):
    for d in detector.predict(frame[y:y2, x:x2]):
      b = d.bounding_box
      box = [max(0, int(b.x1 + x)), max(0, int(b.y1 + y)),
             min(frame.shape[1], int(b.x2 + x)), min(frame.shape[0], int(b.y2 + y))]
      if box[2] > box[0] and box[3] > box[1]:
        detections.append({'box': box, 'detection_confidence': float(d.confidence)})
  kept = []
  for d in sorted(detections, key=lambda d: -d['detection_confidence']):
    if all(iou(d['box'], old['box']) < .4 for old in kept):
      kept.append(d)
  return kept


def assign_tracks(detections, active, frame, fps, next_id):
  active = {key: value for key, value in active.items() if frame - value['frame'] <= fps}
  used = set()
  for d in detections:
    matches = [(iou(d['box'], t['box']), key) for key, t in active.items() if key not in used]
    score, key = max(matches, default=(0, next_id))
    if score < .2:
      key = next_id
      next_id += 1
    used.add(key)
    d['track'] = key
    active[key] = {'box': d['box'], 'frame': frame}
  return active, next_id


def summarize(rows, threshold):
  groups = {}
  for row in rows:
    groups.setdefault(row['encounter'], []).append(row)
  result = []
  for encounter, observations in groups.items():
    votes = Counter()
    for row in observations:
      if row['text']:
        votes[row['text']] += row['ocr_confidence']
    text = votes.most_common(1)[0][0] if votes else ''
    matching = [r for r in observations if r['text'] == text]
    confidence = statistics.mean(r['ocr_confidence'] for r in matching) if matching else 0.
    best = max(observations, key=lambda r: r['quality'])
    result.append({'encounter': encounter, 'text': text, 'confidence': confidence,
                   'accepted': bool(text) and len(matching) >= 3 and confidence >= threshold,
                   'observations': len(observations), 'agreeing_frames': len(matching),
                   'crop': best['crop'], 'camera': best['camera'], 'segment': best['segment'],
                   'first_offset_seconds': observations[0]['offset_seconds'],
                   'last_offset_seconds': observations[-1]['offset_seconds']})
  return result


def write_report(out, tracks, stats):
  cells = []
  for t in tracks:
    e = {k: html.escape(str(v), quote=True) for k, v in t.items()}
    cells.append(f"""<article><img src="{e['crop']}"><p>{e['text'] or 'unreadable'} ({e['confidence']})</p>
      <small>{e['encounter']}<br>{'accepted candidate' if t['accepted'] else 'abstained'}</small></article>""")
  out.joinpath('report.html').write_text("""<!doctype html><meta charset="utf-8"><title>ALPR study</title>
      <style>body{font:16px sans-serif;max-width:1400px;margin:auto;background:#eee}
      section{display:flex;flex-wrap:wrap}article{background:white;padding:12px;margin:8px;width:260px;overflow-wrap:anywhere}
      img{max-width:250px;max-height:120px;image-rendering:pixelated}</style>
      <h1>ALPR candidates for verification</h1><p>Predictions are not ground truth.
      Confidence is a model score, not measured accuracy. Tracks can split across gaps and segment boundaries.</p>
      <pre>""" + html.escape(json.dumps(stats, indent=2)) + '</pre><section>' + ''.join(cells) + '</section>')



def main():
  p = argparse.ArgumentParser(description=__doc__)
  p.add_argument('dataset', type=Path)
  p.add_argument('--output', type=Path, required=True)
  p.add_argument('--model-cache', type=Path, default=Path('/mnt/algo14/comma3-alpr/models'))
  p.add_argument('--detector', default='yolo-v9-s-608-license-plate-end2end')
  p.add_argument('--ocr', default='cct-s-v2-global-model')
  p.add_argument('--provider', choices=['cuda', 'cpu'], default='cuda')
  p.add_argument('--sample-fps', type=float, default=5)
  p.add_argument('--tiled', action='store_true')
  p.add_argument('--threshold', type=float, default=.85, help='Exploratory threshold; freeze after labeled tuning')
  p.add_argument('--limit-segments', type=int, default=0)
  p.add_argument('--limit-frames', type=int, default=0)
  p.add_argument('--cameras', nargs='+', choices=['fcamera', 'ecamera'], default=['fcamera', 'ecamera'])
  args = p.parse_args()
  if not 0 < args.sample_fps <= 20 or not 0 <= args.threshold <= 1:
    p.error('sample fps must be in (0,20], threshold in [0,1]')
  args.output.mkdir(parents=True, exist_ok=True)
  args.model_cache.mkdir(parents=True, exist_ok=True)
  from fast_alpr import ALPR
  from fast_plate_ocr.inference import hub as ocr_hub
  from open_image_models.detection.core import hub as detector_hub
  import onnxruntime as ort
  ocr_hub.MODEL_CACHE_DIR = args.model_cache / 'ocr'
  detector_hub.MODEL_CACHE_DIR = args.model_cache / 'detector'
  if args.provider == 'cuda':
    ort.preload_dlls()
  providers = ['CUDAExecutionProvider', 'CPUExecutionProvider'] if args.provider == 'cuda' else ['CPUExecutionProvider']
  options = ort.SessionOptions()
  options.intra_op_num_threads = 2
  options.inter_op_num_threads = 1
  engine = ALPR(detector_model=args.detector, ocr_model=args.ocr,
                detector_providers=providers, ocr_providers=providers,
                detector_sess_options=options, ocr_sess_options=options)
  actual_providers = {'detector': engine.detector.detector.model.get_providers(),
                      'ocr': engine.ocr.ocr_model.model.get_providers()}
  if args.provider == 'cuda' and any('CUDAExecutionProvider' not in v for v in actual_providers.values()):
    raise RuntimeError(f'CUDA requested but a model fell back to CPU: {actual_providers}')
  manifest = json.loads((args.dataset / 'manifest.json').read_text())
  config = {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()}
  config['packages'] = {k: importlib.metadata.version(k) for k in ['fast-alpr', 'fast-plate-ocr', 'open-image-models', 'av', 'numpy']}
  config['execution_providers'] = actual_providers
  model_files = [detector_hub.download_model(args.detector), *ocr_hub.download_model(args.ocr)]
  config['model_sha256'] = {str(f.relative_to(args.model_cache)): sha256(f) for f in model_files}
  config_file = args.output / 'config.json'
  if config_file.exists() and json.loads(config_file.read_text()) != config:
    p.error('output contains a different run configuration; use a new output directory')
  config_file.write_text(json.dumps(config, indent=2) + '\n')
  segments = manifest['segments'][:args.limit_segments or None]
  all_rows, clip_stats = [], []
  process = psutil.Process()
  for segment in segments:
    name = segment['segment']
    folder = args.dataset / name
    index_path = folder / 'index.json'
    index = json.loads(index_path.read_text()) if index_path.exists() else {'frames': {}, 'clock_offset_ns': None}
    for camera in args.cameras:
      key = f'{name}/{camera}.hevc'
      if key not in manifest['verified']:
        print(f'Not yet verified: {key}', flush=True)
        continue
      video = folder / f'{camera}.hevc'
      if sha256(video) != manifest['verified'][key]:
        raise ValueError(f'input checksum changed: {video}')
      out = args.output / name / camera
      out.mkdir(parents=True, exist_ok=True)
      predictions = out / 'predictions.jsonl'
      done = out / 'complete.json'
      index_sha = sha256(index_path) if index_path.exists() else None
      previous = json.loads(done.read_text()) if done.exists() else {}
      if previous and previous.get('index_sha256') == index_sha and previous.get('source_sha256') == manifest['verified'][key]:
        all_rows.extend(json.loads(line) for line in predictions.read_text().splitlines())
        clip_stats.append(json.loads(done.read_text()))
        continue
      done.unlink(missing_ok=True)
      rows, active, next_id, latencies = [], {}, 0, []
      peak_rss = process.memory_info().rss
      begin = time.monotonic()
      timing = index.get('frames', {}).get(camera, {})
      offsets_known = sum(str(i) in timing for i in range(1200))
      print(f'Analyzing {key}', flush=True)
      with av.open(str(video)) as container, predictions.open('w') as output:
        sampled = 0
        for frame_index, raw_frame in enumerate(container.decode(video=0)):
          if args.limit_frames and frame_index >= args.limit_frames:
            break
          if frame_index / 20 + 1e-8 < sampled / args.sample_fps:
            continue
          sampled += 1
          frame = raw_frame.to_ndarray(format='bgr24')
          tick = time.monotonic()
          detections = detect(engine.detector, frame, args.tiled)
          active, next_id = assign_tracks(detections, active, frame_index, 20, next_id)
          # Context frames permit annotation of missed detections, not just OCR successes.
          if frame_index % 20 == 0:
            cv2.imwrite(str(out / f'context-{frame_index:06}.jpg'), frame)
          for detection in detections:
            x1, y1, x2, y2 = detection['box']
            crop = frame[y1:y2, x1:x2]
            prediction = engine.ocr.predict(crop)
            confidence = prediction.confidence if prediction else 0.
            if isinstance(confidence, list):
              confidence = statistics.mean(confidence) if confidence else 0.
            crop_path = out / f'{frame_index:06}-{detection["track"]:04}.png'
            cv2.imwrite(str(crop_path), crop)
            stamp = timing.get(str(frame_index), {})
            mono = stamp.get('mono_ns')
            wall = mono + index['clock_offset_ns'] if mono is not None and index['clock_offset_ns'] is not None else None
            row = {'segment': name, 'camera': camera, 'frame': frame_index, 'offset_seconds': frame_index / 20,
                   'frame_id': stamp.get('frame_id'), 'mono_ns': mono, 'wall_ns': wall,
                   'timing': 'recorded' if mono is not None else 'nominal_20fps',
                   'encounter': f'{name}/{camera}/{detection["track"]}', **detection,
                   'text': prediction.text if prediction else '', 'ocr_confidence': float(confidence),
                   'region': prediction.region if prediction else None,
                   'quality': float(cv2.Laplacian(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()) * crop.shape[0] * crop.shape[1],
                   'crop': str(crop_path.relative_to(args.output))}
            rows.append(row)
            output.write(json.dumps(row) + '\n')
          latencies.append(time.monotonic() - tick)
          peak_rss = max(peak_rss, process.memory_info().rss)
      stats = {'segment': name, 'camera': camera, 'sampled_frames': sampled, 'detections': len(rows),
               'elapsed_seconds': time.monotonic() - begin, 'peak_rss_bytes': peak_rss,
               'mean_inference_seconds': statistics.mean(latencies) if latencies else None,
               'p95_inference_seconds': float(np.quantile(latencies, .95)) if latencies else None,
               'indexed_frames': offsets_known, 'source_sha256': manifest['verified'][key], 'index_sha256': index_sha}
      done.write_text(json.dumps(stats, indent=2) + '\n')
      clip_stats.append(stats)
      all_rows.extend(rows)
  tracks = summarize(all_rows, args.threshold)
  (args.output / 'tracks.json').write_text(json.dumps(tracks, indent=2) + '\n')
  (args.output / 'stats.json').write_text(json.dumps(clip_stats, indent=2) + '\n')
  with (args.output / 'predictions.csv').open('w') as f:
    if all_rows:
      writer = csv.DictWriter(f, fieldnames=list(all_rows[0]))
      writer.writeheader()
      writer.writerows(all_rows)
  write_report(args.output, tracks, {'clips': len(clip_stats), 'candidate_tracks': len(tracks),
                                    'accepted_candidates': sum(t['accepted'] for t in tracks),
                                    'accuracy': 'not measured: independent labels required'})
  print(f'Wrote {len(tracks)} candidate tracks to {args.output}', flush=True)


if __name__ == '__main__':
  main()

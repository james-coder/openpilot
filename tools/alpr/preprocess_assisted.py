"""GPU vehicle/plate proposals and a close-first review queue; never alter human labels."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime
import importlib.metadata
import json
import os
from pathlib import Path
import statistics
import time

import av
import cv2
import numpy as np

from openpilot.tools.alpr.run import assign_tracks, detect, iou, sha256
from openpilot.tools.alpr.assisted_selection import illumination, associate_radar, score_sample



def main():
  p = argparse.ArgumentParser(description=__doc__)
  p.add_argument('data', type=Path)
  p.add_argument('--output', required=True, type=Path)
  args = p.parse_args()
  out = args.output
  out.mkdir(parents=True, exist_ok=True)
  if (out / 'reviews.json').exists():
    raise RuntimeError('This queue already has human reviews; use a new output directory for another experiment')
  cache = args.data / 'models'
  (cache / 'ultralytics-config').mkdir(parents=True, exist_ok=True)
  os.environ['YOLO_CONFIG_DIR'] = str(cache / 'ultralytics-config')
  from ultralytics import YOLO, settings
  settings.update({'sync': False})
  import torch
  torch.set_num_threads(4)
  if not torch.cuda.is_available():
    raise RuntimeError('This preprocessing run requires CUDA')
  vehicle_path = cache / 'vehicles/yolo11s.pt'
  vehicle_path.parent.mkdir(parents=True, exist_ok=True)
  vehicles = YOLO(str(vehicle_path))
  from fast_alpr import ALPR
  from fast_plate_ocr.inference import hub as ocr_hub
  from open_image_models.detection.core import hub as detector_hub
  import onnxruntime as ort
  ocr_hub.MODEL_CACHE_DIR = cache / 'ocr'
  detector_hub.MODEL_CACHE_DIR = cache / 'detector'
  ort.preload_dlls()
  options = ort.SessionOptions()
  options.intra_op_num_threads = 2
  options.inter_op_num_threads = 1
  providers = ['CUDAExecutionProvider', 'CPUExecutionProvider']
  engine = ALPR(detector_model='yolo-v9-s-608-license-plate-end2end', ocr_model='cct-s-v2-global-model',
                detector_providers=providers, ocr_providers=providers, detector_sess_options=options, ocr_sess_options=options)
  for model in [engine.detector.detector.model, engine.ocr.ocr_model.model]:
    if 'CUDAExecutionProvider' not in model.get_providers():
      raise RuntimeError('ALPR silently fell back to CPU')
  candidates = json.loads((out / 'candidates.json').read_text())
  all_rows = []
  stats = Counter()
  start = time.monotonic()
  gamma_lut = np.array([255 * (i / 255) ** (1 / 1.8) for i in range(256)], dtype=np.uint8)
  for clip in candidates['clips']:
    name, camera = clip['segment'], clip['camera']
    target = out / name / camera
    target.mkdir(parents=True, exist_ok=True)
    cached = target / 'proposals.json'
    if cached.exists():
      all_rows.extend(json.loads(cached.read_text()))
      continue
    wanted = {f['frame']: f for f in clip['frames']}
    rows, active, next_id = [], {}, 0
    video = args.data / '2026-09-study' / name / f'{camera}.hevc'
    if sha256(video) != clip['source_sha256']:
      raise RuntimeError(f'Source checksum mismatch: {video}')
    print(f'Preprocessing {name}/{camera}: {len(wanted)} candidate frames', flush=True)
    with av.open(str(video)) as container:
      for number, raw in enumerate(container.decode(video=0)):
        if number not in wanted:
          continue
        meta = wanted[number]
        frame = raw.to_ndarray(format='bgr24')
        stats['frames'] += 1
        result = vehicles.predict(frame, device=0, classes=[2, 3, 5, 7], imgsz=960, conf=.25, verbose=False)[0]
        vehicle_detections = [{'box': [int(v) for v in box], 'class': vehicles.names[int(cls)], 'confidence': float(conf)}
                              for box, cls, conf in zip(result.boxes.xyxy.cpu().tolist(), result.boxes.cls.cpu().tolist(),
                                                       result.boxes.conf.cpu().tolist(), strict=True)]
        active, next_id = assign_tracks(vehicle_detections, active, number, 20, next_id)
        for vehicle in vehicle_detections:
          x1, y1, x2, y2 = vehicle['box']
          if x2 - x1 < 140 or y2 - y1 < 90:
            continue
          x1, y1 = max(0, x1), max(0, y1)
          x2, y2 = min(frame.shape[1], x2), min(frame.shape[0], y2)
          detections = []
          for prior in meta['existing_plates']:
            b = prior['box']
            if x1 <= (b[0] + b[2]) / 2 <= x2 and y1 <= (b[1] + b[3]) / 2 <= y2:
              detections.append({'box': b, 'detection_confidence': prior['detection_confidence']})
          for detection in detect(engine.detector, frame[y1:y2, x1:x2], False):
            b = detection['box']
            detection['box'] = [b[0] + x1, b[1] + y1, b[2] + x1, b[3] + y1]
            if all(iou(detection['box'], d['box']) < .4 for d in detections):
              detections.append(detection)
          usable = []
          for d in detections:
            a, b, c, e = d['box']
            if c-a >= 40 and e-b >= 12 and 1.2 < (c-a)/(e-b) < 5 and (b+e)/2 > y1+.35*(y2-y1):
              usable.append(d)
          if not usable:
            continue
          d = max(usable, key=lambda d: d['detection_confidence'])
          a, b, c, e = d['box']
          crop = frame[b:e, a:c]
          grey = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
          readings = []
          for variant, pixels in [('original', crop), ('shadows_lifted', cv2.LUT(crop, gamma_lut))]:
            prediction = engine.ocr.predict(pixels)
            confidence = prediction.confidence if prediction else 0
            if isinstance(confidence, list):
              confidence = statistics.mean(confidence) if confidence else 0
            readings.append({'variant': variant, 'text': prediction.text if prediction else '', 'confidence': float(confidence)})
          ident = f'{name}/{camera}/vehicle-{vehicle["track"]:03}'
          image_name = f'frame-{number:06}.png'
          if not (target / image_name).exists():
            cv2.imwrite(str(target / image_name), frame)
          crop_name = f'plate-{number:06}-{vehicle["track"]:03}.png'
          cv2.imwrite(str(target / crop_name), crop)
          row = {'id': f'{ident}/{number}', 'encounter': ident, 'segment': name, 'camera': camera, 'frame': number,
                 'image': str((target / image_name).relative_to(args.data)),
                 'crop': str((target / crop_name).relative_to(args.data)), 'box': d['box'], 'vehicle_box': vehicle['box'],
                 'vehicle_class': vehicle['class'], 'vehicle_confidence': vehicle['confidence'],
                 'width': c-a, 'height': e-b, 'sharpness': round(float(cv2.Laplacian(grey, cv2.CV_64F).var()), 2),
                 'detection_confidence': d['detection_confidence'], 'ocr': readings,
                 'radar': associate_radar(meta['radar'], d['box'], camera),
                 'lighting': illumination(meta['wall_ns'], float(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).mean()))}
          rows.append(row)
          stats['plate_observations'] += 1
    cached.write_text(json.dumps(rows, indent=2) + '\n')
    all_rows.extend(rows)
  grouped = defaultdict(list)
  for row in all_rows:
    grouped[row['encounter']].append(row)
  encounters = []
  for ident, rows in grouped.items():
    # Keep a handful of distinct moments, with the sharpest view first.
    ordered = sorted(rows, key=score_sample, reverse=True)
    selected = []
    for row in ordered:
      if all(abs(row['frame'] - s['frame']) >= 4 for s in selected):
        selected.append(row)
      if len(selected) == 8:
        break
    best = selected[0]
    near = [r['radar']['range_m'] for r in rows if r['radar']]
    votes = Counter()
    for r in rows:
      for o in r['ocr']:
        if o['confidence'] >= .75 and o['text']:
          votes[o['text']] += o['confidence']
    text = votes.most_common(1)[0][0] if votes else ''
    close = best['radar'] is not None and 3 <= best['radar']['range_m'] <= 8 and best['width'] >= 75
    encounters.append({'id': ident, 'samples': selected, 'observation_count': len(rows),
                       'suggested_text': text, 'near_range_m': min(near) if near else None,
                       'tier': 'close' if close else 'large' if best['width'] >= 75 else 'explore',
                       'score': score_sample(best), 'auto_vehicle_id': ident})
  encounters.sort(key=lambda e: ({'close': 0, 'large': 1, 'explore': 2}[e['tier']], -e['score']))
  queue = {'version': 1, 'dataset_id': 'assisted-v1', 'created_at': datetime.now().isoformat(),
           'encounters': encounters, 'stats': {**stats, 'all_observations': len(all_rows), 'encounters': len(encounters),
             'close_encounters': sum(e['tier'] == 'close' for e in encounters), 'elapsed_seconds': time.monotonic()-start},
           'provenance': {'vehicle_model': 'yolo11s.pt', 'vehicle_model_sha256': sha256(vehicle_path),
                          'candidate_manifest_sha256': sha256(out / 'candidates.json'),
                          'plate_source_config_sha256': sha256(args.data / 'runs/s-native-5fps/config.json'),
                          'vehicle_license': 'Ultralytics AGPL-3.0; local research use',
                          'packages': {p: importlib.metadata.version(p) for p in ['ultralytics', 'torch', 'fast-alpr', 'onnxruntime-gpu']},
                          'device': torch.cuda.get_device_name(0), 'assisted': True,
                          'association_limit': 'Vehicle IDs are local visual tracks; radar association is approximate, not calibrated.'}}
  (out / 'queue.json').write_text(json.dumps(queue, indent=2) + '\n')
  print(json.dumps(queue['stats'], indent=2), flush=True)


if __name__ == '__main__':
  main()

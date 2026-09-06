"""Offline gyro-guided blur sensitivity experiment, not calibrated image recovery.

Uses recorded angular velocity, nominal narrow-camera geometry and assumed exposure
durations. No transcript, OCR score, or learned restoration enters the calculation.
"""
import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

from openpilot.tools.alpr.fuse_burst import fourier_accumulate, match_tones


def rotational_flow(raw_gyro, pixel, focal=2648., center=(964., 604.)):
  # locationd: sensor -> device [-z,-y,-x]; device -> camera view [y,z,x].
  wx, wy, wz = -np.asarray(raw_gyro)[[1, 0, 2]]
  x, y = (np.asarray(pixel)-center)/focal
  return focal * np.array([x*y*wx-(1+x*x)*wy+y*wz, (1+y*y)*wx-x*y*wy-x*wz])


def motion_kernel(flow, exposure_ms):
  displacement = np.asarray(flow)*exposure_ms/1000
  size = int(np.ceil(np.max(np.abs(displacement))))+5
  size += 1-size % 2
  kernel = np.zeros((size, size), np.float64)
  # Bilinear splatting keeps subpixel motion, with a centered symmetric exposure.
  for point in np.linspace(-.5, .5, 101)[:, None]*displacement + size//2:
    x, y = point
    ix, iy = int(x), int(y)
    for dx, dy in [(0, 0), (1, 0), (0, 1), (1, 1)]:
      kernel[iy+dy, ix+dx] += (1-abs(x-ix-dx))*(1-abs(y-iy-dy))
  return kernel/kernel.sum()


def deconvolve(image, kernel, regularization=.04):
  pad = max(32, kernel.shape[0])
  pixels = np.pad(image.astype(float), ((pad, pad), (pad, pad), (0, 0)), mode='reflect')
  psf = np.zeros(pixels.shape[:2])
  h, w = kernel.shape
  psf[:h, :w] = kernel
  psf = np.roll(psf, (-(h//2), -(w//2)), axis=(0, 1))
  transfer = np.fft.fft2(psf)
  inverse = transfer.conj()/(np.abs(transfer)**2+regularization)*(1+regularization)
  result = np.fft.ifft2(np.fft.fft2(pixels, axes=(0, 1))*inverse[..., None], axes=(0, 1)).real
  return np.clip(result[pad:-pad, pad:-pad], 0, 255).astype(np.float32)


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('data', type=Path)
  parser.add_argument('--encounter', required=True)
  args = parser.parse_args()
  base = args.data
  folder = base/'assisted-v1/fusion'/hashlib.sha256(args.encounter.encode()).hexdigest()[:12]
  audit_path = folder/'imu-audit.json'
  audit = json.loads(audit_path.read_text())
  queue = json.loads((base/'assisted-v1/queue.json').read_text())
  encounter = next(e for e in queue['encounters'] if e['id'] == args.encounter)
  if any(s['camera'] != 'fcamera' for s in encounter['samples']):
    raise ValueError('Only the nominal narrow-camera geometry is supported')
  report_path = folder/'report.json'
  report = json.loads(report_path.read_text())
  index = json.loads((base/'2026-09-study'/encounter['samples'][0]['segment']/'index.json').read_text())
  reference = encounter['samples'][0]
  x1, y1, x2, y2 = reference['box']
  px, py = round((x2-x1)*.2), round((y2-y1)*.25)
  shape = (x2-x1+2*px, y2-y1+2*py)
  rows = []
  for sample in encounter['samples']:
    src = next(s for s in report['sources'] if s['sample_id'] == sample['id'])
    if not src['used']:
      continue
    image_path = base/sample['image']
    if hashlib.sha256(image_path.read_bytes()).hexdigest() != src['source_sha256']:
      raise ValueError('Source image changed since the alignment report')
    t = index['frames']['fcamera'][str(sample['frame'])]['mono_ns']
    camera = next(c for c in audit['camera'] if c['frame_id'] == index['frames']['fcamera'][str(sample['frame'])]['frame_id'])
    if camera['sensor'] != 'ox03c10':
      raise ValueError('This experiment only supports the audited OX03C10 recording')
    gyro = [g['values'] for g in audit['gyroscope'] if abs(g['mono_ns']-t) <= 20_000_000]
    if len(gyro) < 2:
      raise ValueError('Insufficient local gyroscope samples')
    box = sample['box']
    flow = rotational_flow(np.mean(gyro, axis=0), [(box[0]+box[2])/2, (box[1]+box[3])/2])
    a, b, c, d = src['bounds']
    patch = cv2.imread(str(image_path))[b:d, a:c]
    rows.append((sample, patch, flow, camera))
  methods, evidence = [], []
  for exposure in [1, 2, 4, 8, 12]:
    aligned = []
    for sample, patch, flow, camera in rows:
      restored = deconvolve(patch, motion_kernel(flow, exposure))
      restored = cv2.resize(restored, shape, interpolation=cv2.INTER_CUBIC)
      if not aligned:
        aligned.append(restored)
      else:
        # Reuse transforms fitted to original pixels, never fit to a preferred transcript.
        src = next(s for s in report['sources'] if s['sample_id'] == sample['id'])
        matrix = np.asarray(src['homography'], np.float32)
        flags = cv2.INTER_CUBIC | cv2.WARP_INVERSE_MAP
        moved = cv2.warpPerspective(restored, matrix, shape, flags=flags, borderMode=cv2.BORDER_REFLECT_101)
        valid = cv2.warpPerspective(np.ones(restored.shape[:2], np.uint8), matrix, shape,
                                    flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP) > 0
        moved = match_tones(moved, aligned[0], valid)
        moved[~valid] = aligned[0][~valid]
        aligned.append(moved)
      if exposure == 1:
        evidence.append({'frame': sample['frame'], 'predicted_flow_px_s': flow.tolist(),
                         'integ_lines': camera['integ_lines'], 'gyro_sample_window_ms': 40})
    result = fourier_accumulate(aligned)
    crop = result[py:py+y2-y1, px:px+x2-x1]
    target = folder/f'gyro-{exposure}ms.png'
    cv2.imwrite(str(target), np.rint(crop).astype(np.uint8))
    methods.append({'id': f'gyro_{exposure}', 'label': f'Gyro-guided combination · assumed {exposure} ms',
                    'image': str(target.relative_to(base))})
  experiment = {
    'calibrated': False, 'assumed_exposure_ms': [1, 2, 4, 8, 12], 'regularization': .04,
    'imu_sha256': hashlib.sha256(audit_path.read_bytes()).hexdigest(), 'frames': evidence,
    'limitations': 'Nominal camera axes and focal length; local constant rotation. Exposure timing, HDR weights, ' +
                  'gyro bias, rolling shutter and target translation are not calibrated. Ringing can resemble characters. ' +
                  'These are sensitivity tests, not recovered ground truth.',
    'method': 'Recorded gyro -> local motion kernel -> regularized inverse filter per frame -> original alignment -> Fourier combination',
  }
  report['methods'] = [m for m in report['methods'] if not m['id'].startswith('gyro_')]+methods
  report['gyro_experiment'] = experiment
  report['imu_summary'] = audit['summary']
  report_path.write_text(json.dumps(report, indent=2)+'\n')
  catalogue_path = base/'assisted-v1/fusion/catalogue.json'
  catalogue = json.loads(catalogue_path.read_text())
  catalogue[args.encounter] = report
  temp = catalogue_path.with_suffix('.tmp')
  temp.write_text(json.dumps(catalogue, indent=2)+'\n')
  temp.replace(catalogue_path)
  cards = []
  for method in [report['methods'][0], *methods]:
    img = cv2.imread(str(base/method['image']))
    img = np.uint8(np.clip(255*(img.astype(float)/255)**(1/1.6)*1.08, 0, 255))
    card = np.full((280, 640, 3), 245, np.uint8)
    card[32:276, 40:596] = cv2.resize(img, (556, 244), interpolation=cv2.INTER_CUBIC)
    cv2.putText(card, method['label'].replace('·', '-'), (12, 22), cv2.FONT_HERSHEY_SIMPLEX, .55, (30, 30, 30), 1)
    cards.append(card)
  cv2.imwrite(str(folder/'gyro-comparison.png'), np.vstack(cards))
  print(json.dumps(experiment, indent=2))


if __name__ == '__main__':
  main()

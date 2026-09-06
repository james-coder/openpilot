"""Classical plate-burst alignment and fusion. No learned restoration or OCR-guided fitting."""
import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np


def fourier_accumulate(images, power=2):
  """Conservative Fourier burst accumulation on registered, photometrically matched images."""
  stack = np.asarray(images, np.float32)
  if stack.ndim != 4 or not len(stack):
    raise ValueError('Expected a nonempty NxHxWxC image array')
  h, w = stack.shape[1:3]
  pad = 24
  padded = np.pad(stack, ((0, 0), (pad, pad), (pad, pad), (0, 0)), mode='reflect')
  spectra = np.fft.fft2(padded, axes=(1, 2))
  # Smooth magnitude across frequencies to avoid selecting isolated noise peaks.
  magnitude = np.abs(spectra).mean(axis=3)
  smooth = np.stack([cv2.GaussianBlur(m.astype(np.float32), (0, 0), 1.2) for m in magnitude])
  weights = np.maximum(smooth, 1e-6) ** power
  weights /= weights.sum(axis=0, keepdims=True)
  result = np.fft.ifft2((spectra * weights[..., None]).sum(axis=0), axes=(0, 1)).real
  return np.clip(result[pad:pad+h, pad:pad+w], 0, 255).astype(np.float32)


def register(template, moving):
  h, w = template.shape[:2]
  reference_grey = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255
  moving_grey = cv2.cvtColor(moving, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255
  # Affine initializes the subsequent projective refinement.
  affine = np.eye(2, 3, dtype=np.float32)
  criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 600, 1e-6)
  cc, affine = cv2.findTransformECC(reference_grey, moving_grey, affine, cv2.MOTION_AFFINE, criteria, None, 5)
  matrix = np.vstack([affine, [0, 0, 1]]).astype(np.float32)
  try:
    cc, matrix = cv2.findTransformECC(reference_grey, moving_grey, matrix.copy(), cv2.MOTION_HOMOGRAPHY, criteria, None, 5)
  except cv2.error:
    pass
  corners = np.array([[[0., 0.], [w-1., 0.], [w-1., h-1.], [0., h-1.]]], np.float32)
  warped = cv2.perspectiveTransform(corners, matrix)[0]
  displacement = np.linalg.norm((warped - corners[0]) / [w, h], axis=1).max()
  if not np.isfinite(matrix).all() or cc < .65 or displacement > .35:
    raise ValueError(f'Alignment rejected: correlation={cc:.3f}, relative displacement={displacement:.3f}')
  flags = cv2.INTER_CUBIC | cv2.WARP_INVERSE_MAP
  aligned = cv2.warpPerspective(moving.astype(np.float32), matrix, (w, h), flags=flags, borderMode=cv2.BORDER_REFLECT_101)
  valid = cv2.warpPerspective(np.ones((h, w), np.uint8), matrix, (w, h), flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP) > 0
  return aligned, valid, float(cc), matrix.tolist()


def match_tones(image, reference, valid):
  result = image.copy()
  for c in range(3):
    src, dst = image[:, :, c][valid], reference[:, :, c][valid]
    src_lo, src_hi = np.percentile(src, [10, 90])
    dst_lo, dst_hi = np.percentile(dst, [10, 90])
    gain = np.clip((dst_hi-dst_lo) / max(1, src_hi-src_lo), .75, 1.35)
    result[:, :, c] = np.clip((result[:, :, c]-np.median(src))*gain+np.median(dst), 0, 255)
  return result


def main():
  p = argparse.ArgumentParser(description=__doc__)
  p.add_argument('data', type=Path)
  p.add_argument('--encounter', required=True)
  args = p.parse_args()
  queue = json.loads((args.data / 'assisted-v1/queue.json').read_text())
  encounter = next(e for e in queue['encounters'] if e['id'] == args.encounter)
  samples = encounter['samples']
  reference = samples[0]
  folder = args.data / 'assisted-v1/fusion' / hashlib.sha256(encounter['id'].encode()).hexdigest()[:12]
  folder.mkdir(parents=True, exist_ok=True)
  a, b, c, d = reference['box']
  padx, pady = round((c-a)*.2), round((d-b)*.25)
  width, height = c-a+2*padx, d-b+2*pady
  patches, source_info = [], []
  for sample in samples:
    image_path = args.data / sample['image']
    image = cv2.imread(str(image_path))
    x1, y1, x2, y2 = sample['box']
    px, py = round((x2-x1)*.2), round((y2-y1)*.25)
    bounds = [x1-px, y1-py, x2+px, y2+py]
    info = {'frame': sample['frame'], 'sample_id': sample['id'], 'image': sample['image'],
            'source_sha256': hashlib.sha256(image_path.read_bytes()).hexdigest(), 'bounds': bounds}
    if bounds[0] < 0 or bounds[1] < 0 or bounds[2] > image.shape[1] or bounds[3] > image.shape[0]:
      info.update(used=False, reason='Plate or registration margin touches the edge of the frame')
      patches.append(None)
    else:
      patch = image[bounds[1]:bounds[3], bounds[0]:bounds[2]]
      patches.append(cv2.resize(patch, (width, height), interpolation=cv2.INTER_CUBIC).astype(np.float32))
    source_info.append(info)
  if patches[0] is None:
    raise ValueError('Reference crop is clipped')
  ref = patches[0]
  aligned, masks = [ref], [np.ones((height, width), bool)]
  source_info[0].update(used=True, correlation=1., homography=np.eye(3).tolist())
  for i, patch in enumerate(patches[1:], 1):
    if patch is None:
      continue
    try:
      image, mask, cc, matrix = register(ref, patch)
      plate_mask = mask[pady:pady+d-b, padx:padx+c-a]
      if plate_mask.mean() < .98:
        raise ValueError('Insufficient valid plate overlap')
      image = match_tones(image, ref, mask)
      # Only a common observed area may contribute to Fourier fusion.
      aligned.append(image)
      masks.append(mask)
      source_info[i].update(used=True, correlation=cc, homography=matrix)
      cv2.imwrite(str(folder / f'aligned-{samples[i]["frame"]}.png'), image.astype(np.uint8))
    except (cv2.error, ValueError) as e:
      source_info[i].update(used=False, reason=str(e).split('\n')[0])
  if len(aligned) < 2:
    raise RuntimeError('Not enough alignable frames for a fusion comparison')
  stack = np.stack(aligned)
  common = np.logical_and.reduce(masks)
  # Invalid perimeter is copied from the reference, never treated as measured black pixels.
  stack[:, ~common] = ref[~common]
  mean = np.mean(stack, axis=0)
  median = np.median(stack, axis=0)
  frequency = fourier_accumulate(stack)
  # A restrained unsharp view is labeled separately from the actual combined image.
  sharpened = np.clip(frequency + .6*(frequency-cv2.GaussianBlur(frequency, (0, 0), .85)), 0, 255)
  methods = []
  for key, label, pixels in [('reference', 'Best individual frame', ref), ('average', 'Aligned average', mean),
                              ('median', 'Aligned median', median), ('fourier', 'Frequency-weighted combination', frequency),
                              ('sharpened', 'Combined + mild sharpening', sharpened)]:
    crop = pixels[pady:pady+d-b, padx:padx+c-a]
    file = folder / f'{key}.png'
    cv2.imwrite(str(file), np.rint(crop).astype(np.uint8))
    methods.append({'id': key, 'label': label, 'image': str(file.relative_to(args.data))})
  report = {'encounter_id': encounter['id'], 'reference_sample_id': reference['id'], 'input_frames': len(samples),
            'used_frames': len(aligned), 'sources': source_info, 'methods': methods,
            'native_width': c-a, 'native_height': d-b, 'generative': False,
            'description': 'Projective alignment, tone matching and classical burst combination; no OCR or learned restoration guides the pixels.',
            'limitation': 'Motion blur is shared across these frames. Combination may reduce noise but does not establish the missing characters.',
            'source': 'https://openaccess.thecvf.com/content_cvpr_2015/papers/Delbracio_Burst_Deblurring_Removing_2015_CVPR_paper.pdf'}
  if (folder / 'imu-audit.json').exists():
    report['imu_summary'] = json.loads((folder / 'imu-audit.json').read_text())['summary']
  (folder / 'report.json').write_text(json.dumps(report, indent=2)+'\n')
  catalogue_path = args.data / 'assisted-v1/fusion/catalogue.json'
  catalogue = json.loads(catalogue_path.read_text()) if catalogue_path.exists() else {}
  catalogue[encounter['id']] = report
  temp = catalogue_path.with_suffix('.tmp')
  temp.write_text(json.dumps(catalogue, indent=2)+'\n')
  temp.replace(catalogue_path)
  # Diagnostic contact sheet: consistent tone transform for all methods and source crops.
  cards = []
  for method in methods:
    pixels = cv2.imread(str(args.data / method['image']))
    pixels = np.uint8(np.clip(255*(pixels.astype(float)/255)**(1/1.6)*1.08, 0, 255))
    card = np.full((280, 640, 3), 245, np.uint8)
    resized = cv2.resize(pixels, (556, 244), interpolation=cv2.INTER_CUBIC)
    card[32:276, 40:596] = resized
    cv2.putText(card, method['label'], (12, 22), cv2.FONT_HERSHEY_SIMPLEX, .6, (30, 30, 30), 1)
    cards.append(card)
  cv2.imwrite(str(folder / 'comparison.png'), np.vstack(cards))
  print(json.dumps({'used':len(aligned),'inputs':len(samples),'sources':source_info,'folder':str(folder)},indent=2))


if __name__ == '__main__':
  main()

"""Evaluate independently reviewed frames; unreviewed frames never become negatives."""
import argparse
import hashlib
import json
import math
import re
from collections import defaultdict
from pathlib import Path


def split_for_route(segment):
  route = segment.rsplit('--', 1)[0]
  return 'tune' if int(hashlib.sha256(route.encode()).hexdigest()[:8], 16) % 5 == 0 else 'test'


def normalize(text):
  # Ignore separators/case, never substitute ambiguous characters (O/0, I/1).
  return re.sub(r'[^A-Z0-9]', '', text.upper())


def overlap(a, b):
  intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
  union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
  return intersection / union if union > 0 else 0


def rate(successes, total):
  if not total:
    return {'numerator': successes, 'denominator': 0, 'rate': None, 'wilson_95': None}
  p, z = successes / total, 1.959963984540054
  mid = (p + z*z / (2*total)) / (1 + z*z / total)
  half = z * math.sqrt(p*(1-p) / total + z*z / (4*total*total)) / (1 + z*z / total)
  return {'numerator': successes, 'denominator': total, 'rate': p, 'wilson_95': [mid-half, mid+half]}


def evaluate(labels, rows, tracks, split='test'):
  predictions = defaultdict(list)
  for row in rows:
    predictions[(row['segment'], row['camera'], row['frame'])].append(row)
  counts = defaultdict(lambda: defaultdict(int))
  truth, track_truth, reviewed_tracks = {}, defaultdict(set), set()
  reviewed = 0
  seen_frames = set()
  for frame in labels['frames']:
    if not frame['reviewed'] or (split != 'all' and split_for_route(frame['segment']) != split):
      continue
    key = (frame['segment'], frame['camera'], frame['frame'])
    if key in seen_frames:
      raise ValueError(f'duplicate reviewed frame: {key}')
    seen_frames.add(key)
    reviewed += 1
    candidates = predictions[key]
    used = set()
    for plate in frame['plates']:
      if len(plate['box']) != 4 or plate['box'][2] <= plate['box'][0] or plate['box'][3] <= plate['box'][1]:
        raise ValueError('invalid ground-truth box')
      if not plate['encounter']:
        raise ValueError('each plate needs a human-assigned encounter ID')
      encounter = frame['segment'].rsplit('--', 1)[0] + '/' + plate['encounter']
      text = normalize(plate['text'])
      if plate['readable'] and not text:
        raise ValueError('readable plate needs independently transcribed text')
      if plate['readable']:
        if encounter in truth and truth[encounter] != text:
          raise ValueError(f'inconsistent text for encounter {encounter}')
        truth[encounter] = text
      width = plate['box'][2] - plate['box'][0]
      groups = ['all', 'camera:' + frame['camera'], 'lighting:' + frame.get('lighting', 'unknown'),
                'width:' + ('<40' if width < 40 else '40-79' if width < 80 else '>=80'),
                'blur:' + plate.get('blur', 'unknown')]
      matches = [(overlap(plate['box'], row['box']), i) for i, row in enumerate(candidates) if i not in used]
      score, idx = max(matches, default=(0, -1))
      found = score >= .5
      exact = False
      if found:
        used.add(idx)
        row = candidates[idx]
        track_truth[row['encounter']].add(encounter)
        exact = plate['readable'] and normalize(row['text']) == text
      for group in groups:
        c = counts[group]
        c['plates'] += 1
        c['detected'] += found
        c['readable'] += plate['readable']
        c['exact'] += exact
        c['readable_detected'] += found and plate['readable']
    for row in candidates:
      reviewed_tracks.add(row['encounter'])
    counts['all']['false_detections'] += len(candidates) - len(used)
    counts['all']['predictions'] += len(candidates)
  encounter_candidates = defaultdict(list)
  accepted = false_accepted = abstained = ambiguous = 0
  for track in tracks:
    tid = track['encounter']
    if tid not in reviewed_tracks:
      continue
    matches = track_truth[tid]
    if len(matches) > 1 or (matches and next(iter(matches)) not in truth):
      ambiguous += 1
      continue
    if not track['accepted']:
      abstained += 1
    else:
      accepted += 1
      expected = truth[next(iter(matches))] if matches else None
      false_accepted += normalize(track['text']) != expected
      for encounter in matches:
        encounter_candidates[encounter].append(normalize(track['text']))
  # Conservative: an encounter is correct only if it has a read and no conflicting accepted reads.
  correct = sum(bool(encounter_candidates[e]) and set(encounter_candidates[e]) == {t} for e, t in truth.items())
  return {'split': split, 'reviewed_frames': reviewed, 'independent_readable_encounters': len(truth),
          'target_200_met': len(truth) >= 200,
          'frame_metrics': {g: {'detection_recall': rate(c['detected'], c['plates']),
                                'end_to_end_exact': rate(c['exact'], c['readable']),
                                'ocr_exact_given_detection': rate(c['exact'], c['readable_detected'])} for g, c in counts.items()},
          'false_detections_on_reviewed_frames': counts['all']['false_detections'],
          'encounter_exact': rate(correct, len(truth)),
          'false_accepted_tracks': rate(false_accepted, accepted),
          'track_abstention': rate(abstained, abstained + accepted),
          'tracks_excluded_ambiguous_or_unreadable': ambiguous,
          'limitations': ['Frame observations are correlated; frame Wilson intervals are descriptive.',
                         'Encounter intervals can still be optimistic when vehicles recur across routes.',
                         'Only exhaustively reviewed frames contribute; unavailable conditions remain unmeasured.']}


def main():
  p = argparse.ArgumentParser(description=__doc__)
  p.add_argument('run', type=Path)
  p.add_argument('labels', type=Path)
  p.add_argument('--split', choices=['tune', 'test', 'all'], default='test')
  p.add_argument('--output', type=Path, required=True)
  args = p.parse_args()
  paths = sorted(p for p in args.run.glob('*/*/predictions.jsonl') if p.with_name('complete.json').exists())
  if not paths and (args.run / 'predictions.jsonl').exists():
    paths = [args.run / 'predictions.jsonl']
  rows = [json.loads(line) for path in paths for line in path.read_text().splitlines()]
  result = evaluate(json.loads(args.labels.read_text()), rows,
                    json.loads((args.run / 'tracks.json').read_text()), args.split)
  args.output.write_text(json.dumps(result, indent=2) + '\n')
  print(json.dumps(result, indent=2))


if __name__ == '__main__':
  main()

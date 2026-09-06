"""Run independent PP-OCRv6 recognition on exactly the primary run's plate crops."""
import argparse
import importlib.metadata
import json
import os
import statistics
import time
from pathlib import Path

from openpilot.tools.alpr.run import sha256, summarize, write_report


def main():
  p = argparse.ArgumentParser(description=__doc__)
  p.add_argument('run', type=Path)
  p.add_argument('--output', required=True, type=Path)
  p.add_argument('--model-cache', type=Path, default=Path('/mnt/algo14/comma3-alpr/models/paddle'))
  p.add_argument('--limit-crops', type=int, default=0)
  args = p.parse_args()
  if args.output.exists() and any(args.output.iterdir()):
    p.error('use an empty output directory')
  os.environ['PADDLE_PDX_CACHE_HOME'] = str(args.model_cache)
  os.environ['PADDLE_HOME'] = str(args.model_cache / 'paddle-home')
  os.environ['HF_HOME'] = str(args.model_cache / 'huggingface')
  from paddleocr import TextRecognition
  model = TextRecognition(model_name='PP-OCRv6_medium_rec', device='cpu', cpu_threads=4)
  args.output.mkdir(parents=True, exist_ok=True)
  rows, latencies = [], []
  sources = sorted(p for p in args.run.glob('*/*/predictions.jsonl') if p.with_name('complete.json').exists())
  with (args.output / 'predictions.jsonl').open('w') as output:
    for path in sources:
      for line in path.read_text().splitlines():
        if args.limit_crops and len(rows) >= args.limit_crops:
          break
        row = json.loads(line)
        crop = (args.run / row['crop']).resolve()
        tick = time.monotonic()
        result = next(iter(model.predict(input=str(crop), batch_size=1)))
        latencies.append(time.monotonic() - tick)
        row.update(text=str(result['rec_text']), ocr_confidence=float(result['rec_score']),
                   crop=os.path.relpath(crop, args.output), region=None)
        rows.append(row)
        output.write(json.dumps(row) + '\n')
  config = json.loads((args.run / 'config.json').read_text())
  tracks = summarize(rows, config['threshold'])
  stats = {'engine': 'PP-OCRv6_medium_rec', 'device': 'cpu', 'crops': len(rows),
           'mean_ocr_seconds': statistics.mean(latencies) if latencies else None,
           'accuracy': 'not measured: independent labels required',
           'comparison_scope': 'OCR on shared detections; this does not compare detection recall'}
  weights = args.model_cache / 'official_models' / 'PP-OCRv6_medium_rec'
  config = {'source_run_config_sha256': sha256(args.run / 'config.json'),
            'source_predictions_sha256': {str(f.relative_to(args.run)): sha256(f) for f in sources},
            'packages': {k: importlib.metadata.version(k) for k in ['paddleocr', 'paddlepaddle', 'paddlex']},
            'model_sha256': {str(f.relative_to(weights)): sha256(f) for f in weights.rglob('*') if f.is_file()},
            'threshold': config['threshold'], **stats}
  (args.output / 'config.json').write_text(json.dumps(config, indent=2) + '\n')
  (args.output / 'tracks.json').write_text(json.dumps(tracks, indent=2) + '\n')
  (args.output / 'stats.json').write_text(json.dumps(stats, indent=2) + '\n')
  write_report(args.output, tracks, stats)
  print(json.dumps(stats, indent=2))


if __name__ == '__main__':
  main()

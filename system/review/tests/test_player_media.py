import os

import numpy as np
import pytest
import zstandard

from openpilot.system.review.player import media
from openpilot.system.review.player.media import MediaError, index_video, read_qlog, read_video, to_rgb
from openpilot.system.review.tests.player_helpers import FPS, frame_red, make_qcamera, make_qlog, make_segment


@pytest.fixture(scope='module')
def clip(tmp_path_factory):
  path = tmp_path_factory.mktemp('clip') / 'qcamera.ts'
  make_qcamera(path, frames=30, gop=10)
  return path


def decoded(index, first):
  return [(i, to_rgb(f)) for i, f in read_video(index, first)]


def red_of(rgb):
  return float(rgb[100:200, 100:200, 0].mean())


def test_index_matches_the_synthetic_clip(clip):
  ix = index_video(clip)
  assert ix.count == 30 and (ix.width, ix.height) == (526, 330)
  assert ix.frame_dur == pytest.approx(1 / FPS)
  assert ix.times[0] == 0.0 and ix.times[-1] == pytest.approx(29 / FPS) and ix.duration == pytest.approx(30 / FPS)
  assert np.all(np.diff(ix.times) > 0)
  assert [i for i, k in enumerate(ix.packet_key) if k] == [0, 10, 20]


def test_decode_from_the_start_yields_every_frame_in_order_with_the_right_picture(clip):
  ix = index_video(clip)
  frames = decoded(ix, 0)
  assert [i for i, _ in frames] == list(range(30))
  for i, rgb in frames:
    assert rgb.shape == (330, 526, 3) and rgb.dtype == np.uint8 and rgb.flags['C_CONTIGUOUS']
    assert abs(red_of(rgb) - frame_red(i)) < 8, i


@pytest.mark.parametrize('target,keyframe', [(0, 0), (9, 0), (10, 10), (17, 10), (20, 20), (29, 20)])
def test_seeking_starts_at_the_keyframe_at_or_before_the_target_and_finds_the_target(clip, target, keyframe):
  ix = index_video(clip)
  assert ix.start_frame_for(target) == keyframe
  frames = decoded(ix, target)
  assert frames[0][0] == keyframe and frames[-1][0] == 29
  by_index = dict(frames)
  assert abs(red_of(by_index[target]) - frame_red(target)) < 8


def test_stepping_through_every_frame_pair_is_consistent(clip):
  ix = index_video(clip)
  full = {i: red_of(rgb) for i, rgb in decoded(ix, 0)}
  for start in range(0, 30, 4):
    part = {i: red_of(rgb) for i, rgb in decoded(ix, start)}
    for i, v in part.items():
      assert abs(v - full[i]) < 2


def test_closing_the_reader_early_releases_the_file(clip):
  ix = index_video(clip)
  it = read_video(ix, 0)
  next(it)
  it.close()       # must not raise or leave the generator running
  assert list(read_video(ix, 25))[0][0] == 20


def test_zero_length_missing_and_garbage_files_are_media_errors(tmp_path):
  empty = tmp_path / 'qcamera.ts'
  empty.write_bytes(b'')
  with pytest.raises(MediaError, match='empty'):
    index_video(empty)
  with pytest.raises(MediaError, match='missing'):
    index_video(tmp_path / 'nope.ts')
  junk = tmp_path / 'junk.ts'
  junk.write_bytes(os.urandom(5000))
  with pytest.raises(MediaError):
    index_video(junk)
  text = tmp_path / 'text.ts'
  text.write_text('this is not a video ' * 200)
  with pytest.raises(MediaError):
    index_video(text)
  with pytest.raises(MediaError):
    index_video(tmp_path)    # a directory


def test_oversized_file_is_refused_without_reading_it(tmp_path, monkeypatch):
  p = tmp_path / 'big.ts'
  p.write_bytes(b'\0' * 2000)
  monkeypatch.setattr(media, 'MAX_VIDEO_BYTES', 1000)
  with pytest.raises(MediaError, match='too large'):
    index_video(p)


def test_too_many_frames_is_refused(clip, monkeypatch):
  monkeypatch.setattr(media, 'MAX_FRAMES', 10)
  with pytest.raises(MediaError, match='too many'):
    index_video(clip)


def test_truncated_video_gives_the_frames_it_has_or_a_clean_error(clip, tmp_path):
  data = clip.read_bytes()
  cut = tmp_path / 'cut.ts'
  cut.write_bytes(data[: len(data) // 2])
  try:
    ix = index_video(cut)
  except MediaError:
    return
  assert 0 < ix.count < 30
  frames = []
  try:
    frames = decoded(ix, 0)
  except MediaError:
    pass
  assert all(0 <= i < ix.count for i, _ in frames)


def test_damaged_packets_in_the_middle_do_not_raise_anything_but_media_error(clip, tmp_path):
  data = bytearray(clip.read_bytes())
  mid = len(data) // 2
  for k in range(mid, mid + 1500, 3):
    data[k] ^= 0xFF
  bad = tmp_path / 'bad.ts'
  bad.write_bytes(bytes(data))
  try:
    ix = index_video(bad)
    list(read_video(ix, 0))
  except MediaError:
    pass


# ---- qlog --------------------------------------------------------------------------------------------------------------------

def test_qlog_gives_frame_times_and_car_samples(tmp_path):
  monos = [500.0 + i / FPS for i in range(40)]
  car = [(500.0 + k * 0.1, 3.0 + k, k == 2, k == 3, False) for k in range(8)]
  make_qlog(tmp_path / 'qlog.zst', monos, car)
  q = read_qlog(tmp_path / 'qlog.zst')
  assert len(q.frame_mono) == 40 and q.frame_mono[10] == pytest.approx(500.5)
  assert list(np.round(q.car_speed)) == [3, 4, 5, 6, 7, 8, 9, 10]
  assert q.car_brake.tolist() == [False, False, True, False, False, False, False, False]
  assert q.car_left.tolist()[3] is True and not q.car_right.any()


def test_qlog_with_gaps_in_the_frame_index_marks_them_unknown(tmp_path):
  make_qlog(tmp_path / 'qlog.zst', [10.0, 10.05, 10.1], [])
  q = read_qlog(tmp_path / 'qlog.zst')
  assert np.isfinite(q.frame_mono).all() and len(q.car_t) == 0


def test_bad_qlogs_are_media_errors(tmp_path):
  with pytest.raises(MediaError):
    read_qlog(tmp_path / 'missing.zst')
  empty = tmp_path / 'empty.zst'
  empty.write_bytes(b'')
  with pytest.raises(MediaError, match='empty'):
    read_qlog(empty)
  junk = tmp_path / 'junk.zst'
  junk.write_bytes(os.urandom(2000))
  with pytest.raises(MediaError):
    read_qlog(junk)


def test_truncated_qlog_keeps_what_decoded(tmp_path):
  make_qlog(tmp_path / 'q.zst', [100.0 + i / FPS for i in range(400)], [(100.0 + k * 0.1, 5.0, 0, 0, 0) for k in range(200)])
  with open(tmp_path / 'q.zst', 'rb') as f:
    raw = zstandard.ZstdDecompressor().stream_reader(f).read()
  # half of the messages, cut mid-message, recompressed: the last message is truncated
  (tmp_path / 't.zst').write_bytes(zstandard.ZstdCompressor().compress(raw[: len(raw) // 2 + 7]))
  q = read_qlog(tmp_path / 't.zst')
  assert 0 < len(q.frame_mono) <= 400


def test_qlog_size_and_time_are_bounded(tmp_path, monkeypatch):
  make_qlog(tmp_path / 'q.zst', [100.0 + i / FPS for i in range(2000)], [])
  monkeypatch.setattr(media, 'MAX_QLOG_RAW_BYTES', 1000)
  with pytest.raises(MediaError, match='too large'):
    read_qlog(tmp_path / 'q.zst')
  monkeypatch.setattr(media, 'MAX_QLOG_RAW_BYTES', 1 << 28)
  monkeypatch.setattr(media, 'MAX_QLOG_EVENTS', 100)
  with pytest.raises(MediaError, match='too long'):
    read_qlog(tmp_path / 'q.zst')
  monkeypatch.setattr(media, 'MAX_QLOG_EVENTS', 1 << 20)
  ticks = iter(range(10_000))
  with pytest.raises(MediaError, match='too long'):
    read_qlog(tmp_path / 'q.zst', max_seconds=0.5, monotonic=lambda: next(ticks))   # every clock reading is later than the last


def test_make_segment_writes_both_files(tmp_path):
  d = make_segment(tmp_path, 3, frames=20)
  assert (d / 'qcamera.ts').stat().st_size > 0 and (d / 'qlog.zst').stat().st_size > 0

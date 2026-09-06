import cv2
import numpy as np
from openpilot.tools.alpr.fuse_burst import fourier_accumulate, register


def test_fourier_combination_preserves_identical_inputs():
  rng = np.random.default_rng(42)
  image = rng.uniform(20, 220, (48, 100, 3)).astype(np.float32)
  np.testing.assert_allclose(fourier_accumulate([image, image]), image, atol=1e-4)


def test_registered_shift_reduces_error_instead_of_applying_warp_backwards():
  reference = np.zeros((90, 180, 3), np.uint8) + 30
  cv2.rectangle(reference, (25, 15), (150, 73), (200, 200, 200), 3)
  cv2.putText(reference, 'TEST', (33, 57), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (160, 180, 190), 2)
  reference = cv2.GaussianBlur(reference, (0, 0), 1).astype(np.float32)
  moving = cv2.warpAffine(reference, np.array([[1, 0, 4], [0, 1, -3]], np.float32), (180, 90), borderMode=cv2.BORDER_REFLECT)
  aligned, valid, cc, _ = register(reference, moving)
  interior = valid.copy()
  interior[:12] = interior[-12:] = False
  interior[:, :12] = interior[:, -12:] = False
  assert cc > .95
  assert np.mean((aligned[interior]-reference[interior])**2) < .05*np.mean((moving[interior]-reference[interior])**2)


def test_frequency_zero_power_equals_average():
  rng = np.random.default_rng(5)
  images = rng.uniform(20, 220, (5, 45, 91, 3)).astype(np.float32)
  np.testing.assert_allclose(fourier_accumulate(images, power=0), images.mean(axis=0), atol=1e-4)

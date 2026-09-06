import React, { useState, useEffect } from 'react';
const defaults = { brightness: 1.08, gamma: 1.6, contrast: 1 };
export function useDisplaySettings() {
  const [settings, setSettings] = useState(() => {
    try {
      const s = JSON.parse(localStorage.getItem('road-review-display'));
      return s && Object.keys(defaults).every((k) => Number.isFinite(s[k]) && s[k] >= 0.4 && s[k] <= 3)
        ? s
        : defaults;
    } catch {
      return defaults;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem('road-review-display', JSON.stringify(settings));
    } catch {}
  }, [settings]);
  return [settings, setSettings];
}
export function enhance(ctx, width, height, s) {
  const image = ctx.getImageData(0, 0, width, height),
    lut = new Uint8ClampedArray(256);
  for (let i = 0; i < 256; i++)
    lut[i] =
      255 *
      Math.pow(Math.max(0, Math.min(1, (i / 255 - 0.5) * s.contrast + 0.5)), 1 / s.gamma) *
      s.brightness;
  for (let i = 0; i < image.data.length; i += 4) {
    image.data[i] = lut[image.data[i]];
    image.data[i + 1] = lut[image.data[i + 1]];
    image.data[i + 2] = lut[image.data[i + 2]];
  }
  ctx.putImageData(image, 0, 0);
}
export function DisplayControls({ settings, setSettings }) {
  return (
    <div className="display-controls">
      <strong>Make it easier to see</strong>
      {[
        ['gamma', 'Lift shadows', 0.5, 3],
        ['brightness', 'Brightness', 0.5, 2],
        ['contrast', 'Contrast', 0.5, 1.8],
      ].map(([k, label, min, max]) => (
        <label key={k}>
          {label}
          <input
            aria-label={label}
            type="range"
            min={min}
            max={max}
            step=".05"
            value={settings[k]}
            onChange={(e) => setSettings({ ...settings, [k]: Number(e.target.value) })}
          />
          <output>{settings[k].toFixed(2)}×</output>
        </label>
      ))}
      <button onClick={() => setSettings({ brightness: 1, gamma: 1, contrast: 1 })}>Original tones</button>
      <button onClick={() => setSettings(defaults)}>Lift shadows preset</button>
      <small>Settings stay with this browser. Original recordings are unchanged.</small>
    </div>
  );
}

// Share the same effective DPR between SDL's backing buffer and resize polling.
addToLibrary({
  emscripten_get_device_pixel_ratio: () => Math.max(1, window.devicePixelRatio * 0.85),
});

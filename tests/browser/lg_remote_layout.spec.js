const { test, expect } = require('@playwright/test');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '../..');
const cssFiles = [
  'frontend/assets/dashboard_v3.css',
  'frontend/assets/dashboard_household_design_system.css',
  'frontend/assets/dashboard_lg_remote.css',
];
const scripts = [
  'frontend/assets/dashboard_household_design_system.js',
  'frontend/assets/dashboard_lg_remote.js',
];
const viewports = [
  { width: 1920, height: 1080 },
  { width: 1440, height: 900 },
  { width: 1366, height: 768 },
];

const capabilities = {
  supported: [
    'power_on', 'power_off', 'up', 'down', 'left', 'right', 'ok', 'back', 'home',
    'volume_up', 'volume_down', 'mute', 'unmute', 'set_volume',
    'play', 'pause', 'stop', 'rewind', 'fast_forward',
  ],
  applications_available: true,
  inputs_available: true,
  applications: [
    { id: 'app-1', label: 'Netflix' },
    { id: 'app-2', label: 'YouTube' },
    { id: 'app-3', label: 'Disney+' },
    { id: 'app-4', label: 'Prime Video' },
    { id: 'app-5', label: 'Apple TV' },
    { id: 'app-6', label: 'Plex' },
  ],
  inputs: [
    { id: 'input-1', label: 'HDMI 1' },
    { id: 'input-2', label: 'HDMI 2' },
    { id: 'input-live', label: 'Live TV' },
  ],
};

for (const viewport of viewports) {
  test(`LG remote grid does not overlap or overflow at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.setContent('<!doctype html><html><body><main><article class="card"><div id="tvButtons"></div></article></main></body></html>');
    for (const file of cssFiles) {
      await page.addStyleTag({ path: path.join(ROOT, file) });
    }
    await page.addInitScript(() => {
      window.tv = async () => ({ ok: true });
    });
    for (const file of scripts) {
      await page.addScriptTag({ path: path.join(ROOT, file) });
    }
    await page.evaluate(payload => window.renderLgCompactRemote(payload), capabilities);

    const layout = await page.evaluate(() => {
      const rect = element => {
        const value = element.getBoundingClientRect();
        return { left: value.left, right: value.right, top: value.top, bottom: value.bottom, width: value.width, height: value.height };
      };
      const intersects = (a, b) =>
        Math.min(a.right, b.right) - Math.max(a.left, b.left) > 0.5 &&
        Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 0.5;
      const controls = document.querySelector('.household-lg-controls');
      const controlRect = rect(controls);
      const sections = [...controls.querySelectorAll('.household-lg-section')].map(rect);
      const overlappingSections = [];
      sections.forEach((first, index) => sections.slice(index + 1).forEach((second, offset) => {
        if (intersects(first, second)) overlappingSections.push([index, index + offset + 1]);
      }));
      const overflowing = [...controls.querySelectorAll('*')]
        .map(element => ({ element, box: rect(element) }))
        .filter(({ box }) => box.left < controlRect.left - 0.5 || box.right > controlRect.right + 0.5)
        .map(({ element }) => element.className);
      const navCells = [...document.querySelector('.household-lg-navigation').children].map(rect);
      const inputRect = rect(document.querySelector('.household-lg-inputs'));
      const nonInputBottom = Math.max(...sections.slice(0, -1).map(item => item.bottom));
      return {
        pageWidth: document.documentElement.scrollWidth,
        overlappingSections,
        overflowing,
        navCellWidths: navCells.map(item => Math.round(item.width * 10) / 10),
        navRows: navCells.map(item => Math.round(item.top)),
        inputBelow: inputRect.top >= nonInputBottom - 0.5,
        applicationCount: document.querySelectorAll('.household-lg-app-grid button').length,
        applicationColumns: getComputedStyle(document.querySelector('.household-lg-app-grid')).gridTemplateColumns.split(' ').length,
        playbackColumns: getComputedStyle(document.querySelector('.household-lg-playback-grid')).gridTemplateColumns.split(' ').length,
      };
    });

    expect(layout.pageWidth).toBeLessThanOrEqual(viewport.width);
    expect(layout.overlappingSections).toEqual([]);
    expect(layout.overflowing).toEqual([]);
    expect(new Set(layout.navCellWidths).size).toBe(1);
    expect(new Set(layout.navRows).size).toBe(3);
    expect(layout.inputBelow).toBe(true);
    expect(layout.applicationCount).toBeLessThanOrEqual(6);
    expect(layout.applicationColumns).toBe(2);
    expect(layout.playbackColumns).toBe(2);
  });
}

/**
 * 백테스트용 샘플 시계열 생성 (실제 data/prices.json 없을 때 사용)
 * 한국장/미국장 종료 시점 각 1회씩 가정
 */
(function (global) {
  "use strict";

  function generateSampleSeries(yearStart, yearEnd, assets) {
    const series = [];
    const start = new Date(yearStart, 0, 1);
    const end = new Date(yearEnd, 11, 31);
    const state = {};
    for (const a of assets) state[a] = a === "KODEX200" ? 28000 : a === "SPY" ? 320 : 140;

    for (let d = new Date(start); d <= end; d.setDate(d.getDate() + 1)) {
      const dow = d.getDay();
      if (dow === 0 || dow === 6) continue;
      const dateStr = d.toISOString().slice(0, 10);
      const t = (d - start) / (365.25 * 24 * 60 * 60 * 1000);
      const drift = 0.05 * t;
      const vol = 0.01;
      const rnd = () => (Math.random() - 0.5) * 2;
      state.KODEX200 = Math.max(1000, state.KODEX200 * (1 + drift * 0.01 + vol * rnd()));
      state.SPY = Math.max(10, state.SPY * (1 + drift * 0.02 + vol * 1.2 * rnd()));
      state.TLT = Math.max(10, state.TLT * (1 + drift * 0.005 + vol * 0.8 * rnd()));

      const kr = {};
      const us = {};
      for (const a of assets) {
        const v = state[a];
        if (a === "KODEX200") {
          kr[a] = Math.round(v);
          us[a] = Math.round(v);
        } else {
          kr[a] = null;
          us[a] = Math.round(v * 100) / 100;
        }
      }
      series.push({ date: dateStr, kr, us });
    }
    return series;
  }

  global.AssetLabSampleData = {
    generate: generateSampleSeries,
  };
})(typeof window !== "undefined" ? window : globalThis);

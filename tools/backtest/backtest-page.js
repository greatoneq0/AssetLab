(function () {
  "use strict";

  const DATA_BASE = "../../data";
  const assets = ["KODEX200", "SPY", "TLT"];
  const defaultWeights = { KODEX200: 0.3, SPY: 0.5, TLT: 0.2 };

  let currentSeries = null;
  let currentMeta = null;

  function el(id) {
    return document.getElementById(id);
  }

  function showDataStatus(msg, isOk) {
    const el = document.getElementById("data-status");
    el.textContent = msg;
    el.style.color = isOk ? "var(--accent2)" : "var(--muted)";
  }

  function showDataMeta(meta, count) {
    const el = document.getElementById("data-meta");
    if (!meta || !count) {
      el.textContent = "";
      return;
    }
    el.innerHTML = `자산: ${(meta.assets || assets).join(", ")} · 관측 수: ${count} · 샘플 데이터 사용 시 시드에 따라 결과가 달라질 수 있습니다.`;
  }

  function loadData() {
    showDataStatus("로딩 중…", false);
    const tryOrder = ["prices.json", "prices.sample.json"];
    let tried = 0;

    function tryOne() {
      const path = DATA_BASE + "/" + tryOrder[tried];
      fetch(path)
        .then((r) => {
          if (!r.ok) throw new Error("Not found");
          return r.json();
        })
        .then((data) => {
          const series = data.series || data;
          const meta = data.meta || {};
          if (!Array.isArray(series) || series.length === 0) throw new Error("Empty series");
          currentSeries = series;
          currentMeta = meta;
          initPeriodSelects(series);
          const obsCount = (AssetLabBacktest.buildObservations(series, meta.assets || assets)).length;
          showDataStatus("데이터 로드됨: " + series.length + "일 (관측 " + obsCount + "회)", true);
          showDataMeta(meta, obsCount);
        })
        .catch(() => {
          tried++;
          if (tried < tryOrder.length) return tryOne();
          if (typeof AssetLabSampleData !== "undefined") {
            currentSeries = AssetLabSampleData.generate(2020, 2024, assets);
            currentMeta = { assets };
            initPeriodSelects(currentSeries);
            const obsCount = AssetLabBacktest.buildObservations(currentSeries, assets).length;
            showDataStatus("샘플 데이터 생성됨 (2020–2024, 관측 " + obsCount + "회)", true);
            showDataMeta(currentMeta, obsCount);
          } else {
            showDataStatus("데이터를 불러올 수 없습니다. data/prices.json 또는 prices.sample.json을 추가하세요.", false);
          }
        });
    }
    tryOne();
  }

  function getDateRange(series) {
    if (!series || series.length === 0) return null;
    const dates = series.map((r) => r.date).filter(Boolean);
    if (dates.length === 0) return null;
    const sorted = [...dates].sort();
    return { min: sorted[0], max: sorted[sorted.length - 1] };
  }

  function initPeriodSelects(series) {
    const range = getDateRange(series);
    if (!range) return;
    const minY = parseInt(range.min.slice(0, 4), 10);
    const minM = parseInt(range.min.slice(5, 7), 10);
    const maxY = parseInt(range.max.slice(0, 4), 10);
    const maxM = parseInt(range.max.slice(5, 7), 10);

    const sy = el("startYear");
    const sm = el("startMonth");
    const ey = el("endYear");
    const em = el("endMonth");

    sy.innerHTML = "";
    ey.innerHTML = "";
    for (let y = minY; y <= maxY; y++) {
      sy.innerHTML += `<option value="${y}" ${y === minY ? "selected" : ""}>${y}년</option>`;
      ey.innerHTML += `<option value="${y}" ${y === maxY ? "selected" : ""}>${y}년</option>`;
    }
    sm.innerHTML = "";
    em.innerHTML = "";
    for (let m = 1; m <= 12; m++) {
      const mm = String(m).padStart(2, "0");
      const label = m + "월";
      sm.innerHTML += `<option value="${mm}" ${m === minM ? "selected" : ""}>${label}</option>`;
      em.innerHTML += `<option value="${mm}" ${m === maxM ? "selected" : ""}>${label}</option>`;
    }
  }

  function getFilteredSeries() {
    if (!currentSeries || currentSeries.length === 0) return [];
    const startY = el("startYear").value;
    const startM = el("startMonth").value;
    const endY = el("endYear").value;
    const endM = el("endMonth").value;
    const startStr = startY + "-" + startM + "-01";
    const lastDay = new Date(parseInt(endY, 10), parseInt(endM, 10), 0).getDate();
    const endStr = endY + "-" + endM + "-" + String(lastDay).padStart(2, "0");
    return currentSeries.filter((r) => r.date >= startStr && r.date <= endStr);
  }

  function getTargetWeights() {
    const w = {};
    for (const a of assets) {
      const input = document.querySelector(`input[name="weight-${a}"]`);
      w[a] = input ? parseFloat(input.value) || 0 : defaultWeights[a];
    }
    const sum = Object.values(w).reduce((s, v) => s + v, 0);
    if (sum <= 0) return defaultWeights;
    for (const a of assets) w[a] /= sum;
    return w;
  }

  function buildOptions() {
    const strategy = el("strategy").value;
    const targetWeights = getTargetWeights();
    const options = {
      assets: currentMeta && currentMeta.assets ? currentMeta.assets : assets,
      targetWeights,
      initialWeights: targetWeights,
      strategy,
      rebalancePoint: el("rebalancePoint").value,
      costBps: parseInt(el("costBps").value, 10) || 10,
    };
    if (strategy === "static") {
      options.staticIntervalBars = parseInt(el("staticIntervalBars").value, 10) || 22;
      options.rebalanceOnFirstOfMonth = el("rebalanceOnFirstOfMonth").checked;
    } else if (strategy === "band") {
      options.bandPct = parseFloat(el("bandPct").value) || 5;
    } else if (strategy === "momentum") {
      options.momentumLookback = parseInt(el("momentumLookback").value, 10) || 63;
    } else if (strategy === "mixed") {
      const checkboxes = document.querySelectorAll(".mix-cb:checked");
      if (checkboxes.length === 0) {
        options.mixedStrategies = [
          { strategy: "static", targetWeights, staticIntervalBars: 22 },
          { strategy: "band", targetWeights, bandPct: 5 },
        ];
      } else {
        options.mixedStrategies = Array.from(checkboxes).map((cb) => {
          const s = cb.getAttribute("data-strategy");
          const base = { strategy: s, targetWeights };
          if (s === "static") base.staticIntervalBars = 22;
          if (s === "band") base.bandPct = 5;
          if (s === "momentum") base.momentumLookback = 63;
          return base;
        });
      }
    }
    return options;
  }

  function drawEquityChart(equity, observations) {
    const canvas = el("equity-chart");
    const ctx = canvas.getContext("2d");
    const w = canvas.width;
    const h = canvas.height;
    const padding = { top: 12, right: 12, bottom: 32, left: 50 };
    const plotW = w - padding.left - padding.right;
    const plotH = h - padding.top - padding.bottom;

    ctx.fillStyle = "rgba(0,0,0,.15)";
    ctx.fillRect(0, 0, w, h);

    if (!equity || equity.length < 2) return;
    const minY = Math.min(...equity);
    const maxY = Math.max(...equity);
    const range = maxY - minY || 1;
    const scaleY = (v) => padding.top + plotH - ((v - minY) / range) * plotH;
    const scaleX = (i) => padding.left + (i / Math.max(1, equity.length - 1)) * plotW;

    ctx.strokeStyle = "rgba(138,208,255,.85)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(scaleX(0), scaleY(equity[0]));
    for (let i = 1; i < equity.length; i++) {
      ctx.lineTo(scaleX(i), scaleY(equity[i]));
    }
    ctx.stroke();

    ctx.fillStyle = "rgba(138,208,255,.12)";
    ctx.lineTo(scaleX(equity.length - 1), padding.top + plotH);
    ctx.lineTo(scaleX(0), padding.top + plotH);
    ctx.closePath();
    ctx.fill();

    ctx.fillStyle = "var(--muted)";
    ctx.font = "11px system-ui";
    ctx.fillText("1.0", padding.left - 8, scaleY(1) + 4);
    const last = equity[equity.length - 1];
    ctx.fillText(last.toFixed(2), padding.left - 28, scaleY(last) + 4);

    if (observations && observations.length > 0) {
      const tickCount = Math.min(6, Math.ceil(equity.length / 100));
      ctx.textAlign = "center";
      ctx.fillStyle = "var(--muted)";
      ctx.font = "10px system-ui";
      for (let k = 0; k <= tickCount; k++) {
        const idx = Math.round((k / tickCount) * (equity.length - 1));
        const obs = observations[idx];
        if (obs && obs.date) {
          const label = obs.date.length >= 7 ? obs.date.slice(0, 7) : obs.date;
          const x = scaleX(idx);
          ctx.fillText(label, x, h - 8);
        }
      }
      ctx.textAlign = "left";
    }
  }

  function run() {
    if (!currentSeries || currentSeries.length === 0) {
      alert("데이터를 먼저 로드해 주세요.");
      return;
    }
    const filtered = getFilteredSeries();
    if (filtered.length === 0) {
      alert("선택한 기간에 해당하는 데이터가 없습니다.");
      return;
    }
    const options = buildOptions();
    const out = AssetLabBacktest.runBacktest(filtered, options);
    const m = out.metrics;

    el("result-panel").style.display = "block";
    el("metrics").innerHTML = `
      <div class="metric"><span class="label">CAGR</span><span class="value">${(m.cagr != null ? m.cagr.toFixed(2) : "-")}%</span></div>
      <div class="metric"><span class="label">MDD</span><span class="value">${(m.mdd != null ? m.mdd.toFixed(2) : "-")}%</span></div>
      <div class="metric"><span class="label">Sharpe</span><span class="value">${m.sharpe != null ? m.sharpe : "-"}</span></div>
      <div class="metric"><span class="label">총 수익률</span><span class="value">${(m.totalReturn != null ? m.totalReturn.toFixed(2) : "-")}%</span></div>
      <div class="metric"><span class="label">총 턴오버 비용</span><span class="value">${(m.totalTurnover != null ? m.totalTurnover.toFixed(2) : "-")}%</span></div>
      <div class="metric"><span class="label">기간(년)</span><span class="value">${(m.years != null ? m.years.toFixed(2) : "-")}</span></div>
    `;
    drawEquityChart(out.equity, out.observations);
  }

  function toggleParams() {
    const strategy = el("strategy").value;
    ["params-static", "params-band", "params-momentum", "params-mixed"].forEach((id) => {
      el(id).style.display = id === "params-" + strategy ? "block" : "none";
    });
  }

  function initWeightInputs() {
    const container = el("weight-inputs");
    container.innerHTML = "";
    for (const a of assets) {
      const v = (defaultWeights[a] * 100).toFixed(0);
      container.innerHTML += `<label>${a} <input type="number" name="weight-${a}" value="${v}" min="0" max="100" step="1"> %</label>`;
    }
  }

  el("strategy").addEventListener("change", toggleParams);
  el("btn-run").addEventListener("click", run);
  initWeightInputs();
  loadData();
  toggleParams();
})();

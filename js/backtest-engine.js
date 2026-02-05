/**
 * AssetLab 백테스트 엔진
 * - 관측 시점: 한국장 종료 후(kr), 미국장 종료 후(us) → 하루 최대 2회
 * - 정적 리밸런싱: 주기(일/월) 고정
 * - 동적 리밸런싱: 밴드, 모멘텀, 변동성 등
 * - 혼합: 정적+동적 또는 복수 동적 전략 조합
 */
(function (global) {
  "use strict";

  const POINTS = ["kr", "us"];
  const ASSET_LIST = ["KODEX200", "SPY", "TLT"];

  /**
   * 시리즈에서 null을 이전 값으로 채워 관측 배열 생성 (날짜 오름차순, kr → us 순)
   */
  function buildObservations(series, assets) {
    const obs = [];
    const prev = {};
    for (const row of series) {
      for (const point of POINTS) {
        const prices = {};
        let hasAny = false;
        for (const a of assets) {
          const v = row[point] && row[point][a];
          if (v != null && !isNaN(v)) {
            prices[a] = Number(v);
            prev[a] = prices[a];
            hasAny = true;
          } else if (prev[a] != null) {
            prices[a] = prev[a];
            hasAny = true;
          }
        }
        if (hasAny) {
          obs.push({
            date: row.date,
            point,
            prices: { ...prices },
          });
        }
      }
    }
    return obs;
  }

  /**
   * 수익률 계산 (t-1 → t)
   */
  function returns(obs, i, assets) {
    if (i === 0) return null;
    const cur = obs[i].prices;
    const prev = obs[i - 1].prices;
    const ret = {};
    for (const a of assets) {
      if (prev[a] != null && prev[a] > 0 && cur[a] != null) {
        ret[a] = (cur[a] - prev[a]) / prev[a];
      }
    }
    return ret;
  }

  /**
   * 과거 N개 관측 기준 모멘텀 (가격 수익률)
   */
  function momentum(obs, i, assets, lookback) {
    if (i < lookback) return null;
    const cur = obs[i].prices;
    const past = obs[i - lookback].prices;
    const mom = {};
    for (const a of assets) {
      if (past[a] != null && past[a] > 0 && cur[a] != null) {
        mom[a] = (cur[a] - past[a]) / past[a];
      }
    }
    return mom;
  }

  /**
   * 관측 구간 변동성 (과거 N일 수익률 표준편차)
   */
  function volatility(obs, i, assets, lookback) {
    if (i < lookback) return null;
    const rets = [];
    for (let k = i - lookback + 1; k <= i; k++) {
      const r = returns(obs, k, assets);
      if (r) rets.push(r);
    }
    const vol = {};
    for (const a of assets) {
      const series = rets.map((x) => x[a]).filter((v) => v != null && !isNaN(v));
      if (series.length >= 2) {
        const mean = series.reduce((s, v) => s + v, 0) / series.length;
        const variance = series.reduce((s, v) => s + (v - mean) ** 2, 0) / (series.length - 1);
        vol[a] = Math.sqrt(Math.max(0, variance));
      }
    }
    return vol;
  }

  /**
   * 포트폴리오 일일 수익률 (weights × asset returns)
   */
  function portfolioReturn(weights, ret) {
    if (!ret) return 0;
    let r = 0;
    for (const [a, w] of Object.entries(weights)) {
      if (ret[a] != null && w) r += w * ret[a];
    }
    return r;
  }

  /**
   * 목표 대비 드리프트 최대 절대값
   */
  function maxDrift(currentWeights, targetWeights, assets) {
    let max = 0;
    for (const a of assets) {
      const c = currentWeights[a] || 0;
      const t = targetWeights[a] || 0;
      max = Math.max(max, Math.abs(c - t));
    }
    return max;
  }

  /**
   * 정적 리밸런싱: 매 N회 또는 매월 첫 관측 시 목표비중으로
   */
  function shouldRebalanceStatic(obsIdx, observations, options) {
    const { intervalBars = 22, rebalanceOnFirstOfMonth = true } = options;
    if (intervalBars > 0 && obsIdx > 0 && obsIdx % intervalBars === 0) return true;
    if (rebalanceOnFirstOfMonth && obsIdx > 0) {
      const cur = observations[obsIdx];
      const prev = observations[obsIdx - 1];
      if (cur && prev && cur.date !== prev.date) {
        const curDay = cur.date.slice(8, 10);
        if (curDay === "01") return true;
      }
    }
    return false;
  }

  /**
   * 밴드 리밸런싱: 최대 드리프트가 bandPct 초과 시
   */
  function shouldRebalanceBand(currentWeights, targetWeights, assets, bandPct) {
    return maxDrift(currentWeights, targetWeights, assets) >= bandPct;
  }

  /**
   * 모멘텀 가중: 모멘텀 상대 강도로 비중 (양수만, 정규화)
   */
  function momentumWeights(obs, i, assets, lookback, targetWeights) {
    const mom = momentum(obs, i, assets, lookback);
    if (!mom) return targetWeights;
    const entries = assets
      .map((a) => ({ a, m: mom[a] != null ? mom[a] : 0 }))
      .filter((x) => x.m > 0);
    if (entries.length === 0) return targetWeights;
    const sum = entries.reduce((s, x) => s + x.m, 0);
    const w = {};
    for (const a of assets) w[a] = 0;
    for (const { a, m } of entries) w[a] = m / sum;
    return w;
  }

  /**
   * 단일 백테스트 실행
   * options: { initialWeights, targetWeights, strategy, rebalancePoint, ... }
   */
  function runSingle(observations, options) {
    const assets = options.assets || ASSET_LIST;
    const targetWeights = options.targetWeights || options.initialWeights || { KODEX200: 0.3, SPY: 0.5, TLT: 0.2 };
    const initialWeights = options.initialWeights || targetWeights;
    const strategy = options.strategy || "static";
    const rebalancePoint = options.rebalancePoint || "both"; // 'kr' | 'us' | 'both'
    const costBps = options.costBps != null ? options.costBps : 10;

    let weights = { ...initialWeights };
    const equity = [1];
    const turnovers = [0];
    let prevWeights = { ...weights };

    for (let i = 1; i < observations.length; i++) {
      const obs = observations[i];
      const pointAllowed = rebalancePoint === "both" || obs.point === rebalancePoint;
      const ret = returns(observations, i, assets);
      const dayReturn = portfolioReturn(weights, ret);
      equity.push(equity[i - 1] * (1 + dayReturn));

      let rebalance = false;
      if (pointAllowed) {
        if (strategy === "static") {
          rebalance = shouldRebalanceStatic(i, observations, {
            intervalBars: options.staticIntervalBars ?? 22,
            rebalanceOnFirstOfMonth: options.rebalanceOnFirstOfMonth !== false,
          });
        } else if (strategy === "band") {
          const currentWeights = weights;
          rebalance = shouldRebalanceBand(
            currentWeights,
            targetWeights,
            assets,
            (options.bandPct ?? 5) / 100
          );
        } else if (strategy === "momentum") {
          rebalance = true; // 매 관측 시 모멘텀 비중 갱신
          const newW = momentumWeights(
            observations,
            i,
            assets,
            options.momentumLookback ?? 63,
            targetWeights
          );
          weights = newW;
        }
      }

      if (rebalance && strategy !== "momentum") {
        weights = { ...targetWeights };
      }

      // 리밸런싱 비용 (비중 변화 절대합 / 2 * costBps)
      let turnover = 0;
      for (const a of assets) {
        turnover += Math.abs((weights[a] || 0) - (prevWeights[a] || 0));
      }
      turnover = (turnover / 2) * (costBps / 10000);
      turnovers.push(turnover);
      equity[i] *= 1 - turnover;
      prevWeights = { ...weights };
    }

    return { equity, weights, turnovers, observations };
  }

  /**
   * 혼합 전략: 여러 하위 전략에 비중 배분 후 각각 백테스트하고 합산
   */
  function runMixed(observations, options) {
    const mixes = options.mixedStrategies || [];
    if (mixes.length === 0) {
      return runSingle(observations, { ...options, strategy: "static" });
    }
    const results = mixes.map((m) =>
      runSingle(observations, {
        ...options,
        strategy: m.strategy,
        targetWeights: m.targetWeights || options.targetWeights,
        staticIntervalBars: m.staticIntervalBars,
        bandPct: m.bandPct,
        momentumLookback: m.momentumLookback,
        rebalancePoint: m.rebalancePoint || options.rebalancePoint,
      })
    );
    const weight = 1 / results.length;
    const equity = results[0].equity.map((_, i) =>
      results.reduce((s, r) => s + r.equity[i] * weight, 0)
    );
    const turnovers = results[0].turnovers.map((_, i) =>
      results.reduce((s, r) => s + r.turnovers[i] * weight, 0)
    );
    return {
      equity,
      weights: results[0].weights,
      turnovers,
      observations,
      components: results,
    };
  }

  /**
   * 지표 계산: CAGR, MDD, Sharpe(연율), 턴오버
   */
  function computeMetrics(equity, turnovers, barsPerYear) {
    if (equity.length < 2) return {};
    const n = equity.length;
    const totalReturn = equity[n - 1] / equity[0] - 1;
    const years = n / (barsPerYear || 252);
    const cagr = years > 0 ? Math.pow(1 + totalReturn, 1 / years) - 1 : 0;
    let peak = equity[0];
    let mdd = 0;
    for (let i = 1; i < n; i++) {
      peak = Math.max(peak, equity[i]);
      const dd = (peak - equity[i]) / peak;
      if (dd > mdd) mdd = dd;
    }
    const dailyRets = [];
    for (let i = 1; i < n; i++) {
      if (equity[i - 1] > 0) dailyRets.push(equity[i] / equity[i - 1] - 1);
    }
    const avgRet = dailyRets.length ? dailyRets.reduce((a, b) => a + b, 0) / dailyRets.length : 0;
    const variance =
      dailyRets.length > 1
        ? dailyRets.reduce((s, r) => s + (r - avgRet) ** 2, 0) / (dailyRets.length - 1)
        : 0;
    const vol = Math.sqrt(Math.max(0, variance));
    const sharpe = vol > 0 && barsPerYear ? (avgRet / vol) * Math.sqrt(barsPerYear) : 0;
    const totalTurnover = turnovers.reduce((a, b) => a + b, 0);
    return {
      cagr: cagr * 100,
      mdd: mdd * 100,
      sharpe: Math.round(sharpe * 100) / 100,
      totalReturn: totalReturn * 100,
      totalTurnover: totalTurnover * 100,
      years,
    };
  }

  /**
   * 시리즈 로드 후 관측 생성 및 백테스트 실행 (진입점)
   */
  function runBacktest(series, options) {
    const assets = options.assets || (options.meta && options.meta.assets) || ASSET_LIST;
    const observations = buildObservations(series, assets);
    const barsPerYear = options.barsPerYear != null ? options.barsPerYear : 504; // kr+us per day ~2 * 252
    const isMixed = options.strategy === "mixed" && options.mixedStrategies && options.mixedStrategies.length > 0;
    const result = isMixed ? runMixed(observations, options) : runSingle(observations, options);
    const metrics = computeMetrics(result.equity, result.turnovers, barsPerYear);
    return {
      observations,
      equity: result.equity,
      metrics,
      result,
    };
  }

  global.AssetLabBacktest = {
    buildObservations,
    runBacktest,
    runSingle,
    runMixed,
    computeMetrics,
    POINTS,
    ASSET_LIST,
  };
})(typeof window !== "undefined" ? window : globalThis);

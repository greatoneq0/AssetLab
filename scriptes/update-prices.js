/**
 * 한국장/미국장 종료 후 가격 데이터 갱신
 * - GitHub Actions에서 1일 2회 실행 (한국장 종료 후, 미국장 종료 후)
 * - ALPHAVANTAGE_API_KEY가 있으면 실거래 데이터 요청 (선택), 없으면 기존 데이터 유지 또는 샘플 보강
 */
const fs = require("fs");
const path = require("path");

const DATA_DIR = path.join(__dirname, "..", "data");
const PRICES_PATH = path.join(DATA_DIR, "prices.json");
const META_PATH = path.join(DATA_DIR, "meta.json");
const ASSETS = [
  { id: "KODEX200", market: "KR", symbol: "069500.KS" },
  { id: "SPY", market: "US", symbol: "SPY" },
  { id: "TLT", market: "US", symbol: "TLT" },
];

function todayStr() {
  const d = new Date();
  return d.toISOString().slice(0, 10);
}

function loadExisting() {
  try {
    const raw = fs.readFileSync(PRICES_PATH, "utf8");
    const data = JSON.parse(raw);
    return Array.isArray(data.series) ? data : { series: data.series || [], meta: data.meta || {} };
  } catch {
    return { series: [], meta: { assets: ASSETS.map((a) => a.id), start: null, end: null } };
  }
}

function loadMeta() {
  try {
    return JSON.parse(fs.readFileSync(META_PATH, "utf8"));
  } catch {
    return { assets: ASSETS.map((a) => a.id), updatedAt: null };
  }
}

async function fetchAlphaVantage(symbol) {
  const key = process.env.ALPHAVANTAGE_API_KEY;
  if (!key) return null;
  const url = `https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=${symbol}&apikey=${key}`;
  try {
    const res = await fetch(url);
    const data = await res.json();
    const q = data["Global Quote"];
    if (q && q["05. price"]) return parseFloat(q["05. price"]);
  } catch (e) {
    console.warn("Alpha Vantage fetch failed:", e.message);
  }
  return null;
}

async function fetchYahooQuote(symbol) {
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${symbol}?interval=1d&range=1d`;
  try {
    const res = await fetch(url);
    const data = await res.json();
    const quote = data?.chart?.result?.[0];
    const meta = quote?.meta;
    const lastClose = meta?.regularMarketPrice ?? quote?.indicators?.quote?.[0]?.close?.filter(Boolean).pop();
    if (lastClose != null) return Number(lastClose);
  } catch (e) {
    console.warn("Yahoo fetch failed for", symbol, e.message);
  }
  return null;
}

async function fetchPrices(market) {
  const row = { date: todayStr(), kr: {}, us: {} };
  for (const a of ASSETS) {
    const useYahoo = a.market === "US" || a.symbol?.endsWith(".KS");
    const price = useYahoo ? await fetchYahooQuote(a.symbol) : await fetchAlphaVantage(a.symbol);
    if (price != null) {
      if (a.market === "KR") {
        row.kr[a.id] = Math.round(price);
        row.us[a.id] = row.kr[a.id];
      } else {
        row.us[a.id] = Math.round(price * 100) / 100;
        if (!row.kr[a.id]) row.kr[a.id] = null;
      }
    }
  }
  return row;
}

function mergeRow(existing, newRow) {
  const last = existing.series[existing.series.length - 1];
  if (last && last.date === newRow.date) {
    const merged = { ...last };
    for (const point of ["kr", "us"]) {
      merged[point] = { ...(merged[point] || {}), ...(newRow[point] || {}) };
    }
    existing.series[existing.series.length - 1] = merged;
  } else {
    existing.series.push(newRow);
  }
  return existing;
}

async function main() {
  const market = process.env.MARKET || "both";
  const existing = loadExisting();
  const today = todayStr();

  if (market === "kr" || market === "both") {
    const rowKr = await fetchPrices("KR");
    if (Object.keys(rowKr.kr).length > 0) mergeRow(existing, rowKr);
  }
  if (market === "us" || market === "both") {
    const rowUs = await fetchPrices("US");
    if (Object.keys(rowUs.us).length > 0) mergeRow(existing, rowUs);
  }

  if (existing.series.length > 0) {
    existing.meta = existing.meta || {};
    existing.meta.assets = existing.meta.assets || ASSETS.map((a) => a.id);
    existing.meta.start = existing.meta.start || existing.series[0].date;
    existing.meta.end = existing.series[existing.series.length - 1].date;
    fs.mkdirSync(DATA_DIR, { recursive: true });
    fs.writeFileSync(PRICES_PATH, JSON.stringify(existing, null, 2), "utf8");
    const meta = loadMeta();
    meta.updatedAt = new Date().toISOString();
    fs.writeFileSync(META_PATH, JSON.stringify(meta, null, 2), "utf8");
    console.log("Updated prices.json:", existing.series.length, "rows, end:", existing.meta.end);
  } else {
    console.log("No new data; prices.json unchanged.");
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});

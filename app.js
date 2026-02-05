const CONFIG = {
  siteName: "AssetLab",
  tagline: "자산배분 자료 · 백테스트 노트 · 실험용 툴 허브",
  // TODO: 본인 레포로 바꾸세요 (예: https://github.com/onegyu/AssetLab)
  repoUrl: "https://github.com/",
  sections: [
    {
      id: "library",
      span: "span6",
      title: "📚 자료실",
      desc: "개념/지표/리밸런싱/리스크 관리 등 자산배분의 핵심을 빠르게 찾아볼 수 있게 정리합니다.",
      items: [
        "기초: 기대수익·변동성·상관·분산효과",
        "리밸런싱: 주기형 vs 밴드형, 세금/거래비용 고려",
        "리스크: MDD, VaR/ES, 위험기여도(RC) 개요",
        "대체자산: 금/원자재/리츠/장단기채, 인플레이션 대응",
      ],
    },
    {
      id: "models",
      span: "span6",
      title: "🧪 모델 & 실험",
      desc: "재현 가능한 실험을 목표로, 가정/데이터/코드를 함께 둡니다.",
      items: [
        "정적 AA: 60/40, 50/30/20, 올웨더류 변형",
        "동적 AA: 모멘텀/추세/리스크 패리티(기초)",
        "헤지: 변동성/통화/금리 민감도에 대한 가벼운 프록시",
        "검증: 워크포워드, 리밸런싱 민감도, 거래비용 스트레스",
      ],
      footnote: `* <a href="tools/backtest/">리밸런싱 백테스트</a>: 정적/동적/혼합 전략을 한국장·미국장 종료 시점 기준으로 백테스트.`,
    },
    {
      id: "structure",
      span: "span4",
      title: "🗂 추천 폴더 구조",
      desc: "GitHub Pages에서 관리가 편한 기본 구조입니다.",
      items: [
        "<span class='kbd'>/docs</span> : 문서(Markdown → HTML)",
        "<span class='kbd'>/data</span> : 샘플 데이터/메타",
        "<span class='kbd'>/tools</span> : 간단 웹 툴(계산기 등)",
      ],
    },
    {
      id: "todo",
      span: "span4",
      title: "📌 다음 할 일",
      desc: "초기 세팅 체크리스트.",
      items: [
        "Repo 링크를 상단 버튼에 연결",
        "첫 문서: “AssetLab 목표/범위” 작성",
        "대표 전략 1개를 백테스트로 정리",
        "리밸런싱 규칙 표준화",
      ],
    },
    {
      id: "start",
      span: "span4",
      title: "⚙️ 시작하기",
      desc: "가장 간단한 운영 방식.",
      items: [
        "<span class='kbd'>app.js</span> / <span class='kbd'>style.css</span> 수정 → push",
        "Pages 설정: <span class='kbd'>main / root</span>",
        "문서 확장 시 <span class='kbd'>/docs</span>에 Markdown 추가",
      ],
    },
    {
      id: "data-update",
      span: "span4",
      title: "📊 가격 데이터 업데이트",
      desc: "prices.json을 수동으로 갱신합니다. 로컬·GitHub Actions 둘 다 지원.",
      items: [],
      customHtml: "data-update-card",
    },
  ],
};

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function render() {
  const year = new Date().getFullYear();

  const cardsHtml = CONFIG.sections
    .map((sec) => {
      const items = sec.items
        .map((x) => `<li>${x}</li>`)
        .join("");

      const foot = sec.footnote ? `<p class="small" style="margin-top:10px;">${sec.footnote}</p>` : "";
      const listOrCustom = sec.customHtml
        ? `<div id="${escapeHtml(sec.customHtml)}" class="custom-card-body"></div>`
        : `<ul class="list">${items}</ul>`;

      return `
        <section class="card ${sec.span || ""}" id="${escapeHtml(sec.id)}">
          <h3>${sec.title}</h3>
          <p>${escapeHtml(sec.desc)}</p>
          ${listOrCustom}
          ${foot}
        </section>
      `;
    })
    .join("");

  const html = `
    <div class="wrap">
      <header>
        <div class="brand">
          <div class="logo" aria-hidden="true"></div>
          <div class="title">
            <h1>${escapeHtml(CONFIG.siteName)}</h1>
            <p>${escapeHtml(CONFIG.tagline)}</p>
          </div>
        </div>

        <nav class="toplinks" aria-label="quick links">
          <a class="pill" href="${CONFIG.repoUrl}" target="_blank" rel="noreferrer">
            <span class="dot" aria-hidden="true"></span>
            GitHub Repo
          </a>
          <a class="pill" href="#start">
            <span class="dot" aria-hidden="true" style="background: var(--accent2); box-shadow: 0 0 0 4px rgba(178,255,207,.10);"></span>
            시작하기
          </a>
        </nav>
      </header>

      <main class="hero">
        <h2>실전 자산배분을 “자료 + 실험”으로 정리합니다.</h2>
        <p>
          AssetLab은 자산배분(AA) 전략을 설계·검증·운영하기 위한
          <b>간단한 레퍼런스</b>와 <b>재현 가능한 실험</b>을 모으는 공간입니다.
          데이터 소스, 가정, 리밸런싱 룰, 리스크 관리(변동성/드로다운)까지 한 번에 추적할 수 있게 구성합니다.
        </p>

        <div class="cta">
          <a class="btn primary" href="tools/backtest/">📈 리밸런싱 백테스트</a>
          <a class="btn" href="#library">📚 자료실 보기</a>
          <a class="btn" href="#models">🧪 모델/실험</a>
          <button class="btn" id="btn-open-readme">📄 README 보기</button>
        </div>

        <div class="notice">
          <b>면책:</b> 이 사이트의 내용은 교육/연구 목적이며 투자 조언이 아닙니다.
          모든 전략은 과거 데이터 기반이며, 실거래 적용 전 반드시 본인 검증이 필요합니다.
        </div>

        <section class="grid" aria-label="sections">
          ${cardsHtml}
        </section>

        <footer>
          <div>© <span id="y">${year}</span> ${escapeHtml(CONFIG.siteName)}</div>
          <div class="small">Built with GitHub Pages · JS-rendered</div>
        </footer>
      </main>
    </div>
  `;

  document.getElementById("app").innerHTML = html;

  // 이벤트 바인딩
  const btn = document.getElementById("btn-open-readme");
  if (btn) {
    btn.addEventListener("click", () => {
      const readmeUrl = CONFIG.repoUrl.replace(/\/$/, "") + "/blob/main/README.md";
      window.open(readmeUrl, "_blank", "noopener,noreferrer");
    });
  }

  // 가격 데이터 업데이트 카드
  const dataUpdateCard = document.getElementById("data-update-card");
  if (dataUpdateCard) {
    const base = CONFIG.repoUrl.replace(/\/$/, "");
    const actionsUrl = base && !base.endsWith("github.com") ? base + "/actions/workflows/update_prices.yml" : null;

    dataUpdateCard.innerHTML = `
      <div class="update-buttons">
        ${actionsUrl ? `<a class="btn primary" href="${actionsUrl}" target="_blank" rel="noreferrer" id="btn-actions-update">GitHub Actions로 업데이트</a>` : ""}
        <button type="button" class="btn" id="btn-copy-cmd">로컬 명령어 복사</button>
      </div>
      <p class="small muted" style="margin-top:10px;">
        로컬: <code>node scripts/update-prices.js</code> 또는 <code>python tools/update_prices.py</code>
      </p>
    `;

    const copyBtn = document.getElementById("btn-copy-cmd");
    if (copyBtn) {
      copyBtn.addEventListener("click", () => {
        const cmd = "node scripts/update-prices.js";
        navigator.clipboard.writeText(cmd).then(
          () => { copyBtn.textContent = "복사됨!"; setTimeout(() => { copyBtn.textContent = "로컬 명령어 복사"; }, 1500); },
          () => { copyBtn.textContent = "복사 실패"; setTimeout(() => { copyBtn.textContent = "로컬 명령어 복사"; }, 1500); }
        );
      });
    }
  }
}

render();

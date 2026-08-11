/**
 * coolattin/static/js/info.js
 *
 * Info-page behaviour that previously lived in an inline <script> in info.html:
 * the YouTube hero background and the page's scroll/reveal interactions.
 * Moved out so script-src can drop 'unsafe-inline'.
 *
 * Note: onYouTubeIframeAPIReady must stay on window — the YouTube iframe API
 * calls it by that global name.
 */
(function () {
"use strict";

/* ── YouTube Background Theme ─────────────────────────── */
const VIDEO_ID = 'L4_a80T4_1g';
let player;
const heroBg = document.getElementById("ipHeroBg");

// Show fallback image initially
if (heroBg) heroBg.classList.add("has-image");

window.onYouTubeIframeAPIReady = function() {
  player = new YT.Player('ytPlayer', {
    videoId: VIDEO_ID,
    playerVars: {
      'autoplay': 1,
      'controls': 0,
      'showinfo': 0,
      'modestbranding': 1,
      'loop': 1,
      'playlist': VIDEO_ID, // Required for loop
      'fs': 0,
      'cc_load_policy': 0,
      'iv_load_policy': 3,
      'autohide': 0,
      'rel': 0,
      'mute': 1,
      'origin': window.location.origin
    },
    events: {
      'onReady': (event) => {
        event.target.mute();
        event.target.setPlaybackQuality('hd1080');
        event.target.playVideo();
      },
      'onStateChange': (event) => {
        if (event.data === YT.PlayerState.PLAYING) {
          // Attempt to force quality again once playing
          event.target.setPlaybackQuality('hd1080');
          // Remove fallback image once video starts
          if (heroBg) heroBg.classList.remove("has-image");
        }
        if (event.data === YT.PlayerState.ENDED) {
          event.target.playVideo();
        }
      }
    }
  });
};

window.updateBgVideo = function() {
  const input = document.getElementById("ytVideoInput");
  const display = document.getElementById("currentVideoId");
  let val = input.value.trim();
  if (!val) return;

  // Extract ID if full URL
  const regExp = /^.*(youtu.be\/|v\/|u\/\w\/|embed\/|watch\?v=|\&v=)([^#\&\?]*).*/;
  const match = val.match(regExp);
  const id = (match && match[2].length === 11) ? match[2] : val;

  if (id.length === 11) {
    if (player && player.loadVideoById) {
      player.loadVideoById({
        videoId: id,
        playlist: id
      });
      if (display) display.textContent = id;
      input.value = "";
    }
  } else {
    alert("Please enter a valid YouTube Video ID or URL");
  }
};

/* ── Hero parallax / load animation ──────────────────────── */
if (heroBg) {
  requestAnimationFrame(() => heroBg.classList.add("loaded"));
  window.addEventListener("scroll", () => {
    const y = window.scrollY;
    // Keep heroBg above the video but animate its parallax
    heroBg.style.transform = `scale(1) translateY(${y * 0.25}px)`;
  }, { passive: true });
}

/* ── Scroll reveal ────────────────────────────────────────── */
const revealEls = document.querySelectorAll(".reveal");
const revealObs = new IntersectionObserver((entries) => {
  entries.forEach(e => { if (e.isIntersecting) { e.target.classList.add("visible"); revealObs.unobserve(e.target); } });
}, { threshold: 0.12 });
revealEls.forEach(el => revealObs.observe(el));

/* ── Intro card carousel ──────────────────────────────────── */
const CARD_COUNT = 5;
let currentCard = 0;
const track = document.getElementById("ipIntroTrack");
const dotsEl = document.getElementById("ipIntroDots");

function goToCard(n) {
  currentCard = (n + CARD_COUNT) % CARD_COUNT;
  track.style.transform = `translateX(-${currentCard * 100}%)`;
  dotsEl.querySelectorAll(".ip-dot").forEach((d, i) => d.classList.toggle("active", i === currentCard));
}

for (let i = 0; i < CARD_COUNT; i++) {
  const btn = document.createElement("button");
  btn.className = "ip-dot" + (i === 0 ? " active" : "");
  btn.onclick = () => goToCard(i);
  dotsEl.appendChild(btn);
}

// Auto-advance
let autoTimer = setInterval(() => goToCard(currentCard + 1), 4000);
track.addEventListener("click", () => { clearInterval(autoTimer); goToCard(currentCard + 1); });

/* ── Social hierarchy ─────────────────────────────────────── */
const TIERS = [
  {
    cls: "t1", name: "Earl Fitzwilliam", sub: "Absentee landowner · Sheffield, England",
    detail: "The 5th Earl Fitzwilliam held the estate but rarely visited Ireland. He delegated all management to resident agents. His instructions and agents' replies form the core of NLI MS 4974–4975 and the broader Fitzwilliam Papers. The earl's distance from the human consequences of the clearances is a recurring theme in the correspondence."
  },
  {
    cls: "t2", name: "Estate Agents", sub: "Day-to-day management on the ground",
    detail: "Agents such as W. Wainwright acted as the Earl's proxy — setting rents, authorising removals, and administering the emigration scheme. Their letters to the Earl, preserved in the NLI, are the primary documentary source for this database and for understanding how decisions were made."
  },
  {
    cls: "t3", name: "Head Tenants", sub: "Large leaseholders · relative security",
    detail: "Head tenants held leases directly from the estate, often subletting parcels to under-tenants. Their position gave them relative security during the Famine. Some head tenants subsequently consolidated cleared land, improving their own position at the expense of removed neighbours."
  },
  {
    cls: "t4", name: "Under-Tenants", sub: "Yearly tenancies · vulnerable to removal",
    detail: "Under-tenants rented small farms from head tenants on annual agreements. They had limited legal security and were the principal targets of the assisted emigration scheme. The emigration books record hundreds of under-tenant families by name and townland."
  },
  {
    cls: "t5", name: "Cottiers", sub: "Cabin + potato garden · subsistence",
    detail: "Cottiers lived in small cabins with a garden plot, often paying rent in labour. Their survival depended entirely on the potato crop. When the blight struck, they had no cash reserves, no savings, and no alternative food supply. The Famine struck this tier first and hardest."
  },
  {
    cls: "t6", name: "Agricultural Labourers", sub: "Landless · wage work · most precarious",
    detail: "Day labourers owned nothing and worked for wages. With no land and wages collapsing as farmers themselves became destitute, many labourers entered the workhouse or died. They are the hardest group to trace in the records — often unnamed, often unrecorded."
  },
];

const pyramid = document.getElementById("ipPyramid");
if (pyramid) {
  let openIdx = -1;
  TIERS.forEach((t, i) => {
    const row = document.createElement("div");
    row.className = "ip-pyramid-row";
    row.innerHTML = `
      <div class="ip-tier ${t.cls}" style="margin:0 auto;">
        <div class="ip-tier-name">${t.name}</div>
        <div class="ip-tier-sub">${t.sub}</div>
      </div>
      <div class="ip-tier-detail" id="ipTier${i}">${t.detail}</div>
      ${i < TIERS.length - 1 ? '<div class="ip-pyramid-arrow"></div>' : ''}
    `;
    row.querySelector(".ip-tier").addEventListener("click", () => {
      const det = document.getElementById("ipTier" + i);
      const isOpen = det.classList.contains("open");
      // Close all
      TIERS.forEach((_, j) => document.getElementById("ipTier" + j).classList.remove("open"));
      if (!isOpen) det.classList.add("open");
    });
    pyramid.appendChild(row);
  });
}

/* ── Timeline slider ─────────────────────────────────────── */
const EVENTS = [
  { year: 1845, title: "Blight Arrives", text: "Phytophthora infestans devastates the potato crop across Ireland for the first time. In Wicklow the failure is partial but severe in many townlands. No major estate response yet." },
  { year: 1846, title: "Total Crop Failure", text: "A second consecutive complete failure of the potato crop. Hunger becomes widespread across Wicklow. The Fitzwilliam estate records mounting arrears and increasing distress among cottiers and labourers." },
  { year: 1847, title: "Black '47 — Scheme Begins", text: "The worst year of Famine mortality. The estate launches its first assisted emigration voyages, contracting William Graves & Son of New Ross as shipping agents. Emigration books NLI MS 4974 begin." },
  { year: 1848, title: "Clearances Intensify", text: "The estate scales up the scheme. Agent Wainwright compiles household lists, selects families with two or more years of arrears, and arranges passages from New Ross. Several hundred depart." },
  { year: 1849, title: "Peak Removals", text: "The scheme reaches its greatest volume. Multiple voyages are arranged. The townlands of Coolattin estate begin to show mass depopulation. Cabins are demolished on surrender throughout this year." },
  { year: 1850, title: "Land Consolidation Begins", text: "Cleared holdings are merged and let to solvent grazing tenants. The landscape begins its shift from tillage strips to open pasture. Famine mortality starts to decline nationally." },
  { year: 1851, title: "Census Taken", text: "The 1851 Census records the transformed population. Townlands cleared in 1849–1850 show near-zero inhabited houses; those not yet cleared show their 1841-era numbers. A snapshot of a landscape mid-clearance." },
  { year: 1852, title: "Continued Departures", text: "Emigration continues at a reduced rate. Some families who had initially declined the offer now accept as conditions remain difficult. The estate maintains the programme." },
  { year: 1853, title: "Estate Restructuring", text: "The Fitzwilliam estate formalises new tenancy arrangements. Larger grazing farms under solvent tenants replace the pre-Famine patchwork of smallholdings. Estate rents begin to recover." },
  { year: 1854, title: "Chain Migration", text: "Previous emigrants send remittances enabling family members to follow voluntarily. The estate-assisted scheme becomes less necessary as chain migration takes over." },
  { year: 1855, title: "Scheme Winds Down", text: "The formal assisted emigration programme is largely complete. Fewer families depart under estate auspices. The worst of the clearances is over, though emigration continues voluntarily." },
  { year: 1856, title: "Programme Ends", text: "The last passages of the Fitzwilliam assisted emigration scheme are arranged. An estimated ~6,000 individuals have left Coolattin estate over nine years. NLI MS 4975 closes." },
];

const rangeEl  = document.getElementById("ipRange");
const yearDisp = document.getElementById("ipYearDisplay");
const tlCards  = document.getElementById("ipTlCards");

function renderTimeline(yr) {
  yearDisp.textContent = yr;
  tlCards.innerHTML = "";
  const shown = EVENTS.filter(e => e.year <= yr).slice(-4);
  shown.forEach(ev => {
    const d = document.createElement("div");
    d.className = "ip-tl-card";
    if (ev.year === yr) d.style.boxShadow = "0 4px 16px rgba(176,141,87,0.18)";
    d.innerHTML = `<div class="ip-tl-year">${ev.year}</div>
      <h4>${ev.title}</h4><p>${ev.text}</p>`;
    tlCards.appendChild(d);
  });
}
if (rangeEl) {
  rangeEl.addEventListener("input", () => renderTimeline(parseInt(rangeEl.value)));
  renderTimeline(1845);
}

/* ── Steps ────────────────────────────────────────────────── */
const STEPS = [
  {
    title: "Identification of tenants",
    preview: "Agents compiled lists of indebted families with two or more years of arrears.",
    detail: `<p>Estate agents — primarily W. Wainwright — compiled lists of tenants whose arrears had accumulated to a level making repayment unrealistic. Families with two or more years of unpaid rent were the primary candidates. The emigration books (NLI MS 4974–4975) record names, townlands, household compositions, and arrears amounts.</p>
    <p>Preference was typically given to families of manageable size. The records show that agents exercised discretion — some families were declined passage, others were prioritised.</p>`
  },
  {
    title: "Surrender of holdings",
    preview: "Tenants surrendered their cabin, outbuildings, and land in exchange for passage.",
    detail: `<p>Tenants were required to formally surrender their holdings — cabin, potato ground, and any outbuildings — as a condition of receiving passage. Arrears were written off on surrender. The transaction is recorded in the emigration books.</p>
    <p>Agent correspondence confirms that cabins were demolished immediately, or within days, of the family's departure. The purpose, stated explicitly in some letters, was to prevent return and to clear the ground for consolidation.</p>`
  },
  {
    title: "Departure from New Ross",
    preview: "Passages were arranged through William Graves & Son of New Ross, Co. Wexford.",
    detail: `<p>The estate contracted William Graves & Son, a shipping firm based in New Ross, Co. Wexford, to arrange passages. Families typically travelled by road or cart to New Ross and embarked there or at nearby quays on the River Barrow.</p>
    <p>Provisions, bedding, and in some cases clothing were supplied for the voyage. Vessels were bound primarily for Quebec City. The voyage typically took four to eight weeks depending on weather conditions.</p>`
  },
  {
    title: "Atlantic crossing",
    preview: "Voyages to Quebec City — several weeks at sea, basic but recorded conditions.",
    detail: `<p>The Atlantic crossing was physically arduous. The emigration books record that provisions were supplied, and agent correspondence describes conditions as adequate by the standards required under the Passenger Acts. However, ships were crowded and conditions at sea were unpredictable.</p>
    <p>The death rate on these crossings was lower than on the notorious 'coffin ships' of 1847, partly because the Fitzwilliam scheme operated primarily from 1848 onwards when some of the worst conditions had eased.</p>`
  },
  {
    title: "Arrival in Canada",
    preview: "Families arrived at Quebec City — largely without further estate support.",
    detail: `<p>On arrival in Quebec City, emigrants were processed at Grosse Île quarantine station before continuing to the city. The estate provided no further organised support beyond the voyage. Families joined existing Irish communities or dispersed into the interior.</p>
    <p>Library and Archives Canada holds arrival records for many of these voyages. Some families can be traced into Canadian and US census records; others disappear from the archive entirely.</p>`
  },
  {
    title: "Land consolidation on the estate",
    preview: "Cleared land was merged into larger grazing farms — the more profitable post-Famine use.",
    detail: `<p>The cleared holdings were merged and let to solvent tenants, typically as larger grazing farms. This reflected a broader shift in Irish agriculture after the Famine — from labour-intensive tillage to cattle and sheep grazing, which required fewer workers and generated higher rents per acre.</p>
    <p>The physical traces of the former smallholdings — field boundaries, ruined walls, ridge-and-furrow patterns — can still be seen in parts of south Wicklow today.</p>`
  },
];

const stepsEl = document.getElementById("ipSteps");
if (stepsEl) {
  STEPS.forEach((s, i) => {
    const div = document.createElement("div");
    div.className = "ip-step";
    div.innerHTML = `
      <div class="ip-step-num">${i + 1}</div>
      <div class="ip-step-body">
        <div class="ip-step-title">${s.title}</div>
        <div class="ip-step-preview">${s.preview}</div>
        <div class="ip-step-expanded">${s.detail}</div>
      </div>`;
    div.addEventListener("click", () => {
      const isOpen = div.classList.contains("open");
      stepsEl.querySelectorAll(".ip-step").forEach(el => el.classList.remove("open"));
      if (!isOpen) div.classList.add("open");
    });
    stepsEl.appendChild(div);
  });
  // Open first by default
  stepsEl.querySelector(".ip-step").classList.add("open");
}

/* ── Philanthropy slider ──────────────────────────────────── */
const verdictRange = document.getElementById("ipVerdictRange");
const philCol  = document.getElementById("ipPhilCol");
const profitCol = document.getElementById("ipProfitCol");
const philPct  = document.getElementById("ipPhilPct");
const profitPct = document.getElementById("ipProfitPct");

function updateVerdict(val) {
  const profit = val / 100;
  const phil = 1 - profit;
  philCol.style.opacity   = Math.max(0.35, phil + 0.15);
  profitCol.style.opacity = Math.max(0.35, profit + 0.15);
  philPct.textContent   = Math.round(phil * 100) + "% weight";
  profitPct.textContent = Math.round(profit * 100) + "% weight";
}
if (verdictRange) {
  verdictRange.addEventListener("input", () => updateVerdict(parseInt(verdictRange.value)));
  updateVerdict(50);
}

/* ── Video placeholder ────────────────────────────────────── */
window.loadVideo = function () {
  const area = document.getElementById("ipVideoArea");
  if (!area) return;
  // Replace placeholder with a note — no specific video URL verified
  area.innerHTML = `
    <div style="padding:40px 24px;color:rgba(255,255,255,0.6);text-align:center;">
      <div style="font-size:2rem;margin-bottom:12px;">🎬</div>
      <div style="font-family:var(--font-display);font-size:1.1rem;color:rgba(255,255,255,0.8);margin-bottom:8px;">
        Video content coming soon
      </div>
      <div style="font-size:0.85rem;line-height:1.6;">
        Drone footage and heritage documentary content for Coolattin Estate<br>
        will be embedded here. Suggested sources: Wicklow County Council<br>
        heritage recordings, Coolattin Lives project media.
      </div>
    </div>`;
};

})();

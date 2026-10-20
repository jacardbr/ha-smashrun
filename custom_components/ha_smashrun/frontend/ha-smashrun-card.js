/*
 * Smashrun card
 *
 * Three tabs, fed by the integration's `ha_smashrun/summary` WebSocket command:
 *   - Statistics:       distance through the year, one line per year
 *                       (cumulative or per month) or bar charts of the
 *                       volume per week, month and year, with year filters
 *                       and a year-over-year table
 *   - Personal records: fastest 5K, 10K, 21K and 42K in the selected years
 *   - Count:            number of runs per distance (<5K, 5K, 10K, 21K, 42K)
 *                       in the selected years
 * Plain JavaScript, no dependencies. Distances follow the Home Assistant unit
 * system (km or mi); texts follow its language (English or Portuguese).
 *
 * Usage:
 *   type: custom:ha-smashrun-card
 *
 * Options: title, default_tab (statistics | records | count), entry_id,
 * statistics_years / records_years / count_years (years selected at first,
 * 0 = every year) and range_5k / range_10k / range_21k / range_42k /
 * range_max (count ranges in km).
 */

const DOMAIN = "ha_smashrun";
const TAG = "ha-smashrun-card";
const TABS = ["statistics", "records", "count"];
const RECORD_KEYS = ["5k", "10k", "21k", "42k"];
const KM_PER_MILE = 1.609344;
// Years selected at first in each tab (card options; 0 = every year).
const YEAR_OPTIONS = { statistics_years: 2, records_years: 0, count_years: 1 };
// Count ranges (card options, km): where the 5K, 10K, 21K and 42K start,
// then where the 42K ends. The <5K range starts at 0.
const RANGE_OPTIONS = { range_5k: 4.5, range_10k: 9.5, range_21k: 20.5, range_42k: 41.5, range_max: 48 };
const REFRESH_MS = 30 * 60 * 1000; // reload even without a sensor change

// Newest year first: HA blue, then distinct hues.
const PALETTE = [
  "#03a9f4",
  "#ff9800",
  "#4caf50",
  "#e91e63",
  "#9c27b0",
  "#009688",
  "#ffc107",
  "#795548",
  "#3f51b5",
  "#8bc34a",
];

const STRINGS = {
  en: {
    statistics: "Statistics",
    records: "Personal records",
    count: "Count",
    cumulative: "Cumulative",
    monthly: "Monthly",
    volume: "Volume",
    detail: "Detail",
    week: "Week",
    week_n: "Week {n}",
    month: "Month",
    year: "Year",
    distance: "Distance",
    runs: "Runs",
    per_run: "Per run",
    vs_prev: "Comparison",
    from_run: "from a {d} run",
    records_note:
      "Estimated from the average pace of runs at least this long (Smashrun's API has no splits for every run). For races of the exact distance it is the real time.",
    no_record: "No run this long yet",
    no_data: "No runs yet.",
    no_runs_selected: "No runs in the selected years.",
    loading: "Loading…",
    error: "Could not load Smashrun data: {e}",
    open_run: "Open on Smashrun",
    count_note: "Each run counts in the range of its distance. Runs outside these ranges are not counted.",
    select_years: "Years",
    all: "All",
    total: "Total",
    all_ranges: "All ranges",
    none: "None",
    same_period: "Year in progress: compared with January to {m} of the year below it.",
  },
  pt: {
    statistics: "Estatísticas",
    records: "Recordes pessoais",
    count: "Contagem",
    cumulative: "Acumulado",
    monthly: "Mensal",
    volume: "Volume",
    detail: "Detalhes",
    week: "Semana",
    week_n: "Semana {n}",
    month: "Mês",
    year: "Ano",
    distance: "Distância",
    runs: "Corridas",
    per_run: "Por corrida",
    vs_prev: "Comparação",
    from_run: "numa corrida de {d}",
    records_note:
      "Estimado pelo pace médio das corridas com pelo menos essa distância (a API do Smashrun não traz as parciais de todas as corridas). Para provas da distância exata, é o tempo real.",
    no_record: "Nenhuma corrida com essa distância ainda",
    no_data: "Nenhuma corrida ainda.",
    no_runs_selected: "Nenhuma corrida nos anos selecionados.",
    loading: "Carregando…",
    error: "Não foi possível carregar os dados do Smashrun: {e}",
    open_run: "Abrir no Smashrun",
    count_note: "Cada corrida conta na faixa da sua distância. Corridas fora dessas faixas não entram.",
    select_years: "Anos",
    all: "Todos",
    total: "Total",
    all_ranges: "Todas as faixas",
    none: "Nenhum",
    same_period: "Ano em andamento: comparado com janeiro a {m} do ano abaixo dele.",
  },
};

const RECORD_LABELS = { "5k": "5K", "10k": "10K", "21k": "21K", "42k": "42K" };
const COUNT_KEYS = ["lt5k", ...RECORD_KEYS];
const COUNT_LABELS = { lt5k: "<5K", ...RECORD_LABELS };

function esc(value) {
  return String(value === undefined || value === null ? "" : value).replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );
}

// 3725 -> "1:02:05", 325 -> "5:25"
function fmtDuration(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return "—";
  const total = Math.round(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

// Rounded-up axis maximum and step: 0, step, 2*step... >= max.
function niceScale(max, ticks = 4) {
  if (!(max > 0)) return { max: 1, step: 1 };
  const raw = max / ticks;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((f) => f * mag).find((s) => s >= raw);
  return { max: Math.ceil(max / step) * step, step };
}

// ISO week (Monday to Sunday) of a date: { year, week }.
function isoWeek(date) {
  const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
  const day = d.getUTCDay() || 7;
  d.setUTCDate(d.getUTCDate() + 4 - day); // Thursday of the same week
  const start = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  return { year: d.getUTCFullYear(), week: Math.ceil(((d - start) / 86400000 + 1) / 7) };
}

class HaSmashrunCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = null;
    this._hass = null;
    this._data = null;
    this._error = null;
    this._loading = false;
    this._loadedFor = null;
    this._loadedAt = 0;
    this._tab = "statistics";
    this._mode = "cumulative";
    this._showTable = false; // year table under the line chart (Detail button)
    this._years = null; // selected years in the chart (Set), null = default
    this._recordYears = null; // selected years in the records (Set), null = default
    this._countYears = null; // selected years in the count (Set), null = default
    this._lang = null;
    this._width = 0; // body width in px, for the chart
    this._resize = null;
  }

  connectedCallback() {
    if (typeof ResizeObserver !== "function" || this._resize) return;
    this._resize = new ResizeObserver((entries) => {
      // Rounded so small layout jitters do not redraw the chart.
      const width = Math.round(entries[0].contentRect.width / 10) * 10;
      if (width && width !== this._width) {
        this._width = width;
        if (this._tab === "statistics" && this._data) this._render();
      }
    });
    this._resize.observe(this);
  }

  disconnectedCallback() {
    (this._scrollWatch || []).forEach((ro) => ro.disconnect());
    this._scrollWatch = [];
    if (this._resize) this._resize.disconnect();
    this._resize = null;
  }

  // ---- Lovelace card API -------------------------------------------------

  static getStubConfig() {
    return {};
  }

  static getConfigForm() {
    return {
      schema: [
        { name: "title", selector: { text: {} } },
        {
          name: "default_tab",
          selector: {
            select: {
              mode: "dropdown",
              options: TABS.map((value) => ({ value, label: STRINGS.en[value] })),
            },
          },
        },
        { name: "entry_id", selector: { config_entry: { integration: DOMAIN } } },
        {
          name: "years",
          type: "expandable",
          flatten: true,
          title: "Years selected at first (0 = every year)",
          schema: Object.keys(YEAR_OPTIONS).map((name) => ({
            name,
            default: YEAR_OPTIONS[name],
            selector: { number: { min: 0, max: 50, step: 1, mode: "box" } },
          })),
        },
        {
          name: "count_ranges",
          type: "expandable",
          flatten: true,
          title: "Count ranges (km)",
          schema: Object.keys(RANGE_OPTIONS).map((name) => ({
            name,
            default: RANGE_OPTIONS[name],
            selector: { number: { min: 0.1, max: 500, step: 0.1, mode: "box", unit_of_measurement: "km" } },
          })),
        },
      ],
      computeLabel: (schema) =>
        ({
          title: "Title",
          default_tab: "Starting tab",
          entry_id: "Smashrun account",
          statistics_years: "Statistics",
          records_years: "Personal records",
          count_years: "Count",
          range_5k: "5K starts at",
          range_10k: "10K starts at",
          range_21k: "21K starts at",
          range_42k: "42K starts at",
          range_max: "42K ends at",
        })[schema.name],
    };
  }

  setConfig(config) {
    if (!config || typeof config !== "object") throw new Error("Invalid configuration");
    if (config.default_tab !== undefined && !TABS.includes(config.default_tab)) {
      throw new Error("default_tab must be statistics, records or count");
    }
    for (const [key, n] of Object.entries(config)) {
      if (key in YEAR_OPTIONS && !(Number.isInteger(Number(n)) && Number(n) >= 0)) {
        throw new Error(`${key} must be a whole number, 0 for every year`);
      }
    }
    const limits = Object.keys(RANGE_OPTIONS).map((k) =>
      Number(config[k] === undefined || config[k] === "" ? RANGE_OPTIONS[k] : config[k])
    );
    if (limits.some((km, i) => !(km > (i ? limits[i - 1] : 0)))) {
      throw new Error("Count ranges must go up: range_5k < range_10k < range_21k < range_42k < range_max");
    }
    const old = this._config;
    const reload =
      old && (old.entry_id !== config.entry_id || this._countLimits().join() !== limits.join());
    this._config = { ...config };
    this._tab = config.default_tab || this._tab;
    // A new "years selected at first" option applies right away (editor preview).
    if (old && old.statistics_years !== config.statistics_years) this._years = null;
    if (old && old.records_years !== config.records_years) this._recordYears = null;
    if (old && old.count_years !== config.count_years) this._countYears = null;
    if (reload) {
      this._loadedFor = null;
      this._data = null;
      this._maybeLoad();
    }
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    const lang = this._language();
    if (lang !== this._lang) {
      this._lang = lang;
      this._render();
    }
    this._maybeLoad();
  }

  getCardSize() {
    return 7;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: 7 };
  }

  // ---- data --------------------------------------------------------------

  _language() {
    const hass = this._hass;
    return (hass && ((hass.locale && hass.locale.language) || hass.language)) || "en";
  }

  _t(key, vars) {
    const table = this._language().toLowerCase().startsWith("pt") ? STRINGS.pt : STRINGS.en;
    let text = table[key] || STRINGS.en[key] || key;
    for (const [k, v] of Object.entries(vars || {})) text = text.replace(`{${k}}`, v);
    return text;
  }

  _imperial() {
    const units = this._hass && this._hass.config && this._hass.config.unit_system;
    return Boolean(units && units.length === "mi");
  }

  _dist(km) {
    return this._imperial() ? km / KM_PER_MILE : km;
  }

  _unit() {
    return this._imperial() ? "mi" : "km";
  }

  _num(value, digits = 1) {
    try {
      return value.toLocaleString(this._language(), {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      });
    } catch (e) {
      return value.toFixed(digits);
    }
  }

  _fmtDist(km, digits = 1) {
    return `${this._num(this._dist(km), digits)} ${this._unit()}`;
  }

  _fmtPace(secondsPerKm) {
    const perUnit = this._imperial() ? secondsPerKm * KM_PER_MILE : secondsPerKm;
    return `${fmtDuration(perUnit)} /${this._unit()}`;
  }

  _fmtDate(iso, opts) {
    const when = new Date(iso);
    if (Number.isNaN(when.getTime())) return "";
    try {
      return when.toLocaleDateString(this._language(), opts || { year: "numeric", month: "short", day: "numeric" });
    } catch (e) {
      return when.toLocaleDateString();
    }
  }

  _monthName(i) {
    try {
      return new Date(2000, i, 1).toLocaleDateString(this._language(), { month: "short" }).replace(".", "");
    } catch (e) {
      return String(i + 1);
    }
  }

  // State of the integration's "total runs" sensor: changes when a run is added.
  _signature() {
    const entities = (this._hass && this._hass.entities) || {};
    const ids = Object.keys(entities).filter(
      (id) =>
        entities[id].platform === DOMAIN &&
        (entities[id].translation_key === "total_runs" || entities[id].translation_key === "total_distance")
    );
    return ids
      .sort()
      .map((id) => {
        const st = this._hass.states[id];
        return st ? st.state : "";
      })
      .join("|");
  }

  async _maybeLoad() {
    const hass = this._hass;
    if (!hass || !this._config || this._loading || typeof hass.callWS !== "function") return;
    const sig = this._signature();
    const stale = Date.now() - this._loadedAt > REFRESH_MS;
    if (this._loadedFor === sig && !stale) return;
    this._loadedFor = sig;
    this._loading = true;
    try {
      const msg = { type: `${DOMAIN}/summary` };
      if (this._config.entry_id) msg.entry_id = this._config.entry_id;
      const limits = this._countLimits();
      if (limits.some((km, i) => km !== Object.values(RANGE_OPTIONS)[i])) msg.count_limits = limits;
      this._data = await hass.callWS(msg);
      this._error = null;
    } catch (err) {
      this._error = (err && (err.message || err.code)) || String(err);
    } finally {
      this._loading = false;
      this._loadedAt = Date.now();
    }
    this._render();
  }

  _yearList() {
    return Object.keys((this._data && this._data.years) || {}).sort();
  }

  _yearColor(year) {
    const years = this._yearList().slice().reverse(); // newest first
    const i = years.indexOf(year);
    return PALETTE[(i < 0 ? 0 : i) % PALETTE.length];
  }

  // The latest N years (card option), or every year for 0.
  _initialYears(option) {
    const years = this._yearList();
    const n = Number(this._config[option] === undefined ? YEAR_OPTIONS[option] : this._config[option]);
    return new Set(n > 0 ? years.slice(-n) : years);
  }

  _countLimits() {
    return Object.keys(RANGE_OPTIONS).map((k) =>
      Number(this._config[k] === undefined || this._config[k] === "" ? RANGE_OPTIONS[k] : this._config[k])
    );
  }

  _selectedYears() {
    const years = this._yearList();
    if (this._years === null) this._years = this._initialYears("statistics_years");
    return years.filter((y) => this._years.has(y));
  }

  _selectedRecordYears() {
    const years = this._yearList();
    if (this._recordYears === null) this._recordYears = this._initialYears("records_years");
    return years.filter((y) => this._recordYears.has(y));
  }

  _selectedCountYears() {
    const years = this._yearList();
    if (this._countYears === null) this._countYears = this._initialYears("count_years");
    return years.filter((y) => this._countYears.has(y));
  }

  // Year filter: All, one chip per year (newest first), then None. `set` is
  // the property holding the selection (_years, _recordYears, _countYears).
  _chips(selected, attr, set) {
    const bulk = (which, on) =>
      `<button class="chip bulk${on ? " on" : ""}" data-bulk="${which}" data-set="${set}" aria-pressed="${on}">${esc(
        this._t(which)
      )}</button>`;
    const years = this._yearList()
      .slice()
      .reverse()
      .map((y) => {
        const on = selected.includes(y);
        return `<button class="chip${on ? " on" : ""}" ${attr}="${y}" aria-pressed="${on}" style="--c:${this._yearColor(
          y
        )}"><span class="dot"></span>${y}</button>`;
      })
      .join("");
    return bulk("all", selected.length === this._yearList().length) + years + bulk("none", !selected.length);
  }

  // ---- rendering ---------------------------------------------------------

  _render() {
    if (!this._config) return;
    const title = this._config.title;
    const tabs = TABS.map(
      (tab) =>
        `<button class="tab${tab === this._tab ? " active" : ""}" data-tab="${tab}" role="tab" aria-selected="${
          tab === this._tab
        }">${esc(this._t(tab))}</button>`
    ).join("");

    let body;
    if (this._error) body = `<div class="empty">${esc(this._t("error", { e: this._error }))}</div>`;
    else if (!this._data) body = `<div class="empty">${esc(this._t("loading"))}</div>`;
    else if (!this._yearList().length) body = `<div class="empty">${esc(this._t("no_data"))}</div>`;
    else if (this._tab === "statistics") body = this._statistics();
    else if (this._tab === "records") body = this._records();
    else body = this._count();

    this.shadowRoot.innerHTML = `
      <style>${STYLE}</style>
      <ha-card>
        ${title ? `<div class="title">${esc(title)}</div>` : ""}
        <div class="tabs" role="tablist">${tabs}</div>
        <div class="body">${body}</div>
      </ha-card>`;
    this._bind();
  }

  _bind() {
    const root = this.shadowRoot;
    // Scrolling bar charts open at the current week or month. The scroll is
    // applied again whenever the chart gets its real size (a card drawn
    // while hidden or before it is laid out has none yet), until the person
    // scrolls it.
    (this._scrollWatch || []).forEach((ro) => ro.disconnect());
    this._scrollWatch = [];
    root.querySelectorAll(".scroller").forEach((el) => {
      const target = Number(el.dataset.scroll) || 0;
      let touched = false;
      const apply = () => {
        if (!touched && el.scrollWidth > el.clientWidth) el.scrollLeft = target;
      };
      apply();
      if (typeof requestAnimationFrame === "function") requestAnimationFrame(apply);
      if (typeof ResizeObserver === "function") {
        const ro = new ResizeObserver(apply);
        ro.observe(el);
        this._scrollWatch.push(ro);
      }
      ["wheel", "pointerdown", "touchstart", "keydown"].forEach((type) =>
        el.addEventListener(type, () => (touched = true), { passive: true })
      );
      el.addEventListener("scroll", () => this._hoverChart(el.closest(".chart-wrap"), null), { passive: true });
    });
    // Line chart: tooltip on hover. Bar charts: on click on a column; a
    // second click on it, or a click anywhere else, closes it.
    root.querySelectorAll(".chart-wrap:not(.bars)").forEach((wrap) => {
      wrap.addEventListener("pointermove", (ev) => this._hoverChart(wrap, ev));
      wrap.addEventListener("pointerdown", (ev) => this._hoverChart(wrap, ev));
      wrap.addEventListener("pointerleave", () => this._hoverChart(wrap, null));
    });
    const bars = [...root.querySelectorAll(".chart-wrap.bars")];
    bars.forEach((wrap) =>
      wrap.addEventListener("click", (ev) => {
        const before = wrap.dataset.open;
        bars.filter((w) => w !== wrap).forEach((w) => this._hoverChart(w, null));
        const col = this._hoverChart(wrap, ev);
        if (col !== null && String(col) === before) this._hoverChart(wrap, null);
      })
    );
    const card = root.querySelector("ha-card");
    if (card && bars.length) {
      card.addEventListener("click", (ev) => {
        if (!ev.composedPath().some((el) => el.classList && el.classList.contains("bars"))) {
          bars.forEach((w) => this._hoverChart(w, null));
        }
      });
    }
    root.querySelectorAll("[data-tab]").forEach((btn) =>
      btn.addEventListener("click", () => {
        this._tab = btn.dataset.tab;
        this._render();
      })
    );
    const detail = root.querySelector("button.detail");
    if (detail) {
      detail.addEventListener("click", () => {
        this._showTable = !this._showTable;
        this._render();
      });
    }
    root.querySelectorAll("[data-mode]").forEach((btn) =>
      btn.addEventListener("click", () => {
        this._mode = btn.dataset.mode;
        this._render();
      })
    );
    root.querySelectorAll("[data-year]").forEach((btn) =>
      btn.addEventListener("click", () => {
        const year = btn.dataset.year;
        this._selectedYears();
        if (this._years.has(year)) this._years.delete(year);
        else this._years.add(year);
        this._render();
      })
    );
    root.querySelectorAll("[data-bulk]").forEach((btn) =>
      btn.addEventListener("click", () => {
        this[btn.dataset.set] = new Set(btn.dataset.bulk === "all" ? this._yearList() : []);
        this._render();
      })
    );
    root.querySelectorAll("[data-count-year]").forEach((btn) =>
      btn.addEventListener("click", () => {
        const year = btn.dataset.countYear;
        this._selectedCountYears();
        if (this._countYears.has(year)) this._countYears.delete(year);
        else this._countYears.add(year);
        this._render();
      })
    );
    root.querySelectorAll("[data-record-year]").forEach((btn) =>
      btn.addEventListener("click", () => {
        const year = btn.dataset.recordYear;
        this._selectedRecordYears();
        if (this._recordYears.has(year)) this._recordYears.delete(year);
        else this._recordYears.add(year);
        this._render();
      })
    );
  }

  // Shows the column under the pointer (a month, week or year): guide line,
  // highlighted dots or bars and a tooltip with each year's distance, newest
  // first. Each chart registers how to find its columns in this._charts
  // (see _statistics and _barChart). null hides the tooltip. Returns the
  // column shown, or null.
  _hoverChart(wrap, ev) {
    const svg = wrap.querySelector("svg.plot") || wrap.querySelector("svg");
    const tip = wrap.querySelector(".tip");
    const guide = svg.querySelector(".guide");
    const info = this._charts && this._charts[Number(wrap.dataset.chart)];
    const hide = () => {
      tip.hidden = true;
      guide.setAttribute("visibility", "hidden");
      svg.querySelectorAll(".hot").forEach((el) => el.classList.remove("hot"));
      delete wrap.dataset.open;
      return null;
    };
    if (!ev || !info) return hide();
    const rect = svg.getBoundingClientRect();
    if (!rect.width) return hide();
    const col = info.index(((ev.clientX - rect.left) / rect.width) * info.W);
    const rows = col === null ? [] : info.rows(col);
    if (!rows.length) return hide();

    const px = info.colX(col);
    guide.setAttribute("x1", px);
    guide.setAttribute("x2", px);
    guide.setAttribute("visibility", "visible");
    svg.querySelectorAll("[data-col]").forEach((el) => el.classList.toggle("hot", Number(el.dataset.col) === col));
    // Arrow and difference in the arrow's color ("▲ 150 km", no sign)
    // against the row's base (see _withBase); none for the baseline.
    const arrow = (r) => {
      if (r.base === undefined || Math.abs(r.v - r.base) < 0.5) return `<span class="diff"><span class="arrow"></span></span>`;
      const up = r.v > r.base;
      const diff = `${this._num(Math.abs(r.v - r.base), 0)} ${info.unit}`;
      return `<span class="diff ${up ? "up" : "down"}"><span class="arrow ${up ? "up" : "down"}">${
        up ? "▲" : "▼"
      }</span>${esc(diff)}</span>`;
    };
    tip.innerHTML = `<div class="tip-title">${esc(info.title(col))}</div>${rows
      .map(
        (r) => `<div class="tip-row"><span class="dot" style="--c:${r.color}"></span>${esc(r.year)}<span class="tip-value">${esc(
          this._num(r.v, 0)
        )} ${esc(info.unit)}</span>${arrow(r)}</div>`
      )
      .join("")}`;
    tip.hidden = false;
    // Beside the guide, on the side with more room.
    const left = (px / info.W) * rect.width;
    const wrapLeft = rect.left - wrap.getBoundingClientRect().left;
    if (left > rect.width / 2) {
      tip.style.left = "";
      tip.style.right = `${wrap.clientWidth - wrapLeft - left + 10}px`;
    } else {
      tip.style.right = "";
      tip.style.left = `${wrapLeft + left + 10}px`;
    }
    // Never past the card's edges (narrow cards).
    const box = tip.getBoundingClientRect();
    const edge = wrap.getBoundingClientRect();
    if (box.left < edge.left) {
      tip.style.right = "";
      tip.style.left = "0px";
    } else if (box.right > edge.right) {
      tip.style.left = "";
      tip.style.right = "0px";
    }
    wrap.dataset.open = String(col);
    return col;
  }

  // Tooltip rows, newest year first, each compared with the next older one
  // shown (its base); the oldest one shown is the baseline and has none.
  _withBase(rows) {
    const sorted = rows.slice().sort((a, b) => Number(b.year) - Number(a.year));
    return sorted.map((r, i) => ({ ...r, base: sorted[i + 1] ? sorted[i + 1].v : undefined }));
  }

  _monthLong(i) {
    try {
      return new Date(2000, i, 1).toLocaleDateString(this._language(), { month: "long" });
    } catch (e) {
      return this._monthName(i);
    }
  }

  // Vertical bar chart. `groups[i]` holds the bars of column i, oldest year
  // first: { year, color, v }. `labels[i]` is the axis text ("" for none),
  // `titles[i]` the tooltip title. With `basePrevious`, a bar is compared
  // with the column before it (year chart) instead of the other bars of its
  // column. Each bar shows its value above it; bars are at least BAR_MIN px
  // wide so the value fits, and a chart wider than the card scrolls
  // sideways with its axis fixed on the left. `scrollTo` is the column to
  // bring into view at first (the current week or month).
  _barChart({ name, labels, titles, groups, basePrevious = false, scrollTo = null }) {
    const BAR_MIN = 24;
    const AXIS = 46; // width of the fixed axis
    const avail = Math.max(280, (this._width || 632) - 32) - AXIS;
    const H = Math.round(Math.min(210, Math.max(160, avail * 0.34)));
    const M = { l: 6, r: 12, t: 22, b: 24 };
    const n = labels.length;
    const perGroup = Math.max(1, ...groups.map((g) => g.length));
    const ratio = n > 20 ? 0.9 : 0.7;
    const W = Math.round(Math.max(avail, M.l + M.r + (n * perGroup * BAR_MIN) / ratio));
    const band = (W - M.l - M.r) / n;
    const max = Math.max(0, ...groups.flat().map((b) => b.v));
    const scale = niceScale(max);
    const Y = (v) => H - M.b - (v / scale.max) * (H - M.t - M.b);
    const unit = this._unit();

    let grid = "";
    let ticks = "";
    for (let v = 0; v <= scale.max + 1e-9; v += scale.step) {
      grid += `<line x1="0" x2="${W}" y1="${Y(v)}" y2="${Y(v)}" class="grid"/>`;
      ticks += `<text x="${AXIS - 6}" y="${Y(v) + 4}" class="axis" text-anchor="end">${esc(
        this._num(v, scale.step < 1 ? 1 : 0)
      )}</text>`;
    }
    let bars = "";
    let values = "";
    let axis = "";
    groups.forEach((group, i) => {
      // At most 48 px per bar, so a few years do not make huge blocks.
      const inner = Math.min(band * ratio, 48 * Math.max(1, group.length));
      const bw = inner / Math.max(1, group.length);
      group.forEach((b, j) => {
        const x = M.l + i * band + (band - inner) / 2 + j * bw;
        const gap = bw > 4 ? 1 : 0;
        bars += `<rect class="bar" data-col="${i}" x="${x.toFixed(1)}" y="${Y(b.v).toFixed(1)}" width="${Math.max(
          0.5,
          bw - gap
        ).toFixed(1)}" height="${Math.max(0, Y(0) - Y(b.v)).toFixed(1)}" fill="${b.color}"/>`;
        if (Math.round(b.v) > 0) {
          values += `<text class="bar-value" x="${(x + (bw - gap) / 2).toFixed(1)}" y="${(Y(b.v) - 4).toFixed(
            1
          )}" text-anchor="middle">${esc(this._num(b.v, 0))}</text>`;
        }
      });
      if (labels[i]) {
        axis += `<text x="${(M.l + (i + 0.5) * band).toFixed(1)}" y="${H - 6}" class="axis" text-anchor="middle">${esc(
          labels[i]
        )}</text>`;
      }
    });

    const id = this._charts.length;
    this._charts.push({
      W,
      unit,
      index: (sx) => {
        const i = Math.floor((sx - M.l) / band);
        return i >= 0 && i < n ? i : null;
      },
      colX: (i) => M.l + (i + 0.5) * band,
      title: (i) => titles[i],
      rows: (i) => {
        if (!basePrevious) return this._withBase(groups[i]);
        const prev = groups[i - 1] && groups[i - 1][0];
        return groups[i].map((b) => ({ ...b, base: prev ? prev.v : undefined }));
      },
    });
    const scrollX = scrollTo === null ? 0 : Math.max(0, M.l + (scrollTo + 1) * band - avail);
    return `
      <div class="chart-title">${esc(this._t(name))}</div>
      <div class="chart-wrap bars" data-chart="${id}" data-name="${name}" data-cols="${n}" data-ml="${M.l}" data-mr="${M.r}">
        <svg viewBox="0 0 ${AXIS} ${H}" width="${AXIS}" class="chart yaxis" aria-hidden="true">
          ${ticks}
          <text x="${AXIS - 6}" y="10" class="axis unit" text-anchor="end">${unit}</text>
        </svg>
        <div class="scroller" data-scroll="${Math.round(scrollX)}">
          <svg viewBox="0 0 ${W} ${H}" width="${W}" class="chart plot" role="img" aria-label="${esc(
            this._t(name)
          )} (${unit})">
            ${grid}
            <line class="guide" x1="0" x2="0" y1="${M.t}" y2="${H - M.b}" visibility="hidden"/>
            ${bars}${values}${axis}
          </svg>
        </div>
        <div class="tip" hidden></div>
      </div>`;
  }

  // Volume: distance per week, month and year of the selected years.
  _volume(selected) {
    const now = new Date();
    const thisWeek = isoWeek(now);
    const color = (y) => this._yearColor(y);
    const weeks = (this._data && this._data.weeks) || {};

    // Weeks: ISO weeks of each year, up to this week for the current year.
    const has53 = selected.some((y) => (weeks[y] || [])[52] > 0);
    const nWeeks = has53 ? 53 : 52;
    const W = Math.max(280, (this._width || 632) - 32);
    const every = 1; // bars are wide enough for every week number (the chart scrolls)
    const weekGroups = Array.from({ length: nWeeks }, (_, i) =>
      selected
        .filter((y) => Number(y) < thisWeek.year || (Number(y) === thisWeek.year && i + 1 <= thisWeek.week))
        .map((y) => ({ year: y, color: color(y), v: this._dist((weeks[y] || [])[i] || 0) }))
    );
    const week = this._barChart({
      name: "week",
      labels: weekGroups.map((_, i) => (i % every === 0 ? String(i + 1) : "")),
      titles: weekGroups.map((_, i) => this._t("week_n", { n: i + 1 })),
      groups: weekGroups,
      scrollTo: selected.includes(String(thisWeek.year)) ? thisWeek.week - 1 : null,
    });

    // Months, up to this month for the current year.
    const monthGroups = Array.from({ length: 12 }, (_, i) =>
      selected
        .filter((y) => Number(y) < now.getFullYear() || (Number(y) === now.getFullYear() && i <= now.getMonth()))
        .map((y) => ({ year: y, color: color(y), v: this._dist(this._data.years[y].months_km[i]) }))
    );
    const monthStep = W < 420 ? 2 : 1;
    const month = this._barChart({
      name: "month",
      labels: monthGroups.map((_, i) => (i % monthStep === 0 ? this._monthName(i) : "")),
      titles: monthGroups.map((_, i) => this._monthLong(i)),
      groups: monthGroups,
      scrollTo: selected.includes(String(now.getFullYear())) ? now.getMonth() : null,
    });

    // Years, oldest first, each compared with the one before it.
    const year = this._barChart({
      name: "year",
      labels: selected,
      titles: selected,
      groups: selected.map((y) => [
        { year: y, color: color(y), v: this._dist(this._data.years[y].months_km.reduce((a, b) => a + b, 0)) },
      ]),
      basePrevious: true,
    });
    return week + month + year;
  }

  // ---- Statistics tab ----------------------------------------------------

  _statistics() {
    const data = this._data.years;
    const years = this._yearList();
    const selected = this._selectedYears();
    const now = new Date();

    this._charts = [];
    const modes = ["cumulative", "monthly", "volume"]
      .map(
        (m) =>
          `<button class="seg${m === this._mode ? " on" : ""}" data-mode="${m}">${esc(this._t(m))}</button>`
      )
      .join("");
    const chips = this._chips(selected, "data-year", "_years");

    // Series: cumulative = 13 points (start of Jan, then each month end);
    // monthly = 12 points (month centres). The current year stops at this month.
    const series = selected.map((y) => {
      const months = data[y].months_km.map((km) => this._dist(km));
      const last = Number(y) === now.getFullYear() ? now.getMonth() : 11;
      let points;
      if (this._mode === "cumulative") {
        let acc = 0;
        points = [{ x: 0, v: 0 }];
        for (let i = 0; i <= last; i++) {
          acc += months[i];
          points.push({ x: i + 1, v: acc, month: i });
        }
      } else {
        points = months.slice(0, last + 1).map((v, i) => ({ x: i + 0.5, v, month: i }));
      }
      return { year: y, color: this._yearColor(y), points };
    });

    // Drawn at the card's real width so the axis text keeps its size.
    const W = Math.max(280, (this._width || 632) - 32);
    const H = Math.round(Math.min(260, Math.max(200, W * 0.45)));
    const M = { l: 46, r: 12, t: 22, b: 24 };
    const max = Math.max(0, ...series.flatMap((s) => s.points.map((p) => p.v)));
    const scale = niceScale(max);
    const X = (x) => M.l + (x / 12) * (W - M.l - M.r);
    const Y = (v) => H - M.b - (v / scale.max) * (H - M.t - M.b);

    let grid = "";
    for (let v = 0; v <= scale.max + 1e-9; v += scale.step) {
      grid += `<line x1="${M.l}" x2="${W - M.r}" y1="${Y(v)}" y2="${Y(v)}" class="grid"/>`;
      grid += `<text x="${M.l - 6}" y="${Y(v) + 4}" class="axis" text-anchor="end">${esc(
        this._num(v, scale.step < 1 ? 1 : 0)
      )}</text>`;
    }
    let months = "";
    const monthStep = W < 420 ? 2 : 1;
    for (let i = 0; i < 12; i += monthStep) {
      months += `<text x="${X(i + 0.5)}" y="${H - 6}" class="axis" text-anchor="middle">${esc(
        this._monthName(i)
      )}</text>`;
    }
    const unit = this._unit();
    const lines = series
      .map((s) => {
        const d = s.points.map((p, i) => `${i ? "L" : "M"}${X(p.x).toFixed(1)} ${Y(p.v).toFixed(1)}`).join(" ");
        const dots = s.points
          .filter((p) => p.month !== undefined)
          .map(
            (p) =>
              `<circle cx="${X(p.x).toFixed(1)}" cy="${Y(p.v).toFixed(1)}" r="3" fill="${s.color}" data-col="${p.month}"/>`
          )
          .join("");
        return `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>${dots}`;
      })
      .join("");

    // Hover: a guide on the month under the pointer and a tooltip with each
    // selected year's distance (see _hoverChart).
    const offset = this._mode === "cumulative" ? 1 : 0.5; // month end or centre
    this._charts.push({
      W,
      unit,
      index: (sx) => Math.min(11, Math.max(0, Math.round(((sx - M.l) / (W - M.l - M.r)) * 12 - offset))),
      colX: (i) => X(i + offset),
      title: (i) => this._monthLong(i),
      rows: (i) =>
        this._withBase(
          series
            .map((s) => ({ s, p: s.points.find((p) => p.month === i) }))
            .filter((r) => r.p)
            .map((r) => ({ year: r.s.year, color: r.s.color, v: r.p.v }))
        ),
    });
    const lineChart = `
      <div class="chart-wrap" data-chart="0">
        <svg viewBox="0 0 ${W} ${H}" class="chart" role="img" aria-label="${esc(this._t("distance"))} (${unit})">
          ${grid}${months}
          <line class="guide" x1="0" x2="0" y1="${M.t}" y2="${H - M.b}" visibility="hidden"/>
          ${lines}
          <text x="${M.l - 6}" y="10" class="axis unit" text-anchor="end">${unit}</text>
        </svg>
        <div class="tip" hidden></div>
      </div>`;

    // Table of the selected years, newest first. Each year is compared with
    // the next older year shown; the oldest one shown is the baseline.
    // Narrow cards drop the "per run" column.
    const compact = W < 400;
    const rows = selected
      .slice()
      .reverse()
      .map((y, i, list) => {
        const km = data[y].months_km.reduce((a, b) => a + b, 0);
        const prev = list[i + 1];
        let delta = "";
        if (prev) {
          // The year in progress is compared with the same months of the previous one.
          const upTo = Number(y) === now.getFullYear() ? now.getMonth() + 1 : 12;
          const prevKm = data[prev].months_km.slice(0, upTo).reduce((a, b) => a + b, 0);
          // Difference in km, like the chart tooltip: green ▲ or red ▼, no sign.
          const diff = this._dist(km - prevKm);
          const hint = upTo < 12 ? ` title="${esc(this._t("same_period", { m: this._monthName(upTo - 1) }))}"` : "";
          if (Math.abs(diff) < 0.5) delta = `<span${hint}>${esc(this._fmtDist(0, 0))}</span>`;
          else {
            const up = diff > 0;
            delta = `<span class="${up ? "up" : "down"}"${hint}><span class="arrow">${up ? "▲" : "▼"}</span> ${esc(
              this._fmtDist(Math.abs(km - prevKm), 0)
            )}</span>`;
          }
        }
        const runs = data[y].runs;
        return `<tr>
          <td class="year"><span class="dot" style="--c:${this._yearColor(y)}"></span>${y}</td>
          <td class="num">${esc(this._fmtDist(km, 0))}</td>
          <td class="num">${runs}</td>
          ${compact ? "" : `<td class="num">${runs ? esc(this._fmtDist(km / runs, 0)) : "—"}</td>`}
          <td class="num">${delta}</td>
        </tr>`;
      })
      .join("");

    // Detail, below the charts: shows or hides the table (hidden at first,
    // the same for every view).
    const detail = `<button class="detail${this._showTable ? " on" : ""}" aria-expanded="${this._showTable}">${esc(
      this._t("detail")
    )}<span class="chevron">${this._showTable ? "▲" : "▼"}</span></button>`;
    const table = `
      <table class="table">
        <thead><tr>
          <th>${esc(this._t("year"))}</th>
          <th class="num">${esc(this._t("distance"))}</th>
          <th class="num">${esc(this._t("runs"))}</th>
          ${compact ? "" : `<th class="num">${esc(this._t("per_run"))}</th>`}
          <th class="num">${esc(this._t("vs_prev"))}</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
    return `
      <div class="toolbar">
        <div class="segmented">${modes}</div>
      </div>
      <div class="chips" aria-label="${esc(this._t("select_years"))}">${chips}</div>
      ${this._mode === "volume" ? this._volume(selected) : lineChart}
      ${detail}${this._showTable ? table : ""}`;
  }

  // ---- Personal records tab ----------------------------------------------

  _records() {
    const all = this._data.records || {};
    const distances = this._data.record_distances || {};
    const selected = this._selectedRecordYears();
    const everyYear = selected.length === this._yearList().length;

    // Every year selected: the all-time records. Otherwise the fastest of
    // the selected years' records.
    const best = (key) => {
      if (everyYear) return all.all && all.all[key];
      let found = null;
      selected.forEach((y) => {
        const rec = all[y] && all[y][key];
        if (rec && (!found || rec.time_s < found.time_s)) found = rec;
      });
      return found;
    };

    const tiles = RECORD_KEYS.map((key) => {
      const rec = best(key);
      if (!rec) {
        return `<div class="tile empty-tile">
          <div class="label">${RECORD_LABELS[key]}</div>
          <div class="value">—</div>
          <div class="meta">${esc(this._t("no_record"))}</div>
        </div>`;
      }
      const target = distances[key] || 0;
      const longer = target && rec.run_distance_km > target * 1.02;
      const date = esc(this._fmtDate(rec.start));
      // The year as a tag in the year's color; the date shows on hover.
      const year = String(this._recordYearOf(rec));
      const tagAttrs = `class="yeartag" style="--c:${this._yearColor(year)}" title="${date}"`;
      const tag = rec.url
        ? `<a ${tagAttrs} href="${esc(rec.url)}" target="_blank" rel="noopener" aria-label="${esc(
            this._t("open_run")
          )}: ${date}"><span class="dot"></span>${esc(year)}</a>`
        : `<span ${tagAttrs}><span class="dot"></span>${esc(year)}</span>`;
      return `<div class="tile">
        <div class="label">${RECORD_LABELS[key]}</div>
        <div class="value">${esc(fmtDuration(rec.time_s))}</div>
        <div class="pace">${esc(this._fmtPace(rec.pace_s_per_km))}</div>
        <div class="meta">${tag}${
          longer ? ` · ${esc(this._t("from_run", { d: this._fmtDist(rec.run_distance_km) }))}` : ""
        }</div>
      </div>`;
    }).join("");

    return `
      <div class="chips" aria-label="${esc(this._t("select_years"))}">${this._chips(selected, "data-record-year", "_recordYears")}</div>
      <div class="tiles">${tiles}</div>
      <div class="note">${esc(this._t("records_note"))}</div>`;
  }

  // Year of a record's run, in HA's time zone like the server-side grouping.
  _recordYearOf(rec) {
    const tz = this._hass && this._hass.config && this._hass.config.time_zone;
    try {
      return new Intl.DateTimeFormat("en", { year: "numeric", timeZone: tz || undefined }).format(new Date(rec.start));
    } catch (e) {
      return new Date(rec.start).getFullYear();
    }
  }

  // ---- Count tab ---------------------------------------------------------

  _count() {
    const selected = this._selectedCountYears();
    const totals = Object.fromEntries(COUNT_KEYS.map((k) => [k, 0]));
    selected.forEach((y) => {
      const counts = (this._data.years[y] && this._data.years[y].counts) || {};
      COUNT_KEYS.forEach((k) => (totals[k] += counts[k] || 0));
    });
    const max = Math.max(1, ...Object.values(totals));

    const buckets = this._data.count_buckets || {};
    // Only ranges with runs in the selected years.
    const tiles = COUNT_KEYS.filter((key) => totals[key]).map((key) => {
      const [low, high] = buckets[key] || [];
      let range = "";
      if (low !== undefined && !low) range = `< ${this._num(this._dist(high), 1)} ${this._unit()}`;
      else if (low !== undefined) {
        range = `${this._num(this._dist(low), 1)} – ${this._num(this._dist(high), 1)} ${this._unit()}`;
      }
      return `<div class="tile">
        <div class="label">${esc(COUNT_LABELS[key])}</div>
        <div class="value">${esc(totals[key].toLocaleString(this._language()))}</div>
        <div class="meta range">${esc(range)}</div>
        <div class="bar"><span style="width:${(100 * totals[key]) / max}%"></span></div>
      </div>`;
    }).join("");
    const sum = COUNT_KEYS.reduce((acc, k) => acc + totals[k], 0);
    if (!sum) {
      return `
      <div class="chips" aria-label="${esc(this._t("select_years"))}">${this._chips(selected, "data-count-year", "_countYears")}</div>
      <div class="empty">${esc(this._t("no_runs_selected"))}</div>`;
    }
    const total = `<div class="tile total">
        <div class="label">${esc(this._t("total"))}</div>
        <div class="value">${esc(sum.toLocaleString(this._language()))}</div>
        <div class="meta range">${esc(this._t("all_ranges"))}</div>
      </div>`;

    return `
      <div class="chips" aria-label="${esc(this._t("select_years"))}">${this._chips(selected, "data-count-year", "_countYears")}</div>
      <div class="tiles">${tiles}${total}</div>
      <div class="note">${esc(this._t("count_note"))}</div>`;
  }
}

const STYLE = `
  :host { display: block; }
  ha-card { padding-bottom: 8px; overflow: hidden; }
  .title { font-size: 1.25em; font-weight: 500; padding: 16px 16px 0; color: var(--primary-text-color); }
  .tabs { display: flex; gap: 4px; padding: 8px 12px 0; border-bottom: 1px solid var(--divider-color); }
  .tab {
    flex: 1; background: none; border: none; cursor: pointer; padding: 10px 6px;
    font: inherit; font-size: 0.95em; color: var(--secondary-text-color);
    border-bottom: 2px solid transparent; margin-bottom: -1px;
  }
  .tab.active { color: var(--primary-color); border-bottom-color: var(--primary-color); font-weight: 500; }
  .body { padding: 12px 16px 4px; }
  .empty { padding: 24px 0; text-align: center; color: var(--secondary-text-color); }
  .toolbar { display: flex; justify-content: flex-end; gap: 8px; margin-bottom: 8px; }
  /* Cumulative / Monthly: same look as the card's tabs. */
  .segmented { display: flex; flex: 1; gap: 4px; border-bottom: 1px solid var(--divider-color); }
  .seg {
    flex: 1; background: none; border: none; cursor: pointer; padding: 8px 6px;
    font: inherit; font-size: 0.95em; color: var(--secondary-text-color);
    border-bottom: 2px solid transparent; margin-bottom: -1px;
  }
  .seg.on { color: var(--primary-color); border-bottom-color: var(--primary-color); font-weight: 500; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
  .chip {
    display: inline-flex; align-items: center; gap: 6px; cursor: pointer; font: inherit; font-size: 0.85em;
    padding: 4px 10px; border-radius: 16px; border: 1px solid var(--divider-color);
    background: none; color: var(--secondary-text-color); opacity: 0.7;
  }
  .chip.on { opacity: 1; color: var(--primary-text-color); border-color: var(--c); }
  .dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; background: var(--c); margin-right: 6px; vertical-align: middle; }
  .chip .dot { margin-right: 0; }
  .chip.bulk { opacity: 1; }
  .chip.bulk.on { color: var(--primary-color); border-color: var(--primary-color); }
  .chart { width: 100%; height: auto; display: block; }
  .chart-wrap { position: relative; touch-action: pan-y; }
  .chart .guide { stroke: var(--secondary-text-color); stroke-width: 1; stroke-dasharray: 3 3; }
  .detail {
    display: flex; align-items: center; justify-content: center; gap: 6px; width: 100%; margin-top: 6px;
    background: none; border: 1px solid var(--divider-color); border-radius: 8px; padding: 6px;
    cursor: pointer; font: inherit; font-size: 0.85em; color: var(--secondary-text-color);
  }
  .detail.on { color: var(--primary-color); border-color: var(--primary-color); }
  .detail .chevron { font-size: 0.75em; }
  .chart-wrap.bars { display: flex; cursor: pointer; }
  .chart-wrap.bars .yaxis { flex: none; width: 46px; }
  .chart-wrap.bars .scroller { flex: 1; min-width: 0; overflow-x: auto; overflow-y: hidden; scrollbar-width: thin; }
  .chart.plot { width: auto; max-width: none; }
  .chart .bar-value { fill: var(--secondary-text-color); font-size: 10px; font-variant-numeric: tabular-nums; }
  .chart rect.bar { opacity: 0.85; }
  .chart rect.bar.hot { opacity: 1; }
  .chart-title { font-size: 0.85em; font-weight: 500; color: var(--secondary-text-color); margin: 10px 0 2px; }
  .chart circle.hot { r: 5; stroke: var(--card-background-color); stroke-width: 2; }
  .tip {
    position: absolute; top: 6px; pointer-events: none; z-index: 1; min-width: 120px;
    background: var(--card-background-color); color: var(--primary-text-color);
    border: 1px solid var(--divider-color); border-radius: 8px; padding: 6px 10px;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.18); font-size: 0.85em;
  }
  .tip[hidden] { display: none; }
  .tip-title { font-weight: 500; margin-bottom: 4px; text-transform: capitalize; }
  .tip-row { display: flex; align-items: center; gap: 6px; white-space: nowrap; }
  .tip-row .dot { margin-right: 0; }
  .tip-value { margin-left: auto; padding-left: 12px; font-variant-numeric: tabular-nums; }
  .tip .diff {
    display: inline-flex; align-items: center; justify-content: flex-end; gap: 4px;
    min-width: 6em; padding-left: 10px; font-variant-numeric: tabular-nums;
  }
  .tip .arrow { font-size: 0.8em; }
  .tip .up { color: var(--success-color, #43a047); }
  .tip .down { color: var(--error-color, #db4437); }
  .chart .grid { stroke: var(--divider-color); stroke-width: 1; }
  .chart .axis { fill: var(--secondary-text-color); font-size: 11px; }
  .chart .unit { font-size: 10px; }
  .table { width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 0.9em; }
  .table th { text-align: left; font-weight: 500; color: var(--secondary-text-color); padding: 6px 4px; border-bottom: 1px solid var(--divider-color); }
  .table td { padding: 6px 4px; border-bottom: 1px solid var(--divider-color); color: var(--primary-text-color); }
  .table tr:last-child td { border-bottom: none; }
  .table .num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
  .table .year { white-space: nowrap; }
  .up { color: var(--success-color, #43a047); }
  .table .arrow { font-size: 0.8em; }
  .down { color: var(--error-color, #db4437); }
  .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; }
  .tile { border: 1px solid var(--divider-color); border-radius: 12px; padding: 12px; }
  .tile .label { font-size: 0.85em; font-weight: 600; color: var(--primary-color); letter-spacing: 0.03em; }
  .tile .value { font-size: 1.6em; font-weight: 500; margin-top: 4px; font-variant-numeric: tabular-nums; color: var(--primary-text-color); }
  .tile .pace { color: var(--primary-text-color); font-variant-numeric: tabular-nums; }
  .tile .meta { font-size: 0.8em; color: var(--secondary-text-color); margin-top: 6px; }
  .yeartag {
    display: inline-flex; align-items: center; gap: 5px; padding: 1px 8px; border-radius: 12px;
    border: 1px solid var(--c); color: var(--primary-text-color); text-decoration: none; vertical-align: middle;
  }
  .yeartag .dot { margin-right: 0; width: 8px; height: 8px; }
  .tile .range { white-space: nowrap; }
  .tile.total { border-color: var(--primary-color); }
  .tile .bar { height: 4px; border-radius: 2px; background: var(--divider-color); margin-top: 8px; overflow: hidden; }
  .tile .bar span { display: block; height: 100%; background: var(--primary-color); }
  .empty-tile .value { color: var(--disabled-text-color, var(--secondary-text-color)); }
  .note { font-size: 0.78em; color: var(--secondary-text-color); margin-top: 10px; }
`;

if (!customElements.get(TAG)) customElements.define(TAG, HaSmashrunCard);

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === TAG)) {
  window.customCards.push({
    type: TAG,
    name: "Smashrun",
    description: "Distance year over year, personal records (5K to marathon) and the latest runs.",
    documentationURL: "https://github.com/jacardbr/ha-smashrun",
  });
}

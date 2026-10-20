/*
 * Smashrun card
 *
 * Three tabs, fed by the integration's `ha_smashrun/summary` WebSocket command:
 *   - Statistics:       distance through the year, one line per year
 *                       (cumulative or per month), with year filters and a
 *                       year-over-year table
 *   - Personal records: fastest 5K, 10K, 21K and 42K in the selected years
 *   - Count:            number of runs per distance (<5K, 5K, 10K, 21K, 42K)
 *                       in the selected years
 * Plain JavaScript, no dependencies. Distances follow the Home Assistant unit
 * system (km or mi); texts follow its language (English or Portuguese).
 *
 * Usage:
 *   type: custom:ha-smashrun-card
 *
 * Options: title, default_tab (statistics | records | count), entry_id.
 */

const DOMAIN = "ha_smashrun";
const TAG = "ha-smashrun-card";
const TABS = ["statistics", "records", "count"];
const RECORD_KEYS = ["5k", "10k", "21k", "42k"];
const KM_PER_MILE = 1.609344;
const DEFAULT_YEARS = 3; // years selected in the chart at first
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
    year: "Year",
    distance: "Distance",
    runs: "Runs",
    per_run: "Per run",
    vs_prev: "vs. previous",
    from_run: "from a {d} run",
    records_note:
      "Estimated from the average pace of runs at least this long (Smashrun's API has no splits for every run). For races of the exact distance it is the real time.",
    no_record: "No run this long yet",
    no_data: "No runs yet.",
    loading: "Loading…",
    error: "Could not load Smashrun data: {e}",
    open_run: "Open on Smashrun",
    count_note: "Each run counts in the range of its distance. Runs outside these ranges are not counted.",
    select_years: "Years",
    all: "All",
    total: "Total",
    all_ranges: "All ranges",
    none: "None",
    same_period: "Year in progress: compared with January to {m} of the previous year.",
  },
  pt: {
    statistics: "Estatísticas",
    records: "Recordes pessoais",
    count: "Contagem",
    cumulative: "Acumulado",
    monthly: "Mensal",
    year: "Ano",
    distance: "Distância",
    runs: "Corridas",
    per_run: "Por corrida",
    vs_prev: "vs. anterior",
    from_run: "numa corrida de {d}",
    records_note:
      "Estimado pelo pace médio das corridas com pelo menos essa distância (a API do Smashrun não traz as parciais de todas as corridas). Para provas da distância exata, é o tempo real.",
    no_record: "Nenhuma corrida com essa distância ainda",
    no_data: "Nenhuma corrida ainda.",
    loading: "Carregando…",
    error: "Não foi possível carregar os dados do Smashrun: {e}",
    open_run: "Abrir no Smashrun",
    count_note: "Cada corrida conta na faixa da sua distância. Corridas fora dessas faixas não entram.",
    select_years: "Anos",
    all: "Todos",
    total: "Total",
    all_ranges: "Todas as faixas",
    none: "Nenhum",
    same_period: "Ano em andamento: comparado com janeiro a {m} do ano anterior.",
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
    this._years = null; // selected years in the chart (Set), null = default
    this._recordYears = null; // selected years in the records (Set), null = all
    this._countYears = null; // selected years in the count (Set), null = all
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
      ],
      computeLabel: (schema) =>
        ({ title: "Title", default_tab: "Starting tab", entry_id: "Smashrun account" })[schema.name],
    };
  }

  setConfig(config) {
    if (!config || typeof config !== "object") throw new Error("Invalid configuration");
    if (config.default_tab !== undefined && !TABS.includes(config.default_tab)) {
      throw new Error("default_tab must be statistics, records or count");
    }
    const entryChanged = this._config && this._config.entry_id !== config.entry_id;
    this._config = { ...config };
    this._tab = config.default_tab || this._tab;
    if (entryChanged) {
      this._loadedFor = null;
      this._data = null;
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

  _selectedYears() {
    const years = this._yearList();
    if (this._years === null) this._years = new Set(years.slice(-DEFAULT_YEARS));
    return years.filter((y) => this._years.has(y));
  }

  _selectedRecordYears() {
    const years = this._yearList();
    if (this._recordYears === null) this._recordYears = new Set(years);
    return years.filter((y) => this._recordYears.has(y));
  }

  _selectedCountYears() {
    const years = this._yearList();
    if (this._countYears === null) this._countYears = new Set(years);
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
    root.querySelectorAll("[data-tab]").forEach((btn) =>
      btn.addEventListener("click", () => {
        this._tab = btn.dataset.tab;
        this._render();
      })
    );
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

  // ---- Statistics tab ----------------------------------------------------

  _statistics() {
    const data = this._data.years;
    const years = this._yearList();
    const selected = this._selectedYears();
    const now = new Date();

    const modes = ["cumulative", "monthly"]
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
              `<circle cx="${X(p.x).toFixed(1)}" cy="${Y(p.v).toFixed(1)}" r="3" fill="${s.color}"><title>${esc(
                `${this._monthName(p.month)} ${s.year}: ${this._num(p.v)} ${unit}`
              )}</title></circle>`
          )
          .join("");
        return `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>${dots}`;
      })
      .join("");

    const chart = `
      <svg viewBox="0 0 ${W} ${H}" class="chart" role="img" aria-label="${esc(this._t("distance"))} (${unit})">
        ${grid}${months}${lines}
        <text x="${M.l - 6}" y="10" class="axis unit" text-anchor="end">${unit}</text>
      </svg>`;

    // Year-over-year table, newest first. Narrow cards drop the "per run" column.
    const compact = W < 400;
    const rows = years
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
          if (prevKm > 0) {
            const pct = ((km - prevKm) / prevKm) * 100;
            const hint = upTo < 12 ? ` title="${esc(this._t("same_period", { m: this._monthName(upTo - 1) }))}"` : "";
            delta = `<span class="${pct >= 0 ? "up" : "down"}"${hint}>${pct >= 0 ? "+" : ""}${this._num(pct, 0)}%${
              upTo < 12 ? "*" : ""
            }</span>`;
          }
        }
        const runs = data[y].runs;
        return `<tr class="${this._years.has(y) ? "sel" : ""}">
          <td class="year"><span class="dot" style="--c:${this._yearColor(y)}"></span>${y}</td>
          <td class="num">${esc(this._fmtDist(km, 0))}</td>
          <td class="num">${runs}</td>
          ${compact ? "" : `<td class="num">${runs ? esc(this._fmtDist(km / runs)) : "—"}</td>`}
          <td class="num">${delta}</td>
        </tr>`;
      })
      .join("");

    return `
      <div class="toolbar">
        <div class="segmented">${modes}</div>
      </div>
      <div class="chips" aria-label="${esc(this._t("select_years"))}">${chips}</div>
      ${chart}
      <table class="table">
        <thead><tr>
          <th>${esc(this._t("year"))}</th>
          <th class="num">${esc(this._t("distance"))}</th>
          <th class="num">${esc(this._t("runs"))}</th>
          ${compact ? "" : `<th class="num">${esc(this._t("per_run"))}</th>`}
          <th class="num">${esc(this._t("vs_prev"))}</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
      ${
        years.includes(String(now.getFullYear())) && now.getMonth() < 11
          ? `<div class="note">* ${esc(this._t("same_period", { m: this._monthName(now.getMonth()) }))}</div>`
          : ""
      }`;
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
      const dateHtml = rec.url
        ? `<a href="${esc(rec.url)}" target="_blank" rel="noopener" title="${esc(this._t("open_run"))}">${date}</a>`
        : date;
      return `<div class="tile">
        <div class="label">${RECORD_LABELS[key]}</div>
        <div class="value">${esc(fmtDuration(rec.time_s))}</div>
        <div class="pace">${esc(this._fmtPace(rec.pace_s_per_km))}</div>
        <div class="meta"><span class="dot" style="--c:${this._yearColor(
          String(this._recordYearOf(rec))
        )}"></span>${dateHtml}${
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
    const tiles = COUNT_KEYS.map((key) => {
      const [low, high] = buckets[key] || [];
      let range = "";
      if (low !== undefined && !low) range = `< ${this._num(this._dist(high), 1)} ${this._unit()}`;
      else if (low !== undefined) {
        range = `${this._num(this._dist(low), 1)} – ${this._num(this._dist(high), 1)} ${this._unit()}`;
      }
      return `<div class="tile${totals[key] ? "" : " empty-tile"}">
        <div class="label">${esc(COUNT_LABELS[key])}</div>
        <div class="value">${esc(totals[key].toLocaleString(this._language()))}</div>
        <div class="meta range">${esc(range)}</div>
        <div class="bar"><span style="width:${(100 * totals[key]) / max}%"></span></div>
      </div>`;
    }).join("");
    const sum = COUNT_KEYS.reduce((acc, k) => acc + totals[k], 0);
    const total = `<div class="tile total${sum ? "" : " empty-tile"}">
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
  .segmented { display: inline-flex; border: 1px solid var(--divider-color); border-radius: 8px; overflow: hidden; }
  .seg { background: none; border: none; padding: 6px 12px; cursor: pointer; font: inherit; font-size: 0.85em; color: var(--secondary-text-color); }
  .seg.on { background: var(--primary-color); color: var(--text-primary-color, #fff); }
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
  .chart .grid { stroke: var(--divider-color); stroke-width: 1; }
  .chart .axis { fill: var(--secondary-text-color); font-size: 11px; }
  .chart .unit { font-size: 10px; }
  .table { width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 0.9em; }
  .table th { text-align: left; font-weight: 500; color: var(--secondary-text-color); padding: 6px 4px; border-bottom: 1px solid var(--divider-color); }
  .table td { padding: 6px 4px; border-bottom: 1px solid var(--divider-color); color: var(--primary-text-color); }
  .table tr:last-child td { border-bottom: none; }
  .table .num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
  .table tr.sel td:first-child { font-weight: 500; }
  .table .year { white-space: nowrap; }
  .up { color: var(--success-color, #43a047); }
  .down { color: var(--error-color, #db4437); }
  .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; }
  .tile { border: 1px solid var(--divider-color); border-radius: 12px; padding: 12px; }
  .tile .label { font-size: 0.85em; font-weight: 600; color: var(--primary-color); letter-spacing: 0.03em; }
  .tile .value { font-size: 1.6em; font-weight: 500; margin-top: 4px; font-variant-numeric: tabular-nums; color: var(--primary-text-color); }
  .tile .pace { color: var(--primary-text-color); font-variant-numeric: tabular-nums; }
  .tile .meta { font-size: 0.8em; color: var(--secondary-text-color); margin-top: 6px; }
  .tile .meta a { color: inherit; }
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

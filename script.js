// frontend/app.js
// Utiliza los endpoints existentes de FastAPI:
// POST /previsualizar-credito
// POST /previsualizar-credito/excel

(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const SVG_NS = "http://www.w3.org/2000/svg";

  const defaults = {
    monto: 30000000,
    tasa: 18.5,
    plazo: 60,
    extra: 200000,
    seguro: 1200
  };

  const fields = ["monto", "tasa", "plazo", "extra", "seguro"];

  const money = new Intl.NumberFormat("es-CO", {
    style: "currency",
    currency: "COP",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  });

  const pesos = new Intl.NumberFormat("es-CO", {
    maximumFractionDigits: 0
  });

  const decimals = new Intl.NumberFormat("es-CO", {
    maximumFractionDigits: 1
  });

  const monthFormat = new Intl.DateTimeFormat("es-CO", {
    month: "short",
    year: "numeric",
    timeZone: "UTC"
  });

  const fullDate = new Intl.DateTimeFormat("es-CO", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC"
  });

  const state = {
    result: null,
    base: null,
    params: null,
    page: 0,
    generation: 0,
    controller: null,
    timer: null,
    chart: null,
    downloading: false,
    extraBefore: 200000
  };

  const baseCache = new Map();

  const fmt = (value) => money.format(Number(value) || 0);

  const sumCents = (rows, field) =>
    rows.reduce(
      (total, row) => total + Math.round(row[field] * 100),
      0
    ) / 100;

  function compact(value) {
    if (value >= 1e9) {
      return `$${decimals.format(value / 1e9)} mil M`;
    }

    if (value >= 1e6) {
      return `$${decimals.format(value / 1e6)} M`;
    }

    if (value >= 1e3) {
      return `$${decimals.format(value / 1e3)} mil`;
    }

    return `$${pesos.format(value)}`;
  }

  function svgElement(name, attributes = {}) {
    const element = document.createElementNS(SVG_NS, name);

    for (const [key, value] of Object.entries(attributes)) {
      element.setAttribute(key, String(value));
    }

    return element;
  }

  function addMonths(dateString, months) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(dateString)) {
      return null;
    }

    const [year, month, day] = dateString.split("-").map(Number);
    const final = new Date(Date.UTC(year, month - 1 + months, 1));

    const daysInMonth = new Date(
      Date.UTC(final.getUTCFullYear(), final.getUTCMonth() + 1, 0)
    ).getUTCDate();

    final.setUTCDate(Math.min(day, daysInMonth));

    return Number.isNaN(final.getTime()) ? null : final;
  }

  function defaultDate() {
    const parts = new Intl.DateTimeFormat("en", {
      timeZone: "America/Bogota",
      year: "numeric",
      month: "2-digit",
      day: "2-digit"
    }).formatToParts(new Date());

    const get = (type) => parts.find((part) => part.type === type).value;
    const today = `${get("year")}-${get("month")}-${get("day")}`;

    return addMonths(today, 1).toISOString().slice(0, 10);
  }

  $("primera-cuota").value = defaultDate();

  function fieldNumber(id) {
    if (["monto", "extra"].includes(id)) {
      const value = $(id).value.trim().replace(/^\$\s*/, "");

      // Pesos enteros: acepta 30000000 o 30.000.000.
      const valid = /^(?:\d+|\d{1,3}(?:\.\d{3})+)$/.test(value);

      return valid ? Number(value.replaceAll(".", "")) : NaN;
    }

    return $(id).value.trim() === "" ? NaN : Number($(id).value);
  }

  function validate() {
    const limits = {
      monto: [1, 1e12],
      tasa: [0, 1000],
      plazo: [1, 1200],
      extra: [0, 1e12],
      seguro: [0, 1e6]
    };

    const messages = {
      monto: "Ingresa pesos enteros entre $1 y $1 billón.",
      tasa: "Ingresa una tasa entre 0 % y 1.000 % EA.",
      plazo: "Elige un número entero entre 1 y 1.200 meses.",
      extra: "Ingresa pesos enteros entre $0 y $1 billón.",
      seguro: "Ingresa un seguro entre $0 y $1 millón por millón prestado."
    };

    const values = {};
    let valid = true;

    for (const id of fields) {
      const value = fieldNumber(id);

      const invalid =
        !Number.isFinite(value) ||
        value < limits[id][0] ||
        value > limits[id][1] ||
        (id === "plazo" && !Number.isInteger(value));

      $(id).setAttribute("aria-invalid", String(invalid));
      $(`${id}-error`).textContent = invalid ? messages[id] : "";

      if (invalid) {
        valid = false;
      }

      values[id] = value;
    }

    if (!valid) {
      return null;
    }

    return {
      monto: values.monto,
      tasa_interes_anual: values.tasa,
      plazo_meses: values.plazo,
      pago_extraordinario: values.extra,
      costo_seguro_por_millon: values.seguro
    };
  }

  function syncControls() {
    for (const id of ["monto", "tasa", "plazo", "extra"]) {
      const slider = $(`${id}-range`);
      const value = fieldNumber(id);

      if (id === "tasa") {
        slider.max = Math.max(60, Number.isFinite(value) ? value : 60);
      }

      if (id === "monto") {
        slider.max = Math.max(
          150000000,
          Number.isFinite(value) ? value : 150000000
        );
      }

      if (id === "extra") {
        slider.max = Math.max(
          1000000,
          Number.isFinite(value) ? value : 1000000
        );
      }

      slider.value = Number.isFinite(value) ? value : slider.min;

      const progress =
        (Number(slider.value) - Number(slider.min)) /
        (Number(slider.max) - Number(slider.min)) *
        100;

      slider.style.setProperty(
        "--progress",
        `${Math.max(0, Math.min(100, progress))}%`
      );

      const accessibleValue = !Number.isFinite(value)
        ? "Valor pendiente"
        : id === "tasa"
          ? `${value} por ciento efectiva anual`
          : id === "plazo"
            ? `${value} meses`
            : `${pesos.format(value)} pesos`;

      slider.setAttribute("aria-valuetext", accessibleValue);
    }

    const months = fieldNumber("plazo");
    const years = Math.floor(months / 12);

    $("years-label").textContent = !Number.isFinite(months)
      ? ""
      : months < 12
        ? "Menos de 1 año"
        : `${years} ${years === 1 ? "año" : "años"}${
            months % 12 ? ` y ${months % 12} m` : ""
          }`;

    $("rate-max-label").textContent =
      `${pesos.format($("tasa-range").max)} %`;

    $("amount-max-label").textContent =
      Number($("monto-range").max) === 150000000
        ? "$150 millones"
        : compact(Number($("monto-range").max));

    document.querySelectorAll("[data-amount]").forEach((button) => {
      button.classList.toggle(
        "active",
        Number(button.dataset.amount) === fieldNumber("monto")
      );
    });

    $("no-extra").textContent = fieldNumber("extra") > 0
      ? "Comparar sin abonos extras"
      : `Probar abonos de $${pesos.format(state.extraBefore)} / mes`;
  }

  function setBusy(busy, message) {
    $("results").setAttribute("aria-busy", String(busy));
    $("calculation-status").lastElementChild.textContent = message;

    $("download").disabled =
      busy || !state.result || state.downloading;
  }

  async function fetchPreview(params, signal) {
   const response = await fetch("https://dfcmax.onrender.com", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
      signal
    });

    if (!response.ok) {
      throw new Error(
        response.status === 422
          ? "Revisa los valores de tu escenario."
          : "No pudimos actualizar tu escenario. Inténtalo de nuevo."
      );
    }

    return response.json();
  }

  function schedule(immediate = false) {
    state.generation++;
    const generation = state.generation;

    clearTimeout(state.timer);
    state.controller?.abort();

    syncControls();

    const params = validate();
    $("error-banner").hidden = true;

    if (!params) {
      setBusy(true, "Revisa los campos indicados");
      return;
    }

    setBusy(true, "Actualizando escenario");

    state.timer = setTimeout(
      () => calculate(params, generation),
      immediate ? 0 : 160
    );
  }

  async function calculate(params, generation) {
    const controller = new AbortController();
    state.controller = controller;

    const timeout = setTimeout(
      () => controller.abort("timeout"),
      15000
    );

    const baseParams = {
      ...params,
      pago_extraordinario: 0
    };

    const baseKey = JSON.stringify(baseParams);

    try {
      const actualPromise = fetchPreview(params, controller.signal);

      const basePromise = params.pago_extraordinario === 0
        ? actualPromise
        : baseCache.has(baseKey)
          ? Promise.resolve(baseCache.get(baseKey))
          : fetchPreview(baseParams, controller.signal);

      const [result, base] = await Promise.all([
        actualPromise,
        basePromise
      ]);

      if (generation !== state.generation) {
        return;
      }

      baseCache.set(baseKey, base);

      if (baseCache.size > 8) {
        baseCache.delete(baseCache.keys().next().value);
      }

      state.result = result;
      state.base = base;
      state.params = { ...params };
      state.page = 0;

      render();

      setBusy(
        false,
        params.tasa_interes_anual === 0
          ? "Tasa 0 % · sin intereses"
          : "Escenario actualizado"
      );
    } catch (error) {
      if (generation !== state.generation) {
        return;
      }

      if (
        error?.name === "AbortError" &&
        controller.signal.reason !== "timeout"
      ) {
        return;
      }

      $("error-banner").firstElementChild.textContent =
        controller.signal.aborted
          ? "El cálculo tardó más de lo esperado. Vuelve a intentar."
          : error instanceof TypeError
            ? "No logramos conectar. Revisa tu conexión y vuelve a intentar."
            : error?.message || "No pudimos completar el cálculo.";

      $("error-banner").hidden = false;
      setBusy(true, "Escenario sin actualizar");
    } finally {
      clearTimeout(timeout);
    }
  }

  function renderDates() {
    const input = $("primera-cuota");

    const date = addMonths(
      input.value,
      (state.result?.plazo_real_meses || 1) - 1
    );

    input.classList.toggle("invalid-date", !date);
    input.setAttribute("aria-invalid", String(!date));

    $("finish-date").textContent = date
      ? monthFormat.format(date).replace(".", "")
      : "Elige una fecha";

    $("first-date-label").textContent = date
      ? `Primera cuota: ${fullDate.format(addMonths(input.value, 0))}`
      : "En los ajustes de tu proyección";
  }

  function render() {
    const result = state.result;
    const base = state.base;
    const params = state.params;

    $("monthly-payment").textContent = fmt(result.cuota_mensual);

    $("monthly-payment").classList.toggle(
      "long-amount",
      fmt(result.cuota_mensual).length > 17
    );

    $("monthly-insurance").textContent = fmt(result.seguro_mensual);
    $("payment-count").textContent = pesos.format(result.plazo_real_meses);

    const fewer = base.plazo_real_meses - result.plazo_real_meses;

    $("term-saved").textContent = fewer > 0
      ? `${pesos.format(fewer)} ${
          fewer === 1 ? "mes" : "meses"
        } antes que sin extras`
      : "Tu plazo proyectado";

    $("extra-payment-note").hidden =
      params.pago_extraordinario === 0;

    $("extra-payment-note").textContent =
      `+ ${fmt(params.pago_extraordinario)} de abono extra / mes`;

    $("total-interest").textContent = fmt(result.total_intereses);

    $("interest-saved").textContent = fmt(
      Math.max(0, base.total_intereses - result.total_intereses)
    );

    $("impact-copy").textContent = params.pago_extraordinario > 0
      ? `Con $${pesos.format(params.pago_extraordinario)} extra al mes, ${
          fewer > 0
            ? `terminas ${pesos.format(fewer)} ${
                fewer === 1 ? "mes" : "meses"
              } antes`
            : "reduces tu capital pendiente"
        }.`
      : "Prueba un abono mensual extra y descubre cuánto tiempo e intereses puedes ahorrar.";

    $("chart-caption").textContent =
      params.tasa_interes_anual === 0
        ? "Sin intereses. Cada abono reduce directamente tu capital."
        : `Saldo en cero después de ${pesos.format(
            result.plazo_real_meses
          )} ${result.plazo_real_meses === 1 ? "cuota" : "cuotas"}.`;

    $("baseline-key").hidden =
      params.pago_extraordinario === 0;

    renderDates();
    renderLineChart();
    renderDonut();
    renderTable();
  }

  function renderLineChart() {
    if (!state.result) {
      return;
    }

    const host = $("balance-chart");
    const width = Math.max(340, $("line-chart-wrap").clientWidth);
    const height = width < 500 ? 235 : 255;

    const left = 53;
    const right = 15;
    const top = 15;
    const bottom = 35;

    const maxBalance =
      state.result.tabla_amortizacion[0].saldo_inicial;

    const maxMonth = Math.max(
      state.base.plazo_real_meses,
      state.result.plazo_real_meses
    );

    const x = (month) =>
      left + month / maxMonth * (width - left - right);

    const y = (balance) =>
      top + (1 - balance / maxBalance) * (height - top - bottom);

    const makePath = (rows) =>
      `M${x(0)},${y(maxBalance)} ` +
      rows.map((row) => `L${x(row.mes)},${y(row.saldo_final)}`).join(" ");

    const actualPath = makePath(state.result.tabla_amortizacion);

    const svg = svgElement("svg", {
      viewBox: `0 0 ${width} ${height}`,
      "aria-hidden": "true"
    });

    const defs = svgElement("defs");
    const gradient = svgElement("linearGradient", {
      id: "balance-gradient",
      x1: 0,
      x2: 0,
      y1: 0,
      y2: 1
    });

    gradient.append(
      svgElement("stop", {
        offset: "0%",
        "stop-color": "#a7c57c",
        "stop-opacity": .23
      }),
      svgElement("stop", {
        offset: "100%",
        "stop-color": "#a7c57c",
        "stop-opacity": .01
      })
    );

    defs.append(gradient);
    svg.append(defs);

    for (let tick = 0; tick <= 4; tick++) {
      const amount = maxBalance * tick / 4;

      svg.append(svgElement("line", {
        x1: left,
        x2: width - right,
        y1: y(amount),
        y2: y(amount),
        class: "grid-line"
      }));

      const label = svgElement("text", {
        x: left - 12,
        y: y(amount) + 4,
        "text-anchor": "end",
        class: "axis-text"
      });

      label.textContent = compact(amount);
      svg.append(label);
    }

    for (let tick = 0; tick <= 4; tick++) {
      const month = Math.round(maxMonth * tick / 4);

      const label = svgElement("text", {
        x: x(month),
        y: height - 10,
        "text-anchor":
          tick === 0 ? "start" : tick === 4 ? "end" : "middle",
        class: "axis-text"
      });

      label.textContent = `Mes ${pesos.format(month)}`;
      svg.append(label);
    }

    svg.append(svgElement("path", {
      d:
        `${actualPath} ` +
        `L${x(state.result.plazo_real_meses)},${y(0)} ` +
        `L${x(0)},${y(0)} Z`,
      fill: "url(#balance-gradient)"
    }));

    if (state.params.pago_extraordinario > 0) {
      svg.append(svgElement("path", {
        d: makePath(state.base.tabla_amortizacion),
        class: "baseline-line"
      }));
    }

    svg.append(
      svgElement("path", {
        d: actualPath,
        class: "balance-line"
      }),
      svgElement("circle", {
        cx: x(state.result.plazo_real_meses),
        cy: y(0),
        r: 4,
        fill: "#285d40",
        stroke: "white",
        "stroke-width": 2
      })
    );

    const crosshair = svgElement("g", {
      id: "crosshair",
      hidden: ""
    });

    crosshair.append(
      svgElement("line", {
        id: "crosshair-line",
        y1: top,
        y2: y(0),
        stroke: "#8ba776",
        "stroke-width": 1,
        "stroke-dasharray": "3 3"
      }),
      svgElement("circle", {
        id: "crosshair-dot",
        r: 4,
        fill: "#285d40",
        stroke: "white",
        "stroke-width": 2
      })
    );

    svg.append(crosshair);
    host.replaceChildren(svg);

    state.chart = {
      x,
      y,
      left,
      right,
      width,
      maxMonth,
      top,
      maxBalance,
      cursor: 0
    };

    $("chart-tooltip").hidden = true;
  }

  function showMonth(month) {
    if (!state.chart) {
      return;
    }

    const chart = state.chart;
    month = Math.max(0, Math.min(chart.maxMonth, month));
    chart.cursor = month;

    const row = state.result.tabla_amortizacion[month - 1];

    const balance = month === 0
      ? chart.maxBalance
      : row
        ? row.saldo_final
        : 0;

    const crosshair = $("crosshair");

    if (!crosshair) {
      return;
    }

    crosshair.removeAttribute("hidden");

    $("crosshair-line").setAttribute("x1", chart.x(month));
    $("crosshair-line").setAttribute("x2", chart.x(month));
    $("crosshair-dot").setAttribute("cx", chart.x(month));
    $("crosshair-dot").setAttribute("cy", chart.y(balance));

    const tooltip = $("chart-tooltip");
    tooltip.replaceChildren();

    tooltip.append(
      document.createTextNode(
        month === 0 ? "Tu punto de partida" : `Mes ${month}`
      )
    );

    const strong = document.createElement("strong");
    strong.textContent = fmt(balance);
    tooltip.append(strong);

    const small = document.createElement("small");

    small.textContent = month > state.result.plazo_real_meses
      ? "Tu crédito ya está cancelado"
      : row
        ? `Pago del mes: ${fmt(row.cuota)}`
        : "Saldo pendiente";

    tooltip.append(small);
    tooltip.hidden = false;

    tooltip.style.left =
      `${Math.max(
        0,
        Math.min(
          chart.width - tooltip.offsetWidth,
          chart.x(month) + 12
        )
      )}px`;

    tooltip.style.top =
      `${Math.max(0, Math.min(120, chart.y(balance) - 75))}px`;
  }

  function hideTooltip() {
    $("chart-tooltip").hidden = true;
    $("crosshair")?.setAttribute("hidden", "");
  }

  $("balance-chart").addEventListener("pointermove", (event) => {
    const chart = state.chart;

    if (!chart) {
      return;
    }

    const rect = event.currentTarget.getBoundingClientRect();
    const offset =
      (event.clientX - rect.left) * chart.width / rect.width;

    const month = Math.round(
      (offset - chart.left) /
      (chart.width - chart.left - chart.right) *
      chart.maxMonth
    );

    showMonth(month);
  });

  $("balance-chart").addEventListener("pointerleave", hideTooltip);
  $("balance-chart").addEventListener("blur", hideTooltip);

  $("balance-chart").addEventListener("keydown", (event) => {
    const allowed = ["ArrowLeft", "ArrowRight", "Home", "End"];

    if (!allowed.includes(event.key) || !state.chart) {
      return;
    }

    event.preventDefault();

    const month = event.key === "Home"
      ? 0
      : event.key === "End"
        ? state.chart.maxMonth
        : state.chart.cursor + (
            event.key === "ArrowRight" ? 1 : -1
          );

    showMonth(month);
  });

  function renderDonut() {
    const result = state.result;

    const totalCapital = sumCents(
      result.tabla_amortizacion,
      "pago_capital"
    );

    const extra = sumCents(
      result.tabla_amortizacion,
      "pago_extraordinario"
    );

    const parts = [
      {
        label: "Capital ordinario",
        value: Math.max(
          0,
          Math.round((totalCapital - extra) * 100) / 100
        ),
        color: "#285d40"
      },
      {
        label: "Intereses",
        value: result.total_intereses,
        color: "#b6d781"
      },
      {
        label: "Seguros",
        value: result.total_seguros,
        color: "#cfbc8e"
      },
      {
        label: "Abonos extras",
        value: extra,
        color: "#7fa69d"
      }
    ];

    const circumference = 2 * Math.PI * 76;
    let offset = 0;

    const svg = svgElement("svg", {
      viewBox: "0 0 200 200",
      "aria-hidden": "true"
    });

    svg.append(svgElement("circle", {
      cx: 100,
      cy: 100,
      r: 76,
      fill: "none",
      stroke: "#f0f3eb",
      "stroke-width": 23
    }));

    $("payment-chart").replaceChildren(svg);
    $("breakdown-legend").replaceChildren();

    const reset = () => {
      $("donut-center-label").textContent = "COSTO TOTAL";
      $("donut-center-value").textContent =
        compact(result.costo_total_prestamo);
      $("donut-center-percent").textContent = "100 % de tus pagos";

      svg.querySelectorAll(".donut-segment").forEach((arc) => {
        arc.classList.remove("highlight", "dim");
      });
    };

    parts.forEach((part, index) => {
      const percent = result.costo_total_prestamo > 0
        ? part.value / result.costo_total_prestamo * 100
        : 0;

      const length = percent / 100 * circumference;

      const arc = svgElement("circle", {
        cx: 100,
        cy: 100,
        r: 76,
        fill: "none",
        stroke: part.color,
        "stroke-width": 23,
        "stroke-dasharray":
          `${length} ${Math.max(0, circumference - length)}`,
        "stroke-dashoffset": -offset,
        class: "donut-segment",
        "data-segment": index
      });

      if (part.value > 0) {
        svg.append(arc);
      }

      offset += length;

      const button = document.createElement("button");
      button.className = "legend-row";
      button.type = "button";

      const dot = document.createElement("span");
      dot.className = "legend-dot";
      dot.style.backgroundColor = part.color;

      const text = document.createElement("span");
      text.className = "legend-text";

      const label = document.createElement("span");
      label.className = "legend-label";
      label.textContent = part.label;

      const amount = document.createElement("strong");
      amount.className = "legend-amount";
      amount.textContent = fmt(part.value);

      const share = document.createElement("span");
      share.className = "legend-percent";
      share.textContent = `${decimals.format(percent)} %`;

      text.append(label, amount);
      button.append(dot, text, share);

      const highlight = () => {
        $("donut-center-label").textContent =
          part.label.toUpperCase();

        $("donut-center-value").textContent = compact(part.value);

        $("donut-center-percent").textContent =
          `${decimals.format(percent)} % de tus pagos`;

        svg.querySelectorAll(".donut-segment").forEach((segment) => {
          segment.classList.toggle("highlight", segment === arc);
          segment.classList.toggle("dim", segment !== arc);
        });
      };

      button.addEventListener("mouseenter", highlight);
      button.addEventListener("focus", highlight);
      button.addEventListener("mouseleave", reset);
      button.addEventListener("blur", reset);
      button.addEventListener("click", highlight);
      arc.addEventListener("pointerenter", highlight);
      arc.addEventListener("pointerleave", reset);

      $("breakdown-legend").append(button);
    });

    reset();
  }

  function renderTable() {
    const rows = state.result.tabla_amortizacion;
    const start = state.page * 12;

    $("amortization-body").replaceChildren();

    rows.slice(start, start + 12).forEach((row) => {
      const tr = document.createElement("tr");

      const values = [
        String(row.mes),
        fmt(row.saldo_inicial),
        fmt(row.pago_capital),
        fmt(row.pago_interes),
        fmt(row.pago_extraordinario),
        fmt(row.seguro_vida),
        fmt(row.cuota),
        fmt(row.saldo_final)
      ];

      values.forEach((value) => {
        const td = document.createElement("td");
        td.textContent = value;
        tr.append(td);
      });

      $("amortization-body").append(tr);
    });

    $("page-label").textContent =
      `Cuotas ${start + 1}–${Math.min(
        start + 12,
        rows.length
      )} de ${rows.length}`;

    $("prev-page").disabled = state.page === 0;
    $("next-page").disabled = start + 12 >= rows.length;
  }

  fields.forEach((id) => {
    $(id).addEventListener("input", () => schedule());

    if (["monto", "extra"].includes(id)) {
      $(id).addEventListener("blur", () => {
        const value = fieldNumber(id);

        if (Number.isFinite(value)) {
          $(id).value = pesos.format(value);
        }
      });
    }

    const slider = $(`${id}-range`);

    slider?.addEventListener("input", () => {
      $(id).value = ["monto", "extra"].includes(id)
        ? pesos.format(slider.value)
        : slider.value;

      schedule();
    });
  });

  $("credit-form").addEventListener("submit", (event) => {
    event.preventDefault();
    schedule(true);
  });

  $("primera-cuota").addEventListener("input", renderDates);

  document.querySelectorAll("[data-amount]").forEach((button) => {
    button.addEventListener("click", () => {
      $("monto").value = pesos.format(Number(button.dataset.amount));
      schedule();
    });
  });

  $("no-extra").addEventListener("click", () => {
    const current = fieldNumber("extra");

    if (current > 0) {
      state.extraBefore = current;
    }

    $("extra").value = pesos.format(
      current > 0 ? 0 : state.extraBefore
    );

    schedule();
  });

  $("reset").addEventListener("click", () => {
    for (const [id, value] of Object.entries(defaults)) {
      $(id).value = ["monto", "extra"].includes(id)
        ? pesos.format(value)
        : value;
    }

    $("primera-cuota").value = defaultDate();
    state.extraBefore = defaults.extra;

    schedule(true);
  });

  $("retry").addEventListener("click", () => schedule(true));

  $("prev-page").addEventListener("click", () => {
    if (state.page > 0) {
      state.page--;
      renderTable();
    }
  });

  $("next-page").addEventListener("click", () => {
    if (
      (state.page + 1) * 12 <
      state.result.tabla_amortizacion.length
    ) {
      state.page++;
      renderTable();
    }
  });

  document.querySelector('a[href="#plan"]').addEventListener(
    "click",
    () => {
      $("plan").open = true;
    }
  );

  document.querySelectorAll(".help-trigger").forEach((button) => {
    button.addEventListener("click", () => {
      $("help-dialog").showModal();
    });
  });

  $("close-help").addEventListener("click", () => {
    $("help-dialog").close();
  });

  $("help-dialog").addEventListener("click", (event) => {
    if (event.target !== event.currentTarget) {
      return;
    }

    const rect = event.currentTarget.getBoundingClientRect();

    if (
      event.clientX < rect.left ||
      event.clientX > rect.right ||
      event.clientY < rect.top ||
      event.clientY > rect.bottom
    ) {
      event.currentTarget.close();
    }
  });

  $("download").addEventListener("click", async () => {
    if (
      !state.params ||
      $("results").getAttribute("aria-busy") === "true"
    ) {
      return;
    }

    const params = { ...state.params };
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20000);

    state.downloading = true;
    $("download").disabled = true;
    $("download-status").textContent = "Preparando tu plan…";

    try {
      const response = await fetch("https://dfcmax.onrender.com/excel", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(params),
        signal: controller.signal
      });

      if (!response.ok) {
        throw new Error("No pudimos descargar el plan.");
      }

      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");

      link.href = url;
      link.download = "Mi_plan_Finia.xlsx";
      document.body.append(link);
      link.click();
      link.remove();

      setTimeout(() => URL.revokeObjectURL(url), 1000);

      $("download-status").textContent =
        "Tu plan está listo para descargar.";
    } catch (error) {
      $("download-status").textContent =
        error.name === "AbortError"
          ? "La descarga tardó demasiado. Vuelve a intentar."
          : "No pudimos descargar el plan. Inténtalo de nuevo.";
    } finally {
      clearTimeout(timeout);
      state.downloading = false;
      $("download").disabled =
        $("results").getAttribute("aria-busy") === "true";
    }
  });

  new ResizeObserver(() => {
    renderLineChart();
  }).observe($("line-chart-wrap"));

  schedule(true);
})();
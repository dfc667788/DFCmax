"""Exportación del motor de crédito: tablas auditables y gráficos nativos de Excel."""
from decimal import Decimal
from io import BytesIO
from math import ceil

from openpyxl import Workbook
from openpyxl.chart import DoughnutChart, LineChart, Reference
from openpyxl.chart.data_source import NumData, NumVal
from openpyxl.chart.label import DataLabelList
from openpyxl.chart.series import DataPoint, SeriesLabel
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.workbook.properties import CalcProperties

MONEY = '"$"#,##0.00;[Red]("$"#,##0.00)'
GREEN, LIME, MUTED = "285D40", "B6D781", "919B8D"
HEADERS = ["Mes", "Saldo Inicial", "Abono a Capital", "Pago Interés",
           "Abono Extraordinario", "Seguro Vida", "Cuota Total", "Saldo Restante"]


def _table(sheet, name, reference):
    table = Table(displayName=name, ref=reference)
    table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium4", showRowStripes=True)
    sheet.add_table(table)


def _heading(sheet, row, end=8):
    for cell in sheet[row][:end]:
        cell.fill = PatternFill("solid", fgColor=GREEN)
        cell.font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    sheet.row_dimensions[row].height = 30


def _title(sheet, title, note, columns=8):
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=columns)
    sheet.cell(1, 1, title).font = Font(name="Calibri", size=22, color=GREEN, bold=True)
    sheet.row_dimensions[1].height = 36
    sheet.merge_cells(start_row=3, start_column=1, end_row=3, end_column=columns)
    sheet.cell(3, 1, note).alignment = Alignment(wrap_text=True, vertical="center")
    sheet.row_dimensions[3].height = 36


def _plan(sheet, result, name):
    sheet.append(HEADERS)
    for payment in result.tabla_amortizacion:
        sheet.append([payment.mes, payment.saldo_inicial, payment.pago_capital,
                      payment.pago_interes, payment.pago_extraordinario,
                      payment.seguro_vida, payment.cuota, payment.saldo_final])
    _heading(sheet, 1)
    _table(sheet, name, f"A1:H{sheet.max_row}")
    sheet.auto_filter.ref = sheet.dimensions
    sheet.freeze_panes = "A2"
    sheet.column_dimensions["A"].width = 9
    for column in range(2, 9):
        sheet.column_dimensions[get_column_letter(column)].width = 23
        for cells in sheet.iter_rows(min_row=2, min_col=column, max_col=column):
            cells[0].number_format = MONEY
    sheet.print_title_rows = "1:1"
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0


def _cache(reference, values):
    reference.numCache = NumData(ptCount=len(values), pt=[
        NumVal(idx=i, v=value) for i, value in enumerate(values) if value is not None
    ])


def crear_libro_credito(result, inputs=None, baseline=None):
    """Los valores del plan pertenecen al motor; no se reimplementa la amortización."""
    book = Workbook()
    graphs = book.active
    graphs.title = "Gráficas"
    data = book.create_sheet("Datos del crédito")
    plan = book.create_sheet("Amortización")
    extras = book.create_sheet("Abonos adicionales")
    source = book.create_sheet("Datos gráficas")
    summary = book.create_sheet("Resumen")
    _plan(plan, result, "PlanPagos")
    last = plan.max_row
    principal = result.tabla_amortizacion[0].saldo_inicial
    _title(data, "Datos del crédito", "COP. Parámetros del escenario exportado; cambiar estos datos no recalcula el plan de pagos.", 3)
    data.append([])  # Las tablas comienzan en la fila 5.
    for c, text in enumerate(["Dato", "Valor", "Unidad / supuesto"], 1): data.cell(5, c, text)
    rows = [
        ("Monto prestado", principal, "COP", MONEY),
        ("Tasa anual ingresada", inputs.tasa_interes_anual / 100 if inputs else None, "Efectiva anual (EA)", "0.00%"),
        ("Convención de tasa", "Efectiva anual (EA)" if inputs else "No disponible", "No es nominal anual", None),
        ("Tasa efectiva mensual", result.tasa_mensual / 100, "Mes vencido; (1 + EA)^(1/12) - 1", "0.00000000%"),
        ("Plazo contratado", inputs.plazo_meses if inputs else None, "Meses", "0"),
        ("Plazo real", result.plazo_real_meses, "Meses hasta saldo cero", "0"),
        ("Abono mensual solicitado", inputs.pago_extraordinario if inputs else None, "COP; reduce plazo y conserva la cuota base", MONEY),
        ("Total de abonos aplicados", f"=SUM('Amortización'!E2:E{last})", "COP; incluido en Abono a Capital del plan", MONEY),
        ("Seguro mensual por millón", inputs.costo_seguro_por_millon if inputs else None, "COP por millón del monto inicial", MONEY),
        ("Seguro mensual", result.seguro_mensual, "COP; base constante: monto inicial", MONEY),
        ("Cuota financiera base", result.cuota_financiera, "COP; capital ordinario más interés", MONEY),
        ("Cuota base con seguro", result.cuota_mensual, "COP; excluye abono extraordinario", MONEY),
        ("Intereses totales", result.total_intereses, "COP", MONEY),
        ("Seguros totales", result.total_seguros, "COP", MONEY),
        ("Total pagado", result.costo_total_prestamo, "COP; capital, intereses, seguro y extras", MONEY),
        ("Tipo de crédito orientativo", result.tipo_credito, "Heurística del simulador; no oferta ni aprobación", None),
        ("Sistema", "Francés de cuota financiera fija", "Los extras reducen el plazo; el pago final se ajusta al saldo", None),
        ("Otros cargos", "No modelados", "No se incluyen comisiones ni gastos no presentes en el simulador", None),
    ]
    for row, (label, value, unit, number_format) in enumerate(rows, 6):
        data.append([label, value if value is not None else "No disponible", unit])
        if number_format: data.cell(row, 2).number_format = number_format
    _heading(data, 5, 3)
    _table(data, "DatosCredito", f"A5:C{data.max_row}")
    data.freeze_panes = "B6"
    for column, width in {"A":32, "B":38, "C":74}.items(): data.column_dimensions[column].width = width
    for row in data.iter_rows(min_row=6):
        for cell in row: cell.alignment = Alignment(vertical="center", wrap_text=True)
        data.row_dimensions[row[0].row].height = 33

    _title(extras, "Abonos adicionales a capital", "Detalle mensual: el último abono puede ser menor que el solicitado. Los extras ya están incluidos en el capital del plan.", 4)
    for c, text in enumerate(["Mes", "Abono solicitado", "Abono aplicado", "Saldo al cierre"], 1): extras.cell(5, c, text)
    for row, payment in enumerate(result.tabla_amortizacion, 6):
        plan_row = row - 4
        extras.append([payment.mes, inputs.pago_extraordinario if inputs else "No disponible",
                       f"='Amortización'!E{plan_row}", f"='Amortización'!H{plan_row}"])
        for column in [2, 3, 4]: extras.cell(row, column).number_format = MONEY
    _heading(extras, 5, 4)
    _table(extras, "DetalleAbonos", f"A5:D{extras.max_row}")
    extras.freeze_panes = "B6"
    for column in "ABCD": extras.column_dimensions[column].width = 25

    # Comparación equivalente a la pantalla, calculada por el mismo motor sin extras.
    if baseline is not None:
        baseline_sheet = book.create_sheet("Sin abonos")
        _plan(baseline_sheet, baseline, "PlanSinAbonos")
    source.append(["Mes", "Tu escenario", "Sin abonos extras", None, "Componente", "Pagos (COP)"])
    months = max(result.plazo_real_meses, baseline.plazo_real_meses if baseline else 0)
    actual_values = [principal] + [row.saldo_final for row in result.tabla_amortizacion]
    actual_values += [None] * (months + 1 - len(actual_values))
    base_values = [principal] + [row.saldo_final for row in baseline.tabla_amortizacion] if baseline else None
    for month in range(months + 1):
        source.cell(month + 2, 1, month)
        if month == 0: source.cell(2, 2, "='Datos del crédito'!B6")
        elif month <= result.plazo_real_meses: source.cell(month + 2, 2, f"='Amortización'!H{month + 1}")
        if baseline:
            source.cell(month + 2, 3, "='Datos del crédito'!B6" if month == 0 else f"='Sin abonos'!H{month + 1}")
    component_formulas = [f"=SUM('Amortización'!C2:C{last})-SUM('Amortización'!E2:E{last})",
                          f"=SUM('Amortización'!D2:D{last})", f"=SUM('Amortización'!F2:F{last})", f"=SUM('Amortización'!E2:E{last})"]
    extra_total = sum(Decimal(str(row.pago_extraordinario)) for row in result.tabla_amortizacion)
    capital_total = sum(Decimal(str(row.pago_capital)) for row in result.tabla_amortizacion)
    component_values = [float(capital_total - extra_total), result.total_intereses, result.total_seguros, float(extra_total)]
    for row, (label, formula) in enumerate(zip(["Capital ordinario", "Intereses", "Seguros", "Abonos extras"], component_formulas), 2):
        source.cell(row, 5, label); source.cell(row, 6, formula)
    for row in source.iter_rows(min_row=2):
        for column in [2, 3, 6]: row[column - 1].number_format = MONEY
    for column in "ABCEF": source.column_dimensions[column].width = 25
    source.freeze_panes = "B2"
    _heading(source, 1, 6)

    _title(graphs, "Finia · Tu crédito en perspectiva", "Gráficos nativos vinculados a datos. Mes 0 = desembolso. El escenario con extras termina al cancelar el saldo.", 12)
    for column in range(1, 13): graphs.column_dimensions[get_column_letter(column)].width = 11
    graphs.sheet_view.showGridLines = False
    graphs["A5"] = "Monto prestado (COP)"; graphs["D5"] = "='Datos del crédito'!B6"; graphs["D5"].number_format = MONEY
    graphs["G5"] = "Plazo real (meses)"; graphs["J5"] = result.plazo_real_meses
    curve = LineChart()
    curve.title = "Saldo pendiente por mes (COP)"
    curve.x_axis.title, curve.y_axis.title = "Mes desde el desembolso", "Saldo (COP)"
    curve.y_axis.scaling.min = 0
    curve.y_axis.numFmt = '"$"#,##0'
    curve.x_axis.tickLblSkip = max(1, ceil(months / 10))
    curve.display_blanks = "gap"
    curve.width, curve.height = 26, 11
    curve.add_data(Reference(source, min_col=2, max_col=3 if baseline else 2, min_row=1, max_row=months + 2), titles_from_data=True)
    curve.set_categories(Reference(source, min_col=1, min_row=2, max_row=months + 2))
    for index, series in enumerate(curve.series):
        series.tx = SeriesLabel(v="Tu escenario" if index == 0 else "Sin abonos extras")
        series.graphicalProperties.line.solidFill = GREEN if index == 0 else MUTED
        series.graphicalProperties.line.width = 26000
        if index: series.graphicalProperties.line.prstDash = "sysDash"
        _cache(series.val.numRef, actual_values if index == 0 else base_values)
        _cache(series.cat.numRef, list(range(months + 1)))
    curve.legend.position = "b"
    graphs.add_chart(curve, "A7")
    donut = DoughnutChart()
    donut.title = "Distribución de tus pagos (COP)"
    donut.holeSize = 65
    donut.width, donut.height = 23, 11
    donut.add_data(Reference(source, min_col=6, min_row=1, max_row=5), titles_from_data=True)
    donut.set_categories(Reference(source, min_col=5, min_row=2, max_row=5))
    _cache(donut.series[0].val.numRef, component_values)
    for index, color in enumerate([GREEN, LIME, "CFBC8E", "7FA69D"]):
        point = DataPoint(idx=index)
        point.graphicalProperties.solidFill = color
        donut.series[0].data_points.append(point)
    donut.dataLabels = DataLabelList()
    donut.dataLabels.showPercent = True
    donut.legend.position = "b"
    graphs.add_chart(donut, "A30")
    graphs.print_area = "A1:L53"
    graphs.sheet_properties.pageSetUpPr.fitToPage = True
    graphs.page_setup.orientation = "portrait"
    graphs.page_setup.fitToWidth = 1
    graphs.page_setup.fitToHeight = 1

    # Se conserva el contrato anterior de la hoja Resumen.
    for name in ["tipo_credito", "cuota_mensual", "cuota_financiera", "seguro_mensual", "total_intereses", "total_seguros", "costo_total_prestamo", "plazo_real_meses"]:
        summary.append([name, getattr(result, name)])
    summary.column_dimensions["A"].width, summary.column_dimensions["B"].width = 32, 30
    for row in summary.iter_rows(min_row=2, max_row=7): row[1].number_format = MONEY
    book.calculation = CalcProperties(fullCalcOnLoad=True, forceFullCalc=True)
    book.active = 0
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()
